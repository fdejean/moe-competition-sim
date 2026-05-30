# The Journey: finance_band 111.27 → cov_iter_dro 114.76

*Every experiment we ran, in plain language — what we tried, why, whether it
worked, and what it taught us. Read top to bottom; it's a story, not a reference.*

---

## The rules of the game (so the rest makes sense)

- A layer has **N experts** (256 for DeepSeek-R1, 128 for Qwen3). They're spread
  across **EP** GPUs (EP = 32, 64, 128, or 256). We also get a budget of **spare
  copies** ("replicas") of hot experts.
- We're handed a **recent window** of how busy each expert was, and we output a
  **placement**: which expert (and which copies) live on which GPU.
- We're scored on **PAR** = busiest-GPU-load ÷ average-GPU-load (1.0 = perfect),
  plus a small **transmit** cost for every expert we *move* since last time.
- `score = 100 × DeepSeek's modeled time ÷ our modeled time`, averaged over 25
  cases. **100 = tie with DeepSeek's balancer (DS-EPLB).**
- **PAR matters ~6000× more than transmit.** A 0.01 PAR drop ≈ 0.6s; one expert
  move ≈ 0.0001s.
- **THE VILLAIN: the time budget.** We get only `~3× DeepSeek's compute time` per
  decision (later relaxed to ~6×). DeepSeek runs on GPU; we run on CPU. Go over and
  the *entire run* is floored to **−1,000,000**. This single rule killed more good
  ideas than anything else.

---

## Act 1 — finance_band (111.27): *holding still beats hustling*

**The idea.** Treat each expert's load like a noisy stock price. DeepSeek's balancer
re-optimizes the whole placement every cycle — like a day-trader churning the
portfolio. We instead draw a **no-trade band** around each expert: only re-place it
if its load moved *past a threshold*. Small wiggles are ignored.

**Why it works.** Two GPUs are equally balanced whether you reshuffle or not — but
reshuffling costs transmit. DeepSeek's transmit is enormous (it moves ~840k–150k
slots); ours collapses to ~15k because we mostly *hold*. Same PAR, a fraction of the
moving cost. **That gap is the entire 100 → 111 jump.** Balance was never our edge
here; *not churning* was.

**Lesson:** transmit looks "6000× cheaper than PAR" per unit, but DeepSeek spends so
much of it that simply *not* spending it is worth 11 points.

---

## Act 2 — the free lunch: cut transmit at zero PAR cost (→ 111.8)

Once we're holding, can we cut the *remaining* transmit without hurting balance? Yes
— because two operations are **PAR-neutral**:

- **Relabel the GPUs.** Which GPU you call "device 7" is arbitrary; permuting the
  labels to match last cycle's layout leaves every load identical but reduces how
  many slots "moved." → **cheap_relabel = 111.751.**
- **Keep experts in their old slot.** Within a GPU, slot order doesn't change load.
  A **stay-bias** in the placement makes the new layout resemble the old one. →
  **sticky_relabel = 111.802.**

Both are *mathematically guaranteed* not to raise PAR. Free points.

> **Dead end that taught us the budget rule:** the first version of relabel matched
> GPUs with an O(N²) loop. It **floored at EP128** — too slow. The fix was an O(N)
> dictionary match. *Rule learned: never bound work by wall-clock; bound it by a
> fixed, cheap operation count.*

We then declared this lane "converged" around 111.8. We were wrong about what came
next — but right that transmit was tapped out.

---

## Act 3 — the oracle: *packing is solved; the enemy is forecasting*

The pivotal experiment. We gave our packer **perfect foresight** — the true *next*
window's load — and measured PAR:

| | EP32 | EP64 | EP128 | EP256 |
|---|---:|---:|---:|---:|
| What we actually get | 1.24 | 1.38 | 1.62 | 2.00 |
| **With perfect foresight** | **1.01** | **1.03** | **1.04** | **1.03** |

**With foresight, the packer is near-perfect at *every* scale.** So placement/packing
is essentially a solved problem. Almost our entire loss is that we place experts on a
**stale snapshot** and the load moves underneath us. **It's a forecasting problem.**

And it gets catastrophic at scale because of **fragmentation**: at EP256, each GPU
holds only ~2 experts. With 8–16 experts per GPU (EP32) one surprise spike averages
out; with 2, a single surprise roughly *doubles* that GPU. Same forecasting error,
far bigger damage.

