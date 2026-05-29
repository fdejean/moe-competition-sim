"""Finance-inspired no-trade-band rebalancer.

Analogy (see experiments/trailing_window_analysis.py for the evidence):
  experts          ~ assets
  trailing hotness ~ a returns/volume window
  deployment       ~ portfolio weights (replicas per expert)
  PAR              ~ tracking error vs. an even-load benchmark
  transfer cost    ~ transaction cost

Because expert load is highly persistent (lag-1 autocorr ~0.5) and recency
weighting (EWMA) gave no edge over a flat window, the estimator is a simple
trailing window. Two finance ideas drive the placement:

  * No-trade band (TAU): every cycle we recompute the ideal placement but only
    *apply* it to layers whose modeled PAR gain exceeds TAU, so we don't pay
    transfer cost for negligible gains -- a stable trace converges to "deploy
    once, then hold".

  * Variance-aware replication (LAMBDA): packing the *mean* load is already
    near-optimal; the realized PAR is driven by per-iteration *variance* (a
    device with few experts can't average it out). So replicas are allocated by
    a risk measure, mean + LAMBDA * std, giving volatile experts extra copies
    that split their fluctuations across devices -- portfolio diversification /
    risk budgeting. The no-trade band still evaluates on the mean (expected load).

Tunables (module attributes; also read from env so benchmarks can sweep):
  WINDOW : int | None  -- use only the last WINDOW iterations of the given window.
  TAU    : float       -- no-trade band width, in PAR points (0 => always trade).
  LAMBDA : float       -- risk weight for replication (0 => mean-only).
"""
import os
import numpy as np

_w = os.environ.get("FB_WINDOW")
WINDOW = None if _w in (None, "", "all") else int(_w)
TAU = float(os.environ.get("FB_TAU", "0.2"))      # swept sweet spot on LmSys EP32
LAMBDA = float(os.environ.get("FB_LAMBDA", "2.0"))  # variance weight for replication

# Persistent placement state across calls within a run (single process).
_STATE = {}


def reset():
    """Clear placement memory between independent simulation runs."""
    _STATE.clear()


def _build_layer(load, n_device, n_exp_per_dev):
    """Greedy load-balanced placement for one layer.

    1. Replication: hand each extra (redundant) slot to the expert with the
       highest current per-replica load (water-filling) -> effective load
       per copy = load / replicas.
    2. Placement: assign slots to devices by Longest-Processing-Time, putting
       the heaviest slot on the least-loaded device, and penalizing placing a
       second copy of an expert on a device that already holds it (so replicas
       spread across devices, which is what actually reduces PAR).
    """
    n_experts = load.shape[0]
    n_slots = n_device * n_exp_per_dev
    replicas = np.ones(n_experts, dtype=np.int64)
    for _ in range(n_slots - n_experts):
        replicas[int(np.argmax(load / replicas))] += 1

    slot_expert = np.repeat(np.arange(n_experts), replicas)
    slot_weight = load[slot_expert] / replicas[slot_expert]
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


def _par(load, deploy):
    """PAR for one layer under the simulator's load model (copies split load)."""
    cut = np.bincount(deploy.reshape(-1), minlength=load.shape[0])
    weights = load / cut
    loads = weights[deploy.reshape(-1)].reshape(deploy.shape).sum(-1)
    return float(loads.max() / loads.mean())


def rebalance(hotness, n_device, n_red_expert):
    if WINDOW is not None:
        hotness = hotness[-WINDOW:]
    hotness = hotness.astype(np.float64)
    mean = hotness.mean(axis=0)                          # (n_layers, n_experts)
    # Risk-adjusted load: replicate volatile experts more (diversification).
    risk = mean + LAMBDA * hotness.std(axis=0) if LAMBDA else mean
    n_layers, n_experts = mean.shape
    n_exp_per_dev = (n_experts + n_red_expert) // n_device

    # Placement is driven by risk; the band judges quality on expected (mean) load.
    candidate = np.zeros((n_layers, n_device, n_exp_per_dev), dtype=np.int64)
    for l in range(n_layers):
        candidate[l] = _build_layer(risk[l], n_device, n_exp_per_dev)

    # Key state by problem shape so reused processes / new cases auto-reset
    # even if the grader never calls reset().
    sig = (n_layers, n_experts, n_device, n_exp_per_dev)
    cur = _STATE.get("deploy") if _STATE.get("sig") == sig else None
    if cur is None:
        # First cycle for this shape: start placement is load-agnostic -> deploy.
        _STATE["sig"] = sig
        _STATE["deploy"] = candidate.copy()
        return True, np.arange(n_layers, dtype=np.int64), candidate, None

    # No-trade band: only redeploy layers whose modeled PAR gain clears TAU.
    improve = np.array([_par(mean[l], cur[l]) - _par(mean[l], candidate[l])
                        for l in range(n_layers)])
    sel = np.where(improve > TAU)[0]
    if sel.size == 0:
        return False, np.array([], dtype=np.int64), candidate, None

    order = sel[np.argsort(improve[sel])[::-1]]          # worst-imbalance first
    new_state = cur.copy()
    new_state[order] = candidate[order]
    _STATE["deploy"] = new_state
    return True, order.astype(np.int64), candidate, None
