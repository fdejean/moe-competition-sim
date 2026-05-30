"""Per-layer portfolio ("bag"): score several placements per call, keep the best.

finance_band applies one fixed rule (lambda=2, full repack, redeploy iff modeled
PAR gain > TAU=0.2). But the *correct* redeploy threshold is ΔPAR > alpha*churn with
alpha = byte_cost * n_iter * n_layers / (60 * interval): a layer is worth redeploying
when the compute it saves over the held window beats the one-time transfer. Because
`minchurn_layer` changes far fewer slots than a from-scratch repack, its break-even
ΔPAR is ~6x smaller -- so there are many small-but-profitable redeploys the band
refuses. The bag exposes that headroom: per layer, per call, it evaluates a portfolio
of candidate placements and returns whichever minimizes  PAR(expected load) + alpha*churn.

Candidates: HOLD, minchurn@{lambda}, full@{lambda} for a few lambdas. HOLD wins ->
change=False on that layer (clock can still freeze if every layer holds, like fb).

Tested faithfully (forward-loop mechanics, transfer cost, DS-EPLB baseline) at the
PROVEN live cadence interval=1024. If the bag beats finance_band here, it's a real
submission candidate.

Out: experiments/results/oracle/bag_sweep.{csv,log} + console table.
"""
import csv
from pathlib import Path

import numpy as np

from experiments.incremental_replace_sweep import (  # noqa: E402
    load_fb, load_trace, minchurn_layer, forward, make_dseplb, make_fb,
    make_minchurn, MODELS, EP_GRID,
)


def held_par(hot_win, deploy, n_experts):
    """Mean per-iteration PAR of a static `deploy` over a block of iters (T,E)."""
    counts = np.bincount(deploy.reshape(-1), minlength=n_experts)
    weights = hot_win / counts
    slots = deploy.reshape(-1)
    w_slot = weights[:, slots].reshape(hot_win.shape[0], *deploy.shape)
    loads = w_slot.sum(-1)
    return float((loads.max(1) / loads.mean(1)).mean())
from dynamic_lb_simulator import (  # noqa: E402
    transmission_time_seconds, modeled_runtime_seconds,
)

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "experiments" / "results" / "oracle"
BYTE_COST = 88_080_384 / 9e11
INTERVAL = 1024
LAMBDAS = (0.0, 2.0)


def make_bag(fb, ep, n_red, n_experts, n_layers, n_iter, interval, lambdas=LAMBDAS,
             tw_scale=1.0, score="mean", hp_stride=16):
    """Portfolio placer. Per layer/call, pick argmin (PAR_proxy + alpha*churn).

    score="mean": proxy = PAR on the window mean (fragile -- rewards lambda=0 over-packing).
    score="heldpar": proxy = realized PAR over the trailing window's ITERATIONS
        (variance-aware -- the true held-window cost; "hold current" is a fair candidate).
    """
    n_epd = (n_experts + n_red) // ep
    alpha = tw_scale * BYTE_COST * n_iter * n_layers / (60.0 * interval)

    def placer(window, cur):
        w = window.astype(np.float64)
        mean = w.mean(0)
        std = w.std(0)
        risks = {lam: mean + lam * std for lam in lambdas}
        wsub = w[::hp_stride]                              # iterations for held-par scoring
        deploy = cur.copy()
        changed = []
        for l in range(n_layers):
            def proxy(d):
                return (held_par(wsub[:, l], d, n_experts) if score == "heldpar"
                        else fb._par(mean[l], d))
            best_cost = proxy(cur[l])                      # HOLD: churn 0
            best_d = None
            for lam, risk in risks.items():
                for builder in ("mc", "full"):
                    d = (minchurn_layer(risk[l], cur[l], ep, n_epd) if builder == "mc"
                         else fb._build_layer(risk[l], ep, n_epd))
                    churn = int((d != cur[l]).sum())
                    if churn == 0:
                        continue
                    cost = proxy(d) + alpha * churn
                    if cost < best_cost - 1e-12:
                        best_cost = cost
                        best_d = d
            if best_d is not None:
                deploy[l] = best_d
                changed.append(l)
        if not changed:
            return False, np.array([], dtype=np.int64), deploy
        return True, np.array(changed, dtype=np.int64), deploy
    return placer


def main():
    fb = load_fb()
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    log = []

    def emit(s):
        print(s, flush=True)
        log.append(s)

    for model, (n_layers, n_experts) in MODELS.items():
        hot = load_trace(model)
        n_iter = len(hot)
        emit(f"\n=== {model} / LmSys  interval={INTERVAL}  (n_iter={n_iter}) ===")
        emit(f"  {'EP':>4} {'method':16} {'meanPAR':>9} {'transmit':>10} "
             f"{'tx_s':>7} {'total_s':>8} {'score':>7}")
        for ep in EP_GRID[model]:
            n_red = ep
            base_par, base_tx = forward(hot, n_layers, n_experts, ep, n_red,
                                        INTERVAL, make_dseplb(ep), False)
            base_time = modeled_runtime_seconds(base_par, base_tx)
            methods = [
                ("finance_band", make_fb(fb, ep, n_red)),
                ("bag_mean", make_bag(fb, ep, n_red, n_experts, n_layers, n_iter,
                                      INTERVAL, score="mean")),
                ("bag_heldpar", make_bag(fb, ep, n_red, n_experts, n_layers, n_iter,
                                         INTERVAL, score="heldpar")),
                ("bag_hp_tw0.5", make_bag(fb, ep, n_red, n_experts, n_layers, n_iter,
                                          INTERVAL, score="heldpar", tw_scale=0.5)),
            ]
            emit(f"  {ep:>4} {'DS-EPLB(base)':16} {base_par:9.4f} {base_tx:10d} "
                 f"{transmission_time_seconds(base_tx):7.2f} {base_time:8.2f} {100.0:7.2f}")
            for name, placer in methods:
                mp, tx = forward(hot, n_layers, n_experts, ep, n_red,
                                 INTERVAL, placer, False)
                total = modeled_runtime_seconds(mp, tx)
                score = 100.0 * base_time / total
                rows.append((model, ep, name, mp, tx, total, score))
                emit(f"  {ep:>4} {name:16} {mp:9.4f} {tx:10d} "
                     f"{transmission_time_seconds(tx):7.2f} {total:8.2f} {score:7.2f}")

    with open(OUT / "bag_sweep.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model", "ep", "method", "mean_par", "transmit", "total_s", "score"])
        w.writerows(rows)
    (OUT / "bag_sweep.log").write_text("\n".join(log) + "\n")
    # quick verdict vs finance_band
    emit("\n--- bag vs finance_band (score delta, interval=1024) ---")
    by = {}
    for model, ep, name, mp, tx, total, score in rows:
        by[(model, ep, name)] = score
    for model, (_, _) in MODELS.items():
        for ep in EP_GRID[model]:
            fbs = by[(model, ep, "finance_band")]
            bags = by[(model, ep, "bag_heldpar")]
            emit(f"  {model:6} EP{ep:<4} finance_band={fbs:6.2f}  bag_heldpar={bags:6.2f}  "
                 f"delta={bags - fbs:+.2f}")


if __name__ == "__main__":
    main()
