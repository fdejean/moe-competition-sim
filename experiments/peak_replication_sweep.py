"""Move B: peak-aware REPLICATION (not placement) for the EP256 cliff.

Both teams' oracle says realized PAR at EP256 (~2.0) vs oracle (~1.02) is a
forecasting/holding gap; at 2 slots/device you cannot *place* your way out. The
peakblock line found co-spiking *placement* buys ~nothing at EP256 -- but it never
allocated REPLICAS by the co-spike. Physically, the only way a static placement
absorbs a co-spike at 2 slots/device is to hold MORE COPIES of the co-spiking
expert and split it across devices.

So: water-fill the redundant slots by a peak-aware load
    repl_w = (1-THETA)*mean + THETA*peak + LAMBDA*std
where peak[e] = mean load of expert e over the top-PEAKFRAC iterations ranked by
TOTAL layer load (the joint co-spike moments). Placement still balances `place_w`
(default mean -- their finding that peak *placement* is neutral). build_layer2
decouples the two so we can isolate the replication effect.

Faithful forward-loop sweep (interval=1024, the proven live cadence), LmSys, all EP.
THETA=0 reproduces finance_band (sanity). Out: results/oracle/peak_repl.{csv,log}.
"""
import csv
from pathlib import Path

import numpy as np

from experiments.incremental_replace_sweep import (  # noqa: E402
    load_fb, load_trace, forward, make_dseplb, make_fb, MODELS, EP_GRID,
)
from dynamic_lb_simulator import (  # noqa: E402
    transmission_time_seconds, modeled_runtime_seconds,
)

OUT = Path(__file__).resolve().parents[1] / "experiments" / "results" / "oracle"
INTERVAL = 1024
LAMBDA = 2.0
TAU = 0.2
PEAKFRAC = 0.10


def build_layer2(repl_w, place_w, n_device, n_exp_per_dev, _par_fn=None):
    """Like finance_band._build_layer but replication uses repl_w, placement place_w."""
    n_experts = repl_w.shape[0]
    n_slots = n_device * n_exp_per_dev
    replicas = np.ones(n_experts, dtype=np.int64)
    for _ in range(n_slots - n_experts):
        replicas[int(np.argmax(repl_w / replicas))] += 1
    slot_expert = np.repeat(np.arange(n_experts), replicas)
    slot_weight = place_w[slot_expert] / replicas[slot_expert]
    order = np.argsort(slot_weight, kind="stable")[::-1]
    deploy = np.full((n_device, n_exp_per_dev), -1, dtype=np.int64)
    dev_load = np.zeros(n_device)
    dev_count = np.zeros(n_device, dtype=np.int64)
    big = float(slot_weight.sum()) + 1.0
    for idx in order:
        e = slot_expert[idx]
        score = dev_load + np.where((deploy == e).any(axis=1), big, 0.0)
        score[dev_count >= n_exp_per_dev] = np.inf
        d = int(np.argmin(score))
        deploy[d, int(dev_count[d])] = e
        dev_load[d] += slot_weight[idx]
        dev_count[d] += 1
    return deploy


def peak_load(window, peakfrac):
    """peak[l,e] = mean load over the top-`peakfrac` iters by TOTAL layer load."""
    w = window
    T, L, E = w.shape
    tot = w.sum(2)                                  # (T, L) total load per layer/iter
    k = max(1, int(peakfrac * T))
    idx = np.argpartition(tot, -k, axis=0)[-k:]     # (k, L)
    peak = np.empty((L, E))
    for l in range(L):
        peak[l] = w[idx[:, l], l, :].mean(0)
    return peak


