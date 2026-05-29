#!/usr/bin/env python3
"""Precompute + cache DS-EPLB baselines for the high-EP proxy cases.

The proxy eval (auto/eval_proxy.py) scores variants across EP {32,64,128,256}
on the committed LmSys traces, full length. DS-EPLB baselines for the new EPs
aren't in _baseline_cache.json yet; computing them needs torch (run_ds_eplb)
and is slow at high EP, so do it once here and let the cache serve every later
variant eval instantly.

Run with the torch-enabled venv:
    .venv/bin/python auto/precompute_baselines.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "auto"))

import eval as ev  # noqa: E402  (auto/eval.py)
from quickstart import MODEL_SHAPES, load_trace  # noqa: E402

CI = 1024
# (model, dataset, ep) — full length is taken from the trace itself.
PROXY_CASES = [
    ("Qwen3", "LmSys", 32),
    ("Qwen3", "LmSys", 64),
    ("Qwen3", "LmSys", 128),
    ("DS-R1", "LmSys", 32),
    ("DS-R1", "LmSys", 64),
    ("DS-R1", "LmSys", 128),
    ("DS-R1", "LmSys", 256),
]


def main() -> None:
    for (model, dataset, ep) in PROXY_CASES:
        full = load_trace(REPO_ROOT, model, dataset, 10_000)  # caps at trace length
        max_iters = len(full)
        key = f"{model}|{dataset}|{ep}|{max_iters}|{CI}"
        t0 = time.time()
        entry = ev.get_baseline(model, dataset, ep, max_iters, CI)
        print(
            f"{key:<28} ds_par={entry['ds_par']:.4f} "
            f"ds_transmit={entry['ds_transmit']:>8} "
            f"ds_time={entry['ds_modeled_time']:.2f}s  "
            f"({time.time()-t0:.1f}s)",
            flush=True,
        )


if __name__ == "__main__":
    main()
