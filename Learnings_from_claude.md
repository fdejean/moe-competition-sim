# Learnings from this session — 2026-05-30

This session was a **peer-coordination session**: a third Claude Code instance
joined the `vero-flo` branch and made contact with the two already running in
this repo. What it learned:

## Multi-instance coordination

- Three Claude Code instances can run in the same repo/branch at once. The
  `claude-peers` MCP (`list_peers`, `send_message`, `set_summary`,
  `check_messages`) is how they see and talk to each other.
- **A peer's `set_summary` is the real coordination channel**, not typed
  replies. Neither peer sent a `send_message` response, but one answered by
  *updating its summary* seconds after being pinged. Read summaries first.
- `last_seen` timestamps tell you who is alive. Re-run `list_peers` to check for
  activity rather than waiting on a reply that may never come.
- Set your own summary immediately on joining so the others know not to collide
  with you.

## Who was doing what (snapshot)

- **bulcww7h** — owned a repo restructure (sources → `codes/`, zips →
  `submissions/`, autoresearch infra → `auto/`, old stuff → `extra/`) and was
  **holding before final commit** to coordinate. This agent is the one to clear
  before anyone else commits.
- **g2evbaiu** — running the overnight autoresearch loop in `auto/`. Built runs
  018 (`global_swap_refine`), 019 (`band_swap_refine`), and 020
  (`band_refine_transmin`). Run 020 = finance_band + swap refine + PAR-neutral
  transmit minimization (device relabel + slot preserve); best local at 100.35,
  ~20% less transmit.

## Coordination conventions (now codified in CLAUDE.md)

- Declare work via `set_summary`.
- Never edit `auto/runs/NNN_*` snapshots after another agent writes them — they
  are frozen.
- A new variant goes in **both** `codes/<name>/` and `auto/runs/NNN_<name>/`.
- Coordinate big restructures via `send_message` **before** committing.

## Project facts re-confirmed this session

- Local scoring (1200 iters, EP32) clusters everything near 100 because ~85% of
  iterations run on the shared fixed-init deployment. Local ranking is
  unreliable — verify locally only that code runs; optimize for the long-trace
  server regime.
- **PAR is worth ~6000× a transmit unit** (0.01 PAR ≈ 0.6s; one transmit ≈
  9.79e-5 s). Always optimize PAR first.
- `~/Desktop/moe-competition-sim-fdejean/` is a read-only clone for diffing —
  do not touch it.

---

## Addendum from bulcww7h — server calibration + API gotchas

The peer notes above cover *how* we coordinated. This section covers *what
worked algorithmically*, including the Codabench server scores that came back
this session.

### Calibration table (local vs server, this session)

The local autoresearch leaderboard was useless for ranking — server scores
spread 4 points across variants that all looked identical locally:

| variant                   | local  | **server** |
|---------------------------|------:|-----------:|
| `finance_band` (TAU=0.2, LAMBDA=2.0) | 100.04 | **111.27** |
| `fb_lambda_1` (LAMBDA=1)  | 100.05 | 110.66 |
| `fb_tau_040` (TAU=0.4)    | 100.04 | 110.63 |
| `fb_tau_080` (TAU=0.8)    | 100.04 | 109.44 |
| `fb_conservative`         | 100.00 | 109.32 |
| `fb_lambda_0` (LAMBDA=0)  | 100.00 | 108.13 |
| `fb_aggressive` (TAU=0.05, LAMBDA=4) | 99.95 | 107.52 |

What this tells us:
- **LAMBDA=2 is the sweet spot.** LAMBDA=0 loses 3 points; LAMBDA=4 loses 4.
  Variance-aware replication is a real lever, not a tuning artifact.
- **TAU matters less.** At LAMBDA=2, TAU=0.2 wins narrowly over 0.4 / 0.8.
- **Local `band_refine_transmin` (peer's 020) still uses LAMBDA=1** — the
  highest-EV next upload is the same algorithm with LAMBDA bumped to 2.

### Submission API contract gotchas (cost three failed submissions)

1. `deployment.shape == (n_layers, n_device, (n_experts + n_red_expert) //
   n_device)` **always** — even when `change=False`. Returning an empty array
   fails with `deployment_table shape (0,0,0) != (94,32,5)`. Cache the last
   good deployment and return that on no-change cycles.
2. Server has a per-trace wall-clock budget. DS-EPLB raw times out at EP64
   (`failed ShareGPT_Qwen3_EP64: rebalance exceeded the per-trace time
   budget`). Throttling per-call doesn't fix it — you need a faster algorithm,
   not less of a slow one.
3. The simulator only calls `rebalance` every `collection_interval`
   iterations (1024 default). Throttling at the submission level is a no-op
   for the first ~1024 cycles.

### EWMA hotness weighting was a wasted iteration

I burned `007_ema_hot_expert` on the hypothesis that recent iterations should
weigh more. Flo's `extra/trailing_window_analysis.py` had already shown:
- Lag-1 autocorrelation of per-expert load is ~0.5 → load is persistent.
- EWMA half-life sweeps gave no rank-corr improvement over flat SMA.

Lesson: **read existing EDA before proposing new estimators.** The relevant
priors were already in `extra/`.

### Submission pipeline

- Codabench has a REST API but no clean Python client. Manual zip upload via
  the web UI is the right path for an overnight loop — don't try to
  auto-submit each iteration.
- `git lfs pull` is mandatory before the `.npy` traces are real. Without it,
  `numpy.load` blows up with "pickled object data" because the LFS pointer
  text file is what `np.load` sees.

### Scoring formula reminder

```
modeled_time = 60.0 * mean_par + transmit * 88_080_384 / 9e11
case_score   = 100 * baseline_modeled_time / submission_modeled_time
final_score  = mean(case_score over all evaluated cases)
```

Hardware constants correspond to BF16 DeepSeek-style experts on H100-class
interconnect. Failed runs (import error, bad shape, timeout) get the floor
score of −1,000,000.

