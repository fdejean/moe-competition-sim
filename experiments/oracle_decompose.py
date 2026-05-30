"""Decompose the realized-vs-oracle PAR gap into FORECAST error vs VARIANCE floor.

oracle_gap.py showed realized PAR >> per-iteration oracle PAR, gap growing with
EP, and concluded "the lever is forecasting, not the packer". True -- but the
per-iteration oracle re-places EVERY iteration on the live load, which we can
never do (we place once per window and hold). So that gap mixes two things:

  realized - perI_oracle  =  (realized - winMean_oracle)   <- FORECAST error
                          +  (winMean_oracle - perI_oracle) <- VARIANCE floor

  * winMean_oracle : best SINGLE placement on the *true mean of the upcoming
    window* (perfect mean forecast, still one held placement). Same packer.
    realized - winMean_oracle is the part a better one-step estimator can recover.
  * perI_oracle    : re-place every iteration on live load (the 1.0x floor).
    winMean_oracle - perI_oracle is irreducible intra-window co-spike: only
    replication or sub-window re-placement can touch it, NOT a better mean.

We also add winMean_oracle fed with mean + LAMBDA*std (the risk estimate the sub
actually balances) to show how much of the variance floor replication already buys.

This tells the builder loop WHERE the headroom is: if realized ~ winMean_oracle,
forecasting is tapped and the residual is variance (need replication / sub-window
redeploy); if realized >> winMean_oracle, a better estimator is the lever.

Run:  /home/wli/miniconda3/bin/python experiments/oracle_decompose.py [metrics.json]
Out:  experiments/results/oracle/oracle_decompose.png  (+ .csv)
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
DEFAULT_METRICS = REPO / "results" / "20260530-095837" / "prediction_result" / "metrics.json"

MODELS = {"DS-R1": (58, 256), "Qwen3": (94, 128)}
EP_GRID = {"DS-R1": [32, 64, 128, 256], "Qwen3": [32, 64, 128]}
INTERVAL = 1024          # the grader's collection_interval (mirrors live)
LAMBDA = 2.0             # risk level the submission balances (mean + LAMBDA*std)
N_WINDOWS = 5            # evaluate this many tiled windows across the trace


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


def mean_par_over_window(fb, win, deploy_per_layer):
    """Mean over (iters, layers) PAR for a FIXED placement held across `win`.

    win: (T, L, E); deploy_per_layer: list of (D,S) deploy tables, one per layer.
    """
    T, L, E = win.shape
    tot, cnt = 0.0, 0
    for l in range(L):
        dep = deploy_per_layer[l]
        cut = np.bincount(dep.reshape(-1), minlength=E)
        for t in range(T):
            w = win[t, l] / cut
            loads = w[dep.reshape(-1)].reshape(dep.shape).sum(-1)
            tot += float(loads.max() / loads.mean())
            cnt += 1
    return tot, cnt


def eval_case(fb, hotness, n_layers, n_experts, ep):
    """Return dict of mean PARs over N_WINDOWS upcoming windows, for 4 placements:
       trailing (realized-style), winMean oracle, winMean+risk oracle, perIter oracle.
    """
    n_red = ep
    n_exp_per_dev = (n_experts + n_red) // ep
    n_iter = hotness.shape[0]
    # window starts: predict window [s, s+INTERVAL) from trailing [s-INTERVAL, s).
    starts = np.linspace(INTERVAL, n_iter - INTERVAL, N_WINDOWS).astype(int)

    acc = {k: [0.0, 0] for k in ("trailing", "winmean", "winrisk", "periter")}
    for s in starts:
        past = hotness[s - INTERVAL:s]                 # what trailing sees
        fut = hotness[s:s + INTERVAL]                  # what we are graded on
        past_mean = past.mean(0)
        past_risk = past_mean + LAMBDA * past.std(0)
        fut_mean = fut.mean(0)
        # placements (same packer)
        dep_tr = [fb._build_layer(past_risk[l], ep, n_exp_per_dev) for l in range(n_layers)]
        dep_wm = [fb._build_layer(fut_mean[l], ep, n_exp_per_dev) for l in range(n_layers)]
        fut_risk = fut_mean + LAMBDA * fut.std(0)
        dep_wr = [fb._build_layer(fut_risk[l], ep, n_exp_per_dev) for l in range(n_layers)]

        for key, dep in (("trailing", dep_tr), ("winmean", dep_wm), ("winrisk", dep_wr)):
            t, c = mean_par_over_window(fb, fut, dep)
            acc[key][0] += t; acc[key][1] += c

        # per-iteration oracle on this same future window
        tot, cnt = 0.0, 0
        for t in range(fut.shape[0]):
            for l in range(n_layers):
                d = fb._build_layer(fut[t, l], ep, n_exp_per_dev)
                tot += fb._par(fut[t, l], d); cnt += 1
        acc["periter"][0] += tot; acc["periter"][1] += cnt

    return {k: acc[k][0] / acc[k][1] for k in acc}


def main():
    metrics_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_METRICS
    realized = {}
    if metrics_path.is_file():
        for c in json.loads(metrics_path.read_text())["cases"]:
            realized[(c["model"], c["dataset"], c["ep"])] = c["mean_par"]

    fb = load_fb()
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for model, (nl, ne) in MODELS.items():
        hot = load_trace(model)
        for ep in EP_GRID[model]:
            t0 = time.time()
            d = eval_case(fb, hot, nl, ne, ep)
            rlz = realized.get((model, "LmSys", ep), float("nan"))
            forecast_gap = d["trailing"] - d["winmean"]   # recoverable by estimator
            var_floor = d["winmean"] - d["periter"]        # irreducible (need replication)
            rows.append((model, ep, rlz, d["trailing"], d["winrisk"],
                         d["winmean"], d["periter"], forecast_gap, var_floor))
            print(f"[{time.time()-t0:5.1f}s] {model:6s} EP{ep:<3d} "
                  f"realized={rlz:.3f} trailing={d['trailing']:.3f} "
                  f"winRisk={d['winrisk']:.3f} winMean={d['winmean']:.3f} "
                  f"perIter={d['periter']:.3f} | forecastGap={forecast_gap:+.3f} "
                  f"varFloor={var_floor:+.3f}", flush=True)

    with open(OUT / "oracle_decompose.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model", "ep", "realized", "trailing_local", "winrisk",
                    "winmean", "periter", "forecast_gap", "var_floor"])
        w.writerows(rows)

    # ---- plot: stacked decomposition of the gap above the perIter floor ----
    fig, axes = plt.subplots(1, 2, figsize=(15, 6), sharey=False)
    for ax, model in zip(axes, MODELS):
        sub = [r for r in rows if r[0] == model]
        eps = [r[1] for r in sub]
        x = np.arange(len(eps))
        periter = np.array([r[6] for r in sub])
        winmean = np.array([r[5] for r in sub])
        trailing = np.array([r[3] for r in sub])
        winrisk = np.array([r[4] for r in sub])
        realized_v = np.array([r[2] for r in sub])

        # baseline floor bar
        ax.bar(x, periter - 1.0, bottom=1.0, color="#2a9d8f", alpha=0.85,
               label="perIter oracle (unreachable floor)")
        ax.bar(x, winmean - periter, bottom=periter, color="#e9c46a", alpha=0.9,
               label="variance floor (need replication / sub-window)")
        ax.bar(x, trailing - winmean, bottom=winmean, color="#e76f51", alpha=0.9,
               label="FORECAST error (better estimator recovers this)")
        ax.plot(x, winrisk, "D", color="#6a4c93", ms=8,
                label="winMean+λσ oracle (risk-aware)")
        ax.plot(x, realized_v, "*", color="black", ms=15,
                label="realized (live grader)")
        ax.axhline(1.0, color="gray", ls=":", lw=1)
        ax.set_xticks(x); ax.set_xticklabels([f"EP{e}" for e in eps])
        ax.set_title(f"{model} / LmSys\nwhere the PAR gap lives")
        ax.set_ylabel("mean PAR")
        ax.grid(alpha=0.25, axis="y")
        ax.legend(fontsize=7.5, loc="upper left")

    fig.suptitle(
        "Decomposing the load-balance gap: forecast error (recoverable by a "
        "one-step estimator) vs variance floor (replication-only)\n"
        "FORECAST band = trailing window vs perfect mean forecast; "
        "VARIANCE band = perfect mean forecast vs per-iteration re-placement",
        fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    png = OUT / "oracle_decompose.png"
    fig.savefig(png, dpi=130)
    print(f"\nwrote {png}\nwrote {OUT / 'oracle_decompose.csv'}")


if __name__ == "__main__":
    main()
