# MoE Load-Balancing — The Intuitive Picture

*A plain-language companion to `memo.md` and `analysis_3005.md`. Written to answer
"what are we actually doing, where are we losing, and what would actually help."*

---

## 0. The vocabulary, in plain words

These are the exact questions that kept coming up. Answers first, theory later.

### What is an "expert"? What is MoE?
A normal LLM layer pushes every token through one big feed-forward network. A
**Mixture-of-Experts (MoE)** layer instead has many small networks — the
**experts** (e.g. 128 or 256 of them) — and a tiny **router** picks just a few
(e.g. 8) to handle each token. Same quality, a fraction of the compute, because
only a slice of the model fires per token.

The catch: the router is **lopsided**. Some experts are wildly popular, others
barely used. That imbalance is the whole problem we're solving.

### What is EP?
**EP = Expert Parallelism = the number of devices (GPUs) the experts are spread
across.** EP32 means the layer's experts live on 32 devices; EP256 means 256
devices. It is just "how many GPUs are sharing this layer's experts."

Why it matters: a layer can't finish until its **slowest device** finishes. If the
popular experts all land on one GPU, that GPU is the bottleneck and everyone else
waits. So load balancing = spreading the *token load* evenly across the EP devices.

### What is EP256, and why "only 2 experts"?
DS-R1 (DeepSeek-R1) has **256 experts per layer**. At **EP256** there are 256
devices. The deployment also keeps some **redundant copies** (replicas) of hot
experts, so the total number of *slots* is bigger than 256 — about 512. Spread 512
slots over 256 devices and you get:

```
slots per device ≈ 512 / 256 ≈ 2
```

So **"only 2 experts" = each GPU holds about 2 expert slots.** That is the
*fragmented* regime, and it is exactly where we lose (see §2). With ~2 experts on a
device there is **nothing to average out a mistake**: if one of those two experts
unexpectedly spikes, that whole device's load roughly doubles and there's no third
or fourth expert to dilute it. Compare EP32: ~8–16 experts per device, so one
surprise spike is one of many — barely moves the device total. **Fragmentation
turns small forecasting errors into big imbalances.**

### What is "packing"?
**Packing = the placement step.** Given each expert's (estimated) load, decide
which device every expert and every replica sits on, so that the *maximum* device
load is as small as possible. It's a bin-packing / scheduling problem: experts are
items, devices are bins, and you want to level the tallest bin.

Our "greedy L∞ packer" (a.k.a. LPT — Longest Processing Time): sort experts
heaviest-first and drop each onto the currently-lightest device. (L∞ = the
infinity-norm = the max; we minimize the max device load.) Replicas of a hot expert
split that expert's load across the devices that hold a copy.

### The oracle result — what it actually means
We ran an experiment (`experiments/oracle_gap.py`, `ceiling_decomp.py`) that asks:
*if the packer magically knew the TRUE next-window load (perfect foresight), how
good would balance get?* The answer was the most important result of the project:

| EP   | realized PAR (what we get) | oracle PAR (perfect foresight) | forecasting gap |
|-----:|---------------------------:|-------------------------------:|----------------:|
| 32   | 1.235                      | 1.011                          | +0.225          |
| 64   | 1.383                      | 1.032                          | +0.351          |
| 128  | 1.616                      | 1.035                          | +0.581          |
| 256  | 1.998                      | 1.025                          | **+0.973**      |

*(DS-R1 LmSys. PAR = max-device-load ÷ mean-device-load, averaged over layers.
**1.0 = perfect balance**, higher = worse. Qwen3 shows the same shape: gap +0.27
@EP32 → +0.77 @EP128.)*

**Read it like this:** with perfect foresight the packer is nearly perfect at
*every* EP, even EP256 (1.025). So **the packer is not the bottleneck.** Almost the
entire gap between what we get (1.24–2.00) and perfect (~1.0) is **forecasting
error** — the cost of placing experts based on a *stale* window and then *holding*
that placement while the real load drifts away underneath it.

