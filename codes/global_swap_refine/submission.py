"""018 — global EPLB + swap-based local-search refinement.

Hypothesis
----------
DeepSeek EPLB (and flo's finance_band) place experts with greedy
Longest-Processing-Time (LPT) packing. LPT is a heuristic, not optimal, for the
makespan (max-device-load) objective. Because the simulator's PAR is

    PAR = max_device_load / mean_device_load

and the *mean* device load is invariant to placement (it always equals
total_hotness / n_device, regardless of how replicas are arranged), the ONLY
lever for lowering PAR is lowering the *max* device load.

A pure-swap local search can only reduce or tie the max device load: swapping
two already-placed physical slots leaves every expert's replica count (`cut`)
unchanged, so each slot's per-replica weight is unchanged — we just move pieces
between devices. We repeatedly take the busiest device and swap one of its heavy
slots for a lighter slot on another device whenever that lowers the running max.
This is guaranteed PAR <= LPT-PAR for every layer.

This variant deploys every layer every cycle (like raw DS-EPLB, change=True) so
the refinement's effect on PAR is isolated from any transmit-saving band.
Replication uses the same water-filling rule as DS-EPLB (split the heaviest
per-replica load), driven by the mean load over the window.

Contract (program.md rule 3): always return a correctly-shaped deployment of
shape (n_layers, n_device, (n_experts + n_red_expert) // n_device), even when
change=False (this variant always returns change=True).
"""
from __future__ import annotations

import time

import numpy as np

# Per-call wall-clock guard. The evaluator aborts a rebalance() call that
# exceeds 10s; we stop refining well before that and return what we have
# (a partially refined deployment is still valid and still >= LPT quality).
_TIME_BUDGET_S = 7.0
_MAX_PASSES_PER_LAYER = 400


def _water_fill_replicas(load: np.ndarray, n_slots: int) -> np.ndarray:
    """DS-EPLB replication: give each extra slot to the expert whose current
    per-replica load is largest. Minimizes the maximum replica load."""
    n_experts = load.shape[0]
    replicas = np.ones(n_experts, dtype=np.int64)
    for _ in range(n_slots - n_experts):
        replicas[int(np.argmax(load / replicas))] += 1
    return replicas


def _lpt_pack(load: np.ndarray, replicas: np.ndarray, n_device: int,
              n_exp_per_dev: int) -> np.ndarray:
    """Greedy LPT with a duplicate penalty so replicas spread across devices."""
    n_experts = load.shape[0]
    slot_expert = np.repeat(np.arange(n_experts), replicas)
    slot_weight = load[slot_expert] / replicas[slot_expert]
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


def _refine_layer(load: np.ndarray, deploy: np.ndarray,
                  deadline: float) -> np.ndarray:
    """Swap local search: lower the max device load without changing any
    expert's replica count. Pure swaps keep `cut` fixed, so per-replica
    weights are constant; we only move pieces between devices."""
    n_device, slots = deploy.shape
    n_experts = load.shape[0]

    cut = np.bincount(deploy.reshape(-1), minlength=n_experts)
    cut[cut == 0] = 1
    piece = load / cut                      # per-replica weight per expert
    slot_piece = piece[deploy]              # (n_device, slots)
    dev_load = slot_piece.sum(axis=1)       # (n_device,)

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
        best = None  # (sA, flat_j)
        for sA in range(slots):
            e = int(deploy[A, sA])
            pe = float(slot_piece[A, sA])
            delta = pe - o_piece                 # load moved A -> B (want > 0)
            new_a = maxload - delta
            new_b = o_load + delta
            new_pair = np.maximum(new_a, new_b)
            ok = (delta > 1e-15) & (new_pair < maxload - 1e-12)
            ok &= ~present[A, o_expert]           # A must not already hold f
            ok &= ~present[o_devidx, e]           # B must not already hold e
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
        # locate f's slot on B
        sB = int(np.where(deploy[B] == f)[0][0])
        pe = float(slot_piece[A, sA])
        pf = float(slot_piece[B, sB])
        delta = pe - pf
        deploy[A, sA], deploy[B, sB] = f, e
        slot_piece[A, sA], slot_piece[B, sB] = pf, pe
        dev_load[A] -= delta
        dev_load[B] += delta
        present[A] = False
        present[A, deploy[A]] = True
        present[B] = False
        present[B, deploy[B]] = True
    return deploy


def rebalance(hotness, n_device, n_red_expert):
    hotness = np.asarray(hotness, dtype=np.float64)
    mean = hotness.mean(axis=0)                       # (n_layers, n_experts)
    n_layers, n_experts = mean.shape
    n_exp_per_dev = (n_experts + n_red_expert) // n_device
    n_slots = n_device * n_exp_per_dev

    deployment = np.zeros((n_layers, n_device, n_exp_per_dev), dtype=np.int64)
    deadline = time.time() + _TIME_BUDGET_S
    per_layer = (deadline - time.time()) / max(n_layers, 1)
    for l in range(n_layers):
        load = mean[l]
        replicas = _water_fill_replicas(load, n_slots)
        deploy = _lpt_pack(load, replicas, n_device, n_exp_per_dev)
        layer_deadline = min(deadline, time.time() + 4.0 * per_layer)
        deployment[l] = _refine_layer(load, deploy, layer_deadline)

    layers_priority = np.arange(n_layers, dtype=np.int64)
    return True, layers_priority, deployment, None
