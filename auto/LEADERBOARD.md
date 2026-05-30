# Auto Leaderboard

Ranked by mean score across all evaluated cases (higher is better).
Score of 100 = matches DS-EPLB baseline. Failed runs sink to the bottom.

| rank | run | mean_score | cases | error |
|---:|:---|---:|:---|:---|
| 1 | `041_cov_place` | 108.8683 | Qwen3/LmSys/EP32=106.25, DS-R1/LmSys/EP32=111.48 |  |
| 2 | `040_cvar_repl` | 108.6276 | Qwen3/LmSys/EP32=106.49, DS-R1/LmSys/EP32=110.76 |  |
| 3 | `069_cheap_relabel_v2` | 108.0051 | Qwen3/LmSys/EP32=105.53, DS-R1/LmSys/EP32=110.48 |  |
| 4 | `070_sticky_relabel` | 108.0051 | Qwen3/LmSys/EP32=105.53, DS-R1/LmSys/EP32=110.48 |  |
| 5 | `020_band_refine_transmin` | 100.3455 | Qwen3/LmSys/EP32=100.31, DS-R1/LmSys/EP32=100.38 |  |
| 6 | `021_transmin_l2` | 100.3352 | Qwen3/LmSys/EP32=100.31, DS-R1/LmSys/EP32=100.36 |  |
| 7 | `019_band_swap_refine` | 100.0584 | Qwen3/LmSys/EP32=100.02, DS-R1/LmSys/EP32=100.10 |  |
| 8 | `014_fb_lambda_1` | 100.0458 | Qwen3/LmSys/EP32=100.00, DS-R1/LmSys/EP32=100.09 |  |
| 9 | `008_finance_band` | 100.0365 | Qwen3/LmSys/EP32=99.98, DS-R1/LmSys/EP32=100.09 |  |
| 10 | `009_fb_tau_005` | 100.0365 | Qwen3/LmSys/EP32=99.98, DS-R1/LmSys/EP32=100.09 |  |
| 11 | `010_fb_tau_010` | 100.0365 | Qwen3/LmSys/EP32=99.98, DS-R1/LmSys/EP32=100.09 |  |
| 12 | `011_fb_tau_040` | 100.0365 | Qwen3/LmSys/EP32=99.98, DS-R1/LmSys/EP32=100.09 |  |
| 13 | `012_fb_tau_080` | 100.0365 | Qwen3/LmSys/EP32=99.98, DS-R1/LmSys/EP32=100.09 |  |
| 14 | `018_global_swap_refine` | 100.0233 | Qwen3/LmSys/EP32=100.02, DS-R1/LmSys/EP32=100.03 |  |
| 15 | `002_seed_ds_eplb` | 100.0000 | Qwen3/LmSys/EP32=100.00, DS-R1/LmSys/EP32=100.00 |  |
| 16 | `006_ds_eplb_hierarchical` | 100.0000 | Qwen3/LmSys/EP32=100.00, DS-R1/LmSys/EP32=100.00 |  |
| 17 | `013_fb_lambda_0` | 99.9998 | Qwen3/LmSys/EP32=100.00, DS-R1/LmSys/EP32=100.00 |  |
| 18 | `017_fb_conservative` | 99.9998 | Qwen3/LmSys/EP32=100.00, DS-R1/LmSys/EP32=100.00 |  |
| 19 | `015_fb_lambda_4` | 99.9516 | Qwen3/LmSys/EP32=99.91, DS-R1/LmSys/EP32=100.00 |  |
| 20 | `016_fb_aggressive` | 99.9516 | Qwen3/LmSys/EP32=99.91, DS-R1/LmSys/EP32=100.00 |  |
| 21 | `007_ema_hot_expert` | 99.5782 | Qwen3/LmSys/EP32=98.88, DS-R1/LmSys/EP32=100.28 |  |
| 22 | `004_hot_expert_par_priority` | 99.4630 | Qwen3/LmSys/EP32=98.87, DS-R1/LmSys/EP32=100.06 |  |
| 23 | `005_hot_expert_top2_redundant` | 99.4630 | Qwen3/LmSys/EP32=98.87, DS-R1/LmSys/EP32=100.06 |  |
| 24 | `001_seed_hot_expert` | 99.4159 | Qwen3/LmSys/EP32=98.80, DS-R1/LmSys/EP32=100.04 |  |
| 25 | `003_seed_ds_eplb_throttled` | 99.1339 | Qwen3/LmSys/EP32=100.00, DS-R1/LmSys/EP32=98.27 |  |
| 26 | `000_seed_smoke` | 97.4126 | Qwen3/LmSys/EP32=96.56, DS-R1/LmSys/EP32=98.27 |  |
| 27 | `050_rep_aware_refine` | — | — | no score.json (not evaluated) |
| 28 | `0516_block_pack_k16` | — | — | no score.json (not evaluated) |
| 29 | `051_fb_tau005_l2` | — | — | no score.json (not evaluated) |
| 30 | `052_fb_tau01_l2` | — | — | no score.json (not evaluated) |
| 31 | `0532_block_pack_k32` | — | — | no score.json (not evaluated) |
| 32 | `053_fb_window512_l2` | — | — | no score.json (not evaluated) |
| 33 | `054_fb_lambda25` | — | — | no score.json (not evaluated) |
| 34 | `055_fb_convex` | — | — | no score.json (not evaluated) |
| 35 | `056_block_pack` | — | — | no score.json (not evaluated) |
| 36 | `057_block_pack_k16` | — | — | no score.json (not evaluated) |
| 37 | `058_block_pack_k32` | — | — | no score.json (not evaluated) |
| 38 | `059_spike_iter_pack` | — | — | no score.json (not evaluated) |
| 39 | `060_predict_trend` | — | — | no score.json (not evaluated) |
| 40 | `061_pack_refine_fast` | — | — | no score.json (not evaluated) |
| 41 | `062_cospike_weight` | — | — | no score.json (not evaluated) |
| 42 | `065_decouple_pack` | — | — | no score.json (not evaluated) |
| 43 | `066_quantile_pack` | — | — | no score.json (not evaluated) |
| 44 | `067_maxsubwin` | — | — | no score.json (not evaluated) |
| 45 | `068_cheap_relabel` | — | — | no score.json (not evaluated) |
| 46 | `07015_sticky_stay015` | — | — | no score.json (not evaluated) |
| 47 | `07040_sticky_stay040` | — | — | no score.json (not evaluated) |
| 48 | `07060_sticky_stay060` | — | — | no score.json (not evaluated) |
| 49 | `074_sticky_epadapt` | — | — | no score.json (not evaluated) |
| 50 | `075_sticky_eplo` | — | — | no score.json (not evaluated) |
| 51 | `076_sticky_eplo00` | — | — | no score.json (not evaluated) |
| 52 | `076_sticky_eplo10` | — | — | no score.json (not evaluated) |
