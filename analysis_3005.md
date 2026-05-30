# MoE Competition — Failure Analysis (2026-05-30)

Where the score is failing, why, and what the oracle says about the ceiling.
Diagnostic run on the `finance_band` grader dump (`extra/prediction_result_2/`,
server ≈ 111.25) + DS-EPLB baseline cache (`auto/_baseline_cache.json`) +
perfect-foresight oracle (`experiments/oracle_gap.py`, LmSys traces only).

---

## 0. TL;DR

1. **It's a forecasting problem, not a packing problem.** A perfect-foresight
   oracle using the *exact same greedy packer* hits PAR ≈ **1.02–1.09 at every EP
   including EP256**. Realized PAR (1.24 → 2.00) is *entirely* the cost of placing
   on a stale trailing window and holding that placement while load drifts.
2. **The gap doubles with EP** (+0.22 at EP32 → +0.97 at EP256). Fewer experts per
   device = forecast error can't be averaged out. EP128→EP256 is the cliff, and
   it's exactly where our block-placement turns OFF (time budget).
3. **The dataset that fails is Mix (DS-R1).** PAR 1.63 → 3.42 and ~4× the transmit
   of single-source datasets. Mix is a non-stationary mixture → the window forecast
   is permanently stale → no-trade band can't settle.
4. **Against DS-EPLB we win comfortably on stationary data** (LmSys 105–115), and a
   large part of that edge is the **no-trade band cutting transmit**, not PAR. The
   score is decided by **Mix + the high-EP end**, where our transmit advantage
   shrinks and PAR blows up — and where the oracle headroom is largest (≈ +1.0).
5. **Ensembling packers has a low ceiling** (packing is already ~solved). The real
   prize is a **regime router on forecasting + redeploy cadence** (keyed on measured
   drift and EP), plus a real EP256 packer.

---

## 1. The oracle: perfect foresight, same packer

`experiments/oracle_gap.py` places experts on each iteration's *true* per-layer load
using `finance_band._build_layer` (our packer). The only thing it has that we don't
is the future. Result (LmSys, sub-sampled iterations, authoritative realized PAR
from the grader dump):

| case        | oracle PAR | realized PAR | gap (forecasting cost) |
|-------------|-----------:|-------------:|-----------------------:|
| DS-R1 EP32  | 1.011 | 1.235 | **+0.225** |
| DS-R1 EP64  | 1.032 | 1.383 | **+0.351** |
| DS-R1 EP128 | 1.035 | 1.616 | **+0.581** |
| DS-R1 EP256 | 1.025 | 1.998 | **+0.973** |
| Qwen3 EP32  | 1.018 | 1.289 | +0.271 |
| Qwen3 EP64  | 1.087 | 1.525 | +0.438 |
| Qwen3 EP128 | 1.069 | 1.839 | +0.770 |

**Interpretation.** The greedy packer is *not* the bottleneck — with the true load it
balances to ~1.02 even at EP256 (2 slots/device). Everything above ~1.05 realized PAR
is forecasting error + redeploy lag.

This reframes the "co-spiking" narrative: co-spiking is the **symptom** of a *static*
placement being unable to track per-iteration co-fluctuation. The peakblock / temporal-
block machinery that took us 111→114 was attacking that gap indirectly (making the held
placement robust across time-blocks and the peak scenario). It worked, but the true gap
it's chipping at is +0.97 at EP256.

Caveat: the oracle re-places **every iteration** on the true load — not feasible under
the transmit budget. So the *achievable* ceiling is below the oracle; the realistic
levers are (a) better next-window forecast, (b) a held placement that is robust across
the window, (c) cheaper/more frequent redeploys (transmit-limited). The oracle bounds
the total opportunity and proves where it is **not** (raw packing skill).

---

## 2. Breakpoint #1 — EP scaling

The forecasting gap grows monotonically and steeply with EP:

```
EP32   +0.22
EP64   +0.35
EP128  +0.58
EP256  +0.97   <- gap nearly doubles vs EP128
```

