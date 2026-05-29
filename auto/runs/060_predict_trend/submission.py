"""060 predict_trend — 020 base (band + variance replication + swap refine +
PAR-neutral transmit min) but with a PREDICTIVE estimator instead of a trailing
mean.

Motivation (my lane: prediction). A deployment, once applied, is HELD for many
future iterations (the no-trade band gates redeploys). So the right load to
place for is the load the deployment will *serve next*, not the stale window
that just passed. Expert hotness is temporally persistent (lag-1 autocorr ~0.5,
LYNX) but it also DRIFTS, and pure recency weighting (EWMA) gave no edge over a
flat mean (flo's EDA) — because EWMA chases noise, not drift. A linear-trend
extrapolation is different: it estimates the *direction* of drift from the
window's two halves and projects HORIZON half-windows forward, so we skate to
where the puck is going.

  forecast = recent_half_mean + HORIZON * (recent_half_mean - older_half_mean)

The whole pipeline (replica water-filling, LPT pack, cut-preserving swap refine,
no-trade band) then targets `forecast` instead of `mean`; variance replication
still uses the full-window std. HORIZON=0 recovers the 020 base exactly.

Tunables (env): FB_TAU, FB_LAMBDA, FB_WINDOW (as before) + FB_HORIZON (default
0.5 half-windows of forward projection; 0 disables prediction).
"""
from __future__ import annotations

import os
import time

import numpy as np

_w = os.environ.get("FB_WINDOW")
WINDOW = None if _w in (None, "", "all") else int(_w)
TAU = float(os.environ.get("FB_TAU", "0.2"))
LAMBDA = float(os.environ.get("FB_LAMBDA", "2.0"))
HORIZON = float(os.environ.get("FB_HORIZON", "0.5"))

_TIME_BUDGET_S = 7.0
_MAX_PASSES_PER_LAYER = 400

_STATE: dict = {}


def reset():
    _STATE.clear()


def _forecast(hotness):
    """Per-(layer,expert) forecast of next-window load via two-half linear trend.

    Returns (forecast, std). forecast >= 0. Falls back to the plain mean when the
    window is too short or HORIZON == 0 (=> identical to the 020 base)."""
    mean = hotness.mean(axis=0)
    std = hotness.std(axis=0)
    T = hotness.shape[0]
    if HORIZON == 0.0 or T < 4:
        return mean, std
    half = T // 2
    older = hotness[:half].mean(axis=0)
    recent = hotness[T - half:].mean(axis=0)
    forecast = recent + HORIZON * (recent - older)
    np.clip(forecast, 0.0, None, out=forecast)
    return forecast, std


def _init_deploy_table(n_device, n_experts, n_red, slots):
    deploy = np.zeros((n_device, slots), dtype=np.int64)
    for d in range(n_device):
        for s in range(slots - 1):
            deploy[d, s] = (d * (slots - 1) + s) % n_experts
        deploy[d, -1] = deploy[d, -2]
    return deploy


def _build_layer(weight, n_device, n_exp_per_dev):
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
    for idx in order:
        e = slot_expert[idx]
        score = dev_load + np.where((deploy == e).any(axis=1), big, 0.0)
        score[dev_count >= n_exp_per_dev] = np.inf
        d = int(np.argmin(score))
        deploy[d, int(dev_count[d])] = e
        dev_load[d] += slot_weight[idx]
        dev_count[d] += 1
    return deploy


def _refine_layer(load, deploy, deadline):
    n_device, slots = deploy.shape
    n_experts = load.shape[0]
    cut = np.bincount(deploy.reshape(-1), minlength=n_experts)
    cut[cut == 0] = 1
    piece = load / cut
    slot_piece = piece[deploy]
    dev_load = slot_piece.sum(axis=1)
    present = np.zeros((n_device, n_experts), dtype=bool)
    rows = np.repeat(np.arange(n_device), slots)
    present[rows, deploy.reshape(-1)] = True
    all_devs = np.arange(n_device)
    for _ in range(_MAX_PASSES_PER_LAYER):
        if time.time() > deadline:
            break
        A = int(np.argmax(dev_load))
        maxload = float(dev_load[A])
        other = all_devs[all_devs != A]
        o_expert = deploy[other].reshape(-1)
        o_piece = slot_piece[other].reshape(-1)
        o_devidx = np.repeat(other, slots)
        o_load = dev_load[o_devidx]
        best_gain = 1e-12
        best = None
        for sA in range(slots):
            e = int(deploy[A, sA])
            pe = float(slot_piece[A, sA])
            delta = pe - o_piece
            new_pair = np.maximum(maxload - delta, o_load + delta)
            ok = (delta > 1e-15) & (new_pair < maxload - 1e-12)
            ok &= ~present[A, o_expert]
            ok &= ~present[o_devidx, e]
            if not ok.any():
                continue
            cand = np.where(ok, new_pair, np.inf)
            j = int(np.argmin(cand))
            gain = maxload - float(cand[j])
            if gain > best_gain:
                best_gain = gain
                best = (sA, j, e)
        if best is None:
            break
        sA, j, e = best
        B = int(o_devidx[j])
        f = int(o_expert[j])
        sB = int(np.where(deploy[B] == f)[0][0])
        delta = float(slot_piece[A, sA] - slot_piece[B, sB])
        deploy[A, sA], deploy[B, sB] = f, e
        slot_piece[A, sA], slot_piece[B, sB] = slot_piece[B, sB], slot_piece[A, sA]
        dev_load[A] -= delta
        dev_load[B] += delta
        present[A] = False
        present[A, deploy[A]] = True
        present[B] = False
        present[B, deploy[B]] = True
    return deploy


