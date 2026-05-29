# Auto Leaderboard

Ranked by mean score across all evaluated cases (higher is better).
Score of 100 = matches DS-EPLB baseline. Failed runs sink to the bottom.

| rank | run | mean_score | cases | error |
|---:|:---|---:|:---|:---|
| 1 | `020_band_refine_transmin` | 100.3455 | Qwen3/LmSys/EP32=100.31, DS-R1/LmSys/EP32=100.38 |  |
| 2 | `021_transmin_l2` | 100.3352 | Qwen3/LmSys/EP32=100.31, DS-R1/LmSys/EP32=100.36 |  |
| 3 | `019_band_swap_refine` | 100.0584 | Qwen3/LmSys/EP32=100.02, DS-R1/LmSys/EP32=100.10 |  |
| 4 | `014_fb_lambda_1` | 100.0458 | Qwen3/LmSys/EP32=100.00, DS-R1/LmSys/EP32=100.09 |  |
| 5 | `008_finance_band` | 100.0365 | Qwen3/LmSys/EP32=99.98, DS-R1/LmSys/EP32=100.09 |  |
| 6 | `009_fb_tau_005` | 100.0365 | Qwen3/LmSys/EP32=99.98, DS-R1/LmSys/EP32=100.09 |  |
| 7 | `010_fb_tau_010` | 100.0365 | Qwen3/LmSys/EP32=99.98, DS-R1/LmSys/EP32=100.09 |  |
| 8 | `011_fb_tau_040` | 100.0365 | Qwen3/LmSys/EP32=99.98, DS-R1/LmSys/EP32=100.09 |  |
| 9 | `012_fb_tau_080` | 100.0365 | Qwen3/LmSys/EP32=99.98, DS-R1/LmSys/EP32=100.09 |  |
| 10 | `018_global_swap_refine` | 100.0233 | Qwen3/LmSys/EP32=100.02, DS-R1/LmSys/EP32=100.03 |  |
| 11 | `002_seed_ds_eplb` | 100.0000 | Qwen3/LmSys/EP32=100.00, DS-R1/LmSys/EP32=100.00 |  |
| 12 | `006_ds_eplb_hierarchical` | 100.0000 | Qwen3/LmSys/EP32=100.00, DS-R1/LmSys/EP32=100.00 |  |
| 13 | `013_fb_lambda_0` | 99.9998 | Qwen3/LmSys/EP32=100.00, DS-R1/LmSys/EP32=100.00 |  |
| 14 | `017_fb_conservative` | 99.9998 | Qwen3/LmSys/EP32=100.00, DS-R1/LmSys/EP32=100.00 |  |
| 15 | `015_fb_lambda_4` | 99.9516 | Qwen3/LmSys/EP32=99.91, DS-R1/LmSys/EP32=100.00 |  |
| 16 | `016_fb_aggressive` | 99.9516 | Qwen3/LmSys/EP32=99.91, DS-R1/LmSys/EP32=100.00 |  |
| 17 | `007_ema_hot_expert` | 99.5782 | Qwen3/LmSys/EP32=98.88, DS-R1/LmSys/EP32=100.28 |  |
| 18 | `004_hot_expert_par_priority` | 99.4630 | Qwen3/LmSys/EP32=98.87, DS-R1/LmSys/EP32=100.06 |  |
| 19 | `005_hot_expert_top2_redundant` | 99.4630 | Qwen3/LmSys/EP32=98.87, DS-R1/LmSys/EP32=100.06 |  |
| 20 | `001_seed_hot_expert` | 99.4159 | Qwen3/LmSys/EP32=98.80, DS-R1/LmSys/EP32=100.04 |  |
| 21 | `003_seed_ds_eplb_throttled` | 99.1339 | Qwen3/LmSys/EP32=100.00, DS-R1/LmSys/EP32=98.27 |  |
| 22 | `000_seed_smoke` | 97.4126 | Qwen3/LmSys/EP32=96.56, DS-R1/LmSys/EP32=98.27 |  |
