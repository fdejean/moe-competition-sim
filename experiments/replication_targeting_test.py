"""Is the per-iteration spike set predictable enough to target replication at it?

Decisive offline test for the colleague's lever: "spend the redundant-slot budget
on the experts most likely to SPIKE NEXT, not the ones heaviest in the window."

The oracle gap (experiments/oracle_gap.py) bundles two costs:
  (a) forecast-of-window error  -- reachable by a better estimate, transfer-free
  (b) within-window per-iteration variance -- one static placement can't absorb a
      load that varies iter-to-iter; the only transfer-free cure is REPLICATING
      the experts that spike so their max is split across devices.

This script measures (b)'s reachability. For each holding window we build ONE
placement (held fixed over the whole window, identical transfer for every policy)
and score realized per-iteration PAR over that window. Policies differ ONLY in the
volatility term that drives replication -- the mean base (trailing mean) is held
constant -- so any difference is purely "which experts did we choose to replicate":

  current        risk = mean_tr + L*std_tr        std over the TRAILING window (what
                                                  finance_band does today)
  pattern        risk = mean_tr + L*std_seasonal  std over the same-phase window one
                                                  period P ago (seasonal spike forecast)
  oracle_replica risk = mean_tr + L*std_future     std over the ACTUAL upcoming window
                                                  (cheating; upper bound for spike
                                                  foresight with NO extra transfer)
  dyn_oracle     re-place every iter on the true load (oracle_gap's oracle; needs the
                 transfer wall broken -- shown only as the far bound)

Read: if `pattern` closes a real fraction of current->oracle_replica, the lever is
alive and worth productionizing. If `oracle_replica` itself barely beats `current`,
the spike set is unpredictable and the lever is dead -- stop here, no submissions.

Out: experiments/results/oracle/replication_targeting.{png,csv} + console table.
"""
import csv
import importlib.util
import os
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "experiments" / "results" / "oracle"

MODELS = {"DS-R1": (58, 256), "Qwen3": (94, 128)}
EP_GRID = {"DS-R1": [32, 64, 128, 256], "Qwen3": [32, 64, 128]}
LAMBDA = 2.0
HOLD = 1024            # grader holds a placement ~this many iters between rebalances
TRAIL = 1024           # trailing window the grader passes to rebalance
STRIDE = 8             # subsample held iters when scoring PAR (PAR is a mean)
DYN_SAMPLES = 48       # iters per window for the (expensive) dynamic oracle


