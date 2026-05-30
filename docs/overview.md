# MoE Competition — Overview

*The single-page orientation doc. What the competition is, how it's scored, the
constraints that actually bind, and what we've learned trying to win it. For the
plain-language deep dive read [`learnings_intuitive.md`](learnings_intuitive.md);
for the blow-by-blow submission log read [`../memo.md`](../memo.md).*

---

## 1. What the competition is

Modern large language models (DeepSeek-V2/V3 style) replace the dense
feed-forward block in most Transformer layers with a **Mixture-of-Experts (MoE)**
layer: many small expert networks (e.g. 128 or 256 per layer) plus a tiny
**router** that sends each token to only a few of them (e.g. top-8). This keeps
total model capacity huge while keeping per-token compute small.

The catch is that the router is **lopsided** — some experts are far more popular
than others. When experts are sharded across GPUs (**expert parallelism, EP**), a
layer can't finish until its **slowest device** finishes. If the popular experts
land on the same GPU, that GPU becomes the bottleneck and everyone waits.

**The competition does not train a model or change the router.** It hands you
**expert hotness traces** (how heavily each expert was used over time) and asks
you to decide the **physical placement**: where each expert — and any redundant
**replica copies** of hot experts — should live across the devices, re-deciding
periodically as hotness shifts. You are graded against DeepSeek's own placement
algorithm, **DS-EPLB**.

So three layers are cleanly separated, and we only own the third:

| Layer | Who owns it |
|---|---|
| Model architecture (how the router picks experts) | fixed, not ours |
| Runtime routing load (how often each expert fires) | given to us as traces |
| **Physical placement (where experts/replicas sit on devices)** | **what we solve** |

---

## 2. The simulator API

Every submission exposes one function:

```python
def rebalance(hotness, n_device, n_red_expert):
    # hotness: (collection_window, n_layers, n_experts)  — recent per-expert load
    # n_device:    number of expert-parallel devices (the EP size)
    # n_red_expert: number of redundant physical slots available
    return change, layers_priority, deployment_table, aux
```

- `deployment_table` has shape `(n_layers, n_device, (n_experts + n_red_expert) // n_device)`
  and maps **every layer, device, expert-slot → a logical expert id**. Repeated
  ids are replicas; a missing logical id makes the placement invalid. The shape
  must be returned **even when `change=False`** (cache and return the last good
  table).
- `layers_priority` lists which layer rows to actually apply, in order. For each
  applied layer the row is a **full replacement** of every slot, not a patch.
- The simulator calls `rebalance` only **every `collection_interval` (1024)
  iterations**, replaying real inference traces for two models (Qwen3, DS-R1) over
  several datasets (ShareGPT, WildChat, LmSys, and a held-out **Mix**) at several
  EP sizes (32/64/128/256).
- You may keep bounded **module-level state** across calls — but key it by model
  shape and EP, because the same process serves every case.

Redeployment cost is counted **slot by slot** (Hamming distance from the previous
table), so even reordering experts without changing replica counts costs transmit.

---

## 3. How it's scored

Two raw metrics per case:

- **PAR** = `max(device_load) / mean(device_load)`, averaged over layers and
  iterations. **1.0 = perfect balance**, higher = worse. (Mean device load is
  placement-invariant, so minimizing PAR ≡ minimizing the **max** device load — a
  makespan / bin-packing problem.)
- **Transmit amount** = number of expert slots that changed on redeployment.

These combine into a modeled runtime, compared against DS-EPLB on the same case:

```
modeled_time = 60.0 * mean_par + transmit_amount * 88_080_384 / 900_000_000_000
case_score   = 100 * ds_eplb_modeled_time / submission_modeled_time
score        = mean(case_score over all ~25 cases)
```

- **100 = tie with DS-EPLB. Above 100 = we're faster.**
- The 25 cases are `{LmSys, ShareGPT, WildChat, Mix} × {Qwen3, DS-R1} × {EP32, 64,
  128, 256}` (EP256 only for DS-R1).
