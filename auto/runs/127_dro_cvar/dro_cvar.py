"""dro_cvar — dro_ambiguity SOTA + DISTRIBUTION-FREE (CVaR) replication sizing.

dro_ambiguity sizes copies by mean+3*std (a GAUSSIAN tail proxy). But expert load
is bursty/heavy-tailed, so std understates rare spikes. Here rep_w = each expert's
EMPIRICAL upper-tail = mean of its worst CVAR_Q (10%) iterations -> copies are sized
to the load that ACTUALLY occurs in the tail, no normality assumption. Floored at
place_w so it never under-counts vs the placement target. Replication-sizing was the
sole winning axis in the 120-126 wave (dro_ambiguity 114.32 > all placement ideas);
this makes that tail insurance distribution-free. Placement/drift/blocks unchanged.

--- dro_ambiguity note ---
dro_ambiguity — drift_router SOTA + DISTRIBUTIONALLY-ROBUST replication sizing.

NEW MECHANISM: replication (how many copies each expert gets) and placement (where
copies go) currently use the SAME risk target (mean + lam*std). We DECOUPLE them: the
number of copies is sized for the WORST distribution in a moment ambiguity set around
the window — an upper-confidence load mean + KAPPA_REP*std with KAPPA_REP (3.0) >
placement lam (2.0). High-variance experts (the ones that spike unpredictably) get
more copies so their load is split across devices BEFORE any single spike lands,
while placement still balances on the gentler risk target. This is the robust-
optimization view of replication (copies = insurance against tail load), distinct
from just inflating the placement std weight. Replication count uses rep_weight;
the placed slot_weight stays = risk (so PAR ordering is unchanged). All EP. Drift
target, block/peak columns, sticky, no-trade band, EP-adaptive lambda unchanged.

--- original drift_router note ---

WHY (experiments/ceiling_decomp.py): with perfect foresight the SAME packer hits
PAR ~1.0-1.4 at every EP, so packing is solved. On STATIONARY data the trailing
mean is a great predictor (forecast gap only +0.03..+0.11) and the residual is
structural static-hold -> nothing to gain. But under NON-STATIONARITY (the Mix
dataset: a mixture whose hot set drifts), the trailing-1024 mean goes stale and
realized PAR explodes (drift stress: trailing PAR 1.6->4.4 while block-foresight
stays ~1.3). That excess is a FORECASTING failure, not packing -> recoverable by
weighting recent iterations when drift is present.

MECHANISM: per layer, measure drift = ||recent_mean - full_mean||_1 / ||full_mean||_1
(recent = last quarter of the given window). Blend the target toward recency by
that drift: adaptive_mean = (1-a)*full_mean + a*recent_mean, a = clip(GAMMA*drift,0,1).
On stationary data drift~0 -> a~0 -> adaptive_mean == full_mean == EXACTLY peakblock
(so we cannot regress the cases we already win). On Mix, a->1 -> lean recent and
track the new regime. Everything else (joint-peak, block columns, EP-gating, sticky
relabel, no-trade band, EP-adaptive lambda) is peakblock, unchanged.

Tunables (env): FB_TAU, FB_LAMBDA, FB_LAMBDA_HI, FB_STAY, FB_WINDOW, FB_BLOCKS,
FB_BLOCK_MAXDEV, FB_THETA, FB_PEAKFRAC, FB_DRIFT_GAMMA (recency gain, default 1.0),
FB_RECENT_FRAC (recent sub-window fraction, default 0.25).
"""
from __future__ import annotations

import os

import numpy as np

_w = os.environ.get("FB_WINDOW")
WINDOW = None if _w in (None, "", "all") else int(_w)
TAU = float(os.environ.get("FB_TAU", "0.2"))
LAMBDA = float(os.environ.get("FB_LAMBDA", "2.0"))
LAMBDA_HI = float(os.environ.get("FB_LAMBDA_HI", "1.5"))
STAY = float(os.environ.get("FB_STAY", "0.25"))
BLOCKS = int(os.environ.get("FB_BLOCKS", "6"))
BLOCK_MAXDEV = int(os.environ.get("FB_BLOCK_MAXDEV", "128"))
THETA = float(os.environ.get("FB_THETA", "0.5"))
PEAKFRAC = float(os.environ.get("FB_PEAKFRAC", "0.10"))
DRIFT_GAMMA = float(os.environ.get("FB_DRIFT_GAMMA", "1.0"))
RECENT_FRAC = float(os.environ.get("FB_RECENT_FRAC", "0.25"))
KAPPA_REP = float(os.environ.get("FB_KAPPA_REP", "3.0"))  # robust replication confidence
CVAR_Q = float(os.environ.get("FB_CVAR_Q", "0.10"))       # replication tail fraction


