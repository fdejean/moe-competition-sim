"""Local validation runner for the three prediction variants.

Reuses benchmark_submissions.run/load_* machinery to score:
  finance_band (reference) vs pred_baseline / pred_seasonal / pred_blend
on the committed LmSys traces, plus DS-EPLB for the score denominator.

Also reports, for the seasonal/blend variants, the detected per-call lag and
whether they engaged the season or fell back to baseline.
"""
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "experiments"))

from benchmark_submissions import (  # noqa: E402
    MODELS, INTERVAL, load_trace, load_submission, run,
)
from dynamic_lb_simulator import (  # noqa: E402
    transmission_time_seconds, modeled_runtime_seconds,
)
from eplb_algorithms import rebalance as ds_rebalance  # noqa: E402


def main():
    for model, (n_layers, n_experts) in MODELS.items():
        ep = 32
        n_red = ep
        hotness = load_trace(model)
        print(f"\n=== {model} / LmSys / EP{ep}  (interval={INTERVAL}) ===")

        ds_fn = ds_rebalance(ep, ep, "deepseek")
        subs = {n: load_submission(n) for n in
                ("finance_band", "pred_baseline", "pred_seasonal", "pred_blend")}
        for m in subs.values():
            m.reset()

        methods = [("DS-EPLB", lambda w: ds_fn(w), False)]
        for name, mod in subs.items():
            methods.append((name, (lambda mm: (lambda w: mm.rebalance(w, ep, n_red)))(mod), False))

        results = {}
        for name, fn, is_def in methods:
            t0 = time.time()
            mean_par, transmit = run(hotness, n_layers, n_experts, ep, n_red, fn, is_def)
            results[name] = (mean_par, transmit)
            print(f"  [{time.time()-t0:5.1f}s] {name:14s} ran", flush=True)

        # Report what the seasonal/blend variants ended up detecting.
        for name in ("pred_seasonal", "pred_blend"):
            mod = subs[name]
            hist = mod._STATE.get("hist", [])
            lag, *_ = (mod._detect_lag(hist) if hasattr(mod, "_detect_lag") else (None,))
            print(f"  {name}: accumulated {len(hist)} calls, "
                  f"final detected lag={lag} (None => fell back to baseline)")

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
