"""079 cospike_kpack — SOTA sticky_eplo + co-spiking-aware placement at SMALL k.

The residual. Server data: mean-load PAR ~1.05 (floor 1.0) but realized scored PAR
~1.696. The ~0.64 gap is CO-SPIKING — experts that spike in the SAME iterations
landing on the same device bottleneck those iterations. finance_band/sticky_eplo
LPT balances only the SCALAR mean load, blind to which experts co-spike.

Why this is the untried point. The peer's block_pack balanced each device's load
across k temporal blocks, but at k=8 (and finer k=16/32) it TIMED OUT at EP256.
Nobody tried COARSE k (2-3): far fewer ops, EP256-safe, and the dominant co-spiking
structure (a few macro-phases of the window) is captured by just 2-3 blocks. The
scalar co-spiking attempt (cospike_weight) HURT because a scalar can't represent
"which phase"; a k=3 vector can.

Mechanism: keep sticky_eplo's replication (variance water-fill), no-trade band, and
O(n_device) PAR-neutral relabel + sticky LPT unchanged. ONLY the device-selection
objective changes: each slot carries a k-vector of block-mean loads, each device
keeps a running k-vector, and the greedy assigns each slot to the device minimizing
the resulting WORST block load (+ collision penalty - stay-bias) instead of the
scalar sum. Two experts that both spike in block j now both raise that device's
block-j load, so the greedy spreads them across devices -> lower per-iteration
(co-spiking) peak. Single pass, O(n_slots * n_device * k); k=3 keeps it
finance_band-speed and EP256-safe.

Tunables (env): FB_TAU, FB_LAMBDA, FB_LAMBDA_HI, FB_STAY, FB_WINDOW (as sticky_eplo)
+ FB_BLOCKS (k, default 3; 1 == sticky_eplo).
"""
from __future__ import annotations

import os

import numpy as np

_w = os.environ.get("FB_WINDOW")
WINDOW = None if _w in (None, "", "all") else int(_w)
TAU = float(os.environ.get("FB_TAU", "0.2"))
LAMBDA = float(os.environ.get("FB_LAMBDA", "2.0"))
LAMBDA_HI = float(os.environ.get("FB_LAMBDA_HI", "1.5"))
STAY = float(os.environ.get("FB_STAY", "0.25"))
BLOCKS = int(os.environ.get("FB_BLOCKS", "3"))

_STATE: dict = {}


def reset():
    _STATE.clear()


def _block_means_all(hotness, k):
    """hotness (T, L, E) -> (L, E, k) mean load per contiguous temporal block."""
    T = hotness.shape[0]
    if k <= 1 or T < k:
        return hotness.mean(axis=0)[:, :, None]
    idx = (np.arange(T) * k) // T
    L, E = hotness.shape[1], hotness.shape[2]
    out = np.zeros((L, E, k))
    counts = np.zeros(k)
    for j in range(k):
        m = idx == j
        counts[j] = m.sum()
        out[:, :, j] = hotness[m].sum(axis=0)
    out /= np.maximum(counts, 1.0)[None, None, :]
    return out


def _init_deploy_table(n_device, n_experts, slots):
    deploy = np.zeros((n_device, slots), dtype=np.int64)
    for d in range(n_device):
        for s in range(slots - 1):
            deploy[d, s] = (d * (slots - 1) + s) % n_experts
        deploy[d, -1] = deploy[d, -2]
    return deploy


def _build_layer(weight, block_w, n_device, n_exp_per_dev, prev=None):
    """weight: (E,) scalar risk for replica counts. block_w: (E,k) block-mean load
    for co-spiking-aware placement."""
    n_experts = weight.shape[0]
    n_slots = n_device * n_exp_per_dev
    replicas = np.ones(n_experts, dtype=np.int64)
    for _ in range(n_slots - n_experts):
        replicas[int(np.argmax(weight / replicas))] += 1

    slot_expert = np.repeat(np.arange(n_experts), replicas)
    slot_weight = weight[slot_expert] / replicas[slot_expert]          # scalar, for sort
    slot_block = block_w[slot_expert] / replicas[slot_expert][:, None]  # (n_slots, k)
    order = np.argsort(slot_weight, kind="stable")[::-1]

    k = slot_block.shape[1]
    deploy = np.full((n_device, n_exp_per_dev), -1, dtype=np.int64)
    dev_block = np.zeros((n_device, k))
    dev_count = np.zeros(n_device, dtype=np.int64)
    big = float(slot_weight.sum()) + 1.0
    stay_amt = STAY * (float(slot_weight.sum()) / max(n_device, 1))
    on_prev = None
    if prev is not None and stay_amt > 0:
        on_prev = np.zeros((n_device, n_experts), dtype=bool)
        for d in range(n_device):
            for e2 in prev[d]:
                if e2 >= 0:
                    on_prev[d, int(e2)] = True
    for idx in order:
        e = int(slot_expert[idx])
        # worst-block load if this slot joins each device
        score = (dev_block + slot_block[idx]).max(axis=1)
        score = score + np.where((deploy == e).any(axis=1), big, 0.0)
        if on_prev is not None:
            score = score - np.where(on_prev[:, e], stay_amt, 0.0)
        score[dev_count >= n_exp_per_dev] = np.inf
        d = int(np.argmin(score))
        deploy[d, int(dev_count[d])] = e
        dev_block[d] += slot_block[idx]
        dev_count[d] += 1
    return deploy