- **PAR dominates transmit ~6000:1.** A 0.01 PAR drop ≈ 0.6 s; one expert move ≈
  0.0001 s. Optimize PAR first; churn is nearly free but pointless unless it buys
  balance.
- Any error, bad shape, or **timeout** floors the entire run to **−1,000,000**.

---

## 4. The constraints that actually bind

These were learned the hard way (each cost real submissions):

1. **The per-case time budget is `3 × DS-EPLB's time for that case` — not a flat
   wall.** DS-EPLB runs on GPU (torch); we run on CPU (numpy). On the server this
   is **tight at EP128/256**. Only **single-build + no-trade-band** code
   (~0.1–0.26 s/call) survives. **Anything iterative** — refine, relabel, local
   search, an O(n·d²) loop, a Python loop over 512 slots — **floors at high EP.**
2. **EP256 is the wall.** At EP256 each device holds only ~2 expert slots; the
   block-build and any per-slot loop time out. EP256 must stay **scalar or fully
   vectorized**.
3. **`Mix · DS-R1 · EP128/256` has an even tighter budget** than LmSys. Gate block
   placement to `EP ≤ 64` (`FB_BLOCK_MAXDEV=64`) unless the EP128 build is verified
   `< 0.15 s`; a single floor wipes the run.
4. **The local proxy is non-predictive.** `auto/eval.py` runs only LmSys-EP32 (the
   easiest, already-saturated case) and even truncates the long traces — so it
   clusters everything near 100 and is *anti-correlated* with server ranking for
   the interesting levers. **The Codabench server is the only real oracle.** Local
   eval is for catching crashes/timeouts, not for ranking.

---

## 5. The reference baselines

- **`smoke`** — valid no-op: builds the round-robin default placement, returns
  `change=False`. Just for API/packaging checks.
- **`hot_expert_baseline`** — puts each layer's hottest experts into the redundant
  slots, redeploys every layer every cycle. Helps when hot experts are persistent,
  but churns a lot (always `change=True`).
- **`DS-EPLB`** — DeepSeek's own algorithm and the **baseline to beat** (score
  100). It greedily replicates hot experts (`weight / replica_count`) and
  water-fills them onto GPUs. It balances well but **redeploys aggressively**, so
  its transmit cost is high — which is exactly the seam our best work exploits.

---

## 6. The single most important finding

We ran a **perfect-foresight oracle**: if the packer magically knew the *true*
next-window load, how good could balance get?

| EP | realized PAR (what we get) | oracle PAR (perfect foresight) | forecasting gap |
|---:|---:|---:|---:|
| 32 | 1.235 | 1.011 | +0.225 |
| 64 | 1.383 | 1.032 | +0.351 |
| 128 | 1.616 | 1.035 | +0.581 |
| 256 | 1.998 | 1.025 | **+0.973** |

**With perfect foresight the packer is near-perfect at every EP, even EP256.** So
**packing is essentially solved.** Almost the entire gap between what we get and
perfect balance is **forecasting error** — placing experts from a *stale* window
and *holding* that placement while load drifts and spikes underneath it.

Two faces of the same disease ("one frozen placement can't track moving load"):

- **Co-spiking** — experts that surge in the *same iteration* and land on the same
  device → an instantaneous peak. (Attacked by **peakblock**.)
- **Drift** — the popularity distribution shifts across windows, so a placement
  tuned to the last window is stale. (Attacked by **drift_router**.)

The benchmark that decides the score is unambiguous: **`Mix · DS-R1 · high-EP`** —
a non-stationary mixture of sources where the window forecast is permanently stale
(PAR up to 3.42 vs ~2.0 for single-source datasets, and ~4× the transmit).

---

## 7. The architecture lineage (what climbed 100 → 114.35)

