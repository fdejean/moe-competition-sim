# MoE EPLB — working memo (vero agent)

*Living document. Every submission, architecture change, experiment, and the
**reasoning behind it** goes here. Append as we go; never rewrite history.*

Last updated: 2026-05-30 ~06:55.

---

## 0. Roles & how to read this
I have run in two modes this session:
- **Builder** (session 1): wrote/submitted variants 040–041. Both floored; the
  failures taught the binding server constraint.
- **Senior-scientist / sparring partner** (current): generate new idea classes,
  pressure-test the loop's conclusions, validate hypotheses with small experiments,
  and feed evidence to the two peer agents (`hik0njmv`, `ba4k2v8b`) who drive the
  submission loop. Idea memo lives in `auto/research/sparring_ideas_vero.md`; this
  file is the full narrative + log.

Peers' division of labour: `hik0njmv` owns the live submission loop and current SOTA
(transmit/relabel family, ~111.8). `ba4k2v8b` built the server-safe bounded-pass
packer and the high-EP baseline tooling, then handed off.

---

## 1. The problem, as I model it (first principles)

**Task:** consume a hotness trace `[iteration, layer, expert]`, emit a deployment
`[layer, device, slot] -> logical_expert_id`. Per redeploy, only changed slots cost
transmit. Re-run `rebalance` every `collection_interval` (1024) iters.

**Scoring (server):** `modeled_time = 60·mean_PAR + transmit·88_080_384/9e11`;
`case_score = 100·ds_eplb_time/our_time`; final = **mean of case_score over 25 cases**
= `{LmSys,ShareGPT,WildChat,Mix} × {Qwen3,DS-R1} × {EP32,64,128,(256 DS-R1)}`.

**The invariant that drives everything (derived from `calculate_par`):**
`PAR = max_d L_d / mean_d L_d`, where a replicated expert's load splits evenly across
its copies (`load_e/cut_e` per copy). The **mean device load = Σ_e hotness_e / n_device
is placement-invariant.** So **minimizing PAR ≡ minimizing the max device load
(makespan)**, and the two levers are:
1. **Replication** — split a hot expert across copies (the dominant lever; PAR floor
   = `w_max/(r·avg)`).
2. **Placement** — balance slots across devices (closes the gap to the floor).
Plus **transmit** — keep the deployment stable / relabel to cut moved slots.

**Cost weighting:** a 0.01 PAR drop ≈ 0.6s; one transmit ≈ 9.79e-5 s → PAR is worth
~6000× a transmit unit *per case*. BUT see §3: transmit turned out to be the only
*recoverable* lever for the easy cases, while PAR is the lever for the hard ones.

---

## 2. Hard constraints learned the hard way (do NOT relitigate)

1. **Per-call time budget = 3× the DS-EPLB baseline time FOR THAT CASE.** Not a flat
   10s. At EP256 (DS-R1) the budget is tiny. **Only finance_band-speed
   (single vectorized build + band, ~0.1–0.17s/call, NO iterative refine/relabel/
   search) survives.** Every iterative method floored to −1e6.
2. **The local proxy (`auto/eval.py`, LmSys EP32) is structurally blind.** It tests
   only the *easiest, already-saturated* cases. Always EP128/EP256 smoke-test before
   submit. A variant can look fast locally (0.8s) yet still floor (3×DS-EPLB < that).
3. **`_relabel_to_old` must be O(n_device), not O(n_device²).** The O(nd²) Python
   set-overlap version floored at EP128/256.

---

## 3. Chronological log — every submission & architecture change

### Session 1 (builder) — the "realized-objective" lane

**Setup & discovery.** Read the PDF/Learnings/base. Found fdejean's
`tools/codabench_submit.py` + `.env` → full auto-submit works. Confirmed
`max_submissions_per_day = 1000` (not scarce). Built a venv (`.venv_auto`, incl.
torch). Wrote `auto/iterate.sh` (zip → snapshot → full-length eval).

**Key derivation.** `mean device load` is placement-invariant → PAR = makespan. The
simulator scores `mean_t PAR(load_t)` (per-iteration, then averaged) but finance_band/
020 build placement from `PAR(mean_load)`. By Jensen `E[max] ≥ max[E]`, so the two
differ exactly when hot experts **co-spike** on the same device. **Measured the gap**
on the committed traces with 020's steady-state deploy: realized PAR is **+6.3%
(Qwen3) / +9.5% (DS-R1)** above mean-load PAR. → a real lever, motivating the lane.

**040 `cvar_repl` (proxy 108.63, NOT submitted).** Rationale: PAR is a *tail* statistic
and routing spikes are *one-sided*, so replication should be sized by an upper-tail
measure (CVaR = mean of worst q-fraction), not two-sided std. Change: replication
weight `mean + λ·(CVaR_q − mean)` instead of `mean + λ·std`; everything else = 020.
Result: +0.07 over 020 on the (noisy, easy-case) proxy — marginal. Superseded by 041.

