# Overnight idea backlog (vero agent) — PAR-first

**Strategic frame (from real server data):** server mean_par≈1.69 is ~97% of the
score; transmit is ~3%. So **every idea here targets lowering PAR**, not transmit.
PAR floor = w_max / (r·avg) → **replication is the real lever; packing closes the
gap to the floor.** Build on `codes/band_refine_transmin/` (band + variance
replication + LPT + swap refine + PAR-neutral relabel) unless an idea replaces a stage.

Status legend: `[ ]` pending · `[~]` in progress · `[x]` done (proxy recorded) ·
`[!]` abandoned (with reason) · `[S]` submitted to server.

## Tier 1 — PAR levers (do first, biggest EV)
- [ ] **rep_aware_refine** (roadmap 2a, *biggest single lever*): after pack+swap,
  find the logical expert contributing most to the peak device; give it +1 replica,
  remove a replica from the highest-slack expert (stays below peak after removal);
  re-pack affected layer; keep iff peak load drops. Loop under wall-clock budget.
  Co-optimizes replication+placement that greedy water-filling mis-couples.
- [ ] **target_load_bsearch** (roadmap 2b): binary-search smallest feasible
  per-device target T; r_e = ceil(w_e / T); spend leftover slots by marginal gain
  w_e/(r_e(r_e+1)); then LPT+swap pack. Principled, path-independent replica counts.
- [ ] **waterfill_rep**: water-filling replica allocation (comms/risk-budgeting):
  allocate replicas so per-replica loads w_e/r_e equalize to a common "water level";
  round; pack. Optimal fractional replication for min makespan.
- [ ] **bldm_pack** (roadmap 1b): replace LPT with cardinality-constrained
  Karmarkar–Karp (largest-differencing) makespan packing; warm-start swap refine.

## Tier 2 — variance / tail risk (finance-rooted)
- [ ] **cvar_replication**: replace mean+λ·std risk with CVaR / expected-shortfall —
  replicate by tail load (e.g. mean of worst 10% of per-iter loads in window).
  Realized PAR is driven by worst-case per-iter load, not the average.
- [ ] **minimax_pct_pack**: pack to minimize p95 of per-device load across the
  window's per-iteration load samples (robust/minimax), not the mean load.

## Tier 3 — search / meta-heuristics
- [ ] **anneal_pack**: simulated annealing on (replica counts + assignment) with
  makespan objective, wall-clock budgeted, warm-started from band+pack. Escapes
  LPT/swap local minima.
- [ ] **lagrangian_replica**: Lagrangian relaxation of replica-budget-constrained
  makespan min; subgradient on dual price of device capacity; round.

## Tier 4 — regime-specific & tuning
- [ ] **group_balance_ep64**: EP64 has few slots/device and is where PAR is worst.
  Hierarchical/grouped placement (DS-V3 grouped routing) to balance within
  node-groups first. Targets the worst server regime.
- [ ] **combo_best**: best Tier-1 packer + best Tier-2 replication, tuned.
- [ ] **sweep_best**: λ/TAU/window sweep on the new best architecture (cheap, low ceiling).

## Done / results (append as we go)
- band_refine_transmin (SOTA at start): proxy 108.557; submitting to server first.
