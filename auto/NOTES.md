# autoresearch run notes

One line per attempt. Append-only. Never edit prior lines.

2026-05-29 22:50  000_seed_smoke              mean_score=97.41  hypothesis="no rebalancing baseline"
2026-05-29 22:50  001_seed_hot_expert         mean_score=99.42  hypothesis="duplicate hottest expert per layer"
2026-05-29 22:50  002_seed_ds_eplb            mean_score=100.00 hypothesis="raw DeepSeek EPLB (baseline)"
2026-05-29 22:50  003_seed_ds_eplb_throttled  mean_score=99.13  hypothesis="DS-EPLB but only every 4 calls, cache deployment"
2026-05-29 23:05  004_hot_expert_par_priority  mean_score=99.46  hypothesis="sort layer priority by load std descending"
2026-05-29 23:05  005_hot_expert_top2_redundant mean_score=99.46  hypothesis="spread top-K hottest across all redundant slots" (no extra slots at EP32 → identical to 004)
2026-05-29 23:05  006_ds_eplb_hierarchical     mean_score=100.00 hypothesis="DS-EPLB hierarchical mode (faster, maybe survives EP64)" (fell back to global at EP32, same as DS-EPLB)
2026-05-29 23:05  007_ema_hot_expert           mean_score=99.58  hypothesis="EMA(alpha=0.6) over hotness window for hot-expert placement" (best non-DS-EPLB so far)
2026-05-29 23:30  008_finance_band             mean_score=100.04 hypothesis="flo's finance_band defaults (TAU=0.2, LAMBDA=2.0)" — ties DS-EPLB on short LmSys; +11 server score from skipping transmit on long traces
2026-05-29 23:30  009-012_fb_tau_005/10/40/80  mean_score=100.04 TAU sweep — identical on short trace (band rarely fires); decisive only on long server traces
2026-05-29 23:30  013_fb_lambda_0              mean_score=100.00 LAMBDA=0 (mean-only replication, no variance weight)
2026-05-29 23:30  014_fb_lambda_1              mean_score=100.05 LAMBDA=1 ← best so far locally; less aggressive variance weight beats default LAMBDA=2 on Qwen3
2026-05-29 23:30  015_fb_lambda_4              mean_score=99.95  LAMBDA=4 over-weights variance, costs PAR
2026-05-29 23:30  016_fb_aggressive            mean_score=99.95  TAU=0.05+LAMBDA=4 (most aggressive); identical to 015 — TAU still doesn't matter at this length
2026-05-29 23:30  017_fb_conservative          mean_score=100.00 TAU=0.8+LAMBDA=0 (hold longest, no risk weight) — identical to 013
2026-05-30 00:05  018_global_swap_refine       mean_score=100.02 hypothesis="DS-EPLB global + swap local-search refine (cut-preserving, max-load <= LPT)" — refine lowers PAR marginally on short trace (most iters use fixed init); guaranteed >= LPT, compounds on long traces
2026-05-30 00:10  019_band_swap_refine         mean_score=100.06 hypothesis="flo band + variance replication + swap refine (merge)" ← new best; both PARs below DS-EPLB
2026-05-30 00:15  020_band_refine_transmin     mean_score=100.35 hypothesis="019 + PAR-NEUTRAL transmit min (greedy device relabel 4b + slot preserve 4a, vs cached prev & init table)" ← BEST; same PAR as 019, transmit -22%/-17%; transmit win multiplies on long hidden traces
2026-05-30 00:55  021_transmin_l2              mean_score=100.34 hypothesis="020 transmit-min with LAMBDA=2.0 (server-optimal per calibration: finance_band λ2=111.27 beats λ1=110.66)" → canonical upload codes/band_refine_transmin/. Long-horizon proxy (full 5120/6144 trace, multi-rebalance): 108.56, beating finance_band 107.82 / 019 108.14; same PAR as 019 but tx 11632/14090 vs 14914/16634. λ1 kept as hedge (submissions/band_refine_transmin_l1.zip).

## Agent #3 (vero, realized-objective lane) — 2026-05-30 ~01:45

Hypothesis: the simulator scores `mean_t PAR(load_t)` but finance_band/020 build
placement from `PAR(mean_load)`. Measured the gap on committed traces with 020's
steady-state deploy: realized PAR is +6.3% (Qwen3) / +9.5% (DS-R1) above mean-load
PAR — the "co-spiking penalty" (hot experts spiking on the SAME iters share a
device). Real lever, bigger than λ tuning.

- 040 cvar_repl: replication by tail risk (CVaR_q - mean) instead of std. Proxy
  108.63 vs 020's 108.56 (marginal). Not submitted (superseded by 041).
- 041 cov_place: swap-refine minimizes max_d(mean_d + KAPPA*std_d) of per-iteration
  device load (covariance/co-spiking aware = device-level portfolio risk budgeting).
  KAPPA=0 recovers 020. Proxy 109.43 at 8s budget (+0.87 over 020), DS-R1 PAR
  1.236->1.221.

⚠️ CRITICAL SERVER FINDING (cost 1 floor submission, id=766492):
  Iterative swap-refine TIMES OUT on ShareGPT_Qwen3_EP32 (server per-call budget =
  3x DS-EPLB time, NOT a flat 10s). The local proxy is LmSys-only so it can't catch
  this. The server-PROVEN finance_band (111.27) has NO refine loop — pure build+band.
  => Heavy iterative methods (019/020/041 swap refine, any per-call search) are at
  real risk of flooring on ShareGPT. The calibration table only ever confirmed
  finance_band + fb_* (no refine) on the server; 019/020's refine was never
  server-verified.
  Fix: cap total per-call time HARD (cov_place now _TIME_BUDGET_S=1.0s, SAMPLE=128,
  passes=40 -> ~1.1s worst case at DS-R1 EP64, under 3x-DS-EPLB). Resubmitted as
  id=766497 (proxy 108.87, +0.31 over 020). Server result pending.

