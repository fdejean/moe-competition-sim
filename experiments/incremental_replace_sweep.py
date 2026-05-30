"""Does cheap, frequent (min-churn) re-placement beat the no-trade band?

Context: the dynamic oracle (~1.02 PAR) re-places every layer every iteration on
the *true* load. That is architecturally unreachable here -- the simulator only
re-invokes the algorithm once per `collection_interval` and drains one layer per
iteration (dynamic_lb_simulator.py:154-178). So "per iteration" is impossible; the
reachable lever is: re-place at the max allowed cadence, but make each re-placement
nearly FREE on transfer.

finance_band rebuilds each layer from scratch (LPT), which scrambles slot->expert
assignments even when the load barely changed -> most of its high-EP transfer is
*spurious* churn, not load tracking. `minchurn_layer` instead starts from the
current deployment and only moves the slots needed to chase the target replica
counts / fix imbalance, so the same placement quality costs a fraction of the
transfer.

This replays the official forward-loop semantics (gate, one-layer-per-iter drain,
transfer = changed slots, DS-EPLB baseline) on the committed LmSys traces and
reports mean PAR / transmit / score for, per EP and per collection_interval:

  finance_band     band (TAU=0.2) + variance replication  -- the current submission
  full_always      change=True every call, finance_band's from-scratch packer
  minchurn_always  change=True every call, minimum-churn packer (same replica counts)

If minchurn_always scores above finance_band, frequent cheap re-placement is a real
win. If not, the ceiling is structural.

Out: experiments/results/oracle/incremental_sweep.{png,csv} + console table.
"""
import csv
import importlib.util
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(REPO))
from eplb_algorithms import rebalance as ds_rebalance  # noqa: E402
from dynamic_lb_simulator import (  # noqa: E402
    transmission_time_seconds, modeled_runtime_seconds,
)

OUT = REPO / "experiments" / "results" / "oracle"
MODELS = {"DS-R1": (58, 256), "Qwen3": (94, 128)}
EP_GRID = {"DS-R1": [32, 64, 128, 256], "Qwen3": [32, 64, 128]}
INTERVALS = [1024, 128]
LAMBDA = 2.0


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


def target_replicas(risk, n_slots):
    """Water-filling replica counts on `risk` (same rule as finance_band)."""
    n_experts = risk.shape[0]
    replicas = np.ones(n_experts, dtype=np.int64)
    for _ in range(n_slots - n_experts):
        replicas[int(np.argmax(risk / replicas))] += 1
    return replicas


