"""083 peak_cospike — COMBO of the two validated PAR levers (memo §9c, §9e).

peak_balance (joint-peak robust placement) hit server 112.159 (NEW SOTA), and the
EP-gated greedy block placement (cospike_hybrid) lowers realized PAR at EP<=128.
This combines them: the LPT/replication target is the JOINT-PEAK blend
  place_w = (1-THETA)*mean + THETA*peak,  peak = mean load over the top-PEAKFRAC
iterations by TOTAL layer load (the co-spiking spikes); AND placement balances
temporal blocks (worst-block greedy) at EP<=128, scalar joint-peak LPT at EP256
(where block greedy floors and E2 says co-spiking placement buys ~nothing anyway).

Two validated levers, one near-scalar pass. sticky_eplo's O(n_device) relabel +
stay-bias + no-trade band + EP-adaptive lambda are unchanged.

Tunables (env): FB_TAU, FB_LAMBDA, FB_LAMBDA_HI, FB_STAY, FB_WINDOW,
FB_BLOCKS (k, default 3), FB_BLOCK_MAXDEV (block path when n_device<=this, default
128), FB_THETA (peak weight, default 0.5), FB_PEAKFRAC (default 0.10).
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
BLOCK_MAXDEV = int(os.environ.get("FB_BLOCK_MAXDEV", "128"))
THETA = float(os.environ.get("FB_THETA", "0.7"))
PEAKFRAC = float(os.environ.get("FB_PEAKFRAC", "0.10"))

_STATE: dict = {}


def reset():
    _STATE.clear()


def _peak_mean(hotness):
    T, L, E = hotness.shape
    p = max(1, int(round(PEAKFRAC * T)))
    total = hotness.sum(axis=2)
    peak_idx = np.argpartition(total, T - p, axis=0)[T - p:]
    l_idx = np.broadcast_to(np.arange(L), (p, L))
    peaked = hotness[peak_idx, l_idx, :]
    return peaked.mean(axis=0)


def _block_means_all(hotness, k):
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


def _replicas_and_order(weight, n_device, n_exp_per_dev):
    n_experts = weight.shape[0]
    n_slots = n_device * n_exp_per_dev
    replicas = np.ones(n_experts, dtype=np.int64)
    for _ in range(n_slots - n_experts):
        replicas[int(np.argmax(weight / replicas))] += 1
    slot_expert = np.repeat(np.arange(n_experts), replicas)
    slot_weight = weight[slot_expert] / replicas[slot_expert]
    order = np.argsort(slot_weight, kind="stable")[::-1]
    return replicas, slot_expert, slot_weight, order


def _on_prev(prev, n_device, n_experts, stay_amt):
    if prev is None or stay_amt <= 0:
        return None
    on_prev = np.zeros((n_device, n_experts), dtype=bool)
    for d in range(n_device):
        for e2 in prev[d]:
            if e2 >= 0:
                on_prev[d, int(e2)] = True
    return on_prev


def _build_scalar(weight, n_device, n_exp_per_dev, prev=None):
    n_experts = weight.shape[0]
    replicas, slot_expert, slot_weight, order = _replicas_and_order(
        weight, n_device, n_exp_per_dev)
    deploy = np.full((n_device, n_exp_per_dev), -1, dtype=np.int64)
    dev_load = np.zeros(n_device)
    dev_count = np.zeros(n_device, dtype=np.int64)
    big = float(slot_weight.sum()) + 1.0
    stay_amt = STAY * (float(slot_weight.sum()) / max(n_device, 1))
    on_prev = _on_prev(prev, n_device, n_experts, stay_amt)
    for idx in order:
        e = int(slot_expert[idx])
        score = dev_load + np.where((deploy == e).any(axis=1), big, 0.0)
        if on_prev is not None:
            score = score - np.where(on_prev[:, e], stay_amt, 0.0)
        score[dev_count >= n_exp_per_dev] = np.inf
        d = int(np.argmin(score))
        deploy[d, int(dev_count[d])] = e
        dev_load[d] += slot_weight[idx]
        dev_count[d] += 1
    return deploy


def _build_block(weight, block_w, n_device, n_exp_per_dev, prev=None):
    n_experts = weight.shape[0]
    replicas, slot_expert, slot_weight, order = _replicas_and_order(
        weight, n_device, n_exp_per_dev)
    slot_block = block_w[slot_expert] / replicas[slot_expert][:, None]
    k = slot_block.shape[1]
    deploy = np.full((n_device, n_exp_per_dev), -1, dtype=np.int64)
    dev_block = np.zeros((n_device, k))
    dev_count = np.zeros(n_device, dtype=np.int64)
    big = float(slot_weight.sum()) + 1.0
    stay_amt = STAY * (float(slot_weight.sum()) / max(n_device, 1))
    on_prev = _on_prev(prev, n_device, n_experts, stay_amt)
    for idx in order:
        e = int(slot_expert[idx])
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
    peak = _peak_mean(hotness) if THETA > 0 else mean
    place_w = (1.0 - THETA) * mean + THETA * peak
    lam = LAMBDA_HI if n_device >= 128 else LAMBDA
    risk = place_w + lam * hotness.std(axis=0) if lam else place_w
    n_layers, n_experts = mean.shape
    n_exp_per_dev = (n_experts + n_red_expert) // n_device

    use_block = n_device <= BLOCK_MAXDEV
    block_w = _block_means_all(hotness, BLOCKS) if use_block else None

    sig = (n_layers, n_experts, n_device, n_exp_per_dev)
    cur = _STATE.get("deploy") if _STATE.get("sig") == sig else None

    candidate = np.zeros((n_layers, n_device, n_exp_per_dev), dtype=np.int64)
    for l in range(n_layers):
        prev = cur[l] if cur is not None else None
        if use_block:
            candidate[l] = _build_block(risk[l], block_w[l], n_device, n_exp_per_dev, prev)
        else:
            candidate[l] = _build_scalar(risk[l], n_device, n_exp_per_dev, prev)

    if cur is None:
        init = _init_deploy_table(n_device, n_experts, n_exp_per_dev)
        for l in range(n_layers):
            candidate[l] = _align(candidate[l], init, place_w[l])
        _STATE["sig"] = sig
        _STATE["deploy"] = candidate.copy()
        return True, np.arange(n_layers, dtype=np.int64), candidate, None

    for l in range(n_layers):
        candidate[l] = _align(candidate[l], cur[l], place_w[l])

    improve = np.array([_par(place_w[l], cur[l]) - _par(place_w[l], candidate[l])
                        for l in range(n_layers)])
    sel = np.where(improve > TAU)[0]
    if sel.size == 0:
        return False, np.array([], dtype=np.int64), candidate, None

    order = sel[np.argsort(improve[sel])[::-1]]
    new_state = cur.copy()
    new_state[order] = candidate[order]
    _STATE["deploy"] = new_state
    return True, order.astype(np.int64), candidate, None