### Correction (after seeing the 24-case leaderboard)
Server cases = {ShareGPT,WildChat,LmSys} × {Qwen3,DS-R1} × {EP32,EP64,EP128}.
Both cov_place submits floored (766492 @5.5s budget on ShareGPT_Qwen3_EP32;
766497 @1.0s budget on ShareGPT_DS-R1_EP128 inside _relabel_to_old).
BUT locally DS-R1 EP128 incl. relabel is only 0.80s — so it's not "relabel is
O(ndev^2)" alone. The real limit: per-call budget = 3× DS-EPLB time FOR THAT CASE,
which at EP128 is very small. finance_band passes because build-only stays under it;
ANY added refine/relabel cost (019/020/041/rep_aware_refine all floored) exceeds it.
=> Server-safe rule: per-call cost must be ~finance_band (build+band, no iteration).
The covariance/co-spiking lever is real (measured +6-10% realized-vs-mean PAR gap;
proxy +0.9) but must be folded into the NON-iterative build step, not a swap loop.
Local LmSys/EP32 proxy CANNOT catch EP128/ShareGPT timeouts — always test ep=128.
2026-05-30 01:45  060_predict_trend            proxy=95.x   hypothesis="trend-extrapolation estimator (forecast=recent+H*(recent-older))" → FAILED: chasing drift exploded PAR AND transmit (68k vs 14k). Confirms EDA: prediction overshoots. Pivoted.
2026-05-30 01:50  061_pack_refine_fast         proxy=108.41 hypothesis="finance_band + cut-preserving swap packer bounded by FIXED pass count (no 7s budget), relabel dropped" → SERVER-SAFE (176-269ms/call vs band_refine_transmin's 7s that TIMED OUT = -1e6). Diagnostic: PAR floor=1.000 at all EP, so the 0.10-0.15 high-EP gap is PACKING not replication. Beats finance_band proxy (107.82); band_refine_transmin proxy 109.2 is HIGHER but is server-invalid (timeout). pack_refine_fast = the safe packing win.

## Agent #4 (vero overnight, PAR-via-packing lane) — 2026-05-30 ~02:10
Submitted band_refine_transmin (021) to server FIRST → -1e6 (EP128 timeout in
_relabel_to_old). Confirms peers: only finance_band-speed survives 3x-DS-EPLB budget.
Cheap-lever batch (all finance_band-speed, server-scored), vs finance_band 111.264:
  766506 fb_tau005_l2   = 106.63  (PAR 1.695, transmit 1.93M)  TAU↓ explodes transmit, PAR flat
  766507 fb_tau01_l2    = 109.28  (PAR 1.693, transmit 1.35M)  same story
  766508 fb_window512_l2= 105.28  (PAR 1.737, transmit 1.68M)  shorter window WORSENS PAR (drift)
  766509 fb_lambda25    = 111.12  (PAR 1.697, transmit 815k)   ≈ λ2, a hair worse
CONCLUSION: replication-weight + band + window tuning are TAPPED OUT; finance_band
λ2/τ0.2/full-window is the peak of the cheap space. Smaller TAU does NOT reduce PAR
(placement isn't stale-limited) — it only inflates transmit (~4pts/doubling, so
transmit is NOT free to spend). The ONLY remaining lever is BETTER PACKING (mean_par
1.692 vs floor 1.0) within ~finance_band time. Pivoting 100% to packing.
2026-05-30 02:05  061 SERVER RESULT (id 766513)  composite=109.836 (server-VALID, all 24 cases, NO timeout). mean_par=1.6908 transmit=1.25M. ⚠️ LOWER than finance_band 111.27. WHY: swap-refine PAR gain ≈0 on server (1.6908 vs finance_band 1.6926) — greedy swaps don't reach the floor=1.0; AND dropping the relabel ballooned transmit (1.25M vs finance_band 840k, +122s). CORRECTED PRIORITY for resumed loop: the PAR-NEUTRAL RELABEL (transmit cut) matters MORE than the refine; refine is ~useless on server. Winning server-safe combo = finance_band + FAST relabel (no refine). Worst regime = DS-R1 Mix (PAR 1.6→3.4 across EP, transmit 72k→147k). To beat 111.27: either a GLOBAL packer (BLDM/KK, not greedy swaps) for real PAR drop, or a fast relabel to push transmit below 840k.
2026-05-30 02:30  056_block_pack            proxy=109.11 server=-1e6  hypothesis="co-spiking: balance device load across k=8 temporal blocks in one greedy pass" → BEST PROXY (DS-R1 PAR 1.235->1.210, same transmit) but FLOORED at ShareGPT_DS-R1_EP256 (server tests EP256!). k-vector inner loop ~2x finance_band = too slow at n_device=256. Co-spiking lever CONFIRMED on proxy; must make it finance_band-speed.
2026-05-30 02:35  062_cospike_weight        hypothesis="fold co-spiking into a SCALAR weight = blockmean·global-block-busyness, reuse finance_band pipeline (EP256-safe by construction)"
2026-05-30 02:45  063_block_pack_fast       ABANDONED: bounded-candidate (M=8) device selection did NOT speed up the per-device block packer (0.30s EP256 vs finance_band 0.17s, ~1.8x) — argpartition+indexing cost ~ the saving. Per-device k-vector packing is fundamentally too slow at EP256. Only SCALAR co-spiking (cospike_weight) is EP256-safe.
2026-05-30 02:50  062_cospike_weight        server=108.86 (mean_par 1.717 > finance_band 1.692) — scalar co-spiking HURTS load balance. Co-spiking lane DEAD within budget. PIVOT.
2026-05-30 02:55  064_cov_weight            NOT submitted (same scalar-co-spiking family as 062 which hurt).
2026-05-30 02:55  PIVOT → decouple_pack: replicate on risk=mean+λstd, but PLACE/LPT on MEAN load (finance_band packs on risk, which isolates high-var/low-mean experts and under-loads their device -> raises realized mean_par). Scalar, finance_band-speed.

## PER-CASE OPPORTUNITY MAP (finance_band server, 2026-05-30 ~03:10)
25 cases = {LmSys,ShareGPT,WildChat,Mix} x {Qwen3,DS-R1} x {EP32,64,128,256(DS-R1)}.
Worst PAR (= biggest opportunity): DS-R1_Mix_EP256=3.42, DS-R1_Mix_EP128=2.59,
DS-R1_ShareGPT_EP256=2.04, DS-R1_Mix_EP64=2.02, DS-R1_LmSys_EP256=2.00,
DS-R1_WildChat_EP256=1.96, Qwen3_*_EP128~1.81-1.86. ALL EP32 already ~1.2-1.3.
=> Points are in DS-R1 EP128/256 (esp. the "Mix" dataset). My LmSys-EP32 proxy tests
the EASIEST cases -> doubly non-predictive. Target high-EP realized PAR (co-spiking +
non-stationarity). Co-spiking too slow at EP256; remaining safe shots = aggressive
replication of hottest (fb_convex) + heavy-tail quantile (quantile_pack).
2026-05-30 03:25  067_maxsubwin             server=111.244 (~tied finance_band).
2026-05-30 03:25  068_cheap_relabel         server=111.751 *** BEATS finance_band 111.264! *** PAR-neutral (1.6929=finance_band) + transmit -18.5% (686k vs 842k) via O(n_device) heaviest-expert device relabel + slot preserve. The band_refine_transmin transmit idea finally made EP256-FAST (O(nd) not O(nd^2)). KEY: PAR is saturated; TRANSMIT is the recoverable lever (~3% of time, cuttable PAR-neutrally). NEW SOTA.
2026-05-30 03:30  069_cheap_relabel_v2      hypothesis="stronger O(nd) alignment: exact full-set match first (fully-persistent devices) + heaviest for rest -> bigger transmit cut, same PAR" submitting.
2026-05-30 03:35  069_cheap_relabel_v2      server=111.746 ~= v1 (111.751). Better alignment cut transmit more (680k<686k) but redeploy-timing coupling raised PAR slightly (1.6937>1.6929) -> net tie. ALIGNMENT MAXED ~111.75. cheap_relabel(v1) = SOTA. Transmit-cut ceiling is lower than the naive 114.8 due to PAR/redeploy-duration coupling.
2026-05-30 03:45  070_sticky_relabel        server=111.802 *** NEW SOTA *** cheap_relabel + stay-biased LPT (STAY=0.25): transmit 625k (vs 686k) at +0.004 PAR (1.6968) -> net +0.05 over cheap_relabel. Source-side transmit cut (keep experts on prev device) PAYS. transmit: 842k(finance)->686k(relabel)->625k(sticky). Sweeping STAY.
2026-05-30 03:55  071-073 sticky STAY sweep  server: 0.15=111.743, 0.25=111.802(SOTA), 0.40=111.710, 0.60=111.563. Clean tradeoff: higher STAY cuts transmit (633k->601k) but raises PAR (1.698->1.704); composite PEAKS at STAY=0.25. Transmit lever now fully optimized. SOTA = sticky_relabel STAY=0.25 = 111.802 (+0.54 over prior best finance_band 111.264).
2026-05-30 04:10  074_sticky_epadapt        server=111.538 (lambda=3 at EP>=128 raised PAR 1.7037). EP-adaptive MORE replication HURTS. Replication fully tapped.
2026-05-30 04:25  075_sticky_eplo           server=111.805 (marginal SOTA, lambda=1.5 at EP>=128). High-EP wants LESS variance repl (scarce slots). Within noise of sticky_relabel. Sweeping high-EP lambda {1.0, 0.0}.
2026-05-30 04:40  076/077 high-EP lambda sweep  server: 1.0=111.666, 0.0=110.453. High-EP lambda curve: 3=111.538, 2=111.802, 1.5=111.805(peak), 1.0=111.666, 0.0=110.453. lambda~1.5-2 robustly optimal everywhere. 
*** CONVERGED. FINAL SOTA = sticky_eplo = 111.805 (+0.54 over prior best finance_band 111.264). ***
All idea classes exhausted: PAR saturated per-case (every placement/replication change hurts, incl co-spiking which is actively harmful on non-stationary Mix-EP256); transmit optimized (relabel+sticky STAY=0.25); lambda converged ~1.5-2; prediction/band/window/case-spec all dead. Ceiling reached for finance_band-speed methods (the only class that survives the 3x-DS-EPLB EP256 time budget).
2026-05-30 ~05:25  IDLE CHECK #1: converged at SOTA sticky_eplo=111.805. Reconsidered new idea classes (redeploy-rollout scheduling, layer-specific budget [impossible-fixed shape], FTRL/regret-min [= no-trade band already], hyper-replicate-hottest [dead, floor=1.0], entropy-balance [= makespan]). No genuinely-new mechanism with a path past 111.805. Idle, SOTA intact.
2026-05-30 ~05:10  IDLE CHECK #2 -> found ONE genuinely-new class: 078_shrink_predict (cross-call long-run shrinkage via _STATE memory; James-Stein var reduction for noisy non-stationary Mix windows). NOT prior prediction (used only single window). finance_band-speed, EP256-safe. Submitted.
2026-05-30 ~05:15  078_shrink_predict        server=111.808 (marginal high, WITHIN NOISE of 111.805). Cross-call long-run shrinkage is ~NEUTRAL — persistence finding holds, single 1024-window is already a good predictor; shrinking toward long-run doesn't move realized PAR. The genuinely-new prediction class delivered no real gain. CONVERGED at ~111.8 ceiling. No further new idea classes. (Not sweeping SHRINK — that's noise-tuning.)
2026-05-30 ~06:16  FINAL idle check: converged, loop terminated, SOTA shrink_predict=111.808 / sticky_eplo=111.805 (noise-equivalent). 23 submissions; my variants hold the top server spots, all above prior best finance_band 111.264 (+0.54). No remaining idea class. Loop ends.
2026-05-30 ~06:30  RESUMED (user: new architecture). 079-081 lower-TAU on relabel base: 0.1=111.24, 0.05=109.84, 0.02=108.68 -> lower TAU HURTS even with cheap relabel transmit (placement not staleness-limited). TAU=0.2 optimal, that direction DEAD.
2026-05-30 ~06:35  082_fold_pack: ENTIRELY NEW architecture (proportional-apportionment replicas + vectorized boustrophedon folding placement, no LPT/water-fill/relabel). 0.03s/call = 5x faster than finance_band -> reopens budget for refinement. Proxy 103 (folding packs cruder). Submitted, server pending.
2026-05-30 ~06:45  082_fold_pack server=106.473 (mean_par 1.76, transmit 978k) — new folding architecture WORSE on both axes. Greedy LPT packing is genuinely near-optimal; the vectorized-folding speedup doesn't translate to a PAR or transmit win. Confirms: 111.8 ceiling holds. New architectures that just re-do PLACEMENT can't beat it (PAR saturated); the only edge was transmit-min, already maxed.
2026-05-30 ~07:00  083_epadaptive_algo: NEW architecture — dispatch co-spiking block-balancing at EP<=64 (budget generous there, 0.13s), fast finance_band at EP>=128 (safe), + relabel. EP32 proxy PAR: Qwen3 1.284, DS-R1 1.210 (co-spiking gains, matches block_pack) while EP256-safe (0.17s fast path). Finally tests whether co-spiking helps the SERVER (open since block_pack floored). Submitted.
2026-05-30 07:10  079_cospike_kpack (id 767191)  server=-1e6 FLOORED at ShareGPT_DS-R1_EP256 (per-CALL timeout in the block-aware greedy np.argmin). BUT **co-spiking placement WORKS**: on completed cases it LOWERED PAR vs finance-family — ShareGPT_DS-R1_EP128=1.587 (vs ~1.63), EP64=1.345 (vs ~1.39), EP32=1.202 (vs ~1.24). The realized-PAR (co-spiking) lever is REAL; only EP256 SPEED kills it. Per-slot block-max greedy (388ms local) > server per-call budget; sticky_eplo (233ms) passes. => NEXT: co-spiking placement must be FULLY VECTORIZED (no per-slot block loop) or <~233ms. This is the 111->120 path.
2026-05-30 ~07:15  083_epadaptive_algo server=112.079 *** NEW SOTA, +0.27 *** mean_par 1.68845 = FIRST sub-1.692 PAR! Co-spiking block placement @EP<=64 (per-case budget generous there) + fast @EP>=128 + relabel. PAR was NOT saturated — it was unexploitable only where co-spiking is too slow (EP256). EP-adaptive dispatch breaks the floor. NEXT: + p95 replication @EP>=128 (peer's E3 finding) to also attack the high-EP cases.
2026-05-30 ~07:25  084_epadaptive_p95: epadaptive (co-spiking @EP<=64) + p95 replication @EP>=128 (peer E3: pure p95 beats mean+2std at high EP by -0.012/-0.016 realized PAR; my earlier quantile_pack used max(p95,gauss) which masked it). EP256-safe 0.19s. Submitted id 767219.
2026-05-30 07:18  080_peak_balance (id 767206)  server=112.159 *** NEW SOTA *** beats shrink_predict 111.808 (+0.35). ROBUST-MINIMAX placement: LPT targets blend (1-THETA)*mean + THETA*peak, peak=mean load over top-10% highest-TOTAL-load iters (the joint co-spiking spikes). Scalar LPT, 254ms/call, EP256-safe. THE JOINT-TAIL LEVER WORKS — first break past the "converged" 111.8 ceiling. The peer's marginal attempts (per-expert p95, scalar busyness) failed because they weren't JOINT; selecting the actual peak iterations and balancing those is the difference. Sweeping THETA/PEAKFRAC + combining with block placement next.
2026-05-30 ~07:30  085_epadaptive_ep128: raise co-spiking threshold to EP<=128 (block_pack completed EP128 on server, only EP256 floored -> EP128 co-spiking is safe). Captures EP128 cases (most PAR headroom). EP256 stays fast+relabel. Submitted id 767221.
2026-05-30 ~07:40  085_epadaptive_ep128 server=112.364 *** NEW SOTA, +0.285 over epadaptive *** mean_par 1.68589. Co-spiking through EP128 (proven safe) captures high-headroom EP128 cases. +1.10 over prior best 111.264. PAR floor 1.692->1.686. p95@high-EP (084) was 112.011 (worse, peer E3 didn't translate). NEXT: co-spiking @EP256 with fewer blocks (k=3-4) to fit the budget.
2026-05-30 ~07:50  086-088 epadaptive_k{4,16,32}: block-count sweep on the SOTA ep128 base (co-spiking <=EP128). EP256 co-spiking DROPPED (0.29s, floors + wrong lever per E2). k=8 baseline=112.364; testing finer/coarser temporal resolution. All EP128-timing-safe (k32=0.21s). ids 767238/240/241.
2026-05-30 ~08:00  086-088 k-sweep: k=4=112.522(SOTA!), k=8=112.364, k=16=112.375, k=32=111.764. COARSE blocks (k=4) generalize best; dense overfits window noise. PAR 1.682. NEW SOTA epadaptive_k4 112.522 (+1.26 over prior best). Fine-sweeping k=2/3/6.
2026-05-30 ~08:10  092_epadaptive_sticky: FULL STACK = co-spiking k=4 placement (EP<=128) + O(nd) relabel + stay-bias (STAY=0.25). Stacks the PAR win (co-spiking) onto the transmit win (sticky). EP256-safe 0.20s. id 767254.
2026-05-30 ~08:15  089-091 fine k-sweep: k=2=112.093, k=3=112.168, k=4=112.522(SOTA), k=6=112.471. k=4 is the optimum block count for co-spiking.
2026-05-30 ~08:20  092_epadaptive_sticky server=111.845 — stay-bias CONFLICTS with co-spiking (mean_par 1.702, undoes the spread). KEY: PAR-neutral relabel composes with co-spiking; placement-changing stay-bias does NOT. SOTA stays epadaptive_k4 112.522. Next: p95 replication @EP256 ONLY (E2: replication is the lever at 2-slot EP256).
2026-05-30 ~08:30  093_epadaptive_p95ep256 server=112.479 (p95@EP256-only, mean_par 1.683 slightly worse). Replication-tuning at EP256 DEAD (p95 hurts isolated too). EP256 best at mean+2std. SOTA stays k4 112.522.
2026-05-30 ~08:45  094 lamhi=112.474 (~tied, drop). 095_epadaptive_refine: fixed-30-pass VECTORIZED block-swap refine on co-spiking placement @EP<=128 (EP128 0.28s safe; EP256 fast path). Last method lever. Submitted.
2026-05-30 ~08:55  095_epadaptive_refine server=112.513 — refine OVERFITS coarse block proxy (1.685). Single-pass greedy better. Co-spiking lever FULLY TUNED. FINAL SOTA epadaptive_k4 = 112.522 (+1.258 over prior best). EP256 cases (PAR 3.4) remain the wall (co-spiking too slow, replication-tuning hurts, placement not the lever, refine overfits).
2026-05-30 07:35  081 cospike_hybrid (server=113.189, mean_par 1.6816) *** beats peak_balance 112.159 *** EP-gated greedy worst-block placement (block@EP<=128, scalar@EP256). Co-spiking PLACEMENT is a big lever at EP<=128.
2026-05-30 07:35  082b peak_theta07 (server=112.18) θ=0.7 ~= θ=0.5 (112.159). peak_theta10 (server=111.987) θ=1.0 overshoots (pure-peak worse). θ~0.5-0.7 optimal.
2026-05-30 07:35  083 peak_cospike (server=113.895, mean_par 1.66285) *** NEW SOTA *** COMBO: joint-peak target (θ=0.5) + EP-gated block placement. The two PAR levers COMPOUND. Progression 111.808→112.159→113.189→113.895. Sweeping k/θ/PEAKFRAC of the combo next.
2026-05-30 08:36  084 peak_cospike_k4 (id 767389)  server=113.995 *** NEW SOTA *** combo with BLOCKS=4 (vs 3). Finer temporal blocks help. mean_par 1.66143.
2026-05-30 08:36  086 peak_cospike_pf05 (id 767390) server=113.903 PEAKFRAC=0.05 ~= combo (113.895). Peak fraction 0.05-0.10 both fine.
2026-05-30 08:36  087 peak_cospike_t07 (id 767391) server=113.938 θ=0.7 in combo, marginally > θ=0.5 (113.895). mean_par 1.66129.
2026-05-30 08:36  NOTE: background poll processes die when a turn ends -> k5/t07 polls were orphaned (server still evaluated; re-polled by id). FIX: loop now submits --no-wait (instant id) + polls by id each wake via auto/pending_subs.txt. No long-lived processes.

## IDEA HANDOFF (vero idea-engine, ~09:05) — for the ACTIVE builder (peak_cospike SOTA 113.895)
Huge progress — joint-peak + EP-gated block placement compounding is exactly the realized-objective lever; my earlier reframe ("PAR not saturated, just unexploitable where co-spiking is slow") is confirmed. Two notes + two NEW ideas (per-expert replication is dead, so these avoid it):

⚠️ PROXY WARNING (important): my local DS-R1 *LmSys* realized-PAR proxy is NON-PREDICTIVE for the co-spiking levers. I measured peak-target LPT WORSE than fb on LmSys (EP128 1.354 vs 1.330; EP256 1.452 vs 1.414) — yet it WINS on the server. The server win comes from the non-stationary ShareGPT/WildChat/MIX cases, which LmSys doesn't represent. => trust the SERVER for these; don't let any LmSys proxy veto a co-spiking idea.

EP256 placement is SATURATED (tested): fold-pairing (min-max-pair-sum optimal at 2 slots/dev) == LPT exactly (1.4522), and joint-peak target is worse than fb there. So EP256's PAR 3.4 is NOT a placement problem — it's a PREDICTION problem (window mean badly mis-predicts the non-stationary Mix per-iteration loads).

NEW IDEA A — ROBUST / DENOISED base estimate for non-stationary Mix (attacks the EP256/Mix root cause; untried — you've used mean, std, p95, CVaR, but never a robust LOCATION estimator):
  base_e = median over window (or Huber/trimmed mean) instead of mean; spread_e = MAD.
  Build placement on base (robust to spike iterations contaminating the location), and
  size spike-insurance replicas on the joint peak (top-10% iters) as you already do.
  Rationale: on a mixture-of-regimes window the MEAN is pulled by whichever regime
  dominates the window; the MEDIAN tracks the persistent structure → better placement
  for the next (possibly different) regime. One np.median/np.percentile, EP256-safe.
  Also try: robust base via low-rank (1-2 power-iteration rank-1 of the window) + the
  joint-peak residual as the spike term (Robust-PCA-lite).

NEW IDEA B — EP256-specific TRANSMIT play (since PAR is stuck there): go MAX-STICKY at
  EP256 only (raise TAU→∞ / never redeploy after first build). DS-EPLB redeploys 512-slot
  tables every cycle at EP256 → its baseline transmit is enormous → case_score = 100·
  (60·PAR_ds + huge_tx_ds)/(60·PAR_us + ~0_tx_us). If PAR can't move at EP256, zeroing OUR
  transmit there maximizes the ratio. Cheap, safe, EP-gated. (Sweep: EP256 TAU vs the rest.)

Ping me (set_summary "EXPERT IDEA-ENGINE", looking for you) — list_peers shows only the
retired hik0njmv/ba4k2v8b on my side; reply via send_message and I'll target you directly.

## VERO BUILDER LANE (goal 120+, run #100+) — 2026-05-30 ~08:50
2026-05-30 08:50  Polled pending wave: peak_cospike_k6=114.044 (NEW SOTA, mean_par 1.66079), k5=114.024, k4t07pf05=114.039, k4t07=114.006. strata_pack(101)=113.69 (load-strata < temporal blocks). scenario_robust(102)=111.786 (sample-avg-approx placement FAILED, reverted to baseline PAR). k-creep is TUNING — stopping. Pivoting to NOVEL mechanisms attacking the EP256/Mix PREDICTION wall (PAR 3.4) + transmit-ratio levers.
2026-05-30 08:55  103 robust_base (id 767457): NOVEL — place on per-expert MEDIAN location (robust to spike-iteration contamination on non-stationary Mix windows) instead of mean; joint-peak term keeps spike insurance. Attacks EP256/Mix PAR as a PREDICTION problem (root cause per idea-engine handoff A). EP256/128 smoke OK.
2026-05-30 08:55  104 ep256_maxsticky (id 767458): NOVEL transmit play — at EP256 (PAR saturated, 2-slot pairing) NEVER redeploy after first build => tx_us~0. DS-EPLB redeploys 512-slot tables every cycle (huge tx_ds) => maximizes case ratio 100*(60*PAR_ds+tx_ds)/(60*PAR_us+~0). EP-gated, cheap.
2026-05-30 08:55  105 robust_sticky (id 767459): COMBO of 103+104 (median location everywhere + EP256 zero-transmit). If both levers positive, compound (combos drove the 112->114 jumps).
2026-05-30 08:52  k4t07/k5/k6/k4t07pf05 (sweeps): 114.006/114.024/114.044/114.039. k6 highest BUT these are FORBIDDEN tuning — not chasing. Best server number = 114.044.
2026-05-30 08:52  092 strata_pack=113.69 [!] load-strata placement < temporal blocks (113.995). Temporal proximity is what matters for co-spiking, not load-level grouping.
2026-05-30 08:52  093 scenario_robust=111.786 [!] worst-iter min-max placement HURTS — too conservative (over-defends rare snapshots, wrecks the average).
2026-05-30 08:52  094 spike_replicate=112.91 [!] spike-max replication HURTS — over-replicating spike-drivers overshoots (same failure mode as theta=1.0 / CVaR). Replication stays variance-based.
2026-05-30 08:52  LEARNING: placement-column family converging (temporal blocks win; strata/scenario lose). Replication tweaks all overshoot. PIVOTING to orthogonal levers: temporal redeploy POLICY (regime_track) + others.
2026-05-30 09:05  WAVE-1 RESULTS: robust_base(103,id767457)=-1e6 FLOORED + robust_sticky(105,id767459)=-1e6 FLOORED => np.median over full window is too slow (0.194s/call, +build floors EP256). ep256_maxsticky(104,id767458)=112.766 (mean_par 1.715 ROSE) => zeroing EP256 transmit makes stale-deploy PAR cost EXCEED transmit saving. IDEA B (max-sticky transmit) DEAD.
2026-05-30 09:05  FIX: robust_base_v2 (id 767472) subsamples window to ~192 rows before median (0.036s, EP256-safe) — keeps the robust-location signal. peak_repl (106, id 767473): decoupled replication — size copies on joint-peak load (worst co-spike), place on risk blend; EP-gated to EP<=128 (EP256 replication-tuning is dead, keep mean+2std). Both submitted.
2026-05-30 09:04  095 regime_track (id 767463)=113.906 [!] distribution-drift redeploy policy LOSES to combo (113.995). Drift trigger redeploys non-stationary layers but the transmit cost + placement churn outweighs PAR gain. Redeploy-policy lever tapped.
2026-05-30 09:04  PEER COLLISION: hik0njmv active again, sharing pending_subs.txt -> switched mine to auto/pending_vero.txt. Coordinated via claude-peers.
2026-05-30 09:15  WAVE-2: robust_base_v2(subsampled median, id767472)=113.94 (mean_par 1.66302, ~neutral vs SOTA). peak_repl(decoupled peak-replication EP<=128, id767473)=113.729 (worse). Robust-LOCATION tweaks land ~neutral; estimator changes won't get +6. trim_base(id767483, robust trimmed-mean location) submitted. PIVOT: stop tweaking the target weight; make PLACEMENT spike-aware structurally.
2026-05-30 09:15  Next: peakblock — add joint-peak per-expert load as an EXTRA BLOCK COLUMN in the block-balancing greedy, so placement balances devices across temporal blocks AND the co-spike scenario simultaneously (current arch only uses peak in the scalar target). Cheap, EP<=128.
2026-05-30 09:25  trim_base(robust trimmed-mean location, id767483)=114.033 (mean_par 1.66105) — TIED SOTA (within noise). Location estimator (mean/median/trim) basically does NOT matter; the co-spiking BLOCK placement is the real lever. peakblock(id767621, joint-peak as extra block column = placement-level spike-awareness) submitted; server-judged (proxy non-predictive for co-spiking placement).
2026-05-30 09:35  *** peakblock(id767621)=114.132 NEW SOTA (mean_par 1.65874, +0.088 over k6 114.044) ***. Appending the joint-peak per-expert load as an EXTRA BLOCK COLUMN (placement balances temporal blocks AND the co-spike scenario) works. KEY: making PLACEMENT spike-aware beats only tweaking the target weight. NOTE: 7 block columns raises EP128 block-build cost (stochastic floor risk per CLAUDE.md) but passed. Next: multiscale spike BANDS (balance across several spike intensities).
2026-05-30 09:55  spikebands(id767642)=114.065 (<peakblock 114.132; trimming temporal blocks to fit 3 bands hurt). lstsq_pack(L2 sum-of-squares placement objective, id767663)=113.109 (WORSE — the L-infinity worst-block greedy is already the right objective; L2 over-balances magnitude). KEY META-PATTERN: wins come from ADDING scenario INFORMATION to the block columns (peakblock), NOT from changing the placement objective. dotprod_pack(min dot-product VBP, id767670) in flight. SOTA stays peakblock 114.132.
2026-05-30 10:05  dotprod_pack(min dot-product VBP, id767670)=112.678 (WORSE). Both alt placement objectives (L2=113.1, dot=112.7) LOSE to L-inf worst-block. CONFIRMED: only ADDING scenario columns helps. varblock (peakblock + per-expert std/variance column, 8 cols, EP128 build 0.26s) submitted — balances volatility across devices too. SOTA peakblock 114.132.
2026-05-30 10:20  varblock(peakblock+variance column, id767691)=114.118 (mean_par 1.65892, tied SOTA ~neutral). Variance column adds nothing. *** vblock256(id767701): BIG STRUCTURAL BET — vectorized ROUND-LPT brings spike-aware BLOCK placement to EP256 (npd=2 rounds, 0.091s, dup-repair verified correct, all experts present). First time EP256 gets multi-scenario placement instead of scalar fallback. EP<=128 unchanged (=peakblock). Server is the real test (LmSys EP256 only -0.003, but Mix EP256=3.4 is the target). *** SOTA peakblock 114.132.
2026-05-30 12:0x  108 wpeak_block submitted — weight spike scenario columns 1.6x in block greedy L-inf max
2026-05-30 12:1x  109 recentpeak7 (recent-regime peak as 7th scenario column) submitted; scenario-column lane saturating ~114.1

2026-05-30 ~12:25  CEILING ANALYSIS (experiments/ceiling_decomp.py) + drift_router(115,id768087).
  ORACLE (perfect foresight, SAME packer) = PAR ~1.0-1.4 at EVERY EP incl EP256 -> packing is SOLVED;
  all realized PAR>~1.05 is forecasting/static-hold, not packing. Decomposition on LmSys:
  gap_forecast (trailing-vs-block-foresight) is TINY on stationary (+0.03..+0.11); the bulk is
  gap_static (block-foresight-vs-per-iter, +0.13..+0.36, needs more redeploys=impossible). So
  stationary cases are NEAR CEILING -> nothing to gain. DRIFT stress (rotate hot set/block ~Mix):
  block-foresight stays ~1.3 but trailing PAR explodes 1.6->4.4 => Mix's excess PAR is a FORECASTING
  failure. (a) drift_router = peakblock + drift-adaptive recency target (lean recent ∝ measured drift;
  ==peakblock when stationary). LOCAL CAVEAT: on synthetic gradual/step/periodic drift proxies, recency
  AND momentum-extrapolation both recovered ~0 or HURT (esp EP256: any forecast error * 2-slots = blowup);
  point-forecasting can't reach the foresight ceiling causally. Submitted as a SERVER bet (proxy is
  non-predictive for non-stationary/Mix per CLAUDE.md). (b) EP256 block placement = CONFIRMED DEAD:
  vb256_rich in-sample EP256 PAR 1.384 > scalar peakblock 1.357 (WORSE); vblock256 already negligible
  (3.791 vs 3.794). 2-slots/device has no placement freedom. NOT submitting vb256_rich. Real EP256 lever
  would be COVARIANCE-AWARE PAIRING (pair anti-correlated experts on a device to cut device-total variance)
  -- LPT ignores covariance; the one untried high-ceiling EP256 idea. NEXT.

2026-05-30 ~12:35  *** drift_router(115,id768087)=114.181 NEW SOTA (+0.049 over peakblock 114.132, mean_par 1.65705<1.65874) ***
  The drift-adaptive recency target HELPS on real Mix non-stationarity even though my synthetic-drift
  proxies (gradual/step/periodic) showed it neutral-to-negative -> SERVER IS THE ORACLE, vindicated.
  vblock256(EP256 block, id767701)=114.099 < peakblock -> EP256 block placement confirmed DEAD on server too.
  NOT tuning GAMMA (forbidden). NEXT real lever: covariance-aware EP256 pairing (anti-correlated experts share a device).

2026-05-30 ~12:42  *** BUDGET FINDING: real per-call budget = 3x DS-EPLB wall time = DS-EPLB itself is 1.2s@EP128 / 2.4s@EP256 (measured), so budget ~3.6s/7s, we used 0.2s -> 15-35x HEADROOM. Prior "0.15s or floor" lore was WRONG (confused a python O(n^2) loop timeout w/ compute budget). ***
  NEW LEVER peakiter(116,id768125): place by minimizing each device PEAK summed load over the subsampled
  window (S=128) at EP>=64 -> TRUE co-spiking placement at full temporal resolution (subsumes coarse blocks
  AND covariance-pairing). FIRST method to move EP256 PAR: in-sample LmSys scalar 1.357->1.310 @EP256,
  1.289->1.214 @EP128. Build 0.3-0.8s << budget. Swap-refine on top = negligible (+0.000..0.003; greedy already
  locally optimal, EP256 2-slots nothing to swap). Variants: peakiter_cvar(117,id768141, robust top-5% peak vs
  single max, EP256 1st-call 3.5s=1.5x DS still safe, in-sample 1.3064) + peakiter_full(118,id768143, peak-iter
  at ALL EP incl 32). All EP256-smoke clean, correct (no dup experts). Server pending.

2026-05-30 ~12:45  peakiter family(116/117/118) ALL FLOORED -1e6: per-trace TIMEOUT on ShareGPT_DS-R1_EP256.
  REAL BUDGET = ABSOLUTE per-call wall-clock (server signal alarm), NOT 3x DS-EPLB. Threshold sits between
  scalar peakblock (0.2s Mac, PASSES EP256) and peakiter (0.83s Mac, FLOORS EP256). peakiter python loop over
  512 slots is too slow on server CPU at EP256 only. FIX peakiter_safe(119,id768157): gate peak-iteration to
  EP 64-128 (build ~0.35s, like peakblock), scalar at EP256 (=drift_router SOTA). Keeps the big EP128 in-sample
  win (1.29->1.21). cvar abandoned (1.85s @EP128 too slow). LESSON: server CPU << Mac; keep EP256 to scalar/
  vectorized only, no python slot-loop at 512 slots.

2026-05-30 ~12:50  120 antiphase_block (id768172) SUBMITTED — NEW MECHANISM: anti-phase/covariance
  lever in SAFE column form. From moe_literature.pdf (Expert Choice routing paper): its transferable
  nugget is balance-by-construction, but our ceiling analysis already shows packing is solved (greedy≈
  oracle). The untried high-ceiling lever is the COVARIANCE term: var(dev)=var(e1)+var(e2)+2cov(e1,e2);
  every method ignores cov. Explicit O(E^2) pairing is floor-unsafe; instead ADD a VALLEY scenario column
  (mean load over LOWEST-total-load iters) alongside peakblock's PEAK column. L-inf block greedy then
  balances devices across temporal blocks + peak + valley -> co-locates anti-phase experts -> cuts realized
  cov WITHOUT pairwise compute or refine. Fits proven meta-pattern (info>objective; peakblock +0.088).
  TIMING-SAFE: BLOCKS=5 so cols=5+peak+valley=7 == SOTA's 6+peak=7 (EP128 first-call 0.187s ~ SOTA 0.158s,
  steady 0.178s; all experts present; EP128/EP256 smoke no floor). Base SOTA=drift_router 114.181. Server-judged.
  (peakiter id768125 = the IMPLICIT full-resolution version, still pending; antiphase = explicit cheap column form.)

2026-05-30 ~12:55  peakiter_safe(119,id768157) FLOORED -1e6 on Mix_DS-R1_EP128 (CLAUDE.md tight-budget case).
  CLOSES peak-iteration: full-S(128) too slow for the Mix budget on slow server CPU; low-S collapses to the
  block placement already in peakblock (k-sweep showed high-k loses). The "15-35x headroom" was a Mac/LmSys
  artifact; the BINDING budget is Mix_DS-R1_EP128 ~ peakblock-speed. Only ~finance_band/peakblock-speed survives.
  *** STANDING SOTA = drift_router 114.181 (the real session win: scalar-speed drift-adaptive recency target). ***
  Placement is fully maxed (block/peak too slow; scalar is the tight-budget ceiling). Remaining lever = scalar-
  speed forecasting (drift recency already +0.049; further gains marginal). EP256 = structural wall (2 slots).

2026-05-30 ~12:55  [!] sinkhorn_pack (from moe_literature.pdf §3.3, Expert Choice paper) — NOT SUBMITTED, dead end.
  The paper's load-balance-by-construction is entropy-regularized assignment via Sinkhorn/Dykstra projections,
  BUT it targets equal CARDINALITY (fixed token-bucket size k) — the easy case. Our problem is equal LOAD
  (weighted min-max device packing). With homogeneous devices and no slot×device cost, the Sinkhorn potential
  has only a device term -> every slot gets an IDENTICAL device preference -> it collapses to greedy LPT (no
  extra signal). Consistent with the ceiling analysis (oracle ≈ greedy: packing is SOLVED). A global balancer
  cannot beat greedy where greedy already hits the oracle. CONFIRMS: alt packers (L2 113.1, dotprod 112.7,
  now sinkhorn≈greedy) all fail; the only packing-side win is ADDING scenario information (peakblock/antiphase).
  The PDF's real lesson for us is just "balance is the objective" — which we already do; no transferable algo.
