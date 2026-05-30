"""059 spike_iter_pack — co-spiking placement on the spikiest iterations.

block_pack balances device load across k UNIFORM time-blocks, but block-averaging
smooths sharp single-iteration spikes. Realized PAR = mean_t max_d(load)/mean(load)
is dominated by the WORST iterations. So instead of uniform blocks this picks, per
layer, the S iterations with the highest cross-expert load variance (the most
imbalanced / co-spiking iterations) and balances device load across exactly those S
iterations in one greedy pass. Directly minimizes the device imbalance on the
iterations that set PAR.

Same single-pass, finance_band-speed structure as block_pack (cost O(n_slots*
n_device*S); EP128-safe). Replication water-fill on mean+LAMBDA*std + no-trade band
unchanged.

Tunables (env): FB_TAU, FB_LAMBDA, FB_WINDOW + FB_SPIKES (S, default 24).
"""
from __future__ import annotations

import os

import numpy as np

_w = os.environ.get("FB_WINDOW")
WINDOW = None if _w in (None, "", "all") else int(_w)
TAU = float(os.environ.get("FB_TAU", "0.2"))
LAMBDA = float(os.environ.get("FB_LAMBDA", "2.0"))
SPIKES = int(os.environ.get("FB_SPIKES", "24"))

_STATE: dict = {}


def reset():
    _STATE.clear()


def _spike_sig(hot_layer, s):
    """hot_layer: (T, n_experts) -> (n_experts, s) loads on the s most-imbalanced
    iterations (highest cross-expert variance)."""
    T = hot_layer.shape[0]
    s = min(s, T)
    var_t = hot_layer.var(axis=1)                       # imbalance of each iteration
    sel = np.argpartition(var_t, T - s)[T - s:]
    return hot_layer[sel].T.copy()                      # (n_experts, s)


def _build_layer_block(bsig, mean, n_device, n_exp_per_dev):
    n_experts = mean.shape[0]
    n_slots = n_device * n_exp_per_dev
    replicas = np.ones(n_experts, dtype=np.int64)
    for _ in range(n_slots - n_experts):
        replicas[int(np.argmax(mean / replicas))] += 1

    slot_expert = np.repeat(np.arange(n_experts), replicas)
    slot_sig = bsig[slot_expert] / replicas[slot_expert][:, None]
    slot_scalar = slot_sig.sum(axis=1)
    order = np.argsort(slot_scalar, kind="stable")[::-1]

    deploy = np.full((n_device, n_exp_per_dev), -1, dtype=np.int64)
    dev_block = np.zeros((n_device, bsig.shape[1]))
    dev_count = np.zeros(n_device, dtype=np.int64)
    present = np.zeros((n_device, n_experts), dtype=bool)
    big = float(slot_scalar.sum()) + 1.0
    for idx in order:
        e = slot_expert[idx]
        sig = slot_sig[idx]
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
        bsig = _spike_sig(hotness[:, l, :], SPIKES)
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
