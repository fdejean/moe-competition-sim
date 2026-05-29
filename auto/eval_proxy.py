#!/usr/bin/env python3
"""Full-length, multi-EP *proxy* evaluation — the predictive ranking tool.

Why this exists: the canonical `auto/eval.py` defaults to max_iters=1200, so
only ~one rebalance happens and ~85% of iters sit on the shared fixed-init
deployment -> every variant ties near 100 and the local leaderboard is useless
for ranking (see Learnings_from_claude.md). This harness instead runs each case
at the trace's FULL length (5120 Qwen3 / 6144 DS-R1) across EP {32,64,128,256},
which is where PAR diverges and where placement/replication quality actually
shows up. Server cases also include EP64/128/256, so this tracks the real
objective far better than the 1200-iter EP32 proxy.

It reuses auto/eval.py's simulator internals (run_submission, get_baseline) so
the scoring is identical to the canonical harness — only the case set and
iteration length differ. It writes `score_proxy.json` into the run dir, leaving
the canonical `score.json` (1200-iter, leaderboard-compatible) untouched.

Run with the torch-enabled venv (baselines need run_ds_eplb):
    .venv/bin/python auto/eval_proxy.py auto/runs/060_xxx
    .venv/bin/python auto/eval_proxy.py codes/finance_band            # any folder
    .venv/bin/python auto/eval_proxy.py codes/finance_band --quick    # EP32 only
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "auto"))

import eval as ev  # noqa: E402  (auto/eval.py)
from quickstart import (  # noqa: E402
    MODEL_SHAPES,
    load_trace,
    modeled_runtime_seconds,
    transmission_time_seconds,
)

CI = 1024

# The proxy case set. EP varies on the one committed dataset (LmSys); varying EP
# is what exposes the PAR-divergence regime the server's EP64/128/256 cases hit.
FULL_CASES = [
    ("Qwen3", "LmSys", 32),
    ("Qwen3", "LmSys", 64),
    ("Qwen3", "LmSys", 128),
    ("DS-R1", "LmSys", 32),
    ("DS-R1", "LmSys", 64),
    ("DS-R1", "LmSys", 128),
    ("DS-R1", "LmSys", 256),
]
QUICK_CASES = [("Qwen3", "LmSys", 32), ("DS-R1", "LmSys", 32)]


def evaluate_proxy(run_dir: Path, cases, per_call_budget: float) -> dict:
    log = StringIO()
    started = time.time()
    result: dict = {"run": run_dir.name, "cases": [], "mean_score": None,
                    "error": None, "kind": "proxy_full_multi_ep"}
    try:
        with redirect_stdout(log), redirect_stderr(log):
            module = ev.load_submission(run_dir)
            if not hasattr(module, "rebalance"):
                raise AttributeError("submission.py must define rebalance()")
            # Fresh per-variant placement memory if the module keeps state.
            if hasattr(module, "reset"):
                module.reset()

            scores: list[float] = []
            for (model, dataset, ep) in cases:
                n_layers, n_experts = MODEL_SHAPES[model]
                hotness = load_trace(REPO_ROOT, model, dataset, 10_000)
                max_iters = len(hotness)
                if hasattr(module, "reset"):
                    module.reset()
                baseline = ev.get_baseline(model, dataset, ep, max_iters, CI)
                mean_par, transmit, runtime, err = ev.run_submission(
                    module.rebalance, hotness, ep, n_layers, n_experts,
                    CI, per_call_budget,
                )
                modeled = modeled_runtime_seconds(mean_par, transmit)
                case_score = (100.0 * baseline["ds_modeled_time"] / modeled
                              if not err else -1_000_000.0)
                result["cases"].append({
                    "model": model, "dataset": dataset, "ep": ep,
                    "mean_par": mean_par, "transmit_amount": transmit,
                    "transmission_time_seconds": transmission_time_seconds(transmit),
                    "total_time_seconds": modeled, "runtime_seconds": runtime,
                    "score": case_score, "error": err,
                })
                if err:
                    result["error"] = err
                    break
                scores.append(case_score)
            if scores:
                result["mean_score"] = float(np.mean(scores))
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
        result["mean_score"] = -1_000_000.0

    result["elapsed_seconds"] = time.time() - started
    (run_dir / "score_proxy.json").write_text(json.dumps(result, indent=2))
    (run_dir / "stdout_proxy.txt").write_text(log.getvalue())
    return result


def main() -> None:
    p = argparse.ArgumentParser(description="Full-length multi-EP proxy eval.")
    p.add_argument("run_dir", type=Path)
    p.add_argument("--quick", action="store_true", help="EP32 only (2 cases).")
    p.add_argument("--per-call-budget", type=float, default=9.0)
    args = p.parse_args()
    cases = QUICK_CASES if args.quick else FULL_CASES
    r = evaluate_proxy(args.run_dir.resolve(), cases, args.per_call_budget)
    print(json.dumps({
        "run": r["run"], "mean_score": r["mean_score"], "error": r["error"],
        "elapsed_seconds": round(r["elapsed_seconds"], 1),
        "cases": [{"case": f"{c['model']}/{c['dataset']}/EP{c['ep']}",
                   "score": round(c["score"], 2), "par": round(c["mean_par"], 4),
                   "transmit": c["transmit_amount"], "error": c["error"]}
                  for c in r["cases"]],
    }, indent=2))


if __name__ == "__main__":
    main()
