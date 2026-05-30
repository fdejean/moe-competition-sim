"""063 block_pack_fast — block_pack's co-spiking gain at finance_band speed (EP256-safe).

block_pack (per-device k-block balancing) gave the best proxy (109.11, DS-R1 PAR
1.235->1.210) but its inner cost evaluated the k-vector block cost over ALL n_device
devices per slot (~2x finance_band) and FLOORED at EP256. Fix: select the device by
the same cheap SCALAR rule as finance_band (one argpartition over n_device), but only
compute the expensive k-block cost for the M least-loaded candidate devices and pick
the best among them. Per-slot cost is then ~finance_band (one O(n_device) op + a tiny
O(M*k) op), so it stays under the server's 3x-DS-EPLB budget even at EP256.

Co-spiking rationale unchanged: balancing each device's load across k temporal blocks
spreads experts that spike in the same iterations, cutting per-iteration (realized)
PAR — the dominant gap (realized 1.69 vs mean-load 1.05).

Tunables (env): FB_TAU, FB_LAMBDA, FB_WINDOW + FB_BLOCKS (k, default 8),
FB_MCAND (candidate devices scored with the block cost, default 8).
"""
from __future__ import annotations

import os

import numpy as np

_w = os.environ.get("FB_WINDOW")
WINDOW = None if _w in (None, "", "all") else int(_w)
TAU = float(os.environ.get("FB_TAU", "0.2"))
LAMBDA = float(os.environ.get("FB_LAMBDA", "2.0"))
BLOCKS = int(os.environ.get("FB_BLOCKS", "8"))
MCAND = int(os.environ.get("FB_MCAND", "8"))

_STATE: dict = {}


def reset():
    _STATE.clear()


def _block_means(hot_layer, k):
    T = hot_layer.shape[0]
    if k <= 1 or T < k:
        return hot_layer.mean(axis=0)[:, None]
    idx = (np.arange(T) * k) // T
    n_exp = hot_layer.shape[1]
    out = np.zeros((n_exp, k))
    counts = np.bincount(idx, minlength=k).astype(np.float64)
    for j in range(k):
        out[:, j] = hot_layer[idx == j].sum(axis=0)
    out /= np.maximum(counts, 1.0)[None, :]
    return out


def _build_layer_fast(bsig, mean, n_device, n_exp_per_dev, mcand):
    n_experts = mean.shape[0]
    n_slots = n_device * n_exp_per_dev
    k = bsig.shape[1]

    replicas = np.ones(n_experts, dtype=np.int64)
    for _ in range(n_slots - n_experts):
        replicas[int(np.argmax(mean / replicas))] += 1

    slot_expert = np.repeat(np.arange(n_experts), replicas)
    slot_sig = bsig[slot_expert] / replicas[slot_expert][:, None]
    slot_scalar = slot_sig.sum(axis=1)
    order = np.argsort(slot_scalar, kind="stable")[::-1]

    deploy = np.full((n_device, n_exp_per_dev), -1, dtype=np.int64)
    dev_block = np.zeros((n_device, k))
    dev_scalar = np.zeros(n_device)
    dev_count = np.zeros(n_device, dtype=np.int64)
    present = np.zeros((n_device, n_experts), dtype=bool)
    big = float(slot_scalar.sum()) + 1.0
    M = min(mcand, n_device)
    for idx in order:
        e = slot_expert[idx]
        sig = slot_sig[idx]
        # cheap scalar score for candidate selection (finance_band-style)
        score = dev_scalar + np.where(present[:, e], big, 0.0)
        score[dev_count >= n_exp_per_dev] = np.inf
        cand = np.argpartition(score, M - 1)[:M] if M < n_device else np.arange(n_device)
        # full block cost only on the M candidates
        bc = (dev_block[cand] + sig[None, :]).max(axis=1)
        bc = bc + np.where(present[cand, e], big, 0.0)
        bc[dev_count[cand] >= n_exp_per_dev] = np.inf
        d = int(cand[int(np.argmin(bc))])
        deploy[d, int(dev_count[d])] = e
        dev_block[d] += sig
        dev_scalar[d] += float(slot_scalar[idx])
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
        bsig = _block_means(hotness[:, l, :], BLOCKS)
        scale = np.where(mean[l] > 0, risk[l] / np.maximum(mean[l], 1e-12), 1.0)
        bsig = bsig * scale[:, None]
        candidate[l] = _build_layer_fast(bsig, risk[l], n_device, n_exp_per_dev, MCAND)

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
