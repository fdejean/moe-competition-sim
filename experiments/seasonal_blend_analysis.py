"""Blend a trailing-window baseline with a seasonal (phase-aware) predictor.

Follow-up to ``trailing_window_analysis.py``. That script showed a trailing mean
(SMA/EWMA) is a low-pass smoother: it captures the *persistent* part of expert
load but is blind to *phase*, so it cannot exploit periodicity. The pooled
autocorrelation of the LmSys traces confirms the load is eventually periodic ---
strongly persistent at short lags, anti-correlated near a half-period, and
re-correlated at the full period P (~2048 iters, 3 cycles in DS-R1).

Two predictors, two regimes:

  * BASELINE  -- trailing SMA over window W. Wins at short horizons where load is
                locally persistent.
  * SEASONAL  -- value one full period ago: af[t+H-P : ...]. Causal whenever the
                horizon H <= P. Wins at long horizons where the baseline has gone
                anti-phase and is actively wrong.

  * BLEND     -- (1-a)*baseline + a*seasonal. The two errors are de-correlated, so
                a convex mix beats both. We use a tuning-free schedule a(H) that
                ramps from baseline (small H) to seasonal (H ~ P), driven by the
                detected period.

Quality metric is the same as the trailing-window study: mean (over layers and
decision points) Spearman rank correlation between the estimate and the realized
future load --- placement only cares about ranking the hot experts.

Figures -> experiments/results/eda/<model>/.
"""
import os
import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

CUR_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(CUR_DIR)

BASE_W = 256          # trailing-window length for the baseline (good short-H estimate)
FUT_W = 32            # width of the future block we score against
ALPHAS = np.linspace(0.0, 1.0, 11)


def rank_corr_layers(pred, truth):
    """Mean Spearman rank corr over layers. pred/truth: (n_layers, n_experts)."""
    corrs = []
    for l in range(pred.shape[0]):
        p = np.argsort(np.argsort(pred[l])).astype(np.float64)
        t = np.argsort(np.argsort(truth[l])).astype(np.float64)
        if p.std() == 0 or t.std() == 0:
            continue
        corrs.append(np.corrcoef(p, t)[0, 1])
    return float(np.mean(corrs))


