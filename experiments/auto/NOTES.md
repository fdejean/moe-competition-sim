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
