"""Analyze the live Codabench per-case output of the three pred_* variants.

Loads the per-case prediction_result/metrics.json from the three submitted runs
(baseline / seasonal+heartbeat / blend+heartbeat), reconstructs the modeled time
of every (model, dataset, ep) case, and visualizes where the seasonal and blend
estimators actually moved the needle vs. the trailing-window baseline -- and
whether the move was a net win (lower time) or a net loss (transmit outran the
PAR gain). Renders a single multi-panel PNG.

Run: /home/wli/miniconda3/bin/python experiments/analyze_pred_runs.py
"""
import json
import os
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[1]

# Scoring model (dynamic_lb_simulator.py):
BALANCED_COMPUTE_SECONDS = 60.0
EXPERT_BYTES = 88_080_384
BW = 900_000_000_000


def tx_time(transmit):
    return transmit * EXPERT_BYTES / BW


def case_time(mean_par, transmit):
    return BALANCED_COMPUTE_SECONDS * mean_par + tx_time(transmit)


# The three live runs (results/<ts>/prediction_result/metrics.json).
RUNS = {
    "baseline":          "20260530-095837",   # pred_baseline (= finance_band control)
    "seasonal+hb":       "20260530-102058",   # pred_seasonal with heartbeat
    "blend+hb":          "20260530-102530",   # pred_blend with heartbeat
}
COMPOSITE = {"baseline": 111.252, "seasonal+hb": 111.259, "blend+hb": 111.233}


def load_cases(ts):
    p = REPO / "results" / ts / "prediction_result" / "metrics.json"
    cases = json.loads(p.read_text())["cases"]
    out = {}
    for c in cases:
        key = (c["model"], c["dataset"], c["ep"])
        out[key] = (c["mean_par"], c["transmit_amount"])
    return out


