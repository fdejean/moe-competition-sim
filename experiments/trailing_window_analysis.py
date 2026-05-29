"""Finance-inspired analysis of trailing windows for expert load.

Treats each expert like an asset and its per-iteration token count like a
return/volume series. Answers two questions that decide a placement policy:

  (A) Estimation window: does a recency-weighted estimate (EWMA, like a
      6-month trailing window) predict the *next* period's hot experts better
      than a flat simple moving average (SMA)? Tested over several window
      lengths / half-lives.

  (B) Signal decay: how well does an estimate made now predict load H
      iterations ahead, as H grows? This is the "alpha decay" curve and it
      sets how often it is worth paying transfer cost to rebalance.

Quality metric = mean (over layers and decision points) Spearman rank
correlation between the estimate and the realized future load, because
placement only cares about getting the *ranking* of hot experts right.

Figures -> experiments/results/eda/<model>/.
"""
import os
import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

CUR_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(CUR_DIR)

SMA_WINDOWS = [32, 64, 128, 256, 512, 1024, 2048]
EWMA_HALFLIVES = [32, 64, 128, 256, 512, 1024]
HORIZONS = [16, 32, 64, 128, 256, 512, 1024, 2048]


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


def analyze(model):
    path = os.path.join(REPO_DIR, "trace", model, "LmSys.npy")
    af = np.load(path).astype(np.float64)  # (iters, layers, experts)
    n_iter, n_layers, n_experts = af.shape
    out_dir = os.path.join(CUR_DIR, "results", "eda", model)
    os.makedirs(out_dir, exist_ok=True)
    tag = f"{model} / LmSys"
    print(f"\n=== {model}  shape={af.shape} ===")

    # decision points: spaced so each has enough past and future
    max_w = max(SMA_WINDOWS)
    fut_h = 512  # future period used to judge estimators in (A)
    starts = list(range(max_w, n_iter - fut_h, 256))

    # ---------- (A) SMA vs EWMA across window length / half-life ----------
    sma_score = {}
    for W in SMA_WINDOWS:
        sc = []
        for t in starts:
            pred = af[t - W:t].mean(axis=0)
            truth = af[t:t + fut_h].mean(axis=0)
            sc.append(rank_corr_layers(pred, truth))
        sma_score[W] = float(np.mean(sc))

    ewma_score = {}
    for hl in EWMA_HALFLIVES:
        decay = 0.5 ** (1.0 / hl)
        # use a generous trailing window of 4 half-lives (cap at max_w)
        W = min(max_w, max(4 * hl, 64))
        ages = np.arange(W)[::-1]               # 0 = most recent
        w = decay ** ages
        w /= w.sum()
        sc = []
        for t in starts:
            win = af[t - W:t]
            pred = np.tensordot(w, win, axes=([0], [0]))  # (layers, experts)
            truth = af[t:t + fut_h].mean(axis=0)
            sc.append(rank_corr_layers(pred, truth))
        ewma_score[hl] = float(np.mean(sc))

    best_sma = max(sma_score, key=sma_score.get)
    best_ewma = max(ewma_score, key=ewma_score.get)
    print(f"  best SMA  window={best_sma:5d}  rank-corr={sma_score[best_sma]:.4f}")
    print(f"  best EWMA halflife={best_ewma:5d}  rank-corr={ewma_score[best_ewma]:.4f}")

    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.plot(list(sma_score), list(sma_score.values()), "o-", label="SMA (flat window)")
    ax.plot(list(ewma_score), list(ewma_score.values()), "s--", label="EWMA (recency-weighted)")
    ax.set_xscale("log", base=2)
    ax.set_title(f"{tag}: predicting next {fut_h}-iter hot experts\n(higher rank corr = better)")
    ax.set_xlabel("SMA window length  /  EWMA half-life (iterations)")
    ax.set_ylabel("mean rank corr with future load")
    ax.legend(); ax.grid(True, alpha=0.3)
    fig.savefig(os.path.join(out_dir, "13_trailing_window_sweep.png"), dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"  -> {os.path.relpath(os.path.join(out_dir, '13_trailing_window_sweep.png'), REPO_DIR)}")

    # ---------- (B) signal decay: estimate now vs load H ahead ----------
    W = best_sma
    starts_b = list(range(W, n_iter - max(HORIZONS) - 1, 256))
    decay_curve = []
    for H in HORIZONS:
        sc = []
        for t in starts_b:
            pred = af[t - W:t].mean(axis=0)
            truth = af[t + H:t + H + 32].mean(axis=0)  # short window centered H ahead
            sc.append(rank_corr_layers(pred, truth))
        decay_curve.append(float(np.mean(sc)))
    print("  signal decay (H -> rank-corr): " +
          ", ".join(f"{h}:{c:.3f}" for h, c in zip(HORIZONS, decay_curve)))

    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.plot(HORIZONS, decay_curve, "o-", color="tab:purple")
    ax.set_xscale("log", base=2)
    ax.set_ylim(0, 1)
    ax.set_title(f"{tag}: placement 'alpha decay'\n(window={W}, how well it predicts H iters ahead)")
    ax.set_xlabel("horizon H ahead (iterations)")
    ax.set_ylabel("rank corr with load H ahead")
    ax.grid(True, alpha=0.3)
    fig.savefig(os.path.join(out_dir, "14_signal_decay.png"), dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"  -> {os.path.relpath(os.path.join(out_dir, '14_signal_decay.png'), REPO_DIR)}")

    # ---------- lag-1 autocorrelation: momentum vs mean-reversion ----------
    # per-(layer,expert) lag-1 autocorr of the load series, averaged.
    x = af - af.mean(axis=0, keepdims=True)
    num = (x[1:] * x[:-1]).sum(axis=0)
    den = (x * x).sum(axis=0)
    ac1 = np.divide(num, den, out=np.zeros_like(num), where=den > 0)
    print(f"  lag-1 autocorr of per-expert load: mean={ac1.mean():.3f} "
          f"(>0 = persistent/trending, <0 = mean-reverting)")


if __name__ == "__main__":
    for m in ["DS-R1", "Qwen3"]:
        analyze(m)
