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
