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

---

## 9. Resumed Vero-agent log (2026-05-30 ~07:00+) — co-spiking PLACEMENT, refined

*Picking the loop back up after the 5h timer. SOTA verified on server:
shrink_predict 111.808 / sticky_eplo 111.805 (mean_par 1.696, transmit ~632k).
User directive: break the 111 ceiling, aim 120, be crazy, stop polishing finance.*

### 9a. My submissions this stretch (architecture + rationale + result)

**`pack_refine_fast` (061) — id 766513 → server 109.836 (VALID, < finance_band).**
- Arch: finance_band + cut-preserving swap packer bounded by a FIXED pass count
  (no 7s wall-clock budget), relabel dropped. 176–269 ms/call.
- Rationale: I thought the high-EP PAR gap was *packing* (floor=1.000) and that 020's
  swap-refine was right but its 7s budget was the timeout cause.
- Result/lesson: VALID but worse. Swap-refine moves realized PAR ~0 (1.6908 vs
  1.6926); dropping relabel ballooned transmit (1.25M vs 840k). **→ mean-packing is
  not the lever; relabel/transmit matters more.** (Matches peer's §3 finding.)

**`predict_trend` (060) — proxy ~95, NOT submitted.** Two-half linear-trend forecast
estimator. Chasing drift exploded PAR *and* transmit. Dead end (confirms flo's EDA).

**`cospike_kpack` (079) — id 767191 → server −1e6 (FLOORED EP256), BUT pivotal.**
- Arch: sticky_eplo + **co-spiking placement** — each slot carries a k-vector of
  temporal block-mean loads; greedy assigns to the device minimizing the resulting
  *worst block load* (k=3). Computational stand-in for min-variance partitioning.
- Rationale: attack the realized-PAR/co-spiking residual; nobody tried *coarse* k
  (peer's block_pack only went finer, k=8/16/32).
- Result/lesson — **NEW EVIDENCE that refines E2:** it timed out *only* at
  ShareGPT_DS-R1_EP256 (per-slot block-max greedy 0.39 s/call > budget). On every
  **completed** case it **LOWERED realized PAR**: ShareGPT_DS-R1_EP128 = **1.587**
  (vs finance-family ~1.63), EP64 = 1.345 (vs ~1.39), EP32 = 1.202 (vs ~1.24).
  → **§6/E2 said spectral SNAKE placement closes only 1–2%; but a GREEDY block
  placement closes meaningfully more on real server EP64/128 cases.** So the nuance
  is: *snake/anti-correlation* placement is weak, but *greedy worst-block* placement
  helps — it's just too slow at EP256. Co-spiking placement is alive at EP≤128.

**`peak_balance` (080) — id pending → submitted.**
- Arch: sticky_eplo, LPT targets a **robust-minimax** weight = blend of window mean
  and the mean load over the **top-10% highest-total-load iterations** (the joint
  co-spiking spikes). Scalar LPT → 254 ms/call (EP256-safe).
- Rationale: capture the *joint* tail (which experts spike *together*) with a single
  scalar weight, unlike the peer's failed *marginal* attempts (per-expert p95,
  scalar busyness). Result: _pending server_.

### 9b. Key reframe of the constraint (calibrated)
DS-EPLB per-call (local, CPU): EP32 0.33s, EP128 1.15s, EP256 2.26s → "3× budget"
looks huge LOCALLY (~6.7s at EP256). But DS-EPLB is **torch (GPU-fast on the
server)** while we are CPU numpy, so the *server* budget is tight. Empirical
pass/fail: sticky_eplo 0.23s/call PASSES; cospike_kpack 0.39s/call FLOORS at EP256.
**The per-slot LOOP length is the cost, not k** (cospike at k=2 and k=3 both 0.39s).

### 9c. Next build (evidence-driven): `cospike_hybrid` (EP-gated co-spiking placement)
- Plan: use the **greedy worst-block placement at EP ≤ 128** (validated PAR gains,
  237 ms at EP128 = safe) and **fall back to sticky_eplo's scalar LPT at EP256**
  (0.23s, safe). E2 *supports* dropping co-spiking at EP256 anyway (at 2 slots/device
  the peak is single-expert variance, not pairwise co-spiking). This keeps every
  validated EP32/64/128 co-spiking gain and removes the only timeout. **Highest-EV
  next submission.**
- Then: combine with the peer's transmit machinery (sticky relabel) so we don't
  regress the 632k transmit while gaining EP≤128 PAR.

### 9d. Reconciliation note (for the team)
E2 (snake, −1–2%) and my cospike_kpack server data (greedy block, real gains at
EP64/128) are **both right**: anti-correlation *ordering* is weak, but *actively
balancing the worst temporal block* via greedy is not. The open question is whether
a **vectorized** worst-block balance exists (snake was the vectorized attempt and it
was weak; greedy works but is EP256-slow → hence the EP-gate hybrid).

---

## 9. Full per-submission detail — vero relabel/transmit/EP-adaptive lane (complete record)

> Appended by the relabel/transmit-lane agent (the "Sessions 2–N" peer in §3 above),
> per user instruction to document EVERY submission with its rationale. Newest at the
> bottom. Server scores are the oracle; the LmSys-EP32 proxy is anti-correlated (§2).

**Why I submitted band_refine_transmin (021) FIRST — −1e6.** It was the prior local
SOTA and had never been server-scored; submitting it validated the whole
implement→eval→submit pipeline AND immediately exposed the EP128 timeout wall. Rationale:
de-risk the tooling on the highest-EV candidate. Takeaway: the O(n_device²) relabel is
fatal at EP128; the server tests EP128.

**rep_aware_refine (050) — −1e6.** Roadmap "biggest lever" (replication-aware refinement).
Rebuilt bounded/EP128-safe, but the per-candidate re-pack loop (~0.6–1.0s) floored at EP64.
Rationale: replication is theoretically the dominant lever; outcome: ANY iterative cost
floors. This is what cemented "only finance_band-speed survives."

**fb_tau005_l2 / fb_tau01_l2 (051/052) — 106.6 / 109.3.** Hypothesis: redeploy more often
(smaller TAU) → track load → lower PAR, and transmit is "6000× cheaper" so it's free.
Outcome: WRONG — PAR flat, transmit exploded (1.93M). Placement is not staleness-limited;
transmit is NOT free to spend.

**fb_window512_l2 (053) — 105.3.** Shorter trailing window to track ShareGPT drift. Outcome:
PAR WORSE (1.737). Confirms persistence > recency; chasing drift overshoots.

**fb_lambda25 (054) — 111.1.** λ=2.5. ≈ finance_band, hair worse. λ=2 is the replication peak.
→ Declared the whole cheap-tuning family (TAU/window/λ) tapped out.

**block_pack (056) — −1e6.** First co-spiking attack: balance each device across k=8 temporal
blocks in one greedy pass (temporal diversification spreads same-block spikers). Best proxy
(109.11; DS-R1 1.235→1.210) but floored at DS-R1_Mix_EP256. Rationale: realized PAR gap is
co-spiking, not mean packing. Lesson: the per-device k-vector loop is ~2× finance_band → fatal
at EP256 (which I'd never smoke-tested). Added EP256 to the smoke harness. **This idea later
became the SOTA once gated to low EP — see epadaptive_algo.**

**cospike_weight (062) — 108.86.** Fold co-spiking into a SCALAR weight (block-mean · global-
block-busyness) to stay finance_band-speed. Outcome: HURT (1.717). The scalar premium distorts
mean balance; per-case it's worse everywhere, worst at high EP. Scalar co-spiking ≠ per-device
co-spiking.

**fb_convex (055) — 110.6.** Over-replicate the hottest (water-fill on risk^1.5) to lower the
high-EP PAR floor. Outcome: HURT (1.706) — replication budget already suffices (floor ~1.0);
concentrating replicas wastes mean balance.

**decouple_pack (065) — 109.4.** Replicate on risk but PLACE on mean (hypothesis: risk-packing
isolates high-var/low-mean experts onto under-loaded devices). Outcome: HURT (1.700) → proved
the OPPOSITE: risk-packing is a FEATURE for the spiky server cases. This is where I realized the
proxy is anti-correlated and switched to server-as-eval.

**quantile_pack (066) — 111.13.** Heavy-tail p95 replication (`max(p95, mean+2std)`, subsampled
for speed). Closest-yet but < finance_band, because `max(p95,gauss)`≈gauss most of the time. NOTE:
the peer's E3 (§6) found PURE p95, EP-gated to EP≥128, beats mean+2std at high EP — my global
`max()` version masked it. **Untested lever: pure-p95 EP≥128.**

**maxsubwin (067) — 111.24.** DRO: replicate by the worst recent sub-window mean (robust to Mix
regime shifts, keeps full history). ≈ tied. (Local LmSys can't exercise its Mix target.)

**cheap_relabel (068) — 111.751 ⭐ FIRST WIN.** The reframe that worked: PAR is saturated and ≈
baseline's; finance_band's edge is holding (low transmit), and its residual transmit is recoverable
PAR-NEUTRALLY. Rebuilt band_refine's relabel in O(n_device) (match device rows by shared heaviest
expert via a dict; keep persisting experts in old slots). PAR identical (1.6929), transmit −18.5%,
EP256-safe → guaranteed ≥ finance_band. First beat of the prior best.

**cheap_relabel_v2 (069) — 111.746.** Stronger O(nd) alignment (exact full-set match then heaviest).
Cut more transmit (680k) but ≈ tied — the relabel isn't perfectly PAR-neutral in the closed loop
(less transmit → shorter redeploy rollout → slightly different realized PAR). Alignment maxed ~111.75.

**sticky_relabel (070) — 111.802 ⭐.** Cut transmit at the SOURCE: stay-bias in the LPT build
(prefer an expert's previous device) so the fresh placement resembles the last one. Transmit 625k
at tiny PAR cost → net +0.05. Source-side cut pays.

**sticky_stay 0.15/0.40/0.60 (071–073) — 111.74 / 111.71 / 111.56.** STAY sweep. Higher STAY cuts
transmit (633k→601k) but raises PAR faster; composite PEAKS at STAY=0.25. Transmit fully optimized.

**sticky_epadapt (074) — 111.54.** λ=3 at high EP (more variance hedging). HURT (1.7037).

**sticky_eplo (075) — 111.805 ⭐.** Opposite — λ=1.5 at high EP (scarce slots want LESS variance
replication). Marginal SOTA (within noise). Real but tiny direction.

**sticky_eplo10 / sticky_eplo00 (076/077) — 111.67 / 110.45.** High-EP λ curve: 3→111.54, 2→111.80,
1.5→111.805(peak), 1.0→111.67, 0→110.45. λ≈1.5–2 robustly optimal.

**shrink_predict (078) — 111.808 ⭐.** Genuinely-new class: cross-call long-run shrinkage of the load
estimate via `_STATE` memory (James-Stein variance reduction; distinct from trend/window). Within
noise → persistence holds; shrinkage ~neutral.

**sticky_tau 0.1/0.05/0.02 (079–081) — 111.24 / 109.84 / 108.68.** Re-tested lower TAU on the relabel
base (hypothesis: cheaper redeploys shift the TAU optimum down). Still HURTS — placement genuinely
isn't staleness-limited. Direction dead.

**fold_pack (082) — 106.47.** Completely-new architecture (user request): proportional-apportionment
replicas + vectorized boustrophedon FOLDING placement, no water-fill/LPT/relabel. 0.03s/call (5×
faster). Outcome: WORSE on both (mean_par 1.76, transmit 978k) — folding packs cruder than greedy LPT
and the global sort reshuffles MORE. Speed alone buys nothing. BUT it proved the budget headroom idea
that led to the next win.

**epadaptive_algo (083) — 112.079 ⭐⭐ NEW SOTA, BROKE THE PAR FLOOR.** Insight: co-spiking placement
floored only at EP256; the time budget is 3× DS-EPLB *per case*, and at EP32/64 it's generous
(block_pack ran 0.13s there). So DISPATCH BY n_device: co-spiking block-balanced placement at EP≤64,
fast finance_band at EP≥128, both + the O(nd) relabel. Outcome: **mean_par 1.68845 — the first time
PAR fell below 1.692** → 112.079 (+0.27). **The "PAR is saturated" verdict was an artifact of the
EP256 speed wall, not a real floor.** Co-spiking helps where it's affordable. This reopens the whole
PAR frontier.

### Open frontier (post-epadaptive)
1. **+ p95 replication at EP≥128** (peer E3: validated locally, −0.012/−0.016 realized PAR at high EP;
   my global quantile_pack masked it). Add pure-p95, EP-gated, to epadaptive's high-EP path.
2. **Raise the co-spiking EP threshold to ≤128** (block_pack ran fine at EP128 = 0.19s, only EP256
   floored; EP128 budget ≈ 2.9s). Captures the EP128 cases (PAR 1.6–2.6, lots of headroom). Risk:
   server ratio is worse than local — needs a careful EP128 timing check.
3. Combine 1+2: co-spiking placement (EP≤128) + p95 replication (EP≥128) + relabel.

**Logging protocol (per user instruction): every change/submission/result is appended here AND to
auto/NOTES.md + auto/loop_state.json, with rationale + proxy + server + takeaway, before moving on.**

### 9e. *** BREAKTHROUGH: peak_balance = 112.159 (NEW SOTA, +0.35 over 111.808) ***
The robust-minimax / joint-peak placement WORKS and breaks the peer's "converged"
111.8 ceiling. Mechanism recap: per layer, pick the top-10% iterations by TOTAL load
(the actual co-spiking spikes), and place/replicate to balance each expert's mean
load DURING those peak iterations (blended 50/50 with the window mean). Still a single
scalar LPT (254ms, EP256-safe). Why it beat the peer's failed tail attempts: theirs
were MARGINAL (each expert's own p95 ~ mean+λstd); this selects the JOINT peak
iterations (which experts are hot together) — that's the co-spiking structure in a
scalar weight. Next: THETA/PEAKFRAC sweep + combine joint-peak weight WITH the EP-gated
block placement (cospike_hybrid) for compounding.
2026-05-30 ~07:40  epadaptive_ep128 = 112.364 NEW SOTA (co-spiking <=EP128). p95@high-EP (084)=112.011 worse. Next: co-spiking @EP256 cheaper-k.
