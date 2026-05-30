"""Visualize what's failing: our load balancer vs a perfect-foresight oracle.

The oracle "knows it all": for every iteration it places experts on that
iteration's *true* per-layer load, using the **same packer** as our submission
(`finance_band._build_layer`). The only thing it has that we don't is the
future. So:

    realized PAR  - oracle PAR  =  the cost of forecasting (placing on the
                                    trailing window instead of the live load)
                                    + redeploy lag.

That gap, growing with expert-parallel size (EP), is exactly "what's failing".

Realized PAR is taken from the real grader output (prediction_result/metrics.json)
so it is authoritative. The oracle is computed locally on the committed LmSys
traces (the only ones we have), sub-sampling iterations for speed (PAR is an
average, so a uniform sub-sample is an unbiased estimate of the mean).

Run:  /home/wli/miniconda3/bin/python experiments/oracle_gap.py [metrics.json]
Out:  experiments/results/oracle/oracle_gap.png  (+ oracle_gap.csv)
"""
import csv
import importlib.util
import json
import sys
import time
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "experiments" / "results" / "oracle"
DEFAULT_METRICS = REPO / "results" / "20260529-233910" / "prediction_result" / "metrics.json"

# (model -> (n_layers, n_experts)); EP grid graded per model.
MODELS = {"DS-R1": (58, 256), "Qwen3": (94, 128)}
EP_GRID = {"DS-R1": [32, 64, 128, 256], "Qwen3": [32, 64, 128]}
TARGET_SAMPLES = 192  # sub-sampled iterations per case for the oracle


def load_fb():
    spec = importlib.util.spec_from_file_location(
        "fb", REPO / "submissions" / "finance_band" / "submission.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_trace(model):
    a = np.load(REPO / "trace" / model / "LmSys.npy").astype(np.float64)
    a[a == 0] = 1.0
    return a


def oracle_par(fb, hotness, n_layers, n_experts, ep):
    """Mean PAR with perfect per-iteration foresight, same packer as the sub."""
    n_red = ep                      # competition: n_red_expert == n_device == EP
    n_exp_per_dev = (n_experts + n_red) // ep
    n_iter = hotness.shape[0]
    stride = max(1, n_iter // TARGET_SAMPLES)
    idxs = range(0, n_iter, stride)
    total, count = 0.0, 0
    for i in idxs:
        h_i = hotness[i]
        for l in range(n_layers):
            deploy = fb._build_layer(h_i[l], ep, n_exp_per_dev)
            total += fb._par(h_i[l], deploy)
            count += 1
    return total / count, len(list(idxs))


def realized_lookup(metrics):
    """{(model, dataset, ep): mean_par} from grader metrics.json."""
    out = {}
    for c in metrics["cases"]:
        out[(c["model"], c["dataset"], c["ep"])] = c
    return out


def main():
    metrics_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_METRICS
    metrics = json.loads(metrics_path.read_text())
    realized = realized_lookup(metrics)
    fb = load_fb()
    OUT.mkdir(parents=True, exist_ok=True)

    # ---- compute oracle for the LmSys cases ----------------------------
    rows = []  # (model, ep, oracle_par, realized_par, gap, n_samples)
    for model, (n_layers, n_experts) in MODELS.items():
        hotness = load_trace(model)
        for ep in EP_GRID[model]:
            t0 = time.time()
            opar, nsamp = oracle_par(fb, hotness, n_layers, n_experts, ep)
            rpar = realized[(model, "LmSys", ep)]["mean_par"]
            rows.append((model, ep, opar, rpar, rpar - opar, nsamp))
            print(f"[{time.time()-t0:5.1f}s] {model:6s} EP{ep:<3d} "
                  f"oracle={opar:.4f} realized={rpar:.4f} gap={rpar-opar:+.4f}",
                  flush=True)

    with open(OUT / "oracle_gap.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model", "ep", "oracle_par", "realized_par", "gap", "n_samples"])
        w.writerows(rows)

    # ---- plot ----------------------------------------------------------
    fig = plt.figure(figsize=(15, 5))
    gs = fig.add_gridspec(1, 3, width_ratios=[1, 1, 1.15])
    axes = {"DS-R1": fig.add_subplot(gs[0, 0]),
            "Qwen3": fig.add_subplot(gs[0, 1])}
    ax_all = fig.add_subplot(gs[0, 2])

    for model, ax in axes.items():
        sub = [r for r in rows if r[0] == model]
        eps = [r[1] for r in sub]
        orc = [r[2] for r in sub]
        rea = [r[3] for r in sub]
        x = np.arange(len(eps))
        ax.plot(x, orc, "o-", color="#2a9d8f", lw=2, label="oracle (knows the future)")
        ax.plot(x, rea, "s-", color="#e63946", lw=2, label="our LB (realized)")
        ax.fill_between(x, orc, rea, color="#e63946", alpha=0.12)
        for xi, o, r in zip(x, orc, rea):
            ax.annotate(f"+{r-o:.2f}", (xi, (o + r) / 2), ha="center", va="center",
                        fontsize=8, color="#9b2226")
        ax.axhline(1.0, color="gray", ls=":", lw=1)
        ax.set_xticks(x); ax.set_xticklabels([f"EP{e}" for e in eps])
        ax.set_title(f"{model} / LmSys\nforecasting gap grows with EP")
        ax.set_ylabel("mean PAR (lower = better)")
        ax.set_ylim(0.95, max(rea) * 1.08)
        ax.grid(alpha=0.25)
        ax.legend(fontsize=8, loc="upper left")

    # Panel C: realized PAR across ALL 24 graded cases (grader metrics.json)
    colors = {"Mix": "#d62728", "ShareGPT": "#1f77b4",
              "WildChat": "#2ca02c", "LmSys": "#ff7f0e"}
    for model in MODELS:
        for dataset in sorted({c["dataset"] for c in metrics["cases"]
                               if c["model"] == model}):
            pts = sorted((c["ep"], c["mean_par"]) for c in metrics["cases"]
                         if c["model"] == model and c["dataset"] == dataset)
            xs = [np.log2(e) for e, _ in pts]
            ys = [p for _, p in pts]
            ls = "-" if model == "DS-R1" else "--"
            ax_all.plot(xs, ys, ls, marker="o", color=colors.get(dataset, "gray"),
                        label=f"{model} / {dataset}", alpha=0.85)
    ax_all.set_title("Realized PAR over all 24 graded cases\n(DS-R1 Mix @ high EP is the worst)")
    ax_all.set_xlabel("EP (log2)")
    ax_all.set_ylabel("mean PAR")
    ax_all.set_xticks([5, 6, 7, 8]); ax_all.set_xticklabels([32, 64, 128, 256])
    ax_all.grid(alpha=0.25)
    ax_all.legend(fontsize=7, ncol=2)

    fig.suptitle(
        "What's failing: our MoE load balancer vs a perfect-foresight oracle "
        "(same packer, fed the true per-iteration load)", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    png = OUT / "oracle_gap.png"
    fig.savefig(png, dpi=130)
    print(f"\nwrote {png}")
    print(f"wrote {OUT / 'oracle_gap.csv'}")


if __name__ == "__main__":
    main()