This reframes everything. All the clever placement machinery we built (peakblock,
drift_router: 111 → 114) was attacking forecasting error *indirectly* — making one
static placement survive across time. It worked, but it's chipping at a wall whose
real size is the **forecasting gap of +0.97 PAR at EP256**, versus only ~0.02–0.05
recoverable from smarter packing. We've been sanding the small gap and barely
touching the big one.

---

## 1. What the current work does (the simple version)

The simulator replays **real inference traces** (per-iteration, per-layer,
per-expert token counts) for two models (Qwen3, DS-R1) over several datasets
(ShareGPT, WildChat, LmSys, a held-out Mix) at several EP sizes (32/64/128/256).

Every `collection_interval` (1024) iterations:
1. You're handed a **window** = recent per-expert token counts, shape (layers ×
   experts).
2. You output a **deployment**: expert (+ replicas) → device for every layer.
3. The simulator charges you for **imbalance** (the slow device) plus a small
   **transmit cost** for every expert you moved since last time.

**Score** = `100 × (DS-EPLB modeled time) / (your modeled time)`, averaged over all
25 cases. Above 100 = you beat DeepSeek's EPLB baseline.

`modeled_time = 60·mean_PAR + transmit · 88_080_384 / 9e11`. **PAR dominates ~6000:1
over transmit** — a 0.01 PAR improvement is worth ~0.6s; one expert move costs
~0.0001s. So: balance is almost everything; churn is nearly free but pointless
unless it improves balance.

### The architecture lineage
- **hot_expert** (upstream baseline): replicate the hottest experts, greedy fill.
- **finance_band** (111.27): treat each expert's load like a noisy asset return; use
  a **no-trade band** — only re-place an expert if its load moved more than a
  threshold. Stabilizes placement, kills pointless churn (its real edge over
  DS-EPLB is *low transmit*).
- **peak_cospike / peakblock** (113.9 → 114.13): the big jump. Don't place on *mean*
  load — build several **scenario columns** capturing **co-spiking** (experts that
  light up together in the same iteration) and balance the worst block, refusing to
  co-locate co-spikers. Attacks per-iteration *variance*, not the average. Gated to
  EP≤128 (the block-build is too slow at EP256).
- **drift_router** (114.181, current SOTA): peakblock + **drift-adaptive recency** —
  lean the target toward more recent windows when drift is detected.

### The hard wall we keep hitting
The per-case time budget is only **3× the DS-EPLB time for that case**, and DS-EPLB
runs on GPU (torch) while we run on CPU (numpy), so on the server it's *tight* at
EP128/256. Anything iterative (refine / relabel / local search, O(nd²)) **times out
and floors the whole run to −1,000,000**. Only **single-build + band** survives. So
we can't just "compute harder" at high EP — the place we most need to be smart is
the place we're least allowed to spend time.

---

## 2. Where we're losing (the breakpoints)

### Breakpoint #1 — EP size is the dominant axis.
Losses scale almost entirely with EP. The forecasting gap is **4–10× bigger than
the packing gap** and grows *super-linearly* — it roughly **doubles each EP step**:

```
EP32   +0.22
EP64   +0.35
EP128  +0.58
EP256  +0.97   <- the cliff
```

Cause: fragmentation (§0). At high EP each device holds ~2 experts, so a single
mis-forecast spikes a device with nothing to average it down.

### Breakpoint #2 — the failure mode is *time*, not *space*.
We are not losing because we can't pack a snapshot well (the oracle proves we can).
We lose because the placement is **static across a window in which load moves**.
Two flavors of "load moves":
- **Co-spiking** (within the window): experts that surge in the *same iteration*
  land on the same device → instantaneous peak. peakblock attacks this.
- **Drift** (across windows): the popularity distribution shifts, so a placement
  tuned to the last window is stale for the next. drift_router attacks this.

Both are symptoms of the same disease: *one frozen placement can't track load that
co-fluctuates and drifts.*