def _relabel_to_old(new_layer, old_layer):
    n_device, slots = new_layer.shape
    new_sets = [set(new_layer[d].tolist()) for d in range(n_device)]
    old_sets = [set(old_layer[d].tolist()) for d in range(n_device)]

    overlap = np.zeros((n_device, n_device), dtype=np.int64)
    for i in range(n_device):
        for k in range(n_device):
            overlap[i, k] = len(new_sets[i] & old_sets[k])
    assign = -np.ones(n_device, dtype=np.int64)
    used_new = np.zeros(n_device, dtype=bool)
    used_old = np.zeros(n_device, dtype=bool)
    flat_order = np.argsort(overlap.reshape(-1))[::-1]
    for idx in flat_order:
        i, k = divmod(int(idx), n_device)
        if used_new[i] or used_old[k]:
            continue
        assign[k] = i
        used_new[i] = True
        used_old[k] = True

    out = np.empty_like(new_layer)
    for old_pos in range(n_device):
        new_row = int(assign[old_pos])
        src = new_layer[new_row]
        old_row = old_layer[old_pos]
        placed = np.zeros(slots, dtype=bool)
        used_src = np.zeros(slots, dtype=bool)
        row_out = -np.ones(slots, dtype=np.int64)
        for s in range(slots):
            e = int(old_row[s])
            for t in range(slots):
                if not used_src[t] and int(src[t]) == e:
                    row_out[s] = e
                    placed[s] = True
                    used_src[t] = True
                    break
        rem = [int(src[t]) for t in range(slots) if not used_src[t]]
        ri = 0
        for s in range(slots):
            if not placed[s]:
                row_out[s] = rem[ri]
                ri += 1
        out[old_pos] = row_out
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
    # Predictive estimator: target the load the deployment will SERVE NEXT.
    target, std = _forecast(hotness)
    risk = target + LAMBDA * std if LAMBDA else target
    n_layers, n_experts = target.shape
    n_exp_per_dev = (n_experts + n_red_expert) // n_device

    deadline = time.time() + _TIME_BUDGET_S
    per_layer = (deadline - time.time()) / max(n_layers, 1)
    candidate = np.zeros((n_layers, n_device, n_exp_per_dev), dtype=np.int64)
    for l in range(n_layers):
        deploy = _build_layer(risk[l], n_device, n_exp_per_dev)
        layer_deadline = min(deadline, time.time() + 4.0 * per_layer)
        candidate[l] = _refine_layer(target[l], deploy, layer_deadline)

    sig = (n_layers, n_experts, n_device, n_exp_per_dev)
    cur = _STATE.get("deploy") if _STATE.get("sig") == sig else None

    if cur is None:
        init = _init_deploy_table(n_device, n_experts, n_red_expert, n_exp_per_dev)
        for l in range(n_layers):
            candidate[l] = _relabel_to_old(candidate[l], init)
        _STATE["sig"] = sig
        _STATE["deploy"] = candidate.copy()
        return True, np.arange(n_layers, dtype=np.int64), candidate, None

    for l in range(n_layers):
        candidate[l] = _relabel_to_old(candidate[l], cur[l])

    # Band judges on the same forecast the placement targets (consistent).
    improve = np.array([_par(target[l], cur[l]) - _par(target[l], candidate[l])
                        for l in range(n_layers)])
    sel = np.where(improve > TAU)[0]
    if sel.size == 0:
        return False, np.array([], dtype=np.int64), candidate, None

    order = sel[np.argsort(improve[sel])[::-1]]
    new_state = cur.copy()
    new_state[order] = candidate[order]
    _STATE["deploy"] = new_state
    return True, order.astype(np.int64), candidate, None
