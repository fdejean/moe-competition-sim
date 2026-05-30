# Novel-mechanism backlog (pull ONE per loop cycle; NOT hyperparam sweeps)
Rule: each cycle = a genuinely different MECHANISM. Mark [x] done(server score), [!] dead(reason).
Base to beat: peak_cospike_k4 = 113.995. Must stay finance_band-speed, EP256-safe, block@EP<=128.

- [~] strata_pack — placement balances LOAD-STRATA (iters grouped by total load = regime proxy), not temporal blocks. Targets interleaved-regime Mix. (submitted)
- [ ] ot_sinkhorn — replace greedy LPT with entropic optimal-transport (Sinkhorn) soft expert->device assignment, rounded. Global vs greedy-local optimizer.
- [ ] regime_track — change-point-gated redeployment: redeploy when window load DISTRIBUTION shifts (KL/L1), not just when PAR gain>TAU. Track non-stationary Mix instead of holding.
- [ ] scenario_robust — place to min the MAX device load across S sampled high-imbalance ITERATIONS simultaneously (sample-average-approximation / robust optimization).
- [ ] match_ep256 — at EP256 (2 slots/dev) it's a PAIRING problem: optimal anti-correlated b-matching (heaviest+lightest fold + correlation-aware), vectorized. Attacks the 2-slot regime E2 flagged.
- [ ] spectral_cut — proper balanced min-cut on the expert co-spiking correlation graph (recursive Fiedler), not the weak snake. 
- [ ] power_d_choices — randomized placement: each slot to the least-loaded of d hashed devices (Mitzenmacher). Escapes LPT pathologies; O(n_slots).
- [ ] marg_peak_repl — replication by marginal realized-peak reduction: give +1 copy to the expert whose extra copy most lowers the sampled max device load; iterate (vectorized, fixed steps).
- [ ] grouped_place — hierarchical/grouped placement (DS-V3 grouped routing): balance within node-groups first, then across — for high-EP comms structure.
- [ ] dro_ambiguity — distributionally-robust replication: size copies for the worst distribution in a Wasserstein/moment ambiguity set around the window.
- [ ] mixture_deploy — classify the window into 1-of-K regime templates (cached in _STATE), deploy the precomputed best placement for that regime (meta/bandit).