def _align(new, old, load):
    nd, sl = new.shape
    old_head = old[np.arange(nd), np.argmax(load[old], axis=1)]
    new_head = new[np.arange(nd), np.argmax(load[new], axis=1)]
    from_head: dict = {}
    for j in range(nd):
        from_head.setdefault(int(new_head[j]), []).append(j)
    assign = -np.ones(nd, dtype=np.int64)
    used = np.zeros(nd, dtype=bool)
    leftover_old = []
    for d in range(nd):
        lst = from_head.get(int(old_head[d]))
        if lst:
            j = lst.pop()
            assign[d] = j
            used[j] = True
        else:
            leftover_old.append(d)
    rem = [j for j in range(nd) if not used[j]]
    for d, j in zip(leftover_old, rem):
        assign[d] = j
    rows = new[assign]
    out = np.empty_like(rows)
    for d in range(nd):
        src = rows[d]
        oldrow = old[d]
        used_src = np.zeros(sl, dtype=bool)
        rowout = -np.ones(sl, dtype=np.int64)
        for s in range(sl):
            e = int(oldrow[s])
            for t in range(sl):
                if not used_src[t] and int(src[t]) == e:
                    rowout[s] = e
                    used_src[t] = True
                    break
        rem_src = [int(src[t]) for t in range(sl) if not used_src[t]]
        ri = 0
        for s in range(sl):
            if rowout[s] < 0:
                rowout[s] = rem_src[ri]
                ri += 1
        out[d] = rowout
    return out


def _par(load, deploy):
    cut = np.bincount(deploy.reshape(-1), minlength=load.shape[0])
    weights = load / cut
    loads = weights[deploy.reshape(-1)].reshape(deploy.shape).sum(-1)
    return float(loads.max() / loads.mean())


def rebalance(hotness, n_device, n_red_expert):
    hotness = np.asarray(hotness, dtype=np.float64)
    if WINDOW is not None:
        hotness = hotness[-WINDOW:]
    mean = hotness.mean(axis=0)
    lam = LAMBDA_HI if n_device >= 128 else LAMBDA
    risk = mean + lam * hotness.std(axis=0) if lam else mean
    block_w = _block_means_all(hotness, BLOCKS)        # (L, E, k)
    n_layers, n_experts = mean.shape
    n_exp_per_dev = (n_experts + n_red_expert) // n_device

    sig = (n_layers, n_experts, n_device, n_exp_per_dev)
    cur = _STATE.get("deploy") if _STATE.get("sig") == sig else None

    candidate = np.zeros((n_layers, n_device, n_exp_per_dev), dtype=np.int64)
    for l in range(n_layers):
        candidate[l] = _build_layer(risk[l], block_w[l], n_device, n_exp_per_dev,
                                    prev=(cur[l] if cur is not None else None))
    if cur is None:
        init = _init_deploy_table(n_device, n_experts, n_exp_per_dev)
        for l in range(n_layers):
            candidate[l] = _align(candidate[l], init, mean[l])
        _STATE["sig"] = sig
        _STATE["deploy"] = candidate.copy()
        return True, np.arange(n_layers, dtype=np.int64), candidate, None

    for l in range(n_layers):
        candidate[l] = _align(candidate[l], cur[l], mean[l])

    improve = np.array([_par(mean[l], cur[l]) - _par(mean[l], candidate[l])
                        for l in range(n_layers)])
    sel = np.where(improve > TAU)[0]
    if sel.size == 0:
        return False, np.array([], dtype=np.int64), candidate, None

    order = sel[np.argsort(improve[sel])[::-1]]
    new_state = cur.copy()
    new_state[order] = candidate[order]
    _STATE["deploy"] = new_state
    return True, order.astype(np.int64), candidate, None
