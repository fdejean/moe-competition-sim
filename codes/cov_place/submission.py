"""041 cov_place — realized-objective placement (covariance / co-spiking aware).

KEY FINDING (measured on the committed traces): with 020's steady-state
deployment, the realized objective mean_t PAR(load_t) runs 6-10% HIGHER than
PAR(mean_load), which is what 020 actually optimizes. That gap is the
"co-spiking penalty": when two experts that spike on the SAME iterations sit on
the same device, that device's per-iteration peak is far above what its mean
load implies (Jensen: E[max] >> max[E]). Mean-load LPT is blind to it.

Fix: optimize placement against the per-iteration peak directly, via a coherent
risk surrogate. For each device d let m_d, s_d be the mean and std of its
per-iteration load over the collection window (s_d^2 = sum of cross-expert
covariances of the experts on d). Minimise

    max_d ( m_d + KAPPA * s_d )

i.e. the worst device's upper-confidence load. This splits experts that spike
together (they inflate s_d) and is happy to co-locate anti-correlated experts
(diversification) -- exactly portfolio risk budgeting at the device level.
KAPPA=0 recovers the mean-load makespan refine (≈020). The swap delta only
re-touches the two affected device rows, so it stays within the time budget.

Replication (mean + LAMBDA*std water-filling), the no-trade band, and the
PAR-neutral transmit relabel are unchanged from 020 to isolate the placement
objective. Tunables: FB_KAPPA (1.5), FB_SAMPLE (256), FB_TAU (0.2), FB_LAMBDA (2.0).
"""
from __future__ import annotations

import os
import time

import numpy as np

_w = os.environ.get("FB_WINDOW")
WINDOW = None if _w in (None, "", "all") else int(_w)
TAU = float(os.environ.get("FB_TAU", "0.2"))
LAMBDA = float(os.environ.get("FB_LAMBDA", "2.0"))
KAPPA = float(os.environ.get("FB_KAPPA", "1.5"))
SAMPLE = int(os.environ.get("FB_SAMPLE", "128"))

_TIME_BUDGET_S = 1.0
_MAX_PASSES_PER_LAYER = 40

_STATE: dict = {}


def reset():
    _STATE.clear()


def _init_deploy_table(n_device, n_experts, n_red, slots):
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


def _dev_obj(DL):
    """Per-device upper-confidence load m_d + KAPPA*s_d from the (n_device, S)
    per-iteration device-load matrix."""
    m = DL.mean(axis=1)
    s = DL.std(axis=1)
    return m + KAPPA * s


def _refine_layer_emp(samp, deploy, deadline):
    """Swap-refine to minimise max_d (mean_d + KAPPA*std_d) of per-iteration
    device load. `samp` is (n_experts, S) per-replica per-iteration load
    (hotness_sample / cut). Pure swaps keep cut fixed -> samp fixed."""
    n_device, slots = deploy.shape
    n_experts = samp.shape[0]
    # device load matrix over the S sampled iterations
    DL = samp[deploy].sum(axis=1)                       # (n_device, S)
    objd = _dev_obj(DL)
    present = np.zeros((n_device, n_experts), dtype=bool)
    rows = np.repeat(np.arange(n_device), slots)
    present[rows, deploy.reshape(-1)] = True
    all_devs = np.arange(n_device)

    for _ in range(_MAX_PASSES_PER_LAYER):
        if time.time() > deadline:
            break
        A = int(np.argmax(objd))
        J = float(objd[A])
        other = all_devs[all_devs != A]
        # "max objective over devices other than A" and the runner-up, so that
        # for a swap touching A and some B we can get max over the rest in O(1).
        oobj = objd[other]
        top_order = np.argsort(oobj)[::-1]
        top1_dev = int(other[top_order[0]]); top1_val = float(oobj[top_order[0]])
        top2_val = float(oobj[top_order[1]]) if other.size > 1 else -np.inf

        o_expert = deploy[other].reshape(-1)
        o_devidx = np.repeat(other, slots)
        # "max over devices except A and this partner's device B"
        other_max = np.where(o_devidx == top1_dev, top2_val, top1_val)

        best_gain = 1e-9
        best = None
        for sA in range(slots):
            e = int(deploy[A, sA])
            pe = samp[e]                                 # (S,)
            pf = samp[o_expert]                          # (n_part, S)
            newA = DL[A][None, :] - pe[None, :] + pf     # (n_part, S)
            newB = DL[o_devidx] - pf + pe[None, :]
            objA = newA.mean(1) + KAPPA * newA.std(1)
            objB = newB.mean(1) + KAPPA * newB.std(1)
            newJ = np.maximum(np.maximum(objA, objB), other_max)
            ok = (~present[A, o_expert]) & (~present[o_devidx, e])
            cand = np.where(ok, newJ, np.inf)
            j = int(np.argmin(cand))
            gain = J - float(cand[j])
            if gain > best_gain:
                best_gain = gain
                best = (sA, j, e)
        if best is None:
            break
        sA, j, e = best
        B = int(o_devidx[j]); f = int(o_expert[j])
        sB = int(np.where(deploy[B] == f)[0][0])
        deploy[A, sA], deploy[B, sB] = f, e
        DL[A] = DL[A] - samp[e] + samp[f]
        DL[B] = DL[B] - samp[f] + samp[e]
        objd[A] = float(DL[A].mean() + KAPPA * DL[A].std())
        objd[B] = float(DL[B].mean() + KAPPA * DL[B].std())
        present[A] = False; present[A, deploy[A]] = True
        present[B] = False; present[B, deploy[B]] = True
    return deploy


def _relabel_to_old(new_layer, old_layer):
    n_device, slots = new_layer.shape
    new_sets = [set(new_layer[d].tolist()) for d in range(n_device)]
    old_sets = [set(old_layer[d].tolist()) for d in range(n_device)]

    overlap = np.zeros((n_device, n_device), dtype=np.int64)
    for i in range(n_device):
        for k in range(n_device):
            overlap[i, k] = len(new_sets[i] & old_sets[k])
    assign = -np.ones(n_device, dtype=np.int64)
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

    # Sample iterations for the empirical objective (evenly across the window).
    T = hotness.shape[0]
    S = min(SAMPLE, T)
    sidx = np.linspace(0, T - 1, S).astype(np.int64)
    hsamp = hotness[sidx]                                # (S, n_layers, n_experts)

    deadline = time.time() + _TIME_BUDGET_S
    per_layer = (deadline - time.time()) / max(n_layers, 1)
    candidate = np.zeros((n_layers, n_device, n_exp_per_dev), dtype=np.int64)
    for l in range(n_layers):
        deploy = _build_layer(risk[l], n_device, n_exp_per_dev)
        cut = np.bincount(deploy.reshape(-1), minlength=n_experts).astype(np.float64)
        cut[cut == 0] = 1.0
        samp = (hsamp[:, l, :] / cut).T.copy()           # (n_experts, S)
        layer_deadline = min(deadline, time.time() + 4.0 * per_layer)
        candidate[l] = _refine_layer_emp(samp, deploy, layer_deadline)

    sig = (n_layers, n_experts, n_device, n_exp_per_dev)
    cur = _STATE.get("deploy") if _STATE.get("sig") == sig else None

    if cur is None:
        init = _init_deploy_table(n_device, n_experts, n_red_expert, n_exp_per_dev)
        for l in range(n_layers):
            candidate[l] = _relabel_to_old(candidate[l], init)
        _STATE["sig"] = sig
        _STATE["deploy"] = candidate.copy()
        return True, np.arange(n_layers, dtype=np.int64), candidate, None

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