### Breakpoint #3 — the dataset that fails is **Mix (DS-R1)**.
The server grader dump (`extra/prediction_result_2/metrics.json`, our finance_band
run ≈ 111.25) gives the real per-case PAR. Three of the four datasets are nearly
identical and tame; **Mix is the outlier on every axis**:

| dataset  | model | EP32 | EP64 | EP128 | EP256 |
|----------|-------|-----:|-----:|------:|------:|
| LmSys    | DS-R1 | 1.24 | 1.38 | 1.62  | 2.00 |
| ShareGPT | DS-R1 | 1.24 | 1.39 | 1.63  | 2.04 |
| WildChat | DS-R1 | 1.22 | 1.37 | 1.59  | 1.96 |
| **Mix**  | DS-R1 | **1.63** | **2.02** | **2.60** | **3.42** |
| LmSys    | Qwen3 | 1.29 | 1.52 | 1.84  | — |
| ShareGPT | Qwen3 | 1.30 | 1.54 | 1.86  | — |
| WildChat | Qwen3 | 1.29 | 1.51 | 1.81  | — |

**Mix · DS-R1 · EP256 is the single worst case at PAR 3.42** (vs ~2.0 for the
single-source datasets) and burns ~4× the transmit (143k vs 34k at EP256). Why:
**Mix is a non-stationary *mixture* of sources.** The load distribution drifts
within and across windows, so the trailing-window forecast is permanently stale,
the no-trade band never settles (→ transmit blow-up), and the placement is always
chasing load that already moved (→ PAR blow-up). This is exactly the forecasting
failure the oracle predicts — and it's the regime that decides the score.

### Breakpoint #4 — the budget edge compounds it at high EP.
The EP128 block-build (~0.25s) sits right at 3×-DS-EPLB for `Mix_DS-R1_EP128` and
floors stochastically; block placement is therefore gated to EP≤128 and **EP256
falls back to scalar mean-packing — the dumbest packer, in the regime with the most
headroom.** A single −1e6 floor wipes the entire run, so we trade away high-EP
cleverness for safety precisely where cleverness is worth the most.

### We *win* vs DS-EPLB on the easy cases — the score is decided at the hard end.
Per-case scores vs the DS-EPLB baseline (LmSys, where the baseline is cached): every
LmSys case **wins** (105–115), and the win *grows* with EP — not because our PAR is
great there, but because **DS-EPLB churns enormously** (EP256 transmit 147k vs our
34k) and our no-trade band cuts transmit. At Qwen3·EP32 our PAR *equals* the
baseline (1.289) and the entire 105.3 comes from transmit savings. So we're
comfortable and largely saturated on stationary single-source data; **all the
remaining points live in Mix and the high-EP end**, where PAR is worst, our transmit
edge shrinks, and the oracle headroom is largest (≈ +1.0 PAR).

---

## 3. Combining / ensembling the best algorithm per regime

The key realization: **different cases fail for different reasons, so one fixed
algorithm leaves points on the table.** The right structure is a **router / ensemble
that dispatches each case to the algorithm that fits its regime** — under the hard
budget constraint. (The codebase already does a primitive version: `FB_BLOCK_MAXDEV`
gates block vs scalar by EP. The proposal is to make this explicit and
two-dimensional.)

### 3a. Per-regime dispatch (what to run where, and why)

| Regime | Experts/device | Dominant problem | Best mechanism | Why |
|---|---|---|---|---|
| **EP32** | ~8–16 | almost none (gap 0.22) | plain mean-target + band | already near-solved; don't over-engineer, don't churn |
| **EP64** | ~4–8 | co-spiking | **peakblock** scenario columns | build budget affordable; variance is the issue |
| **EP128** | ~2–4 | forecasting + budget edge | **scalar, variance-aware** target | block-build floors here; must stay cheap |
| **EP256** | ~2 | forecasting (gap 0.97) | **cheap drift-robust scalar + variance-weighted replicas** | fragmentation makes every forecast error catastrophic; no time to compute |

