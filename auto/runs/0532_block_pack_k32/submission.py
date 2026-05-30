"""056 block_pack — co-spiking-aware placement via temporal block balancing.

THE finding (server-confirmed, 2026-05-30): the per-snapshot mean-load PAR is only
~1.05 (packing floor is 1.0), but the *realized* scored PAR is ~1.69. Closing the
1.05->1.0 packing gap (swap refine / pack_refine_fast) is invisible against 1.69 and
costs transmit. The real ~0.64 gap is **co-spiking**: experts that spike in the SAME
iterations landing on the same device bottleneck those iterations. finance_band's
LPT balances only the scalar MEAN load, so it is blind to which experts spike
together.

block_pack balances each device's load across **k temporal blocks** of the window at
once (multi-factor risk diversification / temporal load balancing). Per expert we
compute a k-vector of block-mean loads; the greedy places each slot on the device
that minimizes the resulting worst block load (not just the mean). Two experts that
both spike in block j now raise that device's block-j load, so the greedy spreads
them across devices — directly reducing per-iteration (co-spiking) imbalance.

Crucially this is a SINGLE greedy pass, O(n_slots * n_device * k), ~finance_band
speed (the peer's iterative cov_place version FLOORED on the server's 3x-DS-EPLB-per-
case budget; an iterative swap is not affordable at EP128 — a one-pass build is).
Replication (variance water-fill on mean+LAMBDA*std) and the no-trade band are
unchanged from finance_band.

Tunables (env): FB_TAU, FB_LAMBDA, FB_WINDOW (as finance_band) + FB_BLOCKS (k, default 8).
"""
from __future__ import annotations

import os

import numpy as np

_w = os.environ.get("FB_WINDOW")
WINDOW = None if _w in (None, "", "all") else int(_w)
TAU = float(os.environ.get("FB_TAU", "0.2"))
LAMBDA = float(os.environ.get("FB_LAMBDA", "2.0"))
BLOCKS = int(os.environ.get("FB_BLOCKS", "32"))

_STATE: dict = {}


def reset():
    _STATE.clear()


def _block_means(hot_layer, k):
    """hot_layer: (T, n_experts) -> (n_experts, k) mean load per temporal block."""
    T = hot_layer.shape[0]
    if k <= 1 or T < k:
        return hot_layer.mean(axis=0)[:, None]
    # split T iterations into k contiguous blocks, mean each (handles ragged tail)
    idx = (np.arange(T) * k) // T            # block id per iteration, 0..k-1
    n_exp = hot_layer.shape[1]
    out = np.zeros((n_exp, k))
    counts = np.bincount(idx, minlength=k).astype(np.float64)
    for j in range(k):
        m = idx == j
        out[:, j] = hot_layer[m].sum(axis=0)
    out /= np.maximum(counts, 1.0)[None, :]
    return out


def _build_layer_block(bsig, mean, n_device, n_exp_per_dev):
    """Greedy placement minimizing the worst per-block device load.

    bsig: (n_experts, k) per-expert block-mean signature (the placement objective).
    mean: (n_experts,) scalar mean load (drives replica water-fill + slot order)."""
    n_experts = mean.shape[0]
    n_slots = n_device * n_exp_per_dev
    k = bsig.shape[1]

    # replication: variance water-fill on the (risk) mean, same as finance_band.
    replicas = np.ones(n_experts, dtype=np.int64)
    for _ in range(n_slots - n_experts):
        replicas[int(np.argmax(mean / replicas))] += 1

    slot_expert = np.repeat(np.arange(n_experts), replicas)
    slot_sig = bsig[slot_expert] / replicas[slot_expert][:, None]    # (n_slots, k)
    slot_scalar = slot_sig.sum(axis=1)                               # ~ mean load
    order = np.argsort(slot_scalar, kind="stable")[::-1]             # heaviest first

    deploy = np.full((n_device, n_exp_per_dev), -1, dtype=np.int64)
    dev_block = np.zeros((n_device, k))           # per-device load in each block
    dev_count = np.zeros(n_device, dtype=np.int64)
    present = np.zeros((n_device, n_experts), dtype=bool)
    big = float(slot_scalar.sum()) + 1.0
    for idx in order:
        e = slot_expert[idx]
        sig = slot_sig[idx]
        # cost = worst block load after adding this slot to each device
        cost = (dev_block + sig[None, :]).max(axis=1)
        cost = cost + np.where(present[:, e], big, 0.0)
        cost[dev_count >= n_exp_per_dev] = np.inf
        d = int(np.argmin(cost))
        deploy[d, int(dev_count[d])] = e
        dev_block[d] += sig
        dev_count[d] += 1
        present[d, e] = True
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
    std = hotness.std(axis=0)
    risk = mean + LAMBDA * std if LAMBDA else mean
    n_layers, n_experts = mean.shape
    n_exp_per_dev = (n_experts + n_red_expert) // n_device

    candidate = np.zeros((n_layers, n_device, n_exp_per_dev), dtype=np.int64)
    for l in range(n_layers):
        # placement signature = per-block risk (mean+LAMBDA*std style via block means
        # of the risk-scaled load). Use block means of the raw load, scaled toward risk.
        bsig = _block_means(hotness[:, l, :], BLOCKS)
        # blend in the variance weight so volatile experts still pull replicas/spread.
        scale = np.where(mean[l] > 0, risk[l] / np.maximum(mean[l], 1e-12), 1.0)
        bsig = bsig * scale[:, None]
        candidate[l] = _build_layer_block(bsig, risk[l], n_device, n_exp_per_dev)

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