Cause: at high EP each device holds very few experts (EP256 ≈ 2 slots). A single
misforecast on one of those 2 experts can't be diluted by the others → it shows up
directly in the device max → PAR. Low EP has ~8 experts/device, so errors average out.

**EP128 → EP256 is the cliff**, and it's compounded by an implementation fact: our
block-placement (the lever that buys PAR at EP≤128) is **disabled at EP256** because the
block build floors the per-call time budget (3× DS-EPLB). So EP256 currently falls back
to scalar mean-packing — the dumbest packer, in the regime with the most headroom.

---

## 3. Breakpoint #2 — the dataset that fails: Mix (DS-R1)

Absolute mean PAR across all graded cases (our `finance_band` run):

| dataset  | model | EP32 | EP64 | EP128 | EP256 |
|----------|-------|-----:|-----:|------:|------:|
| LmSys    | DS-R1 | 1.24 | 1.38 | 1.62  | 2.00 |
| ShareGPT | DS-R1 | 1.24 | 1.39 | 1.63  | 2.04 |
| WildChat | DS-R1 | 1.22 | 1.37 | 1.59  | 1.96 |
| **Mix**  | DS-R1 | **1.63** | **2.02** | **2.60** | **3.42** |
| LmSys    | Qwen3 | 1.29 | 1.52 | 1.84  | — |
| ShareGPT | Qwen3 | 1.30 | 1.54 | 1.86  | — |
| WildChat | Qwen3 | 1.29 | 1.51 | 1.81  | — |

The three single-source datasets are nearly identical and tame. **Mix is the outlier on
every axis**:
- PAR +0.4 (EP32) to +1.4 (EP256) above single-source.
- Transmit ~4× higher (Mix EP256 = 143,559 vs LmSys EP256 = 34,121).

Cause: Mix is a *non-stationary mixture* of sources. The load distribution drifts, so the
trailing-window mean is permanently stale and the no-trade band never settles — it must
keep redeploying (hence the transmit blow-up), and the placement is always behind the
load (hence the PAR blow-up). This is the regime CLAUDE.md flags as the EP128/EP256
budget edge (Mix_DS-R1_EP128 block build sits right at the 3×-DS-EPLB floor).

---

## 4. Per-case scores vs DS-EPLB (LmSys, where baseline is available)

`modeled_time = 60·PAR + transmit · 88_080_384 / 9e11`;
`case_score = 100 · ds_time / our_time`.

| case          | ourPAR | ourTx  | ourTime | dsPAR | dsTx    | dsTime | score | who |
|---------------|-------:|-------:|--------:|------:|--------:|-------:|------:|-----|
| Qwen3 EP32    | 1.289  | 14914  | 78.80   | 1.289 | 57792   | 82.97  | 105.3 | WIN |
| Qwen3 EP64    | 1.525  | 17916  | 93.25   | 1.532 | 69973   | 98.76  | 105.9 | WIN |
| Qwen3 EP128   | 1.839  | 23874  | 112.66  | 1.853 | 92894   | 120.25 | 106.7 | WIN |
| DS-R1 EP32    | 1.235  | 16634  | 75.75   | 1.258 | 82489   | 83.54  | 110.3 | WIN |
| DS-R1 EP64    | 1.383  | 18811  | 84.82   | 1.425 | 91892   | 94.49  | 111.4 | WIN |
| DS-R1 EP128   | 1.616  | 24073  | 99.34   | 1.692 | 110046  | 112.26 | 113.0 | WIN |
| DS-R1 EP256   | 1.998  | 34121  | 123.23  | 2.120 | 146768  | 141.56 | 114.9 | WIN |

Two key reads:

1. **We win *more* at high EP on LmSys, not less** — because DS-EPLB churns enormously
   (EP256 transmit 147k vs our 34k). Much of our edge over baseline is the **no-trade
   band cutting transmit**, not PAR. At Qwen3 EP32 our PAR *equals* baseline (1.289) —
   the entire 105.3 comes from transmit savings.