**041 `cov_place` — FLAGSHIP, submitted twice, BOTH FLOORED.**
Rationale: attack the co-spiking gap directly. Swap-refine minimizes
`max_d (mean_d + κ·std_d)` of each device's *per-iteration* load (device-load variance
includes cross-expert covariance → it splits co-spiking experts; κ=0 recovers 020).
This is portfolio risk-budgeting at the device level.
- Proxy: **109.43** at 8s budget (+0.87 over 020) — biggest jump in the lane; DS-R1
  PAR 1.236→1.221. The idea *works* on the proxy.
- **Submission 766492** (budget 5.5s): **−1e6**, timeout on `ShareGPT_Qwen3_EP32`.
- Tightened to 1.0s budget / SAMPLE=128 / 40 passes (EP64 worst-case 1.1s locally).
- **Submission 766497** (budget 1.0s): **−1e6**, timeout on `ShareGPT_DS-R1_EP128`
  inside `_relabel_to_old`.
- **Lesson (the expensive one):** locally even DS-R1 EP128 w/ relabel was 0.8s, yet it
  floored — proving the budget is **3× DS-EPLB, tiny at high EP**, and that *any*
  iterative cost on top of build is fatal. Net contribution from me: 0 leaderboard
  points, but the constraint finding saved the peers (who were about to floor the
  same way) — recorded in `auto/NOTES.md` + memory.

### Sessions 2–N (peers, summarized from NOTES — for completeness of the record)
- **021 band_refine_transmin**: −1e6 (EP128 timeout in O(nd²) relabel).
- **050 rep_aware_refine** (roadmap 2a): −1e6 (EP64 timeout; rep-aware loop too slow).
- **051–054 fb_tau/window/lambda sweeps**: 106.6 / 109.3 / 105.3 / 111.1 — all ≤
  finance_band 111.26. Cheap tuning tapped out. Smaller TAU only inflates transmit;
  shorter window worsens PAR (drift).
- **056 block_pack** (co-spiking, k=8 temporal blocks): best proxy (109.11) but −1e6 at
  EP256 (per-device k-loop too slow). Co-spiking lever confirmed on proxy, but not
  EP256-affordable as built.
- **062 cospike_weight** (scalar co-spiking): 108.86 — scalar co-spiking *hurts* balance.
- **061 pack_refine_fast**: 109.84 (server-VALID) but < finance_band — greedy swap
  refine barely moves realized PAR and dropping relabel ballooned transmit. → **mean
  packing is not the lever; the PAR-neutral relabel matters more than the refine.**
- **068 cheap_relabel**: **111.751** — first beat of finance_band. PAR-neutral O(nd)
  device-relabel + slot-preserve cuts transmit −18.5% at zero PAR cost.
- **070–073 sticky_relabel** (stay-biased LPT, STAY sweep): **111.802** at STAY=0.25.
  Source-side transmit cut. Transmit: 842k(finance)→686k(relabel)→625k(sticky).
- **074–077 EP-adaptive λ**: high-EP wants λ≈1.5–2; λ=3 hurts, λ=0 hurts. Converged.
- **075 sticky_eplo = 111.805**, **078 shrink_predict = 111.808** (James-Stein cross-call
  shrinkage; within noise). Loop declared convergence + "no remaining idea class."
- **082 fold_pack** (NEW: proportional-apportionment replicas + vectorized boustrophedon
  placement, no LPT/relabel): **0.03s/call = 5× faster than finance_band**. Proxy ~103
  (crude) but reopens the budget for cheap refinement. Server pending at time of writing.

---

## 4. The strategic reframe (my main scientific contribution as sparring partner)

