"""block_refine — cov_iter (114.61) + ITERATIVE peak-reducing refine at EP64/128 (6x).

Most cases live at EP<=128, still placed by the fast block greedy. With the 6x budget we
add bounded LOCAL SEARCH on top of it: compute each device's realized load time series
over the subsample, and repeatedly swap an expert off the current worst-peak device with
one on a low-peak device whenever that lowers the global max realized peak (duplicate-safe,
replica-neutral). This directly descends the L-inf realized objective the score cares about
— the iterative refinement that FLOORED at 3x and is now affordable at 6x. Bounded rounds,
bounded candidates -> safe. EP256 keeps cov_iter's min-peak matching; EP32 unchanged (tight
budget). Swaps preserve the expert multiset -> all experts present, no floor.

--- cov_iter — cov_match winner (114.35) + RICHER EP256 matching for the 6x budget.

cov_match's phase-fold pairing won at EP256 with a cheap O(n log n) 1D heuristic. With
the per-call budget extended to ~6x DS-EPLB, we can afford the REAL objective: greedily
match each light slot to the device whose fixed heavy partner MINIMIZES the resulting
realized peak max_t(heavy[t] + light[t]) over the subsampled window — i.e. directly
minimize co-spiking instead of approximating it with one principal axis. O(n_device^2 * s),
vectorized per step (this is the kind of richer EP256 placement that floored at 3x but
fits at 6x). Heavy slots (one per device) fixed by load; light slots assigned heaviest-
first. Feasible by construction -> no floor. EP<=128 byte-identical to SOTA.

--- cov_match — drift_router SOTA + EXPLICIT anti-correlation PAIRING at EP256.

NEW MECHANISM (the covariance lever, explicit & safe): at EP256 each device holds
exactly 2 slots, so placement IS a pairing problem and device load = sum of its two
slots. Realized PAR = max_t(slot_a[t]+slot_b[t]); pairing ANTI-correlated experts cuts
each device's realized peak (var(dev)=var(a)+var(b)+2cov(a,b)). We do this explicitly
but in O(n log n), fully vectorized (no O(E^2) python loop -> no floor risk): subsample
the window, find the dominant co-spiking temporal mode u via 5 power iterations, and
score each slot by its projection (phase) onto u. Then a 2D fold: split slots into a
heavy half and a light half by load (balances the MEAN, the classic min-max pairing),
and within the fold pair each heavy slot with a light slot of OPPOSITE phase (balances
the realized PEAK by anti-correlating). Distinct from spectral_cut (which feeds phase as
a soft column to the greedy); this does the hard matching the 2-slot regime actually is.
EP256 only (n_exp_per_dev==2; else falls back to scalar greedy == SOTA). Feasible by
construction -> no floor. EP<=128 byte-identical to SOTA.

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


def _replicas_and_order(weight, n_device, n_exp_per_dev):
    n_experts = weight.shape[0]
    n_slots = n_device * n_exp_per_dev
    replicas = np.ones(n_experts, dtype=np.int64)
    for _ in range(n_slots - n_experts):
        replicas[int(np.argmax(weight / replicas))] += 1
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


def _build_scalar(weight, n_device, n_exp_per_dev, prev=None):
    n_experts = weight.shape[0]
    replicas, slot_expert, slot_weight, order = _replicas_and_order(
        weight, n_device, n_exp_per_dev)
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


def _build_covmatch(weight, hot_l, n_device, n_exp_per_dev, prev=None, n_sub=48):
    """Greedy min-realized-peak matching for the 2-slot (EP256) regime: heavy slots
    fixed one-per-device by load; each light slot (heaviest first) goes to the device
    whose heavy partner minimizes max_t(heavy[t]+light[t]) over the subsample. Directly
    minimizes co-spiking (the real objective), affordable under the 6x budget. Falls back
    to scalar greedy when n_exp_per_dev != 2."""
    if n_exp_per_dev != 2:
        return _build_scalar(weight, n_device, n_exp_per_dev, prev)
    replicas, slot_expert, slot_weight, order = _replicas_and_order(
        weight, n_device, n_exp_per_dev)
    T = hot_l.shape[0]
    s = min(n_sub, T)
    ti = np.linspace(0, T - 1, s).astype(np.int64)
    sig = hot_l[ti][:, slot_expert] / replicas[slot_expert][None, :]   # (s, n_slots)
    by_w = np.argsort(slot_weight, kind="stable")[::-1]
    heavy = by_w[:n_device]                              # one heavy slot per device d
    light = by_w[n_device:]
    sig_h = sig[:, heavy]                                # (s, n_device) fixed heavy loads
    light_order = light[np.argsort(slot_weight[light], kind="stable")[::-1]]
    avail = np.ones(n_device, dtype=bool)
    assign = np.full(n_device, -1, dtype=np.int64)       # device -> light slot id
    for j in light_order:
        peaks = (sig_h + sig[:, j][:, None]).max(0)      # resulting realized peak per device
        peaks[~avail] = np.inf
        d = int(np.argmin(peaks))
        assign[d] = j
        avail[d] = False
    deploy = np.empty((n_device, 2), dtype=np.int64)
    deploy[:, 0] = slot_expert[heavy]
    deploy[:, 1] = slot_expert[assign]
    return deploy


REFINE_ROUNDS = int(os.environ.get("FB_REFINE_ROUNDS", "60"))
REFINE_CAND = int(os.environ.get("FB_REFINE_CAND", "8"))


def _refine_peak(deploy, hot_l, n_rounds=REFINE_ROUNDS, n_cand=REFINE_CAND, n_sub=48):
    """Bounded local search: swap an expert off the worst-peak device with one on a
    low-peak device when it lowers the global max realized peak. Replica-neutral and
    duplicate-safe, so it never changes which experts exist -> no floor."""
    nd, epd = deploy.shape
    E = hot_l.shape[1]
    T = hot_l.shape[0]
    s = min(n_sub, T)
    ti = np.linspace(0, T - 1, s).astype(np.int64)
    Hs = hot_l[ti]                                        # (s, E)
    rep = np.bincount(deploy.reshape(-1), minlength=E).astype(np.float64)
    rep[rep == 0] = 1.0
    perslot = Hs / rep[None, :]                           # (s, E) per-copy load
    dload = perslot[:, deploy].sum(axis=2)               # (s, nd) device load series
    for _ in range(n_rounds):
        peak = dload.max(0)                              # (nd,)
        dw = int(np.argmax(peak))
        pmax = peak[dw]
        cand = np.argsort(peak)[:n_cand]
        cand = cand[cand != dw]
        best_val = pmax
        best = None
        for si in range(epd):
            e1 = int(deploy[dw, si]); c1 = perslot[:, e1]
            for dt in cand:
                dt = int(dt)
                row_dt = deploy[dt]
                if (deploy[dw] == row_dt[:, None]).any():
                    pass
                for sj in range(epd):
                    e2 = int(deploy[dt, sj])
                    if e2 == e1:
                        continue
                    if (deploy[dw] == e2).any() or (deploy[dt] == e1).any():
                        continue
                    c2 = perslot[:, e2]
                    ndw = float((dload[:, dw] - c1 + c2).max())
                    ndt = float((dload[:, dt] - c2 + c1).max())
                    v = ndw if ndw > ndt else ndt
                    if v < best_val - 1e-12:
                        best_val = v
                        best = (si, dt, sj, e1, e2, c1, c2)
        if best is None:
            break
        si, dt, sj, e1, e2, c1, c2 = best
        deploy[dw, si] = e2
        deploy[dt, sj] = e1
        dload[:, dw] = dload[:, dw] - c1 + c2
        dload[:, dt] = dload[:, dt] - c2 + c1
    return deploy


def _build_block(weight, block_w, n_device, n_exp_per_dev, prev=None):
    n_experts = weight.shape[0]
    replicas, slot_expert, slot_weight, order = _replicas_and_order(
        weight, n_device, n_exp_per_dev)
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
    risk = place_w + lam * hotness.std(axis=0) if lam else place_w
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
            cand = _build_block(risk[l], block_w[l], n_device, n_exp_per_dev, prev)
            if n_device >= 64:   # 6x-budget local search; skip EP32 (tight budget)
                cand = _refine_peak(cand, hotness[:, l, :])
            candidate[l] = cand
        else:
            # EP>BLOCK_MAXDEV (EP256, 2 slots/device): explicit anti-correlation pairing.
            candidate[l] = _build_covmatch(risk[l], hotness[:, l, :], n_device,
                                           n_exp_per_dev, prev)

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
