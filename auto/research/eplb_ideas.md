# EPLB research notes — concrete ideas to beat DS-EPLB

Source: background literature sweep (deepseek-ai/EPLB, vLLM `default.py`, MoE-GPS
arxiv 2506.07366, LYNX arxiv 2411.08982, BLDM/CKK partitioning, LPT revisited
arxiv 1801.05489). Compiled 2026-05-29.

## Scoring reality (drives all priorities)
- `modeled_time = 60*mean_PAR + transmit*9.79e-5`. A PAR drop of 0.01 saves 0.6s;
  one transmit costs ~1e-4s. **PAR is worth ~6000x a transmit.**
- So: maximize PAR reduction first; only cut transmit when it's PAR-neutral.
- **PAR floor**: with the hottest expert load `w_max` replicated `r` times over
  device average load `avg`, `PAR >= w_max / (r * avg)`. **Replication is the real
  lever** — packing only closes the gap to that floor.
- Local-eval caveat: max_iters=1200, collection_interval=1024 → ~85% of iters run
  on the FIXED init deployment shared by submission and baseline, so local scores
  cluster at ~100. The hidden Codabench traces are longer / multi-rebalance, where
  deployment quality and transmit savings compound. Optimize for the long-trace
  regime, validate robustness locally.

## Roadmap (priority order) — each becomes a run NNN_*
1. [DONE 018] Global replicate + LPT pack + **swap local search** (1a). Pure swaps
   keep `cut` fixed → per-replica weights fixed → only moves pieces between
   devices → guaranteed max-load (and PAR) <= LPT. Removes 30-70% of LPT's gap.
2. [build] **Merge with flo's band** (019): variance replication + no-trade band
   (transmit saver, change-point gate) + swap refine.
3. **Replication-aware refinement (2a)** — *single biggest lever*: find the logical
   expert contributing most to the peak device; give it one more replica, take a
   replica from the expert with the most slack (lowest weight/logcnt, stays below
   peak after removal); re-pack; keep if peak drops. Loop. Co-optimizes the two
   stages that greedy mis-couples.
4. **Target-load binary search replication (2b)**: binary-search smallest feasible
   per-device target T; expert e needs ceil(w_e / T) replicas; distribute leftover
   replicas by weight/logcnt; pack. Principled, path-independent replica counts.
5. **BLDM packing (1b)** — cardinality-constrained Karmarkar-Karp; beats LPT
   makespan by a wide margin. Optional **CKK (1c)** branch-and-bound under a
   wall-clock budget for the last sliver, warm-started from BLDM.
6. **Transmit min, PAR-NEUTRAL (stacks with band, cache prev deploy in _STATE):**
   - 4a **preserve_intragpu_slots** (vLLM): per device, keep an expert in its old
     slot if it's still assigned to that device; only fill vacated slots with
     arrivals. Kills all "stayed on same device" transfers.
   - 4b **Hungarian device relabel**: overlap matrix O[i][j] = |old experts on gpu i
     ∩ new bin j|; `scipy.optimize.linear_sum_assignment(-O)` to relabel new bins
     onto old GPUs maximizing retained experts. Apply 4a after. O(gpus^3), trivial.
   - 4c stability-biased tie-breaking in packing toward the old location.
7. **Prediction (3):** expert routing is temporally persistent (LYNX). Trailing
   windowed-mean / persistence is strong; EMA(alpha 0.3-0.7) optional. MoE-GPS:
   predicting the *distribution* suffices. Change-point gate (3c) = flo's band.

## Hard contract reminders
- `rebalance(hotness, n_device, n_red_expert)` → `(change, layers_priority,
  deployment, None)`; `deployment.shape == (n_layers, n_device,
  (n_experts+n_red)//n_device)` ALWAYS, even when `change=False` (cache last good).
- Per-call budget 10s → keep any search wall-clock-guarded (~7s) and degrade
  gracefully (partial refinement is still valid & >= LPT).
- Must be robust across EP32/EP64, Qwen3 (94L/128E) & DS-R1 (58L/256E), and hidden
  datasets (ShareGPT). Never return an empty/zero-shaped array.
