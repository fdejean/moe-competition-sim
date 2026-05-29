#!/usr/bin/env python3
"""Evaluate a single submission folder and write score.json next to it.

Usage:
    python experiments/auto/eval.py experiments/auto/runs/000_seed_smoke
    python experiments/auto/eval.py experiments/auto/runs/000_seed_smoke --model DS-R1
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from quickstart import (  # noqa: E402
    MODEL_SHAPES,
    cal_par_per_iter,
    compute_redeploy_cost,
    init_deploy_table,
    load_trace,
    modeled_runtime_seconds,
    run_default,
    run_ds_eplb,
    transmission_time_seconds,
)

BASELINE_CACHE = REPO_ROOT / "experiments" / "auto" / "_baseline_cache.json"


def load_submission(run_dir: Path):
    """Import submission.py from run_dir as an isolated module."""
    sub_path = run_dir / "submission.py"
    if not sub_path.exists():
        raise FileNotFoundError(f"missing submission.py in {run_dir}")

    sys.path.insert(0, str(run_dir))
    try:
        spec = importlib.util.spec_from_file_location(
            f"submission_{run_dir.name}", sub_path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"could not load {sub_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(run_dir))


def run_submission(
    rebalance_fn,
    hotness: np.ndarray,
    ep: int,
    n_layers: int,
    n_experts: int,
    collection_interval: int,
    per_call_time_budget: float,
) -> tuple[float, int, float, str | None]:
    """Same loop as run_ds_eplb, but uses the submission's rebalance()."""
    start = time.time()
    cur_deploy_table = np.array(
        [init_deploy_table(ep, n_experts, ep, default=False) for _ in range(n_layers)]
    )
    next_deploy_table = np.zeros_like(cur_deploy_table)

    redeploy_finish_iter = 0
    expert_ready = True
    cur_layers_priority: list[int] = []
    transmit_amount = 0
    pars_sum = 0.0
    count = 0

    for i in range(1, len(hotness) + 1):
        cur_hotness = hotness[i - 1]
        pars = cal_par_per_iter(cur_hotness, cur_deploy_table)
        pars_sum += float(pars.sum())
        count += pars.size

        if not expert_ready and cur_layers_priority:
            adjust_layer_idx = cur_layers_priority.pop(0)
            transmit_amount += compute_redeploy_cost(
                cur_deploy_table[adjust_layer_idx],
                next_deploy_table[adjust_layer_idx],
            )
            cur_deploy_table[adjust_layer_idx] = next_deploy_table[adjust_layer_idx]

        if len(cur_layers_priority) == 0 and not expert_ready:
            expert_ready = True
            redeploy_finish_iter = i

        if i == redeploy_finish_iter + collection_interval + 1 and expert_ready:
            train_window = hotness[i - collection_interval:i]
            call_start = time.time()
            change, layers_priority, deployment_table, _ = rebalance_fn(
                train_window, ep, ep
            )
            call_elapsed = time.time() - call_start
            if call_elapsed > per_call_time_budget:
                return (
                    pars_sum / count,
                    transmit_amount,
                    time.time() - start,
                    f"rebalance call exceeded per-call budget ({call_elapsed:.2f}s > {per_call_time_budget}s)",
                )
            if change:
                selected_layers = np.asarray(layers_priority, dtype=np.int64)
                if deployment_table.shape != cur_deploy_table.shape:
                    return (
                        pars_sum / count,
                        transmit_amount,
                        time.time() - start,
                        f"deployment_table shape {deployment_table.shape} != {cur_deploy_table.shape}",
                    )
                cur_layers_priority = selected_layers.tolist()
                expert_ready = False
                next_deploy_table[selected_layers] = deployment_table[selected_layers]

    return pars_sum / count, transmit_amount, time.time() - start, None


