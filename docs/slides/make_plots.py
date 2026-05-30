"""Generate presentation graphs for the MoE load-balancing deck.

Two kinds of figure:
  - DATA: computed directly from the committed trace trace/DS-R1/LmSys.npy
  - RESULT: our documented experiment numbers (oracle gap, per-case PAR, score climb)
All styled for projection: white plot area (sits on a dark slide), large fonts.
"""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = os.path.dirname(os.path.abspath(__file__)) + "/plots"
os.makedirs(OUT, exist_ok=True)

# ---- shared style -----------------------------------------------------------
plt.rcParams.update({
    "figure.dpi": 150,
    "savefig.dpi": 150,
    "font.size": 13,
    "axes.titlesize": 15,
    "axes.titleweight": "bold",
    "axes.labelsize": 13,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "savefig.bbox": "tight",
    "savefig.facecolor": "white",
})
ACCENT = "#2563eb"   # cobalt
HOT = "#ea580c"      # orange
GRAY = "#64748b"

DSR1 = "trace/DS-R1/LmSys.npy"


def save(fig, name):
    p = f"{OUT}/{name}.png"
    fig.savefig(p)
    plt.close(fig)
    print("wrote", p)


# =============================================================================
# 1. DATA — expert hotness over time (the busiest layer). Load is spiky + drifts.
# =============================================================================
def plot_hotness_time():
    a = np.load(DSR1)                      # (iters, layers, experts)
    layer_tot = a.sum(axis=(0, 2))
    L = int(layer_tot.argmax())
    layer = a[:, L, :].astype(float)       # (iters, experts)
    top = layer.sum(0).argsort()[::-1][:5]
    fig, ax = plt.subplots(figsize=(9, 4.6))
    for e in top:
        ax.plot(layer[:, e], lw=1.1, label=f"expert {e}")
    ax.set_title(f"DS-R1 / LmSys: top-5 experts over time (busiest layer {L})")
    ax.set_xlabel("iteration")
    ax.set_ylabel("tokens routed")
    ax.legend(fontsize=10, ncol=1, frameon=False)
    save(fig, "01_hotness_time")


# =============================================================================
# 2. DATA — high-EP load is DIFFUSE: cumulative share of total load by rank.
#    The top experts carry only a small slice -> the peak is variance, not a
#    single mega-hot expert.
# =============================================================================
def plot_load_concentration():
    a = np.load(DSR1)
    load = a.mean(axis=0).mean(axis=0)     # avg per-expert load (over layers)
    s = np.sort(load)[::-1]
    share = np.cumsum(s) / s.sum()
    n = len(s)
    fig, ax = plt.subplots(figsize=(8.4, 4.6))
    ax.plot(np.arange(1, n + 1), share * 100, color=ACCENT, lw=2.4)
    ax.plot([1, n], [0, 100], color=GRAY, ls="--", lw=1.4, label="perfectly even")
    k = 8
    ax.axvline(k, color=HOT, ls=":", lw=1.6)
    ax.annotate(f"top {k} of {n} experts\ncarry only {share[k-1]*100:.0f}% of load",
                xy=(k, share[k-1]*100), xytext=(k+40, 32), fontsize=11,
                color=HOT, arrowprops=dict(arrowstyle="->", color=HOT))
    ax.set_title("DS-R1 / LmSys: load is diffuse, not concentrated")
    ax.set_xlabel("expert rank (hottest first)")
    ax.set_ylabel("cumulative % of total load")
    ax.set_xlim(1, n)
    ax.set_ylim(0, 100)
    ax.legend(fontsize=10, frameon=False, loc="lower right")
    save(fig, "02_load_concentration")


# =============================================================================
# 3. RESULT — THE key finding. Realized PAR vs perfect-foresight oracle PAR.
#    Packing is solved; the gap is forecasting, and it explodes with EP.
# =============================================================================
def plot_oracle_gap():
    ep = ["EP32", "EP64", "EP128", "EP256"]
    realized = [1.235, 1.383, 1.616, 1.998]
    oracle = [1.011, 1.032, 1.035, 1.025]
    x = np.arange(len(ep))
    w = 0.38
    fig, ax = plt.subplots(figsize=(9, 4.8))
    ax.bar(x - w/2, realized, w, color=HOT, label="what we get (realized)")
    ax.bar(x + w/2, oracle, w, color=ACCENT, label="perfect foresight (oracle)")
    for i in range(len(ep)):
        gap = realized[i] - oracle[i]
        ax.annotate(f"+{gap:.2f}", xy=(x[i], realized[i]), xytext=(0, 6),
                    textcoords="offset points", ha="center", fontsize=11,
                    color=HOT, fontweight="bold")
    ax.axhline(1.0, color=GRAY, ls="--", lw=1.2)
    ax.text(3.35, 1.02, "1.0 = perfect", color=GRAY, fontsize=10, ha="right")
    ax.set_title("Packing is solved — the gap is FORECASTING (DS-R1 LmSys)")
    ax.set_ylabel("PAR  (max device load / mean)")
    ax.set_xticks(x)
    ax.set_xticklabels(ep)
    ax.set_ylim(0.9, 2.15)
    ax.legend(fontsize=10, frameon=False, loc="upper left")
    save(fig, "03_oracle_gap")


