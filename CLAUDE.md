# Evaluation

The evaluator reports:

- `mean_par`: average peak-to-average ratio across evaluated iterations and layers. Lower is better.
- `transmit_amount`: total count of expert placement changes during redeployment. Lower is better.
- `transmission_time_seconds`: estimated total expert-transfer time across evaluated cases. Lower is better.
- `total_time_seconds`: estimated compute plus transfer time across evaluated cases. Lower is better.
- `score`: primary leaderboard score, modeled as speedup versus DS-EPLB under the hardware assumptions below.

## Score Formula

The leaderboard models runtime as worst-device compute time plus expert-transfer overhead:

```
modeled_time = compute_time + transfer_time
compute_time = balanced_compute_seconds * mean_par
transfer_time = transmit_amount * expert_bytes / bandwidth_bytes_per_second
```

The fixed hardware assumptions are:

- `balanced_compute_seconds = 60.0`
- `expert_bytes = 88_080_384`
- `bandwidth_bytes_per_second = 900_000_000_000`

These correspond to BF16 DeepSeek-style experts with approximately `3 * 7168 * 2048` parameters per expert and an H100-class high-performance interconnect.

For each evaluated case, the scorer compares modeled runtime against the DS-EPLB baseline for the same dataset, model, and EP size:

```
submission_modeled_time =
    60.0 * submission_mean_par
    + submission_transmit_amount * 88_080_384 / 900_000_000_000

baseline_modeled_time =
    60.0 * ds_eplb_mean_par
    + ds_eplb_transmit_amount * 88_080_384 / 900_000_000_000

case_score = 100 * baseline_modeled_time / submission_modeled_time
score = mean(case_score over evaluated cases)
```

## Interpretation

- `100` means equal modeled runtime to DS-EPLB for that case.
- Higher than `100` means faster modeled runtime than DS-EPLB.
- Lower than `100` means slower modeled runtime than DS-EPLB.
- Lower PAR helps by reducing compute time.
- Lower transmit helps by reducing transfer time.

Submissions that fail to import, return invalid shapes, or require unavailable traces receive a failed score.
