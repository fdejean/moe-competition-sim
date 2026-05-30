"""020 — 019 (band + variance replication + swap refine) + PAR-NEUTRAL transmit min.

The simulator counts transmit as the element-wise (old != new) Hamming distance
of the (n_device, slots) grid per redeployed layer. Two relabelings change that
count with ZERO effect on PAR (PAR = max/mean of per-device loads):

  * 4b device relabel: permuting whole device rows just permutes the load vector
    -> max and mean unchanged -> PAR identical. Greedy max-overlap matching of the
    new candidate's devices onto the previous deployment's devices minimizes how
    many experts move between devices. (No scipy -> greedy Hungarian-free matching.)
  * 4a slot preservation: permuting slots within one device leaves that device's
    expert set and load untouched -> PAR identical. Keep each expert in its old
    slot index when it persists on that device; only vacated slots take arrivals.

Applied against the cached previous deployment (and, on the first deploy, against
the simulator's known init table, which is the biggest single transmit chunk).
All PAR-neutral, so it never costs leaderboard points and saves transmit on the
long multi-rebalance hidden traces where the transfer term actually bites.
"""
from __future__ import annotations

import os
import time

import numpy as np

_w = os.environ.get("FB_WINDOW")
WINDOW = None if _w in (None, "", "all") else int(_w)
TAU = float(os.environ.get("FB_TAU", "0.2"))
LAMBDA = float(os.environ.get("FB_LAMBDA", "1.0"))

_TIME_BUDGET_S = 7.0
_MAX_PASSES_PER_LAYER = 400

_STATE: dict = {}


def reset():
    _STATE.clear()


def _init_deploy_table(n_device, n_experts, n_red, slots):
    """Mirror quickstart.init_deploy_table(default=False) — the simulator's start
    grid, so the first redeploy can be relabeled to it to cut first-move transmit."""
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
    """PAR-neutral relabel of `new_layer` to minimize Hamming distance to
    `old_layer`: (4b) greedy max-overlap device matching, then (4a) per-device
    slot alignment. Returns the relabeled grid (same loads, same expert sets)."""
    n_device, slots = new_layer.shape
    new_sets = [set(new_layer[d].tolist()) for d in range(n_device)]
    old_sets = [set(old_layer[d].tolist()) for d in range(n_device)]

    # 4b: greedy match new device rows -> old device positions by set overlap.
    overlap = np.zeros((n_device, n_device), dtype=np.int64)
    for i in range(n_device):
        for k in range(n_device):
            overlap[i, k] = len(new_sets[i] & old_sets[k])
    assign = -np.ones(n_device, dtype=np.int64)   # assign[old_pos] = new_row
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
        # 4a: keep persisting experts in their old slot index.
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
        # First deploy: relabel toward the simulator's known init grid (free
        # transmit cut on the largest single redeploy).
        init = _init_deploy_table(n_device, n_experts, n_red_expert, n_exp_per_dev)
        for l in range(n_layers):
            candidate[l] = _relabel_to_old(candidate[l], init)
        _STATE["sig"] = sig
        _STATE["deploy"] = candidate.copy()
        return True, np.arange(n_layers, dtype=np.int64), candidate, None

    # Relabel every layer's candidate toward the current deployment (PAR-neutral).
    for l in range(n_layers):
        candidate[l] = _relabel_to_old(candidate[l], cur[l])

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
