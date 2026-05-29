# Auto Leaderboard

Ranked by mean score across all evaluated cases (higher is better).
Score of 100 = matches DS-EPLB baseline. Failed runs sink to the bottom.

| rank | run | mean_score | cases | error |
|---:|:---|---:|:---|:---|
| 1 | `002_seed_ds_eplb` | 100.0000 | Qwen3/LmSys/EP32=100.00, DS-R1/LmSys/EP32=100.00 |  |
| 2 | `006_ds_eplb_hierarchical` | 100.0000 | Qwen3/LmSys/EP32=100.00, DS-R1/LmSys/EP32=100.00 |  |
| 3 | `007_ema_hot_expert` | 99.5782 | Qwen3/LmSys/EP32=98.88, DS-R1/LmSys/EP32=100.28 |  |
| 4 | `004_hot_expert_par_priority` | 99.4630 | Qwen3/LmSys/EP32=98.87, DS-R1/LmSys/EP32=100.06 |  |
| 5 | `005_hot_expert_top2_redundant` | 99.4630 | Qwen3/LmSys/EP32=98.87, DS-R1/LmSys/EP32=100.06 |  |
| 6 | `001_seed_hot_expert` | 99.4159 | Qwen3/LmSys/EP32=98.80, DS-R1/LmSys/EP32=100.04 |  |
| 7 | `003_seed_ds_eplb_throttled` | 99.1339 | Qwen3/LmSys/EP32=100.00, DS-R1/LmSys/EP32=98.27 |  |
| 8 | `000_seed_smoke` | 97.4126 | Qwen3/LmSys/EP32=96.56, DS-R1/LmSys/EP32=98.27 |  |