2. So on stationary data we are comfortable and largely saturated. **The score is
   decided by Mix and the high-EP end**, where (a) PAR is worst (3.4), (b) our transmit
   advantage shrinks (Mix forces redeploys), and (c) the oracle headroom is largest.

(No local Mix/ShareGPT/WildChat oracle — only LmSys traces are committed. The gap
pattern is identical across datasets and Mix is strictly worse, so the conclusion holds
a fortiori.)

---

## 5. What this means for next moves

### 5a. Ensemble / per-regime routing (the user's idea)

Supported by the data, with a caveat.

- **Already an ensemble (coarse):** SOTA routes by EP — block placement @EP≤128, scalar
  @EP256, EP-adaptive λ. Per-EP packer selection exists.
- **Everything a router needs is observable online** from `hotness`, `n_device`,
  `n_layers`:
  - **EP** (given) → packer choice.
  - **Model** (n_layers 58=DS-R1 / 94=Qwen3) → different slot pressure.
  - **Drift / non-stationarity** (measurable: variance of per-window means, or
    early-vs-late window divergence) → the big one. Detect Mix-like drift and switch
    from *hold-long-window + no-trade band* to *drift-aware* (shorter effective window,
    recency weighting, faster redeploy cadence). Keep hold-and-band on stationary cases.
- **Caveat (from the oracle):** ensembling different *packers* per regime has a low
  ceiling — packing is already ~solved (1.02 everywhere). The real headroom is in
  **forecasting + redeploy cadence**. Prior single-global-policy attempts lost
  (`regime_track` 113.9, `drift_adapt`) because they applied one compromise everywhere
  instead of **routing**. The high-value version:

  > A regime router that picks the **forecasting estimator + redeploy cadence per case**
  > (keyed on measured drift and EP), not just the packer — aggressive/recency-weighted
  > on Mix-style drift, hold-and-band on stationary — **plus a real EP256 packer**.

### 5b. Concrete candidates, in priority order

1. **(measure first) Local Mix oracle** — build/borrow a Mix-like non-stationary trace
   and run the oracle to learn the *achievable* ceiling on the regime that decides the
   score, before building. Know the prize.
2. **Drift detector + per-regime forecasting/cadence router** — attacks Mix, the failing
   dataset, in the dimension the oracle says has headroom (forecasting, not packing).
3. **EP256 spike-aware packer (`vblock256` direction)** — EP256 is where PAR is worst
   *and* the packer is currently dumbest (scalar fallback). Must stay under the 3×
   DS-EPLB per-call budget (vectorized round-LPT, no iterative refine).

### 5c. Hard constraints (unchanged, do not relitigate)

- Per-call budget = 3× DS-EPLB for that case; iterative refine/relabel/search at EP128+
  floors to −1e6. Must be finance_band-speed, single build + band.
- Replication / λ / τ / window tuning is tapped out.
- Changing the placement *objective* (L2 `lstsq_pack` 113.1, dot-product `dotprod_pack`
  112.7) loses; the win pattern is *adding scenario columns* to the L∞ worst-block
  greedy (peakblock 114.13).

---

## 6. Reproduce

```bash
# oracle gap (LmSys, both models)
.venv/bin/python experiments/oracle_gap.py extra/prediction_result_2/metrics.json
# -> experiments/results/oracle/oracle_gap.{csv,png}

# per-case scores vs DS-EPLB baseline: see the inline script in the session,
# data sources: extra/prediction_result_2/metrics.json + auto/_baseline_cache.json
```

Data provenance:
- `extra/prediction_result_2/metrics.json` — per-case PAR + transmit, finance_band run (server ≈ 111.25).
- `extra/prediction_result/metrics.json` — weaker run (server ≈ 108.12), used for contrast.
- `auto/_baseline_cache.json` — DS-EPLB per-case PAR/transmit/modeled-time (LmSys).
