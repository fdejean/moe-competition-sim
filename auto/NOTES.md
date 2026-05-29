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
