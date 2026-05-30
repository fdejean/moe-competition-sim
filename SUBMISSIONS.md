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
| 767663 | lstsq_pack | 113.109 | L2 sum-of-squares placement objective — WORSE (L-inf is right) |
| 767670 | dotprod_pack | 112.678 | min dot-product VBP — WORSE |
| 767691 | varblock | 114.118 | peakblock + variance column (tied, ~neutral) |
| 767701 | vblock256 | pending | **EP256 vectorized round-LPT** spike-aware placement (cracks the EP256 scalar wall) |
- 767670 · **dotprod_pack** · _pending_ · run auto/runs/3633_dotprod_pack
- 767691 · **varblock** · _pending_ · run auto/runs/113_varblock
- 767701 · **vblock256** · _pending_ · run auto/runs/114_vblock256
- 768087 · **drift_router** · **114.181 (SOTA)** · drift-adaptive recency target (forecasting lever for Mix)
- 768125 · **peakiter** · _pending_ · run auto/runs/116_peakiter
- 768141 · **peakiter_cvar** · _pending_ · run auto/runs/117_peakiter_cvar
- 768143 · **peakiter_full** · _pending_ · run auto/runs/118_peakiter_full
- 768157 · **peakiter_safe** · _pending_ · run auto/runs/119_peakiter_safe
- 768172 · **antiphase_block** · _pending_ · run auto/runs/120_antiphase_block
- 768207 · **drift_gate** · _pending_ · run auto/runs/121_drift_gate
- 768253 · **mixture_deploy** · _pending_ · run auto/runs/122_mixture_deploy
- 768255 · **spectral_cut** · _pending_ · run auto/runs/123_spectral_cut
- 768256 · **grouped_place** · _pending_ · run auto/runs/124_grouped_place
- 768257 · **power_d_choices** · _pending_ · run auto/runs/125_power_d_choices
- 768258 · **dro_ambiguity** · _pending_ · run auto/runs/126_dro_ambiguity