**The load also moves in two distinct ways:**
- **Co-spiking** (within a window): two experts that surge in the *same instant* land
  on the same GPU → an instantaneous peak.
- **Drift** (across windows): the popular set slowly shifts, so last window's
  placement is stale for the next.

**The case that decides everything: "Mix" on DeepSeek-R1 at high EP.** "Mix" blends
traffic sources, so its popular set is permanently shifting — the snapshot is *always*
wrong. Its PAR hits **3.42** vs ~2.0 for single-source data. All the remaining points
live here.

> **The local test was a liar.** Our offline scorer only ran the *easiest* case
> (LmSys, EP32) and even truncated the traces — so everything clustered at ~100 and it
> ranked our best ideas *worst*. From here on, the live Codabench server was the only
> honest signal.

---

## Act 4 — breaking the "PAR is saturated" myth (→ 112.5)

For a while every attempt to lower PAR failed, so we concluded PAR was saturated at
~1.69. **That was an artifact of the time budget, not a real floor.** The smarter
placement only *timed out at EP256* — at EP32/64 it ran comfortably.

**The unlock — EP-adaptive dispatch:** use the expensive-but-better method exactly
where the per-case budget allows it, and fall back to the cheap method where it
doesn't.

- **epadaptive_algo = 112.079** — co-spiking-aware placement at EP≤64, fast finance
  at EP≥128. **First time PAR fell below 1.692.**
- **epadaptive_ep128 = 112.364** — pushed the threshold up to EP≤128 (it only floored
  at EP256).
