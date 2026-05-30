# Auto Leaderboard

Ranked by mean score across all evaluated cases (higher is better).
Score of 100 = matches DS-EPLB baseline. Failed runs sink to the bottom.

| rank | run | mean_score | cases | error |
|---:|:---|---:|:---|:---|
| 1 | `095_epadaptive_refine` | 109.3468 | Qwen3/LmSys/EP32=105.94, DS-R1/LmSys/EP32=112.75 |  |
| 2 | `083_epadaptive_algo` | 109.1091 | Qwen3/LmSys/EP32=105.71, DS-R1/LmSys/EP32=112.51 |  |
| 3 | `041_cov_place` | 108.8683 | Qwen3/LmSys/EP32=106.25, DS-R1/LmSys/EP32=111.48 |  |
| 4 | `040_cvar_repl` | 108.6276 | Qwen3/LmSys/EP32=106.49, DS-R1/LmSys/EP32=110.76 |  |
| 5 | `069_cheap_relabel_v2` | 108.0051 | Qwen3/LmSys/EP32=105.53, DS-R1/LmSys/EP32=110.48 |  |
| 6 | `070_sticky_relabel` | 108.0051 | Qwen3/LmSys/EP32=105.53, DS-R1/LmSys/EP32=110.48 |  |
| 7 | `020_band_refine_transmin` | 100.3455 | Qwen3/LmSys/EP32=100.31, DS-R1/LmSys/EP32=100.38 |  |
| 8 | `021_transmin_l2` | 100.3352 | Qwen3/LmSys/EP32=100.31, DS-R1/LmSys/EP32=100.36 |  |
| 9 | `019_band_swap_refine` | 100.0584 | Qwen3/LmSys/EP32=100.02, DS-R1/LmSys/EP32=100.10 |  |
| 10 | `014_fb_lambda_1` | 100.0458 | Qwen3/LmSys/EP32=100.00, DS-R1/LmSys/EP32=100.09 |  |
| 11 | `008_finance_band` | 100.0365 | Qwen3/LmSys/EP32=99.98, DS-R1/LmSys/EP32=100.09 |  |
| 12 | `009_fb_tau_005` | 100.0365 | Qwen3/LmSys/EP32=99.98, DS-R1/LmSys/EP32=100.09 |  |
| 13 | `010_fb_tau_010` | 100.0365 | Qwen3/LmSys/EP32=99.98, DS-R1/LmSys/EP32=100.09 |  |
| 14 | `011_fb_tau_040` | 100.0365 | Qwen3/LmSys/EP32=99.98, DS-R1/LmSys/EP32=100.09 |  |
| 15 | `012_fb_tau_080` | 100.0365 | Qwen3/LmSys/EP32=99.98, DS-R1/LmSys/EP32=100.09 |  |
| 16 | `018_global_swap_refine` | 100.0233 | Qwen3/LmSys/EP32=100.02, DS-R1/LmSys/EP32=100.03 |  |
| 17 | `002_seed_ds_eplb` | 100.0000 | Qwen3/LmSys/EP32=100.00, DS-R1/LmSys/EP32=100.00 |  |
| 18 | `006_ds_eplb_hierarchical` | 100.0000 | Qwen3/LmSys/EP32=100.00, DS-R1/LmSys/EP32=100.00 |  |
| 19 | `013_fb_lambda_0` | 99.9998 | Qwen3/LmSys/EP32=100.00, DS-R1/LmSys/EP32=100.00 |  |
| 20 | `017_fb_conservative` | 99.9998 | Qwen3/LmSys/EP32=100.00, DS-R1/LmSys/EP32=100.00 |  |
| 21 | `015_fb_lambda_4` | 99.9516 | Qwen3/LmSys/EP32=99.91, DS-R1/LmSys/EP32=100.00 |  |
| 22 | `016_fb_aggressive` | 99.9516 | Qwen3/LmSys/EP32=99.91, DS-R1/LmSys/EP32=100.00 |  |
| 23 | `007_ema_hot_expert` | 99.5782 | Qwen3/LmSys/EP32=98.88, DS-R1/LmSys/EP32=100.28 |  |
| 24 | `004_hot_expert_par_priority` | 99.4630 | Qwen3/LmSys/EP32=98.87, DS-R1/LmSys/EP32=100.06 |  |
| 25 | `005_hot_expert_top2_redundant` | 99.4630 | Qwen3/LmSys/EP32=98.87, DS-R1/LmSys/EP32=100.06 |  |
| 26 | `001_seed_hot_expert` | 99.4159 | Qwen3/LmSys/EP32=98.80, DS-R1/LmSys/EP32=100.04 |  |
| 27 | `003_seed_ds_eplb_throttled` | 99.1339 | Qwen3/LmSys/EP32=100.00, DS-R1/LmSys/EP32=98.27 |  |
| 28 | `000_seed_smoke` | 97.4126 | Qwen3/LmSys/EP32=96.56, DS-R1/LmSys/EP32=98.27 |  |
| 29 | `050_rep_aware_refine` | — | — | no score.json (not evaluated) |
| 30 | `0516_block_pack_k16` | — | — | no score.json (not evaluated) |
| 31 | `051_fb_tau005_l2` | — | — | no score.json (not evaluated) |
| 32 | `052_fb_tau01_l2` | — | — | no score.json (not evaluated) |
| 33 | `0532_block_pack_k32` | — | — | no score.json (not evaluated) |
| 34 | `053_fb_window512_l2` | — | — | no score.json (not evaluated) |
| 35 | `054_fb_lambda25` | — | — | no score.json (not evaluated) |
| 36 | `055_fb_convex` | — | — | no score.json (not evaluated) |
| 37 | `056_block_pack` | — | — | no score.json (not evaluated) |
| 38 | `057_block_pack_k16` | — | — | no score.json (not evaluated) |
| 39 | `058_block_pack_k32` | — | — | no score.json (not evaluated) |
| 40 | `059_spike_iter_pack` | — | — | no score.json (not evaluated) |
| 41 | `060_predict_trend` | — | — | no score.json (not evaluated) |
| 42 | `061_pack_refine_fast` | — | — | no score.json (not evaluated) |
| 43 | `062_cospike_weight` | — | — | no score.json (not evaluated) |
| 44 | `065_decouple_pack` | — | — | no score.json (not evaluated) |
| 45 | `066_quantile_pack` | — | — | no score.json (not evaluated) |
| 46 | `067_maxsubwin` | — | — | no score.json (not evaluated) |
| 47 | `068_cheap_relabel` | — | — | no score.json (not evaluated) |
| 48 | `07015_sticky_stay015` | — | — | no score.json (not evaluated) |
| 49 | `07040_sticky_stay040` | — | — | no score.json (not evaluated) |
| 50 | `07060_sticky_stay060` | — | — | no score.json (not evaluated) |
| 51 | `074_sticky_epadapt` | — | — | no score.json (not evaluated) |
| 52 | `075_sticky_eplo` | — | — | no score.json (not evaluated) |
| 53 | `076_sticky_eplo00` | — | — | no score.json (not evaluated) |
| 54 | `076_sticky_eplo10` | — | — | no score.json (not evaluated) |
| 55 | `078_shrink_predict` | — | — | no score.json (not evaluated) |
| 56 | `079_cospike_kpack` | — | — | no score.json (not evaluated) |
| 57 | `079_sticky_tau002` | — | — | no score.json (not evaluated) |
| 58 | `079_sticky_tau005` | — | — | no score.json (not evaluated) |
| 59 | `079_sticky_tau010` | — | — | no score.json (not evaluated) |
| 60 | `080_peak_balance` | — | — | no score.json (not evaluated) |
| 61 | `081_cospike_hybrid` | — | — | no score.json (not evaluated) |
| 62 | `082_fold_pack` | — | — | no score.json (not evaluated) |
| 63 | `083_peak_cospike` | — | — | no score.json (not evaluated) |
| 64 | `084_epadaptive_p95` | — | — | no score.json (not evaluated) |
| 65 | `085_epadaptive_ep128` | — | — | no score.json (not evaluated) |
| 66 | `086_epadaptive_k16` | — | — | no score.json (not evaluated) |
| 67 | `086_epadaptive_k32` | — | — | no score.json (not evaluated) |
| 68 | `086_epadaptive_k4` | — | — | no score.json (not evaluated) |
| 69 | `089_epadaptive_k2` | — | — | no score.json (not evaluated) |
| 70 | `089_epadaptive_k3` | — | — | no score.json (not evaluated) |
| 71 | `089_epadaptive_k6` | — | — | no score.json (not evaluated) |
| 72 | `092_epadaptive_sticky` | — | — | no score.json (not evaluated) |
| 73 | `093_epadaptive_p95ep256` | — | — | no score.json (not evaluated) |
| 74 | `094_epadaptive_k4_lamhi` | — | — | no score.json (not evaluated) |
