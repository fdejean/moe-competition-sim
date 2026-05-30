# Senior-scientist sparring memo — new idea classes for MoE EPLB
*Author: vero agent #5 (idea-generation / sparring role). 2026-05-30 ~06:40.*

## The reframe the loop is missing

The loop converged at ~111.8 and declared "PAR saturated at 1.69, only transmit
is left." **That is a global-mean artifact.** Score = mean over 25 cases of
`100·ds_eplb_time/our_time`. The per-case opportunity map (in NOTES) shows:

```
DS-R1 Mix    EP256 PAR=3.42   <-- 2.4x above floor, NOT saturated
DS-R1 Mix    EP128 PAR=2.59
DS-R1 ShareGPT EP256 PAR=2.04
DS-R1 Mix    EP64  PAR=2.02
... all EP32 cases ~1.2-1.3 (these are the only "saturated" ones, and the LmSys-EP32
proxy tests ONLY these -> doubly non-predictive)
```

So 1.69 is the **mean of {1.2 easy cases ... 3.42 hard cases}**. The hard high-EP
DS-R1 cases are where almost all remaining points are, and **every fast method tried
so far either tests the wrong cases (proxy) or hurts the global mean (co-spiking
scalar). No fast method has actually cracked the hard cases.** The frontier is not
transmit — it is **robust/co-spiking-aware placement for non-stationary high-EP
DS-R1**, done in finance_band time.

Why those cases are hard (mechanism, not vibes):
- **Granularity:** EP256 -> (256+256)/256 = **2 slots/device**; EP128 -> 3. With 2-3
  slots you cannot isolate a mega-hot expert; placement is a tight bin-packing.
- **Non-stationarity:** "Mix" = a *mixture* of workloads in one window. The window
  MEAN mis-predicts per-iteration load -> realized PAR (3.42) >> mean-load PAR (~1.05).
  This is a *prediction/robustness* failure, not a packing-of-the-mean failure.
- This is exactly the co-spiking gap I measured earlier (+6-10% on easy cases; it is
  far larger here). Co-spiking attempts died on EP256 **speed**, not on the idea.

**Enabler:** peer's `082_fold_pack` hit **0.03s/call (5x faster than finance_band)**.
That reopens the per-call budget for a *cheap, vectorized* refinement the old
per-device k-block methods couldn't afford. New ideas should ride a fast base.

---

## New idea classes (all O(T·E + E log E), vectorized, EP256-safe by construction)

### 1. factor_snake — spectral co-spiking placement (FAST realization of the proven lever)
The dominant co-spiking direction = top singular vector `v1` of the centered window
matrix `W (T×E)`. Experts with same-sign large `v1[e]` spike together.
- Compute `v1` with 2-3 **power iterations** on `W^TW` (pure matvecs; ~ms).
- Build the slot list + replica counts exactly as finance_band (water-fill).
- Order slots by `v1`-loading and **boustrophedon (snake) deal** onto devices so each
  device's summed `v1` loading ≈ 0 -> devices are *anti-correlated on the dominant
  spike factor* -> per-iteration peak collapses. One sort, one pass.
This is risk-parity / Markowitz at the device level, but solved by a single
eigenvector instead of an O(n_device) iterative refine. **The co-spiking lever made
finance_band-speed.** Most promising.

### 2. regime_robust — distributionally-robust replication for "Mix"
Mix windows are mixtures of regimes; the mean under-serves whichever regime is hot
next. Split the window into k sub-windows (k=4-8), compute each expert's **max
sub-window mean** (worst-regime load), water-fill replicas on THAT. Guarantees copies
for any expert hot in ANY regime -> bounds worst-regime PAR. (Robust optimization, not
forecasting -> dodges the "prediction overshoots" EDA finding.) k reshapes + max =
trivially fast. Directly targets the 3.42 cases.

### 3. tail_replicate (EP-gated) — quantile replication where the floor bites
At high EP, slots are abundant (n_red = n_experts) and PAR is set by the single
dominant expert. Size replicas from a **high quantile (p90) of per-iter load**, ONLY
at EP>=128. Distinct from the dead λ·std sweep: std *flattens* on heavy/bimodal tails
(many experts look volatile) and steals the marginal replica from the true peak;
a quantile tracks the actual peak and concentrates copies there. Cheap, targeted.
(λ-tuning was global and tested on easy cases — this is EP- and tail-specific.)

### 4. decouple_pack — pack on MEAN, replicate on RISK (likely a latent finance_band bug)
finance_band PACKS (LPT) on the risk vector mean+λstd. That over-isolates
high-variance/low-mean experts, under-loading their device and *raising* realized
mean_par. Replication should use risk; **placement should use the mean**. NOTES shows
a pivot to this but no server result — confirm it. One-line change, principled.

### 5. power_of_two_choices — queueing-theory placement (wild but trivially fast)
Balls-into-bins: assign each slot to the **lesser-loaded of 2 random candidate
devices**. Gives near-optimal max-load with O(n_slots) cost and no LPT adversarial
worst-case. Untried; from randomized load balancing (Mitzenmacher). A cheap escape
from greedy-LPT local minima at EP256. (Derandomize the "random" with a hash of the
expert id for determinism.)

### 6. ANALYSIS to redirect everyone's aim (do this first, ~20 min)
From finance_band's saved per-case `prediction_result` (in extra/), compute for each
case the **marginal score gain of cutting its PAR by 0.5**, i.e.
`d(case_score)/d(PAR)` = `-100·baseline_time / (60·PAR^2)·...`. Rank cases by leverage.
I claim DS-R1 Mix EP256/EP128 dwarf everything. This tells all three agents exactly
which 3-4 cases to optimize and lets us build a **high-EP-only proxy** (the current
LmSys-EP32 proxy tests the saturated cases). Without this we're optimizing blind.

---

## Strategic guidance for the loop
- STOP polishing transmit (maxed at ~111.8). The remaining points are in **high-EP
  DS-R1 PAR**, which is *not* saturated.
- Build a **high-EP proxy** (DS-R1, EP128/EP256, and if any Mix-like trace exists) —
  the LmSys-EP32 proxy is structurally blind to where the points are.
- Ride the **fold_pack 0.03s base** so a cheap vectorized refine fits the budget.
- Rank order to try: **1 (factor_snake) > 2 (regime_robust) > 3 (tail_replicate) >
  4 (decouple_pack) > 5 (po2)**. Each is one vectorized pass, EP256-safe by design.
