# Slide text — Dynamic Expert Placement (114.76)

*Copy-paste into your deck. Each slide has two layers:*
***▣ ON SLIDE** = the credible/technical bullets the professors read.*
***🗣 PLAIN** = the intuition — what it actually means, for you to say out loud and understand.*

---

## SLIDE 1 — Title

**▣ ON SLIDE**
- **Dynamic Expert Placement as Robust Makespan Minimization**
- Mixture-of-Experts inference · scored against DeepSeek-EPLB · **100 = parity → we reach 114.76**
- *(your names / team)*

**🗣 PLAIN**
> We took DeepSeek's own load-balancing algorithm and beat it by ~15%.

---

## SLIDE 2 — The problem

**▣ ON SLIDE**
- MoE layers: 256 experts/layer, a router sends each token to a few. Experts are sharded across devices (expert parallelism, EP 32→256).
- A layer finishes only when its **slowest device** finishes. Hot experts colliding on one device = bottleneck.
- We don't train the model or touch the router. **Given expert-hotness traces, we choose the physical placement** (which expert + replicas on which device), re-deciding each window.
- Metric: **PAR = max device load / mean device load.** 1.0 = perfect. (PAR ≈ 97% of score.)

**🗣 PLAIN**
> Each layer has 256 little "expert" sub-networks spread over GPUs. Some experts are way more popular than others. If the popular ones pile onto one GPU, that GPU chokes and everyone waits for it. Our only job: decide which expert lives on which GPU, and re-decide as popularity shifts. The score is just "how overloaded is the busiest GPU vs the average."

---

## SLIDE 3 — The reframe (this is the contribution)

**▣ ON SLIDE**
- Replicated load splits evenly ⇒ **mean device load is placement-invariant** ⇒ minimizing PAR ≡ **makespan minimization**.
- But the grader averages PAR **over iterations**, while we commit **one** placement per window. So the true objective is the **expectation of a max**, not the max of an expectation:
  $$\min_\pi\ \mathbb{E}_t\big[\max_d L_d(x_t)\big]\quad\ne\quad \min_\pi\ \max_d\ \mathbb{E}_t[L_d]$$
- The Jensen gap = **within-device cross-expert covariance** (experts that spike in the *same* iteration — "co-spiking").
- DeepSeek optimizes max-of-mean. **We build surrogates for the expectation-of-max.**

**🗣 PLAIN**
> DeepSeek balances each GPU's *average* load. But you're not graded on the average moment — you're graded on the *worst* moments. What hurts is two experts spiking at the **same instant** on the same GPU. On average they look fine; in that instant the GPU is on fire. **That difference is the entire competition, and it's what we go after.**

---

## SLIDE 4 — Where the points are: the oracle

**▣ ON SLIDE**
- Ceiling decomposition: give the *same* packer the **true next-window load**.

| EP | realized PAR | oracle (perfect foresight) |
|---:|---:|---:|
| 32 | 1.24 | 1.01 |
| 64 | 1.38 | 1.03 |
| 128 | 1.62 | 1.03 |
| 256 | 2.00 | **1.03** |

- With foresight, balance is near-perfect **at every scale** ⇒ **packing is solved.**
- The whole realized gap is **forecasting + static-hold error**, worst on non-stationary `Mix·DS-R1·EP256` (PAR 3.4). **It's a prediction problem, not a packing problem.**

**🗣 PLAIN**
> We ran a cheat test: let the algorithm peek at the future. With perfect foresight, balance is nearly perfect everywhere. So the packing isn't the issue — we're just **guessing the future from stale history**, and the load keeps moving under us. That reframes the whole problem as forecasting.

---

## SLIDE 5 — What we built: surrogates for E[max]

**▣ ON SLIDE**
- **① Multi-scenario L∞ packing.** LPT greedy where each slot carries a *vector* of scenario loads (temporal sub-windows + a joint-peak column = expert profile conditioned on the busiest iterations). Greedy refuses to co-locate co-spiking experts.
- **② Drift-gated recency.** A relative-drift statistic gates a blend toward recent windows — **identically zero on stationary data**, so we never regress cases we already win.
- **③ Robust replication.** Replica counts water-filled on **μ + 3σ** (one-sided Cantelli bound) — decoupled from the gentler placement target. A copy is a coarse committed hedge, so size it for the bad case.
- **EP-adaptive dispatch:** expensive method where the time budget allows (EP≤128), fast scalar where it doesn't (EP256). *"Saturated" was a timeout artifact, not a real ceiling.*

**🗣 PLAIN**
> Three tricks, each attacking spikes a different way: **(1)** make the packer balance the *worst moments*, not just averages, so it splits up experts that spike together; **(2)** when traffic *shifts*, lean on recent history — but only when a shift is actually detected; **(3)** when making backup copies of hot experts, size them for the bad case, like insurance. And: use the slow-but-smart method only where we can afford it; use a fast one at the biggest scale so it never times out.

---

## SLIDE 6 — The EP256 endgame + the stack

**▣ ON SLIDE**
- At EP256, **2 slots/device** ⇒ placement collapses to a **cardinality-2 matching**. Device load = sum of two series; **var(h+ℓ) minimized by pairing anti-correlated experts.**
- Greedy min–max matching on the realized peak: fix heavy slots, match each light slot to minimize max over time. (Exact greedy floored at 3× budget; feasible at 6×.)
- **The winner stacks the two strongest levers** — matching sets the *assignment*, robust water-fill sets the *replica counts* — **disjoint variables ⇒ near-additive gains.**
- Wrapped in a **no-trade band** (impulse-control: hold placement unless the balance gain clears a threshold → kills DeepSeek's transmit cost) + PAR-neutral relabeling.

**🗣 PLAIN**
> At the biggest scale every GPU holds exactly 2 experts, so the game becomes "pair them up." The trick: **pair experts that spike at opposite times** — when one's hot the other's cool, so the GPU stays smooth. The final submission just combines our best moves; they don't fight because each controls a different decision (who-goes-where vs. how-many-copies). All of it sits inside a "don't reshuffle unless it's worth it" rule — that's where DeepSeek wastes time and we don't.

---

## SLIDE 7 — Result & takeaways

**▣ ON SLIDE**
- **111.3 → 114.76**, climbing via: no-trade band → PAR-neutral transmit cuts → EP-adaptive dispatch → multi-scenario L∞ → drift gating → robust replication → anti-correlated matching.
- **Three messages:**
  1. The objective is **expectation-of-max**, not max-of-expectation.
  2. The ceiling analysis says the residual is **forecasting, not packing**.
  3. The compute budget forces **single vectorized builds** — anything iterative floors at scale; the constraint *is* the design.
- Remaining headroom: forecaster ensembling at EP256 (oracle says ~+1.0 PAR is recoverable).

**🗣 PLAIN**
> We went from 111 to 114.76 by stacking these ideas. Big picture: we balance the worst moment not the average, the real bottleneck is predicting the future not packing, and every method had to be fast enough to never time out. The leftover gains are all in better forecasting.