def _cvar_load(hotness, place_w):
    """Per-expert empirical upper-tail: mean of each expert's worst CVAR_Q iters
    (distribution-free spike insurance). Floored at place_w. -> (L, E)."""
    T = hotness.shape[0]
    if T > 128:                                # subsample: tail-mean estimate is fine,
        hotness = hotness[np.linspace(0, T - 1, 128).astype(np.int64)]  # keeps cost ~scalar
        T = 128
    q = max(1, int(round(CVAR_Q * T)))
    part = np.partition(hotness, T - q, axis=0)[T - q:]   # (q, L, E) top-q per expert
    return np.maximum(part.mean(axis=0), place_w)

_STATE: dict = {}


def _adaptive_mean(hotness, full_mean):
    """Blend the full-window mean toward the recent sub-window, per layer, by the
    measured drift. Stationary -> returns full_mean unchanged (== peakblock)."""
    T = hotness.shape[0]
    r = max(1, int(round(RECENT_FRAC * T)))
    if r >= T:
        return full_mean
    recent_mean = hotness[-r:].mean(axis=0)               # (L, E)
    denom = np.abs(full_mean).sum(axis=1) + 1e-12         # (L,)
    drift = np.abs(recent_mean - full_mean).sum(axis=1) / denom   # (L,)
    a = np.clip(DRIFT_GAMMA * drift, 0.0, 1.0)[:, None]   # (L,1)
    return (1.0 - a) * full_mean + a * recent_mean


def reset():
    _STATE.clear()


def _peak_mean(hotness):
    T, L, E = hotness.shape
    p = max(1, int(round(PEAKFRAC * T)))
    total = hotness.sum(axis=2)
    peak_idx = np.argpartition(total, T - p, axis=0)[T - p:]
    l_idx = np.broadcast_to(np.arange(L), (p, L))
    peaked = hotness[peak_idx, l_idx, :]
    return peaked.mean(axis=0)


def _block_means_all(hotness, k):
    T = hotness.shape[0]
    if k <= 1 or T < k:
        return hotness.mean(axis=0)[:, :, None]
    idx = (np.arange(T) * k) // T
    L, E = hotness.shape[1], hotness.shape[2]
    out = np.zeros((L, E, k))
    counts = np.zeros(k)
    for j in range(k):
        m = idx == j
        counts[j] = m.sum()
        out[:, :, j] = hotness[m].sum(axis=0)
    out /= np.maximum(counts, 1.0)[None, None, :]
    return out


def _init_deploy_table(n_device, n_experts, slots):
    deploy = np.zeros((n_device, slots), dtype=np.int64)
    for d in range(n_device):
        for s in range(slots - 1):
            deploy[d, s] = (d * (slots - 1) + s) % n_experts
        deploy[d, -1] = deploy[d, -2]
    return deploy


def _replicas_and_order(weight, n_device, n_exp_per_dev, rep_weight=None):
    n_experts = weight.shape[0]
    n_slots = n_device * n_exp_per_dev
    # DRO: size copies by the robust (worst-case-in-ambiguity) load rep_weight, but
    # keep the placed slot_weight = weight (the gentler placement risk) so PAR is
    # ordered the same. rep_weight defaults to weight (== SOTA) when not supplied.
    rw = weight if rep_weight is None else rep_weight
    replicas = np.ones(n_experts, dtype=np.int64)
    for _ in range(n_slots - n_experts):
        replicas[int(np.argmax(rw / replicas))] += 1
    slot_expert = np.repeat(np.arange(n_experts), replicas)
    slot_weight = weight[slot_expert] / replicas[slot_expert]
    order = np.argsort(slot_weight, kind="stable")[::-1]
    return replicas, slot_expert, slot_weight, order


def _on_prev(prev, n_device, n_experts, stay_amt):
    if prev is None or stay_amt <= 0:
        return None
    on_prev = np.zeros((n_device, n_experts), dtype=bool)
    for d in range(n_device):
        for e2 in prev[d]:
            if e2 >= 0:
                on_prev[d, int(e2)] = True
    return on_prev


def _build_scalar(weight, n_device, n_exp_per_dev, prev=None, rep_weight=None):
    n_experts = weight.shape[0]
    replicas, slot_expert, slot_weight, order = _replicas_and_order(
        weight, n_device, n_exp_per_dev, rep_weight)
    deploy = np.full((n_device, n_exp_per_dev), -1, dtype=np.int64)
    dev_load = np.zeros(n_device)
    dev_count = np.zeros(n_device, dtype=np.int64)
    big = float(slot_weight.sum()) + 1.0
    stay_amt = STAY * (float(slot_weight.sum()) / max(n_device, 1))
    on_prev = _on_prev(prev, n_device, n_experts, stay_amt)
    for idx in order:
        e = int(slot_expert[idx])
        score = dev_load + np.where((deploy == e).any(axis=1), big, 0.0)
        if on_prev is not None:
            score = score - np.where(on_prev[:, e], stay_amt, 0.0)
        score[dev_count >= n_exp_per_dev] = np.inf
        d = int(np.argmin(score))
        deploy[d, int(dev_count[d])] = e
        dev_load[d] += slot_weight[idx]
        dev_count[d] += 1
    return deploy


