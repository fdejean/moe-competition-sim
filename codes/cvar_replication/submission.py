"""051 — cvar_replication: finance_band with CVaR (tail-load) replication weight.

finance_band replicates by a risk measure mean + LAMBDA*std. But realized PAR is
driven by the *worst-case* per-iteration device load, not the average — a device
holding an expert that spikes hard on a few iterations bottlenecks those steps even
if its mean load is moderate. So instead of mean+LAMBDA*std we weight replication by
the **CVaR / expected shortfall** of each expert's load: the mean of its worst
ALPHA-fraction of iterations in the window. That is the canonical finance tail-risk
measure (expected loss in the bad tail) and it directly targets the per-iteration
peaks that set PAR. Experts that are merely steadily-warm get fewer replicas;
experts that occasionally spike get more.

Pure replication-WEIGHT change over the proven finance_band pipeline (water-fill +
LPT + no-trade band), so it is EP128-time-safe by construction (one extra sort over
the window axis; no per-device-squared work, no relabel, no search).

Tunables (env): FB_WINDOW, FB_TAU (band), FB_CVAR_ALPHA (tail fraction, default 0.2),
FB_LAMBDA (kept as a small blend so steady mean still counts).
"""
from __future__ import annotations

import os

import numpy as np

_w = os.environ.get("FB_WINDOW")
WINDOW = None if _w in (None, "", "all") else int(_w)
TAU = float(os.environ.get("FB_TAU", "0.2"))
LAMBDA = float(os.environ.get("FB_LAMBDA", "1.0"))
ALPHA = float(os.environ.get("FB_CVAR_ALPHA", "0.2"))   # worst 20% of iterations

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


def _cvar(hotness, alpha):
    """Per-(layer,expert) CVaR_{1-alpha}: mean of the worst `alpha` fraction of
    iterations. hotness: (T, n_layers, n_experts) -> (n_layers, n_experts)."""
    T = hotness.shape[0]
    k = max(1, int(np.ceil(alpha * T)))
    # top-k largest per (layer,expert) along the iteration axis
    part = np.partition(hotness, T - k, axis=0)[T - k:]
    return part.mean(axis=0)


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
    tail = _cvar(hotness, ALPHA)
    # replication weight: tail risk, with a small mean blend for steady load.
    risk = tail + LAMBDA * (tail - mean)  # emphasize the tail-vs-mean gap (spikiness)
    risk = np.maximum(risk, mean)         # never below the mean load
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