def main():
    data = {name: load_cases(ts) for name, ts in RUNS.items()}
    keys = list(data["baseline"].keys())
    labels = [f"{m}/{d}/{ep}" for (m, d, ep) in keys]

    base = data["baseline"]
    variants = ["seasonal+hb", "blend+hb"]

    # Per-case decomposition of the time delta vs baseline:
    #   dt = 60*dPAR  +  dTransmitTime   (negative dt = faster = better)
    rows = {}  # variant -> dict of arrays
    for v in variants:
        dpar_t, dtx_t, dt = [], [], []
        for k in keys:
            bp, btx = base[k]
            vp, vtx = data[v][k]
            dpar_t.append(BALANCED_COMPUTE_SECONDS * (vp - bp))
            dtx_t.append(tx_time(vtx) - tx_time(btx))
            dt.append(case_time(vp, vtx) - case_time(bp, btx))
        rows[v] = {
            "dpar_t": np.array(dpar_t),
            "dtx_t": np.array(dtx_t),
            "dt": np.array(dt),
        }

    # ---- figure -------------------------------------------------------
    n = len(keys)
    y = np.arange(n)
    fig = plt.figure(figsize=(15, 11))
    gs = fig.add_gridspec(2, 2, height_ratios=[3, 1.1], hspace=0.32, wspace=0.28)

    # Panel A & B: per-case time-delta decomposition, one per active variant.
    for col, v in enumerate(variants):
        ax = fig.add_subplot(gs[0, col])
        dpar = rows[v]["dpar_t"]
        dtx = rows[v]["dtx_t"]
        dt = rows[v]["dt"]
        ax.barh(y, dpar, color="tab:blue", alpha=0.85,
                label="PAR-time delta (60*dPAR)")
        # stack transmit delta starting from the PAR bar end
        ax.barh(y, dtx, left=dpar, color="tab:orange", alpha=0.85,
                label="transmit-time delta")
        # net dt markers
        ax.scatter(dt, y, color="black", zorder=5, s=22, label="net time delta")
        ax.axvline(0, color="k", lw=0.9)
        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=7)
        ax.invert_yaxis()
        ax.set_xlabel("seconds vs baseline   (<0 = faster = better)")
        engaged = int(np.sum(np.abs(dt) > 1e-9))
        wins = int(np.sum(dt < -1e-9))
        losses = int(np.sum(dt > 1e-9))
        ax.set_title(f"{v}: per-case time delta vs baseline\n"
                     f"engaged on {engaged}/{n} cases "
                     f"({wins} faster, {losses} slower); "
                     f"live composite {COMPOSITE[v]:.3f}")
        ax.legend(loc="lower right", fontsize=8)
        ax.grid(True, axis="x", alpha=0.3)

    # Panel C: composite scores bar (the bottom line).
    axc = fig.add_subplot(gs[1, 0])
    names = list(COMPOSITE.keys())
    vals = [COMPOSITE[k] for k in names]
    colors = ["tab:gray", "tab:green", "tab:red"]
    bars = axc.bar(names, vals, color=colors, alpha=0.85)
    axc.set_ylim(min(vals) - 0.03, max(vals) + 0.03)
    axc.axhline(COMPOSITE["baseline"], color="k", ls="--", lw=0.8)
    for b, val in zip(bars, vals):
        axc.text(b.get_x() + b.get_width() / 2, val,
                 f"{val:.3f}\n({val - COMPOSITE['baseline']:+.3f})",
                 ha="center", va="bottom", fontsize=9)
    axc.set_ylabel("live composite score")
    axc.set_title("Live leaderboard composite (dashed = baseline)")
    axc.grid(True, axis="y", alpha=0.3)

    # Panel D: net time-delta summary by variant (sum over cases) + win/loss split.
    axd = fig.add_subplot(gs[1, 1])
    width = 0.35
    xb = np.arange(len(variants))
    tot_dpar = [rows[v]["dpar_t"].sum() for v in variants]
    tot_dtx = [rows[v]["dtx_t"].sum() for v in variants]
    axd.bar(xb - width / 2, tot_dpar, width, color="tab:blue",
            label="sum PAR-time delta")
    axd.bar(xb + width / 2, tot_dtx, width, color="tab:orange",
            label="sum transmit-time delta")
    axd.axhline(0, color="k", lw=0.8)
    axd.set_xticks(xb)
    axd.set_xticklabels(variants)
    axd.set_ylabel("summed seconds vs baseline")
    axd.set_title("Aggregate effect: did PAR gains outrun transmit cost?")
    axd.legend(fontsize=8)
    axd.grid(True, axis="y", alpha=0.3)

    fig.suptitle(
        "pred_* variants -- live Codabench per-case analysis (heartbeat fix)\n"
        "time = 60*mean_PAR + transmit*88080384/9e11   |   only PAR & transmit "
        "differ from baseline; seasonal estimate is the lever",
        fontsize=12)

    out = REPO / "experiments" / "results" / "pred_variants_live_analysis.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)

    # ---- console summary ----------------------------------------------
    print(f"PNG -> {out}")
    for v in variants:
        dt = rows[v]["dt"]
        eng = [(labels[i], rows[v]["dpar_t"][i], rows[v]["dtx_t"][i], dt[i])
               for i in range(n) if abs(dt[i]) > 1e-9]
        print(f"\n=== {v}  (composite {COMPOSITE[v]:.3f}, "
              f"{COMPOSITE[v]-COMPOSITE['baseline']:+.3f} vs baseline) ===")
        print(f"  engaged on {len(eng)}/{n} cases; "
              f"net time delta summed = {dt.sum():+.4f}s "
              f"(PAR {rows[v]['dpar_t'].sum():+.4f}s, "
              f"transmit {rows[v]['dtx_t'].sum():+.4f}s)")
        for lab, dp, dx, d in sorted(eng, key=lambda r: r[3]):
            verdict = "FASTER" if d < 0 else "slower"
            print(f"    {lab:22s} dPARt={dp:+.4f}s dTXt={dx:+.4f}s "
                  f"net={d:+.4f}s  [{verdict}]")


if __name__ == "__main__":
    main()
