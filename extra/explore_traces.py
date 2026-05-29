"""Exploratory data analysis for the MoE expert-hotness traces.

Each trace is an int32 array of shape (n_iterations, n_layers, n_experts) where
entry [i, l, e] is the number of tokens routed to expert e in layer l during
iteration i. This script summarizes the load distribution (which experts
matter), the per-layer imbalance (what drives the PAR score the competition
optimizes), and how stable the hot experts are over time.

One figure per graph is written to experiments/results/eda/<model>/.
"""
import os
import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

CUR_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(CUR_DIR)
TOP_K = 8  # experts activated per token, used to recover token count


def gini(x):
    """Gini coefficient of a non-negative load vector (0 = even, 1 = skewed)."""
    x = np.sort(x.astype(np.float64))
    n = x.size
    if x.sum() == 0:
        return 0.0
    idx = np.arange(1, n + 1)
    return float(np.sum((2 * idx - n - 1) * x) / (n * x.sum()))


def save(fig, out_dir, name):
    path = os.path.join(out_dir, name)
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"  -> {os.path.relpath(path, REPO_DIR)}")


def analyze(model):
    path = os.path.join(REPO_DIR, "trace", model, "LmSys.npy")
    a = np.load(path)  # (iters, layers, experts) int32
    n_iter, n_layers, n_experts = a.shape
    out_dir = os.path.join(CUR_DIR, "results", "eda", model)
    os.makedirs(out_dir, exist_ok=True)
    af = a.astype(np.float64)
    tag = f"{model} / LmSys"

    # ---- shared quantities ----
    layer_load = af.mean(axis=0)                       # (layers, experts) time-avg
    par_ideal = layer_load.max(axis=1) / layer_load.mean(axis=1)
    layer_gini = np.array([gini(layer_load[l]) for l in range(n_layers)])
    global_expert = af.sum(axis=(0, 1))                # (experts,)
    tokens_per_iter = af.sum(axis=(1, 2)) / TOP_K
    busiest_layer = int(par_ideal.argmax())

    print(f"\n=== {model}  shape={a.shape} ===")
    print(f"  ideal-PAR/layer mean/min/max: {par_ideal.mean():.3f}/{par_ideal.min():.3f}/{par_ideal.max():.3f}")
    print(f"  Gini/layer      mean/min/max: {layer_gini.mean():.3f}/{layer_gini.min():.3f}/{layer_gini.max():.3f}")
    print(f"  busiest layer: {busiest_layer}")

    # ---- 1. load heatmap (LINEAR) ----
    fig, ax = plt.subplots(figsize=(11, 6))
    im = ax.imshow(layer_load, aspect="auto", cmap="viridis")
    ax.set_title(f"{tag}: time-averaged load (tokens) per (layer, expert)")
    ax.set_xlabel("expert"); ax.set_ylabel("layer")
    fig.colorbar(im, ax=ax, fraction=0.046, label="avg tokens / iteration")
    save(fig, out_dir, "01_heatmap_load.png")

    # ---- 2. ideal PAR per layer ----
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.bar(np.arange(n_layers), par_ideal, color="tab:red", alpha=0.8)
    ax.axhline(par_ideal.mean(), ls="--", color="k", label=f"mean {par_ideal.mean():.2f}")
    ax.set_title(f"{tag}: ideal PAR per layer (max/mean load, lower=balanced)")
    ax.set_xlabel("layer"); ax.set_ylabel("ideal PAR"); ax.legend()
    save(fig, out_dir, "02_par_per_layer.png")

    # ---- 3. Gini per layer ----
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.bar(np.arange(n_layers), layer_gini, color="tab:blue", alpha=0.8)
    ax.axhline(layer_gini.mean(), ls="--", color="k", label=f"mean {layer_gini.mean():.3f}")
    ax.set_title(f"{tag}: load skew (Gini) per layer  (0=even, 1=concentrated)")
    ax.set_xlabel("layer"); ax.set_ylabel("Gini"); ax.legend()
    save(fig, out_dir, "03_gini_per_layer.png")

    # ---- 4. Lorenz / cumulative load share ----
    share = np.sort(global_expert)[::-1] / global_expert.sum()
    cum = np.cumsum(share)
    fig, ax = plt.subplots(figsize=(8, 6))
    x = np.arange(1, n_experts + 1) / n_experts
    ax.plot(x, cum, color="tab:purple", lw=2)
    ax.plot([0, 1], [0, 1], "--", color="gray", label="perfectly even")
    half = int(np.searchsorted(cum, 0.5)) + 1
    ax.axvline(half / n_experts, color="tab:red", ls=":",
               label=f"{half} experts carry 50%")
    ax.set_title(f"{tag}: cumulative load share (experts sorted hottest-first)")
    ax.set_xlabel("fraction of experts"); ax.set_ylabel("cumulative load share"); ax.legend()
    save(fig, out_dir, "04_lorenz_load_share.png")

    # ---- 5. sorted per-expert total load ----
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.bar(np.arange(n_experts), np.sort(global_expert)[::-1], color="tab:green", alpha=0.8)
    ax.axhline(global_expert.mean(), ls="--", color="k", label="mean load")
    ax.set_title(f"{tag}: total load per expert (summed over all layers/iters, sorted)")
    ax.set_xlabel("expert rank"); ax.set_ylabel("total tokens"); ax.legend()
    save(fig, out_dir, "05_expert_load_sorted.png")

    # ---- 6. histogram of per-layer ideal PAR ----
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.hist(par_ideal, bins=20, color="tab:red", alpha=0.8, edgecolor="k")
    ax.set_title(f"{tag}: distribution of per-layer ideal PAR")
    ax.set_xlabel("ideal PAR"); ax.set_ylabel("number of layers")
    save(fig, out_dir, "06_par_hist.png")

    # ---- 7. top-5 experts over time, busiest layer ----
    layer_series = af[:, busiest_layer, :]
    hottest = np.argsort(layer_series.mean(axis=0))[::-1][:5]
    fig, ax = plt.subplots(figsize=(11, 5))
    for ee in hottest:
        ax.plot(layer_series[:, ee], lw=0.8, alpha=0.8, label=f"expert {ee}")
    ax.set_title(f"{tag}: top-5 experts over time (busiest layer {busiest_layer})")
    ax.set_xlabel("iteration"); ax.set_ylabel("tokens routed"); ax.legend(fontsize=8)
    save(fig, out_dir, "07_top5_busiest_layer.png")

    # ---- 8. global imbalance over time (mean ideal-PAR across layers, per iter) ----
    # per-iter, per-layer PAR = max/mean over experts; average across layers.
    par_t = (af.max(axis=2) / af.mean(axis=2)).mean(axis=1)   # (iters,)
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(par_t, color="tab:orange", lw=0.7)
    ax.axhline(par_t.mean(), ls="--", color="k", label=f"mean {par_t.mean():.2f}")
    ax.set_title(f"{tag}: instantaneous mean ideal-PAR across layers over time")
    ax.set_xlabel("iteration"); ax.set_ylabel("mean ideal PAR"); ax.legend()
    save(fig, out_dir, "08_par_over_time.png")

    # ---- 9. rank stability: early vs late per-(layer,expert) load ----
    w = min(256, n_iter // 4)
    early = af[:w].mean(axis=0).ravel()
    late = af[-w:].mean(axis=0).ravel()
    re = np.argsort(np.argsort(early)); rl = np.argsort(np.argsort(late))
    corr = np.corrcoef(re, rl)[0, 1]
    fig, ax = plt.subplots(figsize=(7, 7))
    ax.scatter(early, late, s=4, alpha=0.25, color="tab:blue")
    lim = max(early.max(), late.max())
    ax.plot([0, lim], [0, lim], "--", color="gray")
    ax.set_title(f"{tag}: expert load early vs late  (rank corr {corr:.3f})")
    ax.set_xlabel(f"avg load, first {w} iters"); ax.set_ylabel(f"avg load, last {w} iters")
    save(fig, out_dir, "09_rank_stability.png")

    # ---- 10. throughput: tokens routed per iteration ----
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(tokens_per_iter, color="tab:gray", lw=0.7)
    ax.set_title(f"{tag}: tokens per iteration (sum/{TOP_K})")
    ax.set_xlabel("iteration"); ax.set_ylabel("tokens")
    save(fig, out_dir, "10_tokens_per_iter.png")

    # ---- 11. distribution of raw per-(iter,layer,expert) load values ----
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.hist(af.ravel(), bins=100, color="tab:cyan", edgecolor="none")
    ax.axvline(af.mean(), ls="--", color="k", label=f"mean {af.mean():.1f}")
    ax.set_title(f"{tag}: distribution of expert load values (every iter/layer/expert)")
    ax.set_xlabel("tokens routed to an expert in one iteration"); ax.set_ylabel("count")
    ax.set_yscale("log"); ax.legend()
    save(fig, out_dir, "11_load_value_distribution.png")

    # ---- 12. distribution of time-averaged per-expert load (per layer overlaid) ----
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.hist(layer_load.ravel(), bins=60, color="tab:olive", edgecolor="k", alpha=0.85)
    even = layer_load.mean()
    ax.axvline(even, ls="--", color="tab:red", label=f"even-load target {even:.1f}")
    ax.set_title(f"{tag}: distribution of time-avg load across all (layer, expert) slots")
    ax.set_xlabel("avg tokens / iteration"); ax.set_ylabel("number of (layer,expert) slots")
    ax.legend()
    save(fig, out_dir, "12_avg_load_distribution.png")


if __name__ == "__main__":
    for m in ["DS-R1", "Qwen3"]:
        analyze(m)
