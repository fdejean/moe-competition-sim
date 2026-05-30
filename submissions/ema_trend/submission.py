"""Double Exponential Smoothing (EMA trend) rebalancer with hysteresis band.

Predicts future load by running a Double EMA (Holt's model) over the trailing
window to capture both level (momentum) and trend (velocity). We project the
trend into the future (horizon H), clip it at 0 to avoid negative weights,
and then apply a variance penalty (like finance_band).

We keep the old placement and only swap layers where the predicted PAR
improvement exceeds a specified TAU threshold (the "cost" penalty).
"""
import os
import numpy as np

TAU = float(os.environ.get("EMA_TAU", "0.2"))
LAMBDA = float(os.environ.get("EMA_LAMBDA", "2.0"))
ALPHA = float(os.environ.get("EMA_ALPHA", "0.2"))
BETA = float(os.environ.get("EMA_BETA", "0.1"))
HORIZON = int(os.environ.get("EMA_HORIZON", "64"))

_STATE = {}

def reset():
    _STATE.clear()

def _build_layer(load, n_device, n_exp_per_dev):
    n_experts = load.shape[0]
    n_slots = n_device * n_exp_per_dev
    replicas = np.ones(n_experts, dtype=np.int64)
    for _ in range(n_slots - n_experts):
        replicas[int(np.argmax(load / replicas))] += 1

    slot_expert = np.repeat(np.arange(n_experts), replicas)
    slot_weight = load[slot_expert] / replicas[slot_expert]
    order = np.argsort(slot_weight, kind="stable")[::-1]

    deploy = np.full((n_device, n_exp_per_dev), -1, dtype=np.int64)
    dev_load = np.zeros(n_device, dtype=np.float64)
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

def _par(load, deploy):
    cut = np.bincount(deploy.reshape(-1), minlength=load.shape[0])
    weights = load / cut
    loads = weights[deploy.reshape(-1)].reshape(deploy.shape).sum(-1)
    # prevent divide by zero
    mean_load = loads.mean()
    if mean_load == 0:
        return 1.0
    return float(loads.max() / mean_load)

def rebalance(hotness, n_device, n_red_expert):
    hotness = hotness.astype(np.float64)
    T, n_layers, n_experts = hotness.shape
    n_exp_per_dev = (n_experts + n_red_expert) // n_device

    # Calculate Double EMA over the window
    s = hotness[0].copy()
    b = np.zeros_like(s)
    for t in range(1, T):
        x = hotness[t]
        s_new = ALPHA * x + (1 - ALPHA) * (s + b)
        b_new = BETA * (s_new - s) + (1 - BETA) * b
        s, b = s_new, b_new

    # Predict future load at H steps ahead
    pred_mean = np.maximum(0.0, s + HORIZON * b)
    
    # We still use the std of the window to gauge variance
    std = hotness.std(axis=0)
    risk = pred_mean + LAMBDA * std if LAMBDA else pred_mean

    candidate = np.zeros((n_layers, n_device, n_exp_per_dev), dtype=np.int64)
    for l in range(n_layers):
        candidate[l] = _build_layer(risk[l], n_device, n_exp_per_dev)

    sig = (n_layers, n_experts, n_device, n_exp_per_dev)
    cur = _STATE.get("deploy") if _STATE.get("sig") == sig else None
    if cur is None:
        _STATE["sig"] = sig
        _STATE["deploy"] = candidate.copy()
        return True, np.arange(n_layers, dtype=np.int64), candidate, None

    # Evaluate improvement based on the *predicted mean* load
    improve = np.array([_par(pred_mean[l], cur[l]) - _par(pred_mean[l], candidate[l])
                        for l in range(n_layers)])
    sel = np.where(improve > TAU)[0]
    if sel.size == 0:
        return False, np.array([], dtype=np.int64), candidate, None

    order = sel[np.argsort(improve[sel])[::-1]]
    new_state = cur.copy()
    new_state[order] = candidate[order]
    _STATE["deploy"] = new_state
    return True, order.astype(np.int64), candidate, None