- **epadaptive_k4 = 112.522** — and we found *coarse* temporal blocks (k=4) beat fine
  ones (k=32 overfit the window's noise).

**Lesson:** "saturated" usually means "too slow where it would help." Spend compute
adaptively.

---

## Act 5 — co-spiking placement: peakblock (→ 114.1)

How do you actually *use* co-spiking? Not by reordering experts cleverly (we tried —
see graveyard), but by **adding scenario columns to the balancing objective.**

The packer normally balances *average* load. We instead make it balance several
**scenarios** at once: a few temporal sub-windows **plus a "joint-peak" column** —
the average load during the top-10% busiest iterations (the moments experts spike
*together*). The packer must keep *every* scenario level, so it refuses to co-locate
experts that spike together.

- **peak_balance = 112.159** — joint-peak as the placement target.
- **cospike_hybrid = 113.189** — greedy block placement (EP-gated).
- **peak_cospike = 113.895**, **peak_cospike_k4 = 113.995** — the two compounded.
- **peakblock = 114.132** — joint-peak as an *extra block column* (cleanest form).

Realized PAR fell 1.696 → 1.663. The "saturated" claim was fully dead.

**Lesson:** the winning lever was always *which scenarios* the packer balances
against — not the packing algorithm, not the objective shape (L2 / dot-product both
lost; plain max-the-worst is right).

---

## Act 6 — drift_router (114.181): *lean recent only when it's really drifting*

peakblock still places on the *whole* window's average. On the non-stationary "Mix"
cases that average is stale. So per layer we **measure drift** — how far the recent
quarter-window differs from the full window — and blend the target toward recent data
*in proportion to that drift*.

The beauty: on **stationary** data, drift ≈ 0, so it becomes *exactly* peakblock and
can't regress the cases we already win. On **Mix**, it leans recent and tracks the new
regime. **= 114.181**, the forecasting lever finally paying off on the real server.

> Note: synthetic drift tests locally looked neutral — but the server (which has the
> real Mix cases) said it helped. Trust the oracle.

---

## Act 7 — replication as insurance: dro_ambiguity (114.32)

New axis entirely. So far we tuned *where* experts go. Now: *how many copies* each hot
expert gets. A replica splits an expert's load across GPUs — it's a **hedge against a
surprise spike.** So size the hedge for the *worst case*, not the average.

**dro_ambiguity** sizes replica counts by **mean + 3·std** (a worst-case "ambiguity"
estimate), while keeping the *placement* risk gentler at mean + 2·std — the two are
**decoupled**. = **114.32.**

> We tried the distribution-free version (size copies by empirical worst-10% load,
> "CVaR") — **dro_cvar = 114.253**, slightly *worse*. The simple mean+κ·std formula was
> already near-optimal on this axis. Replication sizing tapped out here.

---

## Act 8 — the 2-slot endgame: cov_match → cov_iter (114.35 → 114.61)

At **EP256 each GPU holds exactly 2 experts.** That changes the problem: placement *is*
a **pairing** problem, and a GPU's load = the sum of its two experts. A GPU's peak is
lowest when its two experts are **anti-correlated** — when one spikes, the other is
quiet (variance of a sum: `var(a)+var(b)+2·cov(a,b)`; make the covariance negative).

- **cov_match = 114.35** — a cheap, fully-vectorized version: find the dominant
  "co-spiking axis" of the window (a few power iterations), score each expert by its
  phase on that axis, then fold heavy-with-light and **pair opposite phases.** O(N log
  N), no risky loop.
- **cov_iter = 114.61** — once the budget was relaxed to ~6×, we could afford the
  *real* objective instead of the cheap proxy: for each light expert (heaviest first),
  greedily pick the GPU whose fixed heavy partner makes the **combined peak**
  `max_t(heavy[t] + light[t])` smallest. Directly minimizes co-spiking. This had
  *floored* under the old 3× budget — pure speed, not merit.

**Lesson:** the endgame at high EP is matching, and the budget relaxation is what let
the honest matching run.

---

## Act 9 — the winner: cov_iter_dro (114.76)

The two best ideas are **orthogonal**:
- **cov_iter** decides *where the two experts on each GPU pair up* (placement).
- **dro_ambiguity** decides *how many copies each hot expert gets* (replication).

They touch different decisions, so they don't interfere — stacking them compounds.
(We'd already confirmed the stack adds ~+0.04 on the weaker cov_match base.) Applied to
the stronger cov_iter base:

> **cov_iter_dro = 114.76 — current best.**

Recipe, end to end: drift-aware recency target + joint-peak scenario columns + temporal
block placement at EP≤128; **greedy min-peak pairing at EP256**; replica counts sized
**mean + 3·std**; all wrapped in the no-trade band + PAR-neutral relabel.

---

## The graveyard (good ideas that died — and why)

Judges love this slide. Every one taught us something.

| Idea | Result | Why it died |
|---|---|---|
| Re-balance more often / smaller no-trade band | worse | transmit explodes; load is persistent, chasing wiggles overshoots |
| Shorter window (track drift) | worse PAR | persistence beats recency; short window = noise |
| EWMA / recency weighting (stationary) | neutral | load is persistent; nothing to gain on stationary data |
| Per-expert tail replication (p95/p99) | won locally, **lost on server** | local proxy was non-predictive; only the *joint* peak transfers |
| Robust *location* (median / trimmed-mean base) | neutral | the regimes are genuinely unpredictable from the window |
| Alt objectives (L2, dot-product packing) | worse | "minimize the worst GPU" (L-infinity) is the right objective |
| Spectral "snake" placement | ~1–2% only | at 2 slots/GPU the peak is one expert's *own* variance, not pairwise |
| Scalar co-spiking weight | worse | a single fudge factor distorts the average balance |
| Over-replication (mean + 4·std) | worse | steals copy budget from the experts that actually peak |
| Pure joint-peak replication (all-in) | worse | over-concentrates copies on spikers, starves the rest |
| Optimal-transport / Sinkhorn packing | = greedy | with equal devices it collapses to plain greedy — no extra signal |
| Peak-iteration placement | **floored at EP256** | a Python loop over 512 slots is too slow on the server CPU |
| Any wall-clock-bounded refine/swap | **floored** | budget is per-case and tiny at high EP; bound by fixed step count instead |

---

## Meta-lessons (the transferable wisdom)

1. **The constraint is the design.** The time budget dictated everything. "Too slow
   where it helps" masquerades as "no improvement possible." Spend compute
   *adaptively* — expensive method where there's room, cheap fallback elsewhere.
2. **Measure the ceiling before optimizing.** The perfect-foresight oracle redirected
   the whole effort from packing (solved) to forecasting (the real prize).
3. **Trust the right oracle.** The convenient local test was anti-correlated with
   reality. We switched to scoring on the live server and stopped fooling ourselves.
4. **Find orthogonal levers and stack them.** Placement (where) and replication (how
   many) don't interfere — so the final win was a *stack*, not a single trick.
5. **Negative results are progress.** Half the graveyard above each eliminated a whole
   branch of the search and pointed at the next real lever.