def load_fb():
    spec = importlib.util.spec_from_file_location(
        "fb", REPO / "submissions" / "finance_band" / "submission.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def detect_period(af):
    """Seasonal period = ACF recovery peak after the first zero crossing."""
    n = af.shape[0]
    x = af - af.mean(axis=0, keepdims=True)
    nfft = 1 << int(np.ceil(np.log2(2 * n)))
    X = np.fft.rfft(x, n=nfft, axis=0)
    ac = np.fft.irfft((X * np.conj(X)).real, n=nfft, axis=0)[:n].sum(axis=(1, 2))
    ac /= ac[0]
    hi = min(len(ac) - 1, n // 2)
    neg = np.where(ac[:hi] < 0)[0]
    if len(neg) == 0:
        return None
    return int(neg[0] + np.argmax(ac[neg[0]:hi]))


def load_trace(model):
    a = np.load(REPO / "trace" / model / "LmSys.npy").astype(np.float64)
    a[a == 0] = 1.0
    return a


def held_par(hot_win, deploy, n_experts):
    """Mean per-iteration PAR for a static `deploy` over a block of iters.

    hot_win:(T,E)  deploy:(D,S) -> scalar mean PAR over the T iters.
    """
    counts = np.bincount(deploy.reshape(-1), minlength=n_experts)
    weights = hot_win / counts                      # (T,E)
    slots = deploy.reshape(-1)
    w_slot = weights[:, slots].reshape(hot_win.shape[0], *deploy.shape)
    loads = w_slot.sum(-1)                           # (T,D)
    return float((loads.max(1) / loads.mean(1)).mean())


def eval_case(fb, hot, n_layers, n_experts, ep, P):
    n_red = ep
    n_exp_per_dev = (n_experts + n_red) // ep
    n_iter = hot.shape[0]
    starts = list(range(max(TRAIL, P), n_iter - HOLD + 1, 256))
    acc = {k: [] for k in ("current", "pattern", "oracle_replica", "dyn_oracle")}
    for t in starts:
        trail = hot[t - TRAIL:t]
        held = hot[t:t + HOLD]
        seas = hot[t - P:t - P + HOLD]               # same-phase window one period back
        mean_tr = trail.mean(0)
        risk = {
            "current": mean_tr + LAMBDA * trail.std(0),
            "pattern": mean_tr + LAMBDA * seas.std(0),
            "oracle_replica": mean_tr + LAMBDA * held.std(0),
        }
        held_s = held[::STRIDE]
        for pol, r in risk.items():
            pars = [held_par(held_s[:, l], fb._build_layer(r[l], ep, n_exp_per_dev),
                             n_experts) for l in range(n_layers)]
            acc[pol].append(np.mean(pars))
        # dynamic oracle: re-place every (sampled) iter on the true load
        dyn = []
        for i in range(0, HOLD, HOLD // DYN_SAMPLES):
            hi = held[i]
            dyn.append(np.mean([fb._par(hi[l], fb._build_layer(hi[l], ep, n_exp_per_dev))
                                for l in range(n_layers)]))
        acc["dyn_oracle"].append(np.mean(dyn))
    return {k: float(np.mean(v)) for k, v in acc.items()}, len(starts)


def main():
    fb = load_fb()
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for model, (n_layers, n_experts) in MODELS.items():
        hot = load_trace(model)
        P = detect_period(hot)
        print(f"\n=== {model}  P={P}  (HOLD={HOLD}, TRAIL={TRAIL}, L={LAMBDA}) ===")
        print(f"  {'EP':>4} {'current':>9} {'pattern':>9} {'orc_repl':>9} "
              f"{'dyn_orc':>9} | {'reach':>7} {'patt_got':>8} {'%captured':>9}")
        for ep in EP_GRID[model]:
            res, npt = eval_case(fb, hot, n_layers, n_experts, ep, P)
            reach = res["current"] - res["oracle_replica"]      # transfer-free headroom
            got = res["current"] - res["pattern"]               # what the forecast captured
            pct = (got / reach * 100) if reach > 1e-9 else float("nan")
            rows.append((model, ep, res["current"], res["pattern"],
                         res["oracle_replica"], res["dyn_oracle"], reach, got, pct, npt))
            print(f"  {ep:>4} {res['current']:>9.4f} {res['pattern']:>9.4f} "
                  f"{res['oracle_replica']:>9.4f} {res['dyn_oracle']:>9.4f} | "
                  f"{reach:>+7.4f} {got:>+8.4f} {pct:>8.1f}%")

    with open(OUT / "replication_targeting.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model", "ep", "current", "pattern", "oracle_replica",
                    "dyn_oracle", "reachable", "pattern_got", "pct_captured", "n_points"])
        w.writerows(rows)

    # ---- plot: PAR ladder per model/EP -------------------------------------
    fig, axes = plt.subplots(1, len(MODELS), figsize=(13, 5.2), squeeze=False)
    for ax, (model, _) in zip(axes[0], MODELS.items()):
        sub = [r for r in rows if r[0] == model]
        x = np.arange(len(sub))
        eps = [r[1] for r in sub]
        ax.plot(x, [r[2] for r in sub], "s-", color="#e63946", label="current (trailing std)")
        ax.plot(x, [r[3] for r in sub], "^-", color="#1f77b4", label="pattern (seasonal std)")
        ax.plot(x, [r[4] for r in sub], "o-", color="#2a9d8f",
                label="oracle-replica (true upcoming std)")
        ax.plot(x, [r[5] for r in sub], ":", color="gray", label="dyn-oracle (re-place/iter)")
        ax.axhline(1.0, color="k", ls=":", lw=0.8, alpha=0.5)
        ax.set_xticks(x); ax.set_xticklabels([f"EP{e}" for e in eps])
        ax.set_title(f"{model} / LmSys: replication-targeting headroom")
        ax.set_ylabel("mean held-window PAR (lower=better)")
        ax.grid(alpha=0.25); ax.legend(fontsize=8)
    fig.suptitle("Can a seasonal spike forecast target replication better than trailing std?\n"
                 "gap current->oracle-replica = transfer-free headroom; pattern = what we can grab",
                 fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(OUT / "replication_targeting.png", dpi=130)
    print(f"\nwrote {OUT / 'replication_targeting.png'}")
    print(f"wrote {OUT / 'replication_targeting.csv'}")


if __name__ == "__main__":
    main()