def get_baseline(model: str, dataset: str, ep: int, max_iters: int,
                 collection_interval: int) -> dict:
    key = f"{model}|{dataset}|{ep}|{max_iters}|{collection_interval}"
    cache: dict[str, dict] = {}
    if BASELINE_CACHE.exists():
        cache = json.loads(BASELINE_CACHE.read_text())
    if key in cache:
        return cache[key]

    n_layers, n_experts = MODEL_SHAPES[model]
    hotness = load_trace(REPO_ROOT, model, dataset, max_iters)
    default_par, _, _ = run_default(hotness, ep, n_layers, n_experts)
    ds_par, ds_transmit, _ = run_ds_eplb(
        hotness, ep, n_layers, n_experts, collection_interval
    )
    entry = {
        "default_par": default_par,
        "ds_par": ds_par,
        "ds_transmit": ds_transmit,
        "ds_modeled_time": modeled_runtime_seconds(ds_par, ds_transmit),
    }
    cache[key] = entry
    BASELINE_CACHE.write_text(json.dumps(cache, indent=2))
    return entry


def evaluate(run_dir: Path, cases: list[tuple[str, str, int]],
             max_iters: int, collection_interval: int,
             per_call_time_budget: float) -> dict:
    log = StringIO()
    started = time.time()
    result: dict = {
        "run": run_dir.name,
        "started_at": started,
        "cases": [],
        "mean_score": None,
        "error": None,
    }

    try:
        with redirect_stdout(log), redirect_stderr(log):
            module = load_submission(run_dir)
            if not hasattr(module, "rebalance"):
                raise AttributeError("submission.py must define a rebalance() function")

            scores: list[float] = []
            for (model, dataset, ep) in cases:
                n_layers, n_experts = MODEL_SHAPES[model]
                hotness = load_trace(REPO_ROOT, model, dataset, max_iters)
                if len(hotness) <= collection_interval:
                    raise ValueError(
                        f"max_iters ({max_iters}) must exceed collection_interval ({collection_interval})"
                    )

                baseline = get_baseline(
                    model, dataset, ep, max_iters, collection_interval
                )
                mean_par, transmit, runtime, err = run_submission(
                    module.rebalance, hotness, ep, n_layers, n_experts,
                    collection_interval, per_call_time_budget,
                )

                modeled = modeled_runtime_seconds(mean_par, transmit)
                case_score = 100.0 * baseline["ds_modeled_time"] / modeled if not err else -1_000_000.0
                case = {
                    "model": model,
                    "dataset": dataset,
                    "ep": ep,
                    "mean_par": mean_par,
                    "transmit_amount": transmit,
                    "transmission_time_seconds": transmission_time_seconds(transmit),
                    "total_time_seconds": modeled,
                    "runtime_seconds": runtime,
                    "score": case_score,
                    "error": err,
                }
                result["cases"].append(case)
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

    result["finished_at"] = time.time()
    result["elapsed_seconds"] = result["finished_at"] - started

    (run_dir / "score.json").write_text(json.dumps(result, indent=2))
    (run_dir / "stdout.txt").write_text(log.getvalue())
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate one submission folder.")
    parser.add_argument("run_dir", type=Path, help="path to runs/<NNN>_<name>/")
    parser.add_argument(
        "--cases", nargs="+",
        default=["Qwen3:LmSys:32", "DS-R1:LmSys:32"],
        help="case specs of the form MODEL:DATASET:EP (default: both committed traces)",
    )
    parser.add_argument("--max-iters", type=int, default=1200)
    parser.add_argument("--collection-interval", type=int, default=1024)
    parser.add_argument(
        "--per-call-budget", type=float, default=10.0,
        help="seconds; abort case if a single rebalance() call exceeds this",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cases = [tuple(c.split(":")) for c in args.cases]
    cases = [(m, d, int(ep)) for (m, d, ep) in cases]
    result = evaluate(
        args.run_dir.resolve(), cases,
        args.max_iters, args.collection_interval, args.per_call_budget,
    )
    print(json.dumps({
        "run": result["run"],
        "mean_score": result["mean_score"],
        "error": result["error"],
        "cases": [
            {"case": f"{c['model']}/{c['dataset']}/EP{c['ep']}",
             "score": c["score"], "par": c["mean_par"],
             "transmit": c["transmit_amount"], "error": c["error"]}
            for c in result["cases"]
        ],
    }, indent=2))


if __name__ == "__main__":
    main()
