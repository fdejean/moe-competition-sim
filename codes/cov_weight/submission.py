"""064 cov_weight — co-spiking as a SCALAR weight (finance_band-speed, EP256-safe).

The real PAR lever is co-spiking (experts hot in the SAME iterations sharing a
device; realized PAR 1.69 >> mean-load PAR 1.05). block_pack captured it with a
per-device k-vector cost, but that inner loop is ~2x finance_band and FLOORED at
EP256 (server tests DS-R1 EP256). The server only tolerates ~finance_band per-call
time, so co-spiking must be folded into a SCALAR weight that reuses finance_band's
exact pipeline (one water-fill + one scalar LPT pass).

Construction: split the window into k temporal blocks; busy_j = total load in block
j (how globally hot that block is). An expert's co-spiking premium is how much of its
load lands in globally-busy blocks:  cospike_e = sum_j blockmean_e[j] * busy_j
(busy normalized to mean 1). Experts that are hot WHEN EVERYTHING IS HOT get
cospike_e > mean_e. We add GAMMA*(cospike-mean) to the finance_band risk so LPT
places these co-spikers first (=> spread across devices) and water-fill gives them
extra replicas — both reduce per-iteration (co-spiking) device imbalance. All scalar,
so per-call cost == finance_band.

Tunables (env): FB_TAU, FB_LAMBDA, FB_WINDOW + FB_GAMMA (co-spiking weight, default
1.0), FB_BLOCKS (k, default 8).
"""
from __future__ import annotations

import os

import numpy as np

_w = os.environ.get("FB_WINDOW")
WINDOW = None if _w in (None, "", "all") else int(_w)
TAU = float(os.environ.get("FB_TAU", "0.2"))
LAMBDA = float(os.environ.get("FB_LAMBDA", "2.0"))
GAMMA = float(os.environ.get("FB_GAMMA", "1.0"))
BLOCKS = int(os.environ.get("FB_BLOCKS", "8"))

_STATE: dict = {}


def reset():
    _STATE.clear()


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


def _cospike(hotness, mean, k):
    """Per-(layer,expert) co-spiking premium = covariance of expert load with the
    per-iteration AGGREGATE load (how much the expert drives the moments when
    EVERYTHING is hot). hotness:(T,L,E), mean:(L,E). One pass over the window."""
    T, L, E = hotness.shape
    g = hotness.sum(axis=2)                       # (T,L) aggregate load per iteration
    gmean = g.mean(axis=0)                         # (L,)
    gc = g - gmean[None, :]                        # (T,L) centered aggregate
    gvar = (gc * gc).mean(axis=0)                  # (L,)
    # cov_e = E[(load_e - mean_e)(g - gmean)] ; beta_e = cov_e / var(g) (units of load)
    cov = np.einsum("tle,tl->le", hotness - mean[None], gc) / T    # (L,E)
    beta = cov / np.maximum(gvar[:, None], 1e-12)                  # (L,E) load per unit agg
    # premium scaled back to load units: beta * std(g) ; experts that ride the
    # aggregate peaks get a positive premium, anti-correlated experts ~0.
    return mean + beta * np.sqrt(np.maximum(gvar, 0.0))[:, None]


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
    cospike = _cospike(hotness, mean, BLOCKS)
    risk = mean + (LAMBDA * std if LAMBDA else 0.0) + GAMMA * np.maximum(cospike - mean, 0.0)
    n_layers, n_experts = mean.shape
    n_exp_per_dev = (n_experts + n_red_expert) // n_device

    candidate = np.zeros((n_layers, n_device, n_exp_per_dev), dtype=np.int64)
    for l in range(n_layers):
        candidate[l] = _build_layer(risk[l], n_device, n_exp_per_dev)

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