def detect_period(af):
    """Dominant period via FFT-based pooled autocorrelation over (layer, expert).

    Returns (P, acf) where acf is the normalized pooled autocorrelation curve.

    The short-lag ACF is a monotone *persistence* shoulder, not seasonality. The
    seasonal recurrence is the recovery peak that follows the anti-phase trough,
    so we find the first lag where the ACF dips below zero and take the argmax
    *after* that. Returns P=None if the ACF never goes negative (no periodicity,
    just persistence).
    """
    n_iter = af.shape[0]
    x = af - af.mean(axis=0, keepdims=True)
    nfft = 1 << int(np.ceil(np.log2(2 * n_iter)))
    X = np.fft.rfft(x, n=nfft, axis=0)
    power = (X * np.conj(X)).real
    ac = np.fft.irfft(power, n=nfft, axis=0)[:n_iter]
    acf = ac.sum(axis=(1, 2))
    acf = acf / acf[0]
    hi = min(len(acf) - 1, n_iter // 2)
    neg = np.where(acf[:hi] < 0)[0]
    if len(neg) == 0:
        return None, acf
    trough = neg[0]
    P = int(trough + np.argmax(acf[trough:hi]))
    if acf[P] < 0.05:  # recovery too weak to be a usable season
        return None, acf
    return P, acf


def alpha_schedule(H, P, acf):
    """Tuning-free blend weight from the *measured* skill of each predictor.

    The trailing window's expected skill at horizon H is the persistence
    autocorrelation acf[H]; the seasonal term's is the recurrence acf[P] (roughly
    horizon-independent). Weight each predictor by its own positive skill:

        a(H) = acf[P]+ / (acf[P]+ + acf[H]+)

    so a ramps from baseline toward seasonal exactly as persistence decays, and
    snaps to pure seasonal where the trailing window goes anti-phase (acf[H]<=0).
    No fitting, no H/P magic number -- just the detected ACF curve.
    """
    rho_base = max(acf[H], 0.0) if H < len(acf) else 0.0
    rho_seas = max(acf[P], 1e-3)
    return float(rho_seas / (rho_seas + rho_base))


def analyze(model):
    path = os.path.join(REPO_DIR, "trace", model, "LmSys.npy")
    af = np.load(path).astype(np.float64)  # (iters, layers, experts)
    n_iter = af.shape[0]
    out_dir = os.path.join(CUR_DIR, "results", "eda", model)
    os.makedirs(out_dir, exist_ok=True)
    tag = f"{model} / LmSys"

    P, acf = detect_period(af)
    print(f"\n=== {model}  shape={af.shape} ===")
    if P is None:
        print("  no usable period detected (load is persistent, not periodic); "
              "seasonal term would add nothing -> skipping.")
        return
    print(f"  detected period P={P}  (acf[P]={acf[P]:.3f}, "
          f"acf[P//2]={acf[P // 2]:.3f})")

    # horizons up to one full period; seasonal term stays causal for H <= P
    HORIZONS = [h for h in [16, 32, 64, 128, 256, 384, 512, 768, 1024,
                            1280, 1536, 1792, P] if h <= P]
    HORIZONS = sorted(set(HORIZONS))

    W = BASE_W
    max_h = max(HORIZONS)
    # decision points: need W past for baseline, and t+H+FUT_W future to score.
    # seasonal ref af[t+H-P:...] is causal because H<=P  =>  t+H-P <= t.
    lo = max(W, P)
    starts = list(range(lo, n_iter - max_h - FUT_W, 128))
    if len(starts) < 3:
        starts = list(range(lo, n_iter - max_h - FUT_W, 32))
    print(f"  W={W}  horizons<=P  decision points={len(starts)}")

    def preds_at(t, H):
        baseline = af[t - W:t].mean(axis=0)
        s0 = t + H - P
        seasonal = af[s0:s0 + FUT_W].mean(axis=0)
        truth = af[t + H:t + H + FUT_W].mean(axis=0)
        return baseline, seasonal, truth

    # ---------- (A) alpha sweep at short / half-period / full-period horizons ----
    probe_H = [h for h in [64, max(1, P // 2), P] if h <= max_h]
    probe_H = sorted(set(probe_H))
    sweep = {}  # H -> array over ALPHAS
    for H in probe_H:
        row = []
        for a in ALPHAS:
            sc = []
            for t in starts:
                b, s, truth = preds_at(t, H)
                sc.append(rank_corr_layers((1 - a) * b + a * s, truth))
            row.append(float(np.mean(sc)))
        sweep[H] = np.array(row)
        a_best = ALPHAS[int(np.argmax(sweep[H]))]
        print(f"  alpha-sweep H={H:5d}: best a={a_best:.2f}  "
              f"corr a=0:{sweep[H][0]:.3f}  best:{sweep[H].max():.3f}  "
              f"a=1:{sweep[H][-1]:.3f}")

    fig, ax = plt.subplots(figsize=(9, 5.5))
    for H in probe_H:
        ax.plot(ALPHAS, sweep[H], "o-", label=f"H={H}")
    ax.set_title(f"{tag}: blend weight sweep (W={W}, P={P})\n"
                 f"a=0 pure trailing window, a=1 pure seasonal")
    ax.set_xlabel("blend weight a  (seasonal fraction)")
    ax.set_ylabel("mean rank corr with load H ahead")
    ax.legend(); ax.grid(True, alpha=0.3)
    fig.savefig(os.path.join(out_dir, "15_blend_alpha_sweep.png"),
                dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"  -> {os.path.relpath(os.path.join(out_dir, '15_blend_alpha_sweep.png'), REPO_DIR)}")

    # ---------- (B) decay vs horizon: baseline vs seasonal vs adaptive blend ----
    base_c, seas_c, blend_c, best_c = [], [], [], []
    for H in HORIZONS:
        a = alpha_schedule(H, P, acf)
        sb, ss, sbl, sbe = [], [], [], []
        for t in starts:
            b, s, truth = preds_at(t, H)
            sb.append(rank_corr_layers(b, truth))
            ss.append(rank_corr_layers(s, truth))
            sbl.append(rank_corr_layers((1 - a) * b + a * s, truth))
            # per-horizon oracle alpha (achievable envelope, for reference)
            sbe.append(max(rank_corr_layers((1 - aa) * b + aa * s, truth)
                           for aa in ALPHAS))
        base_c.append(np.mean(sb)); seas_c.append(np.mean(ss))
        blend_c.append(np.mean(sbl)); best_c.append(np.mean(sbe))
    print("  decay (H: base / seasonal / blend):")
    for H, b, s, bl in zip(HORIZONS, base_c, seas_c, blend_c):
        print(f"    {H:5d}:  {b:+.3f} / {s:+.3f} / {bl:+.3f}")

    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.axhline(0, color="k", lw=0.8, alpha=0.5)
    ax.plot(HORIZONS, base_c, "o-", color="tab:blue", label="baseline (trailing window)")
    ax.plot(HORIZONS, seas_c, "s-", color="tab:green", label=f"seasonal (one period P={P} back)")
    ax.plot(HORIZONS, blend_c, "^-", color="tab:red", lw=2.2, label="adaptive blend a(H)")
    ax.plot(HORIZONS, best_c, ":", color="tab:gray", label="best fixed-a (envelope)")
    ax.axvline(P // 2, color="tab:orange", ls="--", alpha=0.5)
    ax.text(P // 2, ax.get_ylim()[0], " P/2", color="tab:orange", va="bottom")
    ax.set_xscale("log", base=2)
    ax.set_title(f"{tag}: blended predictor beats the trailing window at long H\n"
                 f"(baseline goes anti-phase near P/2; seasonal recovers it)")
    ax.set_xlabel("horizon H ahead (iterations)")
    ax.set_ylabel("mean rank corr with load H ahead")
    ax.legend(); ax.grid(True, alpha=0.3)
    fig.savefig(os.path.join(out_dir, "16_seasonal_blend_decay.png"),
                dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"  -> {os.path.relpath(os.path.join(out_dir, '16_seasonal_blend_decay.png'), REPO_DIR)}")

    # summary: where the blend helps most vs the pure baseline
    gain = np.array(blend_c) - np.array(base_c)
    j = int(np.argmax(gain))
    print(f"  max blend gain over baseline: +{gain[j]:.3f} rank-corr at H={HORIZONS[j]}")


if __name__ == "__main__":
    for m in ["DS-R1", "Qwen3"]:
        analyze(m)
