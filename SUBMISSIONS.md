# Submissions index — id · name · server score · idea
Each name = its own folder `codes/<name>/`, named zip `submissions/<name>.zip`
(what Codabench displays), and frozen snapshot `auto/runs/NNN_<name>/` (with a
uniquely-named `<name>.py` copy). The file inside the zip MUST be `submission.py`
(Codabench requirement) — the FOLDER/ZIP names are the identifiers.

| id | name | server | idea |
|----|------|-------:|------|
| 767416 | peak_cospike_k6 | 114.044 | joint-peak target + temporal block placement (k=6) |
| 767457 | robust_base | -1e6 | median location — FLOORED (np.median too slow) |
| 767458 | ep256_maxsticky | 112.766 | zero transmit @EP256 — DEAD (stale PAR cost > tx saving) |
| 767472 | robust_base_v2 | 113.94 | subsampled-median location (~neutral) |
| 767473 | peak_repl | 113.729 | decoupled peak-replication EP<=128 |
| 767483 | trim_base | 114.033 | robust trimmed-mean location (tied) |
| 767621 | peakblock | **114.132** | joint-peak as EXTRA BLOCK COLUMN (placement spike-aware) — SOTA |
| 767642 | spikebands | 114.065 | multiscale spike bands (k4 temporal + 3 peak bands) |
| 767663 | lstsq_pack | pending | L2 sum-of-squares placement objective (vector-bin-packing) |
