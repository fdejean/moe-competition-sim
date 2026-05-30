"""086 epadaptive_k32 (co-spiking <=EP128, 32 temporal blocks) — run EXPENSIVE co-spiking placement only where the budget allows.

The wall all session: co-spiking-aware per-device placement (block balancing) lowers
PAR on the proxy but FLOORS at EP128/256 (the 3x-DS-EPLB per-case time budget is tiny
when n_device is large). KEY: that budget is PER CASE, and at EP32/64 it is generous
(~0.9-1.5s; block balancing runs in ~0.2s there). So dispatch by n_device:
  * n_device <= 64  -> co-spiking BLOCK-BALANCED placement (balance each device's load
    across k temporal blocks => spread experts that spike in the same iterations).
  * n_device >= 128 -> fast finance_band water-fill + LPT (the only thing that survives).
Then the proven PAR-neutral transmit machinery (O(nd) device relabel + slot preserve)
+ no-trade band on top. This finally tests co-spiking on the server for the 16 EP32/64
cases without risking the -1e6 timeout on the EP128/256 cases.

Tunables: FB_TAU, FB_LAMBDA, FB_BLOCKS (k for low-EP co-spiking, default 8),
FB_EPCUT (n_device threshold for co-spiking, default 64).
"""
from __future__ import annotations

import os

import numpy as np

TAU = float(os.environ.get("FB_TAU", "0.2"))
LAMBDA = float(os.environ.get("FB_LAMBDA", "2.0"))
BLOCKS = int(os.environ.get("FB_BLOCKS", "32"))
EPCUT = int(os.environ.get("FB_EPCUT", "128"))

_STATE: dict = {}


def reset():
    _STATE.clear()


# ---- fast path: finance_band water-fill + LPT (used at high EP) ----
def _build_fast(weight, n_device, n_per):
    n_experts = weight.shape[0]
    n_slots = n_device * n_per
    replicas = np.ones(n_experts, dtype=np.int64)
    for _ in range(n_slots - n_experts):
        replicas[int(np.argmax(weight / replicas))] += 1
    slot_expert = np.repeat(np.arange(n_experts), replicas)
    slot_weight = weight[slot_expert] / replicas[slot_expert]
    order = np.argsort(slot_weight, kind="stable")[::-1]
    deploy = np.full((n_device, n_per), -1, dtype=np.int64)
    dev_load = np.zeros(n_device)
    dev_count = np.zeros(n_device, dtype=np.int64)
    big = float(slot_weight.sum()) + 1.0
    for idx in order:
        e = slot_expert[idx]
        score = dev_load + np.where((deploy == e).any(axis=1), big, 0.0)
        score[dev_count >= n_per] = np.inf
        d = int(np.argmin(score))
        deploy[d, int(dev_count[d])] = e
        dev_load[d] += slot_weight[idx]
        dev_count[d] += 1
    return deploy


# ---- co-spiking path: block-balanced placement (used at low EP) ----
def _block_means(hot_layer, k):
    T = hot_layer.shape[0]
    if k <= 1 or T < k:
        return hot_layer.mean(axis=0)[:, None]
    idx = (np.arange(T) * k) // T
    out = np.zeros((hot_layer.shape[1], k))
    counts = np.bincount(idx, minlength=k).astype(np.float64)
    for j in range(k):
        out[:, j] = hot_layer[idx == j].sum(axis=0)
    out /= np.maximum(counts, 1.0)[None, :]
    return out


def _build_cospike(bsig, risk_scalar, n_device, n_per):
    n_experts = risk_scalar.shape[0]
    n_slots = n_device * n_per
    replicas = np.ones(n_experts, dtype=np.int64)
    for _ in range(n_slots - n_experts):
        replicas[int(np.argmax(risk_scalar / replicas))] += 1
    slot_expert = np.repeat(np.arange(n_experts), replicas)
    slot_sig = bsig[slot_expert] / replicas[slot_expert][:, None]
    slot_scalar = slot_sig.sum(axis=1)
    order = np.argsort(slot_scalar, kind="stable")[::-1]
    deploy = np.full((n_device, n_per), -1, dtype=np.int64)
    dev_block = np.zeros((n_device, bsig.shape[1]))
    dev_count = np.zeros(n_device, dtype=np.int64)
    present = np.zeros((n_device, n_experts), dtype=bool)
    big = float(slot_scalar.sum()) + 1.0
    for idx in order:
        e = slot_expert[idx]
        sig = slot_sig[idx]
        cost = (dev_block + sig[None, :]).max(axis=1) + np.where(present[:, e], big, 0.0)
        cost[dev_count >= n_per] = np.inf
        d = int(np.argmin(cost))
        deploy[d, int(dev_count[d])] = e
        dev_block[d] += sig
        dev_count[d] += 1
        present[d, e] = True
    return deploy


def _align(new, old, load):
    nd, sl = new.shape
    old_head = old[np.arange(nd), np.argmax(load[old], axis=1)]
    new_head = new[np.arange(nd), np.argmax(load[new], axis=1)]
    fh: dict = {}
    for j in range(nd):
        fh.setdefault(int(new_head[j]), []).append(j)
    assign = -np.ones(nd, dtype=np.int64)
    used = np.zeros(nd, dtype=bool)
    left = []
    for d in range(nd):
        lst = fh.get(int(old_head[d]))
        if lst:
            j = lst.pop(); assign[d] = j; used[j] = True
        else:
            left.append(d)
    rem = [j for j in range(nd) if not used[j]]
    for d, j in zip(left, rem):
        assign[d] = j
    rows = new[assign]
    out = np.empty_like(rows)
    for d in range(nd):
        src = rows[d]; oldrow = old[d]
        used_src = np.zeros(sl, dtype=bool); rowout = -np.ones(sl, dtype=np.int64)
        for s in range(sl):
            e = int(oldrow[s])
            for t in range(sl):
                if not used_src[t] and int(src[t]) == e:
                    rowout[s] = e; used_src[t] = True; break
        rs = [int(src[t]) for t in range(sl) if not used_src[t]]; ri = 0
        for s in range(sl):
            if rowout[s] < 0:
                rowout[s] = rs[ri]; ri += 1
        out[d] = rowout
    return out


def _par(load, deploy):
    cut = np.bincount(deploy.reshape(-1), minlength=load.shape[0])
    cut[cut == 0] = 1
    w = load / cut
    loads = w[deploy.reshape(-1)].reshape(deploy.shape).sum(-1)
    return float(loads.max() / loads.mean())


def rebalance(hotness, n_device, n_red_expert):
    hotness = np.asarray(hotness, dtype=np.float64)
    mean = hotness.mean(axis=0)
    std = hotness.std(axis=0)
    risk = mean + LAMBDA * std if LAMBDA else mean
    n_layers, n_experts = mean.shape
    n_per = (n_experts + n_red_expert) // n_device
    low_ep = n_device <= EPCUT

    candidate = np.zeros((n_layers, n_device, n_per), dtype=np.int64)
    for l in range(n_layers):
        if low_ep:
            bsig = _block_means(hotness[:, l, :], BLOCKS)
            scale = np.where(mean[l] > 0, risk[l] / np.maximum(mean[l], 1e-12), 1.0)
            candidate[l] = _build_cospike(bsig * scale[:, None], risk[l], n_device, n_per)
        else:
            candidate[l] = _build_fast(risk[l], n_device, n_per)

    sig = (n_layers, n_experts, n_device, n_per)
    cur = _STATE.get("deploy") if _STATE.get("sig") == sig else None
    if cur is None:
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
