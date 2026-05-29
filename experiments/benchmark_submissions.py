"""Benchmark participant submissions against the DS-EPLB / Default baselines.

Replays the official forward-loop semantics from dynamic_lb_simulator.py
(one-layer-per-iteration redeploy drain, algorithm-time penalty, PAR averaged
over all iterations/layers, transfer cost = changed expert slots) and reports
mean PAR, transfer volume, modeled time, and score (100 * baseline / total,
baseline = DS-EPLB) for each method on the committed LmSys traces.
"""
import importlib.util
import os
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from dynamic_lb_simulator import (  # noqa: E402
    EXPERT_BYTES, TRANSFER_BANDWIDTH_BYTES_PER_SECOND,
    transmission_time_seconds, modeled_runtime_seconds,
)
from eplb_algorithms import rebalance as ds_rebalance  # noqa: E402

MODELS = {"DS-R1": (58, 256), "Qwen3": (94, 128)}
INTERVAL = 1024


def load_trace(model):
    a = np.load(REPO / "trace" / model / "LmSys.npy").copy()
    a[a == 0] = 1
    return a


def load_submission(name):
    spec = importlib.util.spec_from_file_location(
        f"sub_{name}", REPO / "submissions" / name / "submission.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def init_deploy(n_device, n_experts, n_red, default):
    n_exp_per_dev = (n_experts + n_red) // n_device
    d = np.zeros((n_device, n_exp_per_dev), dtype=np.int64)
    for dev in range(n_device):
        if default:
            for s in range(n_exp_per_dev):
                d[dev, s] = (dev * n_exp_per_dev + s) % n_experts
        else:
            for s in range(n_exp_per_dev - 1):
                d[dev, s] = (dev * (n_exp_per_dev - 1) + s) % n_experts
            d[dev, -1] = d[dev, -2]
    return d


def layer_counts(layer_deploy, n_experts):
    return np.bincount(layer_deploy.reshape(-1), minlength=n_experts)


def par_per_iter(h, deploy, counts):
    """Vectorized per-layer PAR. h:(L,E) deploy:(L,D,S) counts:(L,E) -> (L,)."""
    weights = h / counts
    slots = deploy.reshape(deploy.shape[0], -1)
    w_slot = np.take_along_axis(weights, slots, axis=1).reshape(deploy.shape)
    loads = w_slot.sum(-1)
    return loads.max(1) / loads.mean(1)


def run(hotness, n_layers, n_experts, ep, n_red, make_fn, is_default):
    n_iter = len(hotness)
    cur = np.stack([init_deploy(ep, n_experts, 0 if is_default else n_red, is_default)
                    for _ in range(n_layers)])
    counts = np.stack([layer_counts(cur[l], n_experts) for l in range(n_layers)])
    nxt = np.zeros_like(cur)

    par_sum, cnt, transmit = 0.0, 0, 0
    redeploy_finish, algo_finish, ready, prio = 0, 0, True, []

    for i in range(1, n_iter + 1):
        pars = par_per_iter(hotness[i - 1], cur, counts)
        par_sum += float(pars.sum()); cnt += pars.size
        if is_default:
            continue

        if not ready and i > algo_finish and prio:
            l = prio.pop(0)
            transmit += int((cur[l] != nxt[l]).sum())
            cur[l] = nxt[l]; counts[l] = layer_counts(cur[l], n_experts)
        if not prio and not ready:
            ready = True; redeploy_finish = i

        if i == redeploy_finish + INTERVAL + 1 and ready:
            st = time.time()
            change, lp, deploy, _ = make_fn(hotness[i - INTERVAL:i])
            if change:
                lp = np.asarray(lp, dtype=np.int64)
                if lp.size:
                    prio = lp.tolist(); ready = False
                    nxt[lp] = deploy[lp]
            algo_iters = max(1, int(np.ceil((time.time() - st) / 0.08)))
            algo_finish = i + algo_iters - 1

    return par_sum / cnt, transmit


def main():
    for model, (n_layers, n_experts) in MODELS.items():
        ep = 32
        n_red = ep
        hotness = load_trace(model)
        print(f"\n=== {model} / LmSys / EP{ep}  (interval={INTERVAL}) ===")

        ds_fn = ds_rebalance(ep, ep, "deepseek")
        fb = load_submission("finance_band")
        hot = load_submission("hot_expert_baseline")
        smoke = load_submission("smoke")
        fb.reset()

        methods = [
            ("Default",       lambda w: None, True),
            ("DS-EPLB",       lambda w: ds_fn(w), False),
            ("smoke(static)", lambda w: smoke.rebalance(w, ep, n_red), False),
            ("hot_expert",    lambda w: hot.rebalance(w, ep, n_red), False),
            ("finance_band",  lambda w: fb.rebalance(w, ep, n_red), False),
        ]

        results = {}
        for name, fn, is_def in methods:
            t0 = time.time()
            mean_par, transmit = run(hotness, n_layers, n_experts, ep, n_red, fn, is_def)
            results[name] = (mean_par, transmit)
            print(f"  [{time.time()-t0:5.1f}s] {name:14s} ran", flush=True)

        base_par, base_tx = results["DS-EPLB"]
        base_time = modeled_runtime_seconds(base_par, base_tx)

        print(f"  {'method':14s} {'meanPAR':>9s} {'transmit':>10s} "
              f"{'tx_time_s':>10s} {'total_s':>10s} {'score':>8s}")
        for name, (mp, tx) in results.items():
            total = modeled_runtime_seconds(mp, tx)
            print(f"  {name:14s} {mp:9.4f} {tx:10d} "
                  f"{transmission_time_seconds(tx):10.3f} {total:10.3f} "
                  f"{100*base_time/total:8.2f}")


if __name__ == "__main__":
    main()