Plus an **orthogonal** switch on *measured drift* (observable online from the
window — e.g. early-half vs late-half divergence):
- **Stationary case (e.g. LmSys)** → mean-of-window target. Chasing drift here
  *hurts* (verified: adaptive mean and shorter windows regress on stationary data).
- **Non-stationary case (Mix)** → drift-adaptive recency target (drift_router).

This is not a hyperparameter sweep — it's a **case-aware mechanism selector** keyed
on **(EP tier) × (drift detected?)**, choosing the placement target *and* the
replication policy per case.

### 3b. The replication twist (cheap, high-leverage)
Today replicas are allocated by **mean load** (replicate the hottest experts). But a
replica is really a **hedge against forecast error**: a replicated expert splits its
load across devices, so a surprise spike is halved. The hedge should go where the
surprise *risk* is — allocate replication budget by **variance / upper-tail** (e.g.
a high quantile of per-iteration load), not by mean alone. This directly attacks the
EP256 forecasting gap and costs nothing at runtime (still one build).

> ⚠️ Honest caveat from the log: per-expert tail-replication (p95/p99) *validated
> locally* (−0.012 to −0.018 realized PAR at high EP) but **did not translate on the
> server** (`epadaptive_p95` 112.0 < 112.5). The *joint*-peak version (balance the
> iterations where experts spike *together*) is what actually moved the server. So
> "variance-aware" must mean **joint/co-spike tail**, not marginal per-expert tail.

### 3c. The real ceiling play — *ensemble the forecaster, not just the placer*
The oracle says the prize (+0.97 PAR at EP256) is in **forecasting the next window**,
not in placement. Per-expert prediction is noisy, which is why single predictors
(recency blend, momentum extrapolation) gave only modest or negative gains. The
classic fix for noisy prediction is **ensembling the predictors**:

> target = average of { EWMA(short half-life), EWMA(long half-life), last-window
> value, joint-peak load } — possibly drift-weighted.

Averaging de-noises: each predictor is wrong in a different direction, and the mean
is more stable than any one. Crucially this is **O(experts)** — a few weighted sums
per window — so it **survives the budget even at EP256**, unlike any iterative
placement refinement. This is the highest-ceiling, budget-legal mechanism left, and
it attacks the gap that actually dominates the score.

> ⚠️ Caveat on *packer* ensembles: ensembling different *packers* per regime has a
> **low ceiling** (packing is already ~solved at 1.0). The valuable ensemble is over
> **forecasters + redeploy cadence**, not packers.

### Intuition, one sentence each
- **Packing** is solved — stop polishing it.
- **Co-spiking** (peakblock) and **drift** (drift_router) are two faces of the same
  "static placement can't track moving load" problem.
- The **win condition** is making the placement *robust to being wrong about the
  future* — via joint-peak / variance-aware replication and an **ensemble
  forecaster** — and doing it **cheaply enough to survive EP256's budget**.
- The **target benchmark** is unambiguous: **Mix · DS-R1 · high-EP**.

---

## 4. One-paragraph summary

We replay real MoE traces and must re-place experts on devices each window to
minimize the slowest device (PAR). We've climbed 100 → 114.18 by making a single
static placement robust to within-window co-spiking and across-window drift. But the
oracle experiment proves the packer is already near-perfect (~1.0 PAR with
foresight); ~90% of the remaining loss at EP256 is **forecasting error**, amplified
by fragmentation (only ~2 experts per device). The benchmark that decides the score
is **Mix · DS-R1 at high EP** (PAR up to 3.42), a non-stationary mixture where the
window forecast is permanently stale. The leverage is no longer in *where* we place
but in *predicting* next-window load and *hedging* against being wrong — best
delivered as a **per-regime ensemble**: peakblock at low EP, a cheap variance-aware
+ ensemble-forecast scalar at high EP, drift-adaptive only when drift is real, and
replication budget allocated by joint-peak risk. All of it must stay single-build to
survive the 3×-DS-EPLB time budget.
