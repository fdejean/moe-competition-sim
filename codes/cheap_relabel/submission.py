"""068 cheap_relabel — finance_band PAR + a FINANCE_BAND-SPEED transmit cut.

finance_band's PAR (~1.692) is ~= the DS-EPLB baseline's; its whole +11 server win is
TRANSMIT (it holds; baseline redeploys every cycle). Its own transmit (842k, ~82s ~3%
of modeled time) is recoverable: band_refine_transmin cut it ~half with a PAR-NEUTRAL
device relabel + slot preserve, but that relabel was O(n_device^2) (overlap matrix +
python match) and FLOORED at EP128/256 on the 3x-DS-EPLB time budget.

This does the SAME PAR-neutral transmit cut but with an O(n_device) alignment:
  * device relabel (4b): permute whole device ROWS of the new layer to sit on the old
    device that shared its HEAVIEST expert (a dict keyed by per-device heaviest expert,
    O(n_device) — no n_device^2 overlap matrix). Permuting rows only permutes the load
    vector -> PAR identical.
  * slot preserve (4a): within each device keep persisting experts in their old slot;
    only vacated slots take arrivals. PAR identical (per-device expert set unchanged).
Both are load-invariant => zero PAR cost, pure transmit saving, finance_band-speed
(O(n_device*slots), no n_device^2, no iteration) => EP256-safe.

Tunables (env): FB_TAU, FB_LAMBDA, FB_WINDOW (as finance_band).
"""
from __future__ import annotations

import os

import numpy as np

_w = os.environ.get("FB_WINDOW")
WINDOW = None if _w in (None, "", "all") else int(_w)
TAU = float(os.environ.get("FB_TAU", "0.2"))
LAMBDA = float(os.environ.get("FB_LAMBDA", "2.0"))

_STATE: dict = {}


def reset():
    _STATE.clear()


def _init_deploy_table(n_device, n_experts, slots):
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


def _align(new, old, load):
    """PAR-neutral relabel of `new` toward `old`, O(n_device*slots). load:(n_experts,).
    4b: match new device rows to old positions by shared heaviest expert (dict).
    4a: within each device, keep persisting experts in their old slot index."""
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

    rows = new[assign]                       # (nd, sl) device rows permuted onto old positions
    out = np.empty_like(rows)
    for d in range(nd):
        src = rows[d]
        oldrow = old[d]
        used_src = np.zeros(sl, dtype=bool)
        rowout = -np.ones(sl, dtype=np.int64)
        # keep persisting experts in their old slot
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
    risk = mean + LAMBDA * hotness.std(axis=0) if LAMBDA else mean
    n_layers, n_experts = mean.shape
    n_exp_per_dev = (n_experts + n_red_expert) // n_device

    candidate = np.zeros((n_layers, n_device, n_exp_per_dev), dtype=np.int64)
    for l in range(n_layers):
        candidate[l] = _build_layer(risk[l], n_device, n_exp_per_dev)

    sig = (n_layers, n_experts, n_device, n_exp_per_dev)
    cur = _STATE.get("deploy") if _STATE.get("sig") == sig else None
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