def _build_block(weight, block_w, n_device, n_exp_per_dev, prev=None, rep_weight=None):
    n_experts = weight.shape[0]
    replicas, slot_expert, slot_weight, order = _replicas_and_order(
        weight, n_device, n_exp_per_dev, rep_weight)
    slot_block = block_w[slot_expert] / replicas[slot_expert][:, None]
    k = slot_block.shape[1]
    deploy = np.full((n_device, n_exp_per_dev), -1, dtype=np.int64)
    dev_block = np.zeros((n_device, k))
    dev_count = np.zeros(n_device, dtype=np.int64)
    big = float(slot_weight.sum()) + 1.0
    stay_amt = STAY * (float(slot_weight.sum()) / max(n_device, 1))
    on_prev = _on_prev(prev, n_device, n_experts, stay_amt)
    for idx in order:
        e = int(slot_expert[idx])
        score = (dev_block + slot_block[idx]).max(axis=1)
        score = score + np.where((deploy == e).any(axis=1), big, 0.0)
        if on_prev is not None:
            score = score - np.where(on_prev[:, e], stay_amt, 0.0)
        score[dev_count >= n_exp_per_dev] = np.inf
        d = int(np.argmin(score))
        deploy[d, int(dev_count[d])] = e
        dev_block[d] += slot_block[idx]
        dev_count[d] += 1
    return deploy


def _align(new, old, load):
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
    rows = new[assign]
    out = np.empty_like(rows)
    for d in range(nd):
        src = rows[d]
        oldrow = old[d]
        used_src = np.zeros(sl, dtype=bool)
        rowout = -np.ones(sl, dtype=np.int64)
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
    full_mean = hotness.mean(axis=0)
    # DRIFT-ADAPTIVE target: lean on recent iters when the window is non-stationary
    # (Mix); identical to full_mean when stationary (LmSys/ShareGPT/WildChat).
    mean = _adaptive_mean(hotness, full_mean)
    peak = _peak_mean(hotness) if THETA > 0 else mean
    place_w = (1.0 - THETA) * mean + THETA * peak
    lam = LAMBDA_HI if n_device >= 128 else LAMBDA
    std = hotness.std(axis=0)
    risk = place_w + lam * std if lam else place_w
    # DRO robust replication target: upper-confidence load over the moment ambiguity
    # set (KAPPA_REP > lam). Sizes copies for the worst-case spike, not the mean.
    rep_w = _cvar_load(hotness, place_w)
    n_layers, n_experts = mean.shape
    n_exp_per_dev = (n_experts + n_red_expert) // n_device

    use_block = n_device <= BLOCK_MAXDEV
    block_w = _block_means_all(hotness, BLOCKS) if use_block else None
    if use_block and THETA > 0:
        # PEAK-AS-BLOCK (novel): append the joint-peak per-expert load as an extra
        # block column so the greedy balances devices across temporal blocks AND the
        # co-spike scenario simultaneously -> placement itself becomes spike-aware,
        # not just the scalar target weight. block_w: (L,E,k) -> (L,E,k+1).
        block_w = np.concatenate([block_w, peak[:, :, None]], axis=2)

    sig = (n_layers, n_experts, n_device, n_exp_per_dev)
    cur = _STATE.get("deploy") if _STATE.get("sig") == sig else None

    candidate = np.zeros((n_layers, n_device, n_exp_per_dev), dtype=np.int64)
    for l in range(n_layers):
        prev = cur[l] if cur is not None else None
        if use_block:
            candidate[l] = _build_block(risk[l], block_w[l], n_device, n_exp_per_dev,
                                        prev, rep_w[l])
        else:
            candidate[l] = _build_scalar(risk[l], n_device, n_exp_per_dev, prev, rep_w[l])

    if cur is None:
        init = _init_deploy_table(n_device, n_experts, n_exp_per_dev)
        for l in range(n_layers):
            candidate[l] = _align(candidate[l], init, place_w[l])
        _STATE["sig"] = sig
        _STATE["deploy"] = candidate.copy()
        return True, np.arange(n_layers, dtype=np.int64), candidate, None

    for l in range(n_layers):
        candidate[l] = _align(candidate[l], cur[l], place_w[l])

    improve = np.array([_par(place_w[l], cur[l]) - _par(place_w[l], candidate[l])
                        for l in range(n_layers)])
    sel = np.where(improve > TAU)[0]
    if sel.size == 0:
        return False, np.array([], dtype=np.int64), candidate, None

    order = sel[np.argsort(improve[sel])[::-1]]
    new_state = cur.copy()
    new_state[order] = candidate[order]
    _STATE["deploy"] = new_state
    return True, order.astype(np.int64), candidate, None