def make_peakrepl(fb, ep, n_red, n_experts, n_layers, theta, peak_in_place):
    """finance_band's band, but replicas water-filled on a peak-aware load."""
    n_epd = (n_experts + n_red) // ep
    state = {"deploy": None, "first": True}

    def placer(window, cur):
        w = window.astype(np.float64)
        mean = w.mean(0)
        std = w.std(0)
        if theta > 0:
            peak = peak_load(w, PEAKFRAC)
            blend = (1 - theta) * mean + theta * peak
        else:
            blend = mean
        repl_w = blend + LAMBDA * std
        place_w = (blend + LAMBDA * std) if peak_in_place else (mean + LAMBDA * std)
        cand = np.stack([build_layer2(repl_w[l], place_w[l], ep, n_epd)
                         for l in range(n_layers)])
        if state["first"]:
            state["first"] = False
            state["deploy"] = cand.copy()
            return True, np.arange(n_layers, dtype=np.int64), cand
        base = state["deploy"]
        improve = np.array([fb._par(mean[l], base[l]) - fb._par(mean[l], cand[l])
                            for l in range(n_layers)])
        sel = np.where(improve > TAU)[0]
        if sel.size == 0:
            return False, np.array([], dtype=np.int64), cand
        order = sel[np.argsort(improve[sel])[::-1]]
        nd = base.copy(); nd[order] = cand[order]; state["deploy"] = nd
        return True, order.astype(np.int64), cand
    return placer


def main():
    fb = load_fb()
    OUT.mkdir(parents=True, exist_ok=True)
    rows, log = [], []

    def emit(s):
        print(s, flush=True); log.append(s)

    for model, (n_layers, n_experts) in MODELS.items():
        hot = load_trace(model)
        emit(f"\n=== {model} / LmSys  interval={INTERVAL} ===")
        emit(f"  {'EP':>4} {'method':18} {'meanPAR':>9} {'transmit':>10} "
             f"{'total_s':>8} {'score':>7}")
        for ep in EP_GRID[model]:
            n_red = ep
            base_par, base_tx = forward(hot, n_layers, n_experts, ep, n_red,
                                        INTERVAL, make_dseplb(ep), False)
            base_time = modeled_runtime_seconds(base_par, base_tx)
            methods = [
                ("finance_band", make_fb(fb, ep, n_red)),
                ("peakrepl0(=fb)", make_peakrepl(fb, ep, n_red, n_experts, n_layers, 0.0, False)),
                ("peakrepl.3", make_peakrepl(fb, ep, n_red, n_experts, n_layers, 0.3, False)),
                ("peakrepl.5", make_peakrepl(fb, ep, n_red, n_experts, n_layers, 0.5, False)),
                ("peakrepl.7", make_peakrepl(fb, ep, n_red, n_experts, n_layers, 0.7, False)),
                ("peakboth.5", make_peakrepl(fb, ep, n_red, n_experts, n_layers, 0.5, True)),
            ]
            emit(f"  {ep:>4} {'DS-EPLB(base)':18} {base_par:9.4f} {base_tx:10d} "
                 f"{base_time:8.2f} {100.0:7.2f}")
            for name, placer in methods:
                mp, tx = forward(hot, n_layers, n_experts, ep, n_red,
                                 INTERVAL, placer, False)
                total = modeled_runtime_seconds(mp, tx)
                score = 100.0 * base_time / total
                rows.append((model, ep, name, mp, tx, total, score))
                emit(f"  {ep:>4} {name:18} {mp:9.4f} {tx:10d} {total:8.2f} {score:7.2f}")

    with open(OUT / "peak_repl.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model", "ep", "method", "mean_par", "transmit", "total_s", "score"])
        w.writerows(rows)
    (OUT / "peak_repl.log").write_text("\n".join(log) + "\n")
    emit("\n--- best peakrepl vs finance_band (score delta) ---")
    by = {}
    for model, ep, name, mp, tx, total, score in rows:
        by.setdefault((model, ep), {})[name] = score
    for (model, ep), d in by.items():
        fbs = d["finance_band"]
        best = max((v, k) for k, v in d.items() if k.startswith("peak"))
        emit(f"  {model:6} EP{ep:<4} fb={fbs:6.2f}  best={best[1]}={best[0]:6.2f}  "
             f"delta={best[0] - fbs:+.2f}")


if __name__ == "__main__":
    main()
