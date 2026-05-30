"""Seasonal Blend predictor submission.

Uses the ACF-detected period and blends a trailing baseline with a seasonal
predictor, just like in trailing_window_analysis.py / seasonal_blend_analysis.py.
"""
import os
import numpy as np

TAU = float(os.environ.get("SB_TAU", "0.2"))
LAMBDA = float(os.environ.get("SB_LAMBDA", "2.0"))

_STATE = {}

def reset():
    _STATE.clear()

def detect_period(af):
    n_iter = af.shape[0]
    x = af - af.mean(axis=0, keepdims=True)
    nfft = 1 << int(np.ceil(np.log2(2 * n_iter)))
    X = np.fft.rfft(x, n=nfft, axis=0)
    power = (X * np.conj(X)).real
    ac = np.fft.irfft(power, n=nfft, axis=0)[:n_iter]
    acf = ac.sum(axis=(1, 2))
    if acf[0] == 0:
        return None, acf
    acf = acf / acf[0]
    hi = min(len(acf) - 1, n_iter // 2)
    neg = np.where(acf[:hi] < 0)[0]
    if len(neg) == 0:
        return None, acf
    trough = neg[0]
    P = int(trough + np.argmax(acf[trough:hi]))
    if acf[P] < 0.05:
        return None, acf
    return P, acf

def alpha_schedule(H, P, acf):
    rho_base = max(acf[H], 0.0) if H < len(acf) else 0.0
    rho_seas = max(acf[P], 1e-3) if P is not None and P < len(acf) else 1e-3
    return float(rho_seas / (rho_seas + rho_base))

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
    mean_load = loads.mean()
    if mean_load == 0: return 1.0
    return float(loads.max() / mean_load)

def rebalance(hotness, n_device, n_red_expert):
    hotness = hotness.astype(np.float64)
    T, n_layers, n_experts = hotness.shape
    n_exp_per_dev = (n_experts + n_red_expert) // n_device
    
    H = 64 # approximate forward horizon projection
    
    P, acf = detect_period(hotness)
    
    if P is not None and T > P:
        a = alpha_schedule(H, P, acf)
        baseline = hotness.mean(axis=0)
        # Seasonal component: load from one period ago
        # We need to take care not to index out of bounds
        start_idx = max(0, T - P)
        seasonal = hotness[start_idx : start_idx + H].mean(axis=0) if T - P + H <= T else hotness[start_idx:].mean(axis=0)
        mean_pred = (1 - a) * baseline + a * seasonal
    else:
        mean_pred = hotness.mean(axis=0)
        
    risk = mean_pred + LAMBDA * hotness.std(axis=0) if LAMBDA else mean_pred

    candidate = np.zeros((n_layers, n_device, n_exp_per_dev), dtype=np.int64)
    for l in range(n_layers):
        candidate[l] = _build_layer(risk[l], n_device, n_exp_per_dev)

    sig = (n_layers, n_experts, n_device, n_exp_per_dev)
    cur = _STATE.get("deploy") if _STATE.get("sig") == sig else None
    if cur is None:
        _STATE["sig"] = sig
        _STATE["deploy"] = candidate.copy()
        return True, np.arange(n_layers, dtype=np.int64), candidate, None

    improve = np.array([_par(mean_pred[l], cur[l]) - _par(mean_pred[l], candidate[l])
                        for l in range(n_layers)])
    sel = np.where(improve > TAU)[0]
    if sel.size == 0:
        return False, np.array([], dtype=np.int64), candidate, None

    order = sel[np.argsort(improve[sel])[::-1]]
    new_state = cur.copy()
    new_state[order] = candidate[order]
    _STATE["deploy"] = new_state
    return True, order.astype(np.int64), candidate, None
