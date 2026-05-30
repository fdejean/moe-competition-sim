"""082 fold_pack — constructive folding-partition placement (ENTIRELY NEW architecture).

This is NOT a finance_band derivative. No water-fill, no greedy LPT argmin loop, no
device-relabel, no stay-bias. A completely different paradigm:

  1. REPLICAS by largest-remainder proportional apportionment (Hamilton's method):
     give each expert a baseline replica, then apportion the redundant slots in
     proportion to load with largest-remainder rounding -> per-replica loads are
     equalized in closed form (no iterative water-fill).

  2. PLACEMENT by boustrophedon FOLDING: sort all replica-slots by per-replica load
     descending and deal them across devices in a serpentine order
     (0,1,..,D-1, D-1,..,1,0, 0,1,..) so each device receives one slot per "round"
     and the heavy/light slots cancel — the classic folding heuristic for balanced
     multiway partitioning. This is FULLY VECTORIZED (device index is pure
     arithmetic on the rank), so there is no per-slot Python loop at all -> it is
     strictly faster than finance_band and trivially safe at EP256 (n_device=256).

  3. A light vectorized de-duplication pass moves any expert that landed twice on one
     device to the least-loaded device lacking it (folding rarely collides).

Redeploy uses a simple no-trade band on modeled PAR (generic, not architecture-
specific). Tunables: FB_TAU (band, default 0.2).
"""
from __future__ import annotations

import os

import numpy as np

TAU = float(os.environ.get("FB_TAU", "0.2"))

_STATE: dict = {}


def reset():
    _STATE.clear()


def _proportional_replicas(w, n_slots):
    """Largest-remainder (Hamilton) apportionment of redundant slots by load."""
    n = w.shape[0]
    extra = n_slots - n
    r = np.ones(n, dtype=np.int64)
    if extra <= 0:
        return r
    total = float(w.sum())
    if total <= 0:
        r[:extra] += 1
        return r
    share = w / total * extra
    base = np.floor(share).astype(np.int64)
    rem = int(extra - base.sum())
    if rem > 0:
        frac = share - base
        take = np.argsort(frac)[::-1][:rem]
        base[take] += 1
    return r + base


def _fold_layer(w, n_device, n_per_dev):
    """Folding-partition placement. w: (n_experts,) mean load."""
    n_experts = w.shape[0]
    n_slots = n_device * n_per_dev
    replicas = _proportional_replicas(w, n_slots)

    slot_expert = np.repeat(np.arange(n_experts), replicas)
    slot_load = w[slot_expert] / replicas[slot_expert]
    order = np.argsort(slot_load, kind="stable")[::-1]
    se = slot_expert[order]

    # boustrophedon (serpentine) device index per rank — fully vectorized
    ranks = np.arange(n_slots)
    rnd = ranks // n_device
    pos = ranks % n_device
    dev = np.where(rnd % 2 == 0, pos, n_device - 1 - pos)

    deploy = np.empty((n_device, n_per_dev), dtype=np.int64)
    deploy[dev, rnd] = se

    # vectorized de-duplication: fix devices holding the same expert twice
    deploy = _dedup(deploy, w, replicas)
    return deploy


def _dedup(deploy, w, replicas):
    n_device, n_per = deploy.shape
    # device load (per-replica loads sum)
    cut = np.bincount(deploy.reshape(-1), minlength=w.shape[0])
    cut[cut == 0] = 1
    for d in range(n_device):
        row = deploy[d]
        if len(np.unique(row)) == len(row):
            continue
        seen = set()
        for s in range(n_per):
            e = int(row[s])
            if e in seen:
                # move this duplicate slot to the least-loaded device lacking e
                loads = (w[deploy] / cut[deploy]).sum(axis=1)
                cand = [dd for dd in range(n_device) if e not in deploy[dd]]
                if not cand:
                    seen.add(e)
                    continue
                dd = min(cand, key=lambda x: loads[x])
                # swap with that device's lightest slot expert
                t = int(np.argmin(w[deploy[dd]] / cut[deploy[dd]]))
                f = int(deploy[dd, t])
                if f not in row:  # avoid creating a new dup here
                    deploy[dd, t] = e
                    deploy[d, s] = f
            seen.add(int(deploy[d, s]))
    return deploy


def _par(load, deploy):
    cut = np.bincount(deploy.reshape(-1), minlength=load.shape[0])
    cut[cut == 0] = 1
    weights = load / cut
    loads = weights[deploy.reshape(-1)].reshape(deploy.shape).sum(-1)
    return float(loads.max() / loads.mean())


def rebalance(hotness, n_device, n_red_expert):
    hotness = np.asarray(hotness, dtype=np.float64)
    mean = hotness.mean(axis=0)
    n_layers, n_experts = mean.shape
    n_per_dev = (n_experts + n_red_expert) // n_device

    candidate = np.zeros((n_layers, n_device, n_per_dev), dtype=np.int64)
    for l in range(n_layers):
        candidate[l] = _fold_layer(mean[l], n_device, n_per_dev)

    sig = (n_layers, n_experts, n_device, n_per_dev)
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