# =============================================================================
# 4. RESULT — per-case PAR heatmap (DS-R1). Mix is the outlier at every EP;
#    Mix/EP256 = 3.42 is the single worst case and decides the score.
# =============================================================================
def plot_percase_heatmap():
    rows = ["LmSys", "ShareGPT", "WildChat", "Mix"]
    cols = ["EP32", "EP64", "EP128", "EP256"]
    par = np.array([
        [1.24, 1.38, 1.62, 2.00],
        [1.24, 1.39, 1.63, 2.04],
        [1.22, 1.37, 1.59, 1.96],
        [1.63, 2.02, 2.60, 3.42],
    ])
    fig, ax = plt.subplots(figsize=(8.4, 4.4))
    im = ax.imshow(par, cmap="inferno", aspect="auto", vmin=1.0, vmax=3.42)
    ax.set_xticks(range(4)); ax.set_xticklabels(cols)
    ax.set_yticks(range(4)); ax.set_yticklabels(rows)
    for i in range(4):
        for j in range(4):
            v = par[i, j]
            ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                    color="white" if v < 2.6 else "black", fontweight="bold")
    ax.set_title("Per-case PAR (DS-R1): 'Mix' is the whole battle")
    fig.colorbar(im, ax=ax, label="mean PAR", fraction=0.046, pad=0.04)
    save(fig, "04_percase_heatmap")


# =============================================================================
# 5. RESULT — the score climb. 100 -> 114.76 by mechanism (not tuning).
# =============================================================================
def plot_score_climb():
    names = ["DS-EPLB\nbaseline", "finance_band", "sticky\nrelabel",
             "epadaptive\n(co-spike)", "peak_cospike", "peakblock",
             "drift_router", "dro_ambiguity", "cov_match", "cov_iter",
             "cov_iter_dro"]
    scores = [100.0, 111.27, 111.80, 112.52, 113.90, 114.13,
              114.18, 114.32, 114.35, 114.61, 114.76]
    x = np.arange(len(scores))
    fig, ax = plt.subplots(figsize=(10, 4.8))
    ax.step(x, scores, where="mid", color=ACCENT, lw=2.0, alpha=0.5)
    ax.scatter(x, scores, color=ACCENT, s=46, zorder=3)
    ax.scatter([x[-1]], [scores[-1]], color=HOT, s=130, zorder=4)
    ax.annotate("114.76", xy=(x[-1], scores[-1]), xytext=(0, 10),
                textcoords="offset points", ha="center", color=HOT,
                fontsize=13, fontweight="bold")
    ax.axhline(100, color=GRAY, ls="--", lw=1.2)
    ax.text(0.1, 100.6, "DS-EPLB = 100", color=GRAY, fontsize=10)
    ax.set_title("Score climb: 100 → 114.76 (new mechanisms, not tuning)")
    ax.set_ylabel("Codabench composite score")
    ax.set_xticks(x)
    ax.set_xticklabels(names, fontsize=8.5, rotation=30, ha="right")
    ax.set_ylim(99, 116)
    save(fig, "05_score_climb")


# =============================================================================
# 6. DATA — co-spiking & the EP256 pairing lever. Two real experts whose loads
#    are anti-correlated: paired on one device, the device peak is FLAT;
#    two correlated experts spike together. This is what cov_iter exploits.
# =============================================================================
def plot_cospike_pairing():
    # Schematic: at EP256 a device holds 2 experts; its peak = max_t(a[t]+b[t]).
    # Pairing ANTI-phase experts flattens the device; pairing IN-phase doubles it.
    t = np.linspace(0, 4 * np.pi, 400)
    base = 1.0 + 0.7 * np.sin(t)                    # spiky expert
    anti = 1.0 - 0.7 * np.sin(t)                    # opposite phase
    corr = 1.0 + 0.7 * np.sin(t)                    # same phase
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.3), sharey=True)
    for ax, partner, title, col in [
        (axes[0], anti, "PAIR ANTI-CORRELATED → device stays flat", ACCENT),
        (axes[1], corr, "PAIR CORRELATED → device peak doubles", HOT)]:
        ax.plot(t, base, color=GRAY, lw=1.3, alpha=0.7, label="expert A")
        ax.plot(t, partner, color=GRAY, lw=1.3, alpha=0.4, ls="--", label="expert B")
        ax.plot(t, base + partner, color=col, lw=3.0, label="device load (A + B)")
        ax.axhline((base + partner).max(), color=col, ls=":", lw=1.4, alpha=0.7)
        ax.set_title(title, fontsize=12, color=col)
        ax.set_xlabel("iteration →")
        ax.set_xticks([])
        ax.legend(fontsize=9.5, frameon=False, loc="upper right")
    axes[0].set_ylabel("load")
    axes[0].set_ylim(0, 2.4)
    fig.suptitle("EP256 = 2 slots/device → placement is a PAIRING problem  (schematic)",
                 fontsize=14, fontweight="bold")
    save(fig, "06_cospike_pairing")


# =============================================================================
# 7. RESULT — transmit: DS-EPLB churns every cycle; the no-trade band HOLDS.
#    PAR is ~equal; the early win is the collapsed transfer cost.
# =============================================================================
def plot_transmit():
    labels = ["DS-EPLB\n(redeploys\nevery cycle)", "finance_band\n(no-trade band)",
              "+ sticky\nrelabel"]
    tx = [842, 686, 625]   # thousands of slot-moves (documented)
    fig, ax = plt.subplots(figsize=(7.8, 4.6))
    bars = ax.bar(labels, tx, color=[HOT, ACCENT, ACCENT], width=0.6)
    for b, v in zip(bars, tx):
        ax.text(b.get_x()+b.get_width()/2, v+8, f"{v}k", ha="center",
                fontweight="bold", fontsize=12)
    ax.set_title("Same balance, far less churn: hold instead of re-shuffle")
    ax.set_ylabel("transmit (k slot-moves, LmSys EP256)")
    ax.set_ylim(0, 950)
    save(fig, "07_transmit")


if __name__ == "__main__":
    plot_hotness_time()
    plot_load_concentration()
    plot_oracle_gap()
    plot_percase_heatmap()
    plot_score_climb()
    plot_cospike_pairing()
    plot_transmit()
    print("\nAll plots in", OUT)
