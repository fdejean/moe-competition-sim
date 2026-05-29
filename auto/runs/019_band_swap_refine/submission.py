"""019 — flo's no-trade band + variance replication + swap local-search refine.

Merges the two orthogonal levers:
  * flo's finance_band: variance-aware replication (mean + LAMBDA*std) and a
    no-trade band (only redeploy a layer when its modeled PAR gain clears TAU)
    -> saves transmit on long/stable traces (the dominant lever there).
  * 018's swap local search: after LPT packing, swap heavy-for-light slots off the
    busiest device while leaving every expert's replica count fixed. Pure swaps
    keep `cut` constant, so per-replica weights are unchanged and the move only
    lowers the running max device load -> PAR <= LPT-PAR, layer by layer.

Refinement runs on the *mean* (expected) load, which is exactly the expectation
of the simulator's per-iteration PAR numerator. Replication/packing still use
flo's risk weight so volatile experts get extra copies.

Tunables via env so benchmarks can sweep: FB_WINDOW, FB_TAU, FB_LAMBDA.
"""
from __future__ import annotations

import os
import time

import numpy as np

_w = os.environ.get("FB_WINDOW")
WINDOW = None if _w in (None, "", "all") else int(_w)
TAU = float(os.environ.get("FB_TAU", "0.2"))
LAMBDA = float(os.environ.get("FB_LAMBDA", "1.0"))   # 1.0 was best locally (run 014)

_TIME_BUDGET_S = 7.0
_MAX_PASSES_PER_LAYER = 400

_STATE: dict = {}


def reset():
    _STATE.clear()


def _build_layer(weight, n_device, n_exp_per_dev):
    """Greedy LPT placement (flo's), driven by `weight` (risk-adjusted)."""
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
    """Swap local search to lower max device load (PAR-reducing, cut-preserving).

    `load` is the weight whose makespan we minimize (here: mean expected load)."""
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
    risk = mean + LAMBDA * hotness.std(axis=0) if LAMBDA else mean
    n_layers, n_experts = mean.shape
    n_exp_per_dev = (n_experts + n_red_expert) // n_device

    deadline = time.time() + _TIME_BUDGET_S
    per_layer = (deadline - time.time()) / max(n_layers, 1)
    candidate = np.zeros((n_layers, n_device, n_exp_per_dev), dtype=np.int64)
    for l in range(n_layers):
        deploy = _build_layer(risk[l], n_device, n_exp_per_dev)
        layer_deadline = min(deadline, time.time() + 4.0 * per_layer)
        candidate[l] = _refine_layer(mean[l], deploy, layer_deadline)

    sig = (n_layers, n_experts, n_device, n_exp_per_dev)
    cur = _STATE.get("deploy") if _STATE.get("sig") == sig else None
    if cur is None:
        _STATE["sig"] = sig
        _STATE["deploy"] = candidate.copy()
        return True, np.arange(n_layers, dtype=np.int64), candidate, None

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