**Claim: "PAR is saturated at 1.69" is a GLOBAL-MEAN ARTIFACT.**
Score is a mean over 25 cases. Per-case (from the loop's own map):
`DS-R1 Mix EP256 = 3.42`, `EP128 = 2.59`, `ShareGPT EP256 = 2.04`, … while all EP32
cases are ~1.2–1.3. So 1.69 = mean of {easy ~1.2 … hard ~3.4}. **The hard high-EP
DS-R1 cases are 2–3× above any floor and are where ~all remaining points are.** The
proxy (LmSys-EP32) tests only the saturated easy cases → it cannot see the frontier.

**Why the hard cases are hard (mechanism):**
- Granularity: EP256 → 2 slots/device, EP128 → 3. Can't isolate a mega-hot expert.
- Non-stationarity: "Mix" = mixture of regimes in one window → the window mean
  mis-predicts per-iteration load → realized PAR ≫ mean-load PAR. Prediction/robustness
  failure, not mean-packing failure.
- This is the co-spiking gap, much larger at high EP. Co-spiking attempts died on EP256
  **speed**, not merit.

**Implication:** stop polishing transmit (maxed ~111.8). The remaining points are in
high-EP DS-R1 PAR, attacked by a **fast, vectorized, EP256-safe** placement/replication.

---

## 5. Idea classes generated (full detail in `auto/research/sparring_ideas_vero.md`)
1. **factor_snake** — spectral co-spiking: top singular vector of the centered window
   (power iteration) = dominant spike axis; snake-deal slots along it so each device
   sums to ~0 on that axis. One sort, EP256-safe.
2. **regime_robust** — DRO replication: water-fill on each expert's *max sub-window
   mean* (worst regime). Robust, not forecasting.
3. **tail_replicate (EP-gated)** — size replicas by a high quantile (p90/p95) of
   per-iter load at EP≥128; concentrates copies on the true peak.
4. **decouple_pack** — pack on MEAN, replicate on RISK (finance_band conflates them).
5. **power_of_two_choices** — assign each slot to the lesser-loaded of 2 hash-chosen
   devices (Mitzenmacher); O(n_slots), escapes LPT worst-cases.
6. **Leverage analysis** — rank cases by `d(score)/d(PAR)`; build a high-EP proxy.

---

## 6. Sparring experiments run (evidence, incl. negative results)

All on DS-R1 LmSys (the hardest case I can reproduce locally), realized PAR =
mean over 400 sampled iters of per-iteration PAR; placement = LPT on the stated weight.

**E1 — co-spiking gap at high EP (confirms the reframe).**
| case | finance_band realized | mean-load | gap |
|---|---|---|---|
| DS-R1 EP128 (3 slots/dev) | 1.330 | 1.136 | **+0.19** |
| DS-R1 EP256 (2 slots/dev) | 1.414 | 1.148 | **+0.27** |
→ The realized-vs-mean gap is huge at high EP (vs +0.06–0.10 on easy cases). Confirmed.

**E2 — factor_snake prototype (NEGATIVE result).** Spectral snake placement closed
only **1–2%** of the gap. Conclusion: at 2 slots/device the per-iteration peak is **not**
a pairwise co-spiking phenomenon — it's dominated by a **single spiky expert's own
variance**. → placement/anti-correlation is the wrong lever here; **replication of the
spiky expert** is the right one. (Idea 1 demoted; ideas 2/3 promoted.)

**E3 — replication statistic vs realized PAR (POSITIVE — actionable).** Placement fixed
(LPT); only the replication-sizing weight varies:
| weight | EP128 realized | EP256 realized |
|---|---|---|
| mean (λ0) | 1.333 (+0.003) | 1.428 (+0.013) |
| **fb mean+2·std** | 1.330 (0) | 1.414 (0) |
| mean+4·std | 1.366 (+0.036) | 1.456 (+0.042) |
| **p95 quantile** | **1.318 (−0.012)** | **1.398 (−0.016)** |
| per-iter max | 1.326 (−0.004) | 1.405 (−0.009) |
| regime_robust k=8 | 1.336 (+0.006) | 1.429 (+0.015) |
→ **p95-quantile replication beats finance_band's mean+2·std at high EP** (−0.012 to
−0.016 realized PAR), and it's a single vectorized op = finance_band-speed. mean+4·std
over-replicates (flattens, steals slots from the peak); regime_robust is flat on LmSys
(would need the non-stationary Mix trace to test its real target). **p95 replication is
the most promising submittable lever found.** Recommended next build:
`finance_band + p95 replication, EP-gated to EP≥128`, on the sticky_relabel/fold_pack
base, then submit and read the *server* per-case PAR on the DS-R1 high-EP cases.

---

## 7. Current best & open frontier
- **Server SOTA:** ~111.808 (shrink_predict) / 111.805 (sticky_eplo) — within noise.
  The +0.54 over finance_band is entirely transmit.
- **Open frontier:** high-EP DS-R1 realized PAR (2–3.4), via **p95/tail replication**
  (E3, validated) and possibly regime-robust replication on the Mix cases (untested
  locally — needs a Mix-like trace or server read).
- **Tooling gap:** no high-EP / Mix proxy. Everyone is optimizing partly blind.

## 8. Logging protocol (going forward — per user instruction)
- Every new variant, parameter change, experiment, or server result is appended here
  with: what changed, the *rationale*, the result (proxy + server), and the takeaway.
- Keep `auto/NOTES.md` (one-liners + server scores) and `auto/loop_state.json` in sync.
- Negative results are logged too — they redirect the search (E2 above saved the team
  from chasing placement at high EP).
