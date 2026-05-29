"""050 — rep_aware_refine: finance_band + replication-aware refinement (roadmap 2a).

EP128-SAFE rewrite. The server tests EP128 (n_device=128); band_refine_transmin's
O(n_device^2) python-loop relabel timed out there and scored -1e6. So this is built
strictly on the proven finance_band base (water-fill replication on risk + LPT pack
+ no-trade band, NO relabel, NO unbounded search) and adds ONE new PAR stage:

  **replication-aware refinement.** Pure water-filling sets replica counts to
  minimize the per-replica *lower bound* but is blind to packing granularity: with
  indivisible slots the actually-packed peak device can exceed that bound. After the
  water-fill seed we run a hard-bounded loop: pack -> find the packed peak device ->
  move ONE replica to the heaviest expert on it (taken from the donor that can best
  afford it) -> re-pack -> keep iff the packed peak strictly drops. Co-optimizes the
  replication + placement stages that greedy water-fill + LPT mis-couple.

Everything is wall-clock guarded (global deadline + tiny per-layer move budget) and
vectorized, so it degrades gracefully to plain finance_band under EP128 time pressure
(remaining layers just keep their water-fill+LPT placement). PAR-only effect.

Tunables (env): FB_WINDOW, FB_TAU (band), FB_LAMBDA (variance replication).
"""
from __future__ import annotations

import os
import time

import numpy as np

_w = os.environ.get("FB_WINDOW")
WINDOW = None if _w in (None, "", "all") else int(_w)
TAU = float(os.environ.get("FB_TAU", "0.2"))
LAMBDA = float(os.environ.get("FB_LAMBDA", "2.0"))

_TIME_BUDGET_S = 6.0          # global wall-clock per rebalance() call
_REP_MAX_MOVES = 8            # max accepted replica moves per layer
_REP_TOP_EXPERTS = 2          # heaviest experts on the peak device to try
_REP_TOP_DONORS = 3           # donor candidates to try per recipient

_STATE: dict = {}


def reset():
    _STATE.clear()


def _pack(weight, replicas, n_device, n_exp_per_dev):
    """LPT placement for given replica counts; heaviest slot -> least-loaded
    device, penalizing a second copy of an expert on a device that holds it."""
    n_experts = weight.shape[0]
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
    return deploy, dev_load


def _build_layer(weight, n_device, n_exp_per_dev, deadline):
    """Water-fill seed -> bounded replication-aware refinement on the packed peak."""
    n_experts = weight.shape[0]
    n_slots = n_device * n_exp_per_dev
    replicas = np.ones(n_experts, dtype=np.int64)
    for _ in range(n_slots - n_experts):
        replicas[int(np.argmax(weight / replicas))] += 1

    deploy, dev_load = _pack(weight, replicas, n_device, n_exp_per_dev)

    moves = 0
    while moves < _REP_MAX_MOVES and time.time() < deadline:
        A = int(np.argmax(dev_load))
        peak = float(dev_load[A])
        expsA = np.unique(deploy[A][deploy[A] >= 0])
        recip_order = expsA[np.argsort(weight[expsA] / replicas[expsA])[::-1]][:_REP_TOP_EXPERTS]

        donors = np.where(replicas > 1)[0]
        if donors.size == 0:
            break
        donor_order = donors[np.argsort(weight[donors] / (replicas[donors] - 1))][:_REP_TOP_DONORS]

        improved = False
        for e in recip_order:
            for f in donor_order:
                if f == e:
                    continue
                trial = replicas.copy()
                trial[e] += 1
                trial[f] -= 1
                d2, dl2 = _pack(weight, trial, n_device, n_exp_per_dev)
                if float(dl2.max()) < peak - 1e-12:
                    replicas, deploy, dev_load = trial, d2, dl2
                    moves += 1
                    improved = True
                    break
            if improved:
                break
        if not improved:
            break
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
    candidate = np.zeros((n_layers, n_device, n_exp_per_dev), dtype=np.int64)
    for l in range(n_layers):
        # even split of remaining time across remaining layers (degrade gracefully)
        remaining = max(n_layers - l, 1)
        layer_deadline = time.time() + max(0.0, (deadline - time.time()) / remaining)
        candidate[l] = _build_layer(risk[l], n_device, n_exp_per_dev, layer_deadline)

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
