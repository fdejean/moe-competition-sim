"""080 peak_balance — SOTA sticky_eplo, but PLACE for the peak iterations (robust
minimax / joint-tail), not the window mean. Fully finance_band-speed (scalar LPT).

The residual lever. Realized scored PAR ~1.696 while mean-load PAR ~1.05 — the gap
is CO-SPIKING: the realized max device load is set by the WORST iterations (when
many hot experts fire together), but finance_band places to balance the window
MEAN and is blind to those peaks.

Prior fast attempts at this failed because they used MARGINAL tail stats:
  * quantile_pack: each expert's OWN p95 -> just inflates volatile experts (~mean+λstd). HURT.
  * cospike_weight: a scalar "busyness" premium -> distorts balance. HURT.
Both miss the JOINT structure (which experts are hot in the SAME iteration).

peak_balance uses the joint tail directly: per layer, find the top-P% iterations by
TOTAL load (the actual co-spiking spikes), and compute each expert's mean load
*during those peak iterations*. Placing/replicating to balance THAT vector balances
the devices for the iterations that set realized PAR — minimax over the window's
worst snapshots, not its average. It's still a single scalar LPT (one weight vector)
=> finance_band-speed, EP256-safe (no per-slot block loop).

A blend keeps it robust: place_w = (1-THETA)*mean + THETA*peak_mean. THETA=0
recovers sticky_eplo; THETA=1 places purely for the peaks.

Tunables (env): FB_TAU, FB_LAMBDA, FB_LAMBDA_HI, FB_STAY, FB_WINDOW (as sticky_eplo)
+ FB_THETA (peak weight, default 0.5) + FB_PEAKFRAC (top fraction of iters treated
as peaks, default 0.10).
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
THETA = float(os.environ.get("FB_THETA", "1.0"))
PEAKFRAC = float(os.environ.get("FB_PEAKFRAC", "0.10"))

_STATE: dict = {}


def reset():
    _STATE.clear()


def _peak_mean(hotness):
    """Per-(layer,expert) mean load over the top-PEAKFRAC iterations by TOTAL layer
    load (the co-spiking spikes). hotness: (T, L, E) -> (L, E)."""
    T, L, E = hotness.shape
    p = max(1, int(round(PEAKFRAC * T)))
    total = hotness.sum(axis=2)                       # (T, L) total load per layer/iter
    # indices of the p peak iterations per layer (argpartition, vectorized)
    peak_idx = np.argpartition(total, T - p, axis=0)[T - p:]   # (p, L)
    l_idx = np.broadcast_to(np.arange(L), (p, L))             # (p, L)
    peaked = hotness[peak_idx, l_idx, :]                       # (p, L, E)
    return peaked.mean(axis=0)                                 # (L, E)


def _init_deploy_table(n_device, n_experts, slots):
    deploy = np.zeros((n_device, slots), dtype=np.int64)
    for d in range(n_device):
        for s in range(slots - 1):
            deploy[d, s] = (d * (slots - 1) + s) % n_experts
        deploy[d, -1] = deploy[d, -2]
    return deploy


def _build_layer(weight, n_device, n_exp_per_dev, prev=None):
    n_experts = weight.shape[0]
    n_slots = n_device * n_exp_per_dev
    replicas = np.ones(n_experts, dtype=np.int64)
    for _ in range(n_slots - n_experts):
        replicas[int(np.argmax(weight / replicas))] += 1

    slot_expert = np.repeat(np.arange(n_experts), replicas)
    slot_weight = weight[slot_expert] / replicas[slot_expert]
    order = np.argsort(slot_weight, kind="stable")[::-1]

    deploy = np.full((n_device, n_exp_per_dev), -1, dtype=np.int64)
    dev_load = np.zeros(n_device)
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
        score = dev_load + np.where((deploy == e).any(axis=1), big, 0.0)
        if on_prev is not None:
            score = score - np.where(on_prev[:, e], stay_amt, 0.0)
        score[dev_count >= n_exp_per_dev] = np.inf
        d = int(np.argmin(score))
        deploy[d, int(dev_count[d])] = e
        dev_load[d] += slot_weight[idx]
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
    place_w = (1.0 - THETA) * mean + THETA * peak     # robust minimax target
    lam = LAMBDA_HI if n_device >= 128 else LAMBDA
    risk = place_w + lam * hotness.std(axis=0) if lam else place_w
    n_layers, n_experts = mean.shape
    n_exp_per_dev = (n_experts + n_red_expert) // n_device

    sig = (n_layers, n_experts, n_device, n_exp_per_dev)
    cur = _STATE.get("deploy") if _STATE.get("sig") == sig else None

    candidate = np.zeros((n_layers, n_device, n_exp_per_dev), dtype=np.int64)
    for l in range(n_layers):
        candidate[l] = _build_layer(risk[l], n_device, n_exp_per_dev,
                                    prev=(cur[l] if cur is not None else None))
    if cur is None:
        init = _init_deploy_table(n_device, n_experts, n_exp_per_dev)
        for l in range(n_layers):
            candidate[l] = _align(candidate[l], init, place_w[l])
        _STATE["sig"] = sig
        _STATE["deploy"] = candidate.copy()
        return True, np.arange(n_layers, dtype=np.int64), candidate, None

    for l in range(n_layers):
        candidate[l] = _align(candidate[l], cur[l], place_w[l])

    # Band judges on the SAME robust target the placement defends.
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