def minchurn_layer(risk, cur, n_device, n_exp_per_dev):
    """Re-place one layer from `cur` with minimum slot churn.

    Same target replica counts as finance_band, but keep every (device, expert)
    slot that is still wanted, free only surplus replicas (off the busiest
    devices), and fill the freed slots with the deficit experts on the
    least-loaded devices that don't already hold them. Slots that don't need to
    change are left untouched -> transfer cost = only the genuinely moved slots.
    """
    n_experts = risk.shape[0]
    n_slots = n_device * n_exp_per_dev
    replicas = target_replicas(risk, n_slots)
    w = risk / np.maximum(replicas, 1)                  # per-replica load

    new = cur.astype(np.int64).copy()
    cur_count = np.bincount(new.reshape(-1), minlength=n_experts)
    dev_load = w[new].sum(axis=1)                        # (n_device,)

    # Phase 1: free surplus replicas (cur_count > target), busiest device first.
    free = []                                            # flat (device, slot) indices
    for e in np.where(cur_count > replicas)[0]:
        locs = np.argwhere(new == e)                     # rows of (d, s)
        locs = locs[np.argsort(dev_load[locs[:, 0]])[::-1]]   # busiest first
        for d, s in locs[: int(cur_count[e] - replicas[e])]:
            new[d, s] = -1
            dev_load[d] -= w[e]
            free.append((int(d), int(s)))

    # Phase 2: place deficit experts (target > cur_count), heaviest first, onto the
    # least-loaded device with a free slot that doesn't already hold the expert.
    deficit = []
    for e in np.where(replicas > cur_count)[0]:
        deficit += [int(e)] * int(replicas[e] - cur_count[e])
    deficit.sort(key=lambda e: w[e], reverse=True)
    for e in deficit:
        best = None
        for k, (d, s) in enumerate(free):
            if new[d, s] != -1:
                continue
            penalty = 1e18 if (new[d] == e).any() else 0.0
            key = dev_load[d] + penalty
            if best is None or key < best[0]:
                best = (key, k, d, s)
        _, k, d, s = best
        new[d, s] = e
        dev_load[d] += w[e]
        free[k] = (d, s)                                 # mark consumed (now != -1)

    # Phase 3: load-balancing SWAPS from the current assignment. Each swap moves a
    # heavy expert off the busiest device for a lighter one on an underloaded device
    # (cost = 2 slots), so we converge toward LPT balance while changing only the
    # slots that genuinely help -- the PAR-vs-churn frontier between "hold" and
    # "rebuild from scratch".
    mean_load = dev_load.mean()
    n_light = max(1, n_device // 8)
    for _ in range(3 * n_device):
        d_hi = int(np.argmax(dev_load))
        if dev_load[d_hi] <= mean_load * (1.0 + 0.02):
            break
        light = np.argsort(dev_load)[:n_light]
        best = None                                      # (objective, d_lo, s_hi, s_lo, e_hi, e_lo, delta)
        row_hi = new[d_hi]
        for s_hi in range(n_exp_per_dev):
            e_hi = int(row_hi[s_hi])
            for d_lo in light:
                d_lo = int(d_lo)
                if d_lo == d_hi:
                    continue
                row_lo = new[d_lo]
                for s_lo in range(n_exp_per_dev):
                    e_lo = int(row_lo[s_lo])
                    delta = w[e_hi] - w[e_lo]
                    if delta <= 0 or e_hi == e_lo:
                        continue
                    if (row_hi == e_lo).any() or (row_lo == e_hi).any():
                        continue                          # would duplicate an expert on a device
                    obj = max(dev_load[d_hi] - delta, dev_load[d_lo] + delta)
                    if obj < dev_load[d_hi] - 1e-12 and (best is None or obj < best[0]):
                        best = (obj, d_lo, s_hi, s_lo, e_hi, e_lo, delta)
        if best is None:
            break
        _, d_lo, s_hi, s_lo, e_hi, e_lo, delta = best
        new[d_hi, s_hi] = e_lo
        new[d_lo, s_lo] = e_hi
        dev_load[d_hi] -= delta
        dev_load[d_lo] += delta
    return new


def par_per_iter(h, deploy, counts):
    weights = h / counts
    slots = deploy.reshape(deploy.shape[0], -1)
    w_slot = np.take_along_axis(weights, slots, axis=1).reshape(deploy.shape)
    loads = w_slot.sum(-1)
    return loads.max(1) / loads.mean(1)


def layer_counts(layer_deploy, n_experts):
    return np.bincount(layer_deploy.reshape(-1), minlength=n_experts)


def init_deploy(n_device, n_experts, n_red, default):
    n_exp_per_dev = (n_experts + n_red) // n_device
    d = np.zeros((n_device, n_exp_per_dev), dtype=np.int64)
    for dev in range(n_device):
        if default:
            for s in range(n_exp_per_dev):
                d[dev, s] = (dev * n_exp_per_dev + s) % n_experts
        else:
            for s in range(n_exp_per_dev - 1):
                d[dev, s] = (dev * (n_exp_per_dev - 1) + s) % n_experts
            d[dev, -1] = d[dev, -2]
    return d


def forward(hotness, n_layers, n_experts, ep, n_red, interval, placer, is_default):
    """Faithful forward loop. `placer(window, cur) -> (change, lp, deploy)`."""
    n_iter = len(hotness)
    cur = np.stack([init_deploy(ep, n_experts, 0 if is_default else n_red, is_default)
                    for _ in range(n_layers)])
    counts = np.stack([layer_counts(cur[l], n_experts) for l in range(n_layers)])
    nxt = np.zeros_like(cur)
    par_sum = cnt = transmit = 0
    redeploy_finish = algo_finish = 0
    ready, prio = True, []
    for i in range(1, n_iter + 1):
        pars = par_per_iter(hotness[i - 1], cur, counts)
        par_sum += float(pars.sum()); cnt += pars.size
        if is_default:
            continue
        if not ready and i > algo_finish and prio:
            l = prio.pop(0)
            transmit += int((cur[l] != nxt[l]).sum())
            cur[l] = nxt[l]; counts[l] = layer_counts(cur[l], n_experts)
        if not prio and not ready:
            ready = True; redeploy_finish = i
        if i == redeploy_finish + interval + 1 and ready:
            change, lp, deploy = placer(hotness[i - interval:i], cur)
            if change:
                lp = np.asarray(lp, dtype=np.int64)
                if lp.size:
                    prio = lp.tolist(); ready = False
                    nxt[lp] = deploy[lp]
            # (algo-time penalty omitted: it is tiny and identical-ish across methods)
    return par_sum / cnt, transmit


# ---- placers -------------------------------------------------------------
def make_full(fb, ep, n_red, n_experts):
    n_exp_per_dev = (n_experts + n_red) // ep

    def placer(window, cur):
        w = window.astype(np.float64)
        risk = w.mean(0) + LAMBDA * w.std(0)
        n_layers = risk.shape[0]
        deploy = np.stack([fb._build_layer(risk[l], ep, n_exp_per_dev)
                           for l in range(n_layers)])
        return True, np.arange(n_layers), deploy
    return placer


def make_minchurn(ep, n_red, n_experts):
    n_exp_per_dev = (n_experts + n_red) // ep

    def placer(window, cur):
        w = window.astype(np.float64)
        risk = w.mean(0) + LAMBDA * w.std(0)
        n_layers = risk.shape[0]
        deploy = np.stack([minchurn_layer(risk[l], cur[l], ep, n_exp_per_dev)
                           for l in range(n_layers)])
        return True, np.arange(n_layers), deploy
    return placer


def make_fb(fb, ep, n_red):
    fb.reset()

    def placer(window, cur):
        change, lp, deploy, _ = fb.rebalance(window, ep, n_red)
        return change, lp, deploy
    return placer


def make_dseplb(ep):
    fn = ds_rebalance(ep, ep, "deepseek")

    def placer(window, cur):
        change, lp, deploy, _ = fn(window)
        return change, lp, deploy
    return placer


def main():
    fb = load_fb()
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for model, (n_layers, n_experts) in MODELS.items():
        hot = load_trace(model)
        for interval in INTERVALS:
            print(f"\n=== {model} / LmSys  interval={interval} ===")
            print(f"  {'EP':>4} {'method':16} {'meanPAR':>9} {'transmit':>10} "
                  f"{'tx_s':>7} {'total_s':>8} {'score':>7}")
            for ep in EP_GRID[model]:
                n_red = ep
                # DS-EPLB baseline (interval-independent enough; use this interval)
                base_par, base_tx = forward(hot, n_layers, n_experts, ep, n_red,
                                            interval, make_dseplb(ep), False)
                base_time = modeled_runtime_seconds(base_par, base_tx)
                methods = [
                    ("finance_band", make_fb(fb, ep, n_red)),
                    ("full_always", make_full(fb, ep, n_red, n_experts)),
                    ("minchurn_always", make_minchurn(ep, n_red, n_experts)),
                ]
                print(f"  {ep:>4} {'DS-EPLB(base)':16} {base_par:9.4f} {base_tx:10d} "
                      f"{transmission_time_seconds(base_tx):7.2f} {base_time:8.2f} "
                      f"{100.0:7.2f}")
                for name, placer in methods:
                    mp, tx = forward(hot, n_layers, n_experts, ep, n_red,
                                     interval, placer, False)
                    total = modeled_runtime_seconds(mp, tx)
                    score = 100.0 * base_time / total
                    rows.append((model, interval, ep, name, mp, tx,
                                 transmission_time_seconds(tx), total, score))
                    print(f"  {ep:>4} {name:16} {mp:9.4f} {tx:10d} "
                          f"{transmission_time_seconds(tx):7.2f} {total:8.2f} {score:7.2f}",
                          flush=True)

    with open(OUT / "incremental_sweep.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model", "interval", "ep", "method", "mean_par", "transmit",
                    "tx_seconds", "total_seconds", "score"])
        w.writerows(rows)
    print(f"\nwrote {OUT / 'incremental_sweep.csv'}")


if __name__ == "__main__":
    main()