| Algorithm | Server | The idea |
|---|---:|---|
| DS-EPLB (baseline) | 100 | replicate hot experts, water-fill, redeploy every cycle |
| `finance_band` | 111.27 | treat each expert's load like a noisy asset; a **no-trade band** only re-places an expert if its load moved past a threshold → kills pointless churn (its real edge is **low transmit**) |
| `cheap_relabel` / `sticky_relabel` | 111.75–111.80 | **PAR-neutral transmit cuts**: permuting device rows / keeping experts in their old slot leaves PAR unchanged but lowers the Hamming transmit charge |
| `epadaptive_k4` | 112.52 | **EP-adaptive dispatch**: use the expensive co-spiking block placement only where the budget allows (EP≤64/128), fast scalar at EP256. First break of the "PAR is saturated" myth |
| `peak_cospike` | 113.9 | balance the **joint-peak iterations** (when experts spike *together*) as scenario columns in the placement greedy, compounded with EP-gated block placement |
| `peakblock` | 114.13 | add the joint-peak load as an **extra block column** the greedy must balance (placement made spike-aware) |
| `drift_router` | 114.18 | peakblock + **drift-adaptive recency**: lean the target toward recent windows in proportion to measured drift (forecasting lever for Mix) |
| `dro_ambiguity` | 114.32 | **robust replication sizing** — size replica counts by `mean + 3·std` (worst-case ambiguity), *decoupled* from the placement-risk target (`mean + 2·std`) |
| **`cov_match`** | **114.35** | explicit **anti-correlation pairing at EP256** (the 2-slot regime): vectorized phase-fold that pairs opposite-phase experts on the same device |

**Patterns that consistently won:** adding informative **scenario columns** to the
balancing greedy (joint-peak, recent-peak); **PAR-neutral** transmit reduction;
**EP-adaptive** method dispatch; **robust replication sizing**.

**Patterns that consistently lost / died:**
- Changing the placement *objective* (L2, dot-product instead of L∞) — L∞ is right.
- Robust *location* estimators (median / trimmed-mean / EWMA base) — ~neutral.
- Per-expert tail replication (p95/p99) — validated locally, **did not translate**
  to the server; only the *joint* (co-spike) lever moves it.
- Any iterative refine / swap / relabel bounded by **wall-clock** — floored.
  (Bound by a **fixed pass count** instead.)
- Aggressive redeploying / smaller no-trade band — transmit explodes, PAR doesn't
  improve (load is persistent; chasing drift overshoots).
- Peak-iteration placement — wins big in-sample but the per-slot Python loop
  **floors at EP256**; conclusively closed on the server.

---

## 8. Where it stands

- **Final SOTA ≈ 114.35 (`cov_match`).** `dro_ambiguity` (114.32) and
  `drift_router` (114.18) are right behind.
- **Packing is solved; placement at EP256 is saturated** (2 slots/device leaves no
  freedom). The only proven EP256 lever is **replication sizing**, and the
  `mean + κ·std` formulation looks tapped out (distribution-free CVaR didn't beat
  it).
- **The remaining headroom is the forecasting gap on `Mix · DS-R1 · high-EP`**
  (oracle says ~+1.0 PAR is theoretically recoverable). It is a *prediction*
  problem, not a placement problem — and it must be solved within the EP256 budget,
  which forbids anything but a single vectorized build.
- The highest-ceiling untried direction noted in the logs: **ensemble the
  forecaster, not the placer** — blend several cheap O(experts) predictors (EWMAs
  at different half-lives, last-window, joint-peak) to de-noise the next-window
  estimate, plus **replication allocated by joint-peak risk**. Cheap enough to
  survive EP256.

---

## 9. Repo map (where things live)

- `codes/<name>/submission.py` — every algorithm variant (one folder each).
- `submissions/<name>.zip` — upload-ready zips, regenerated from `codes/`.
- `auto/` — the autoresearch loop: `eval.py` (local scorer, non-predictive),
  `NOTES.md` (append-only attempt log + every server score), `LEADERBOARD.md`,
  `novel_ideas.md` (mechanism backlog), `runs/NNN_<name>/` (frozen snapshots).
- `tools/codabench_submit.py` — headless Codabench submit/poll over the REST API.
- `memo.md` — the full narrative log with rationale for every submission.
- `docs/learnings_intuitive.md` — the plain-language companion to this file.
- `extra/` — flo's original EDA, benchmarks, and server metric dumps.
