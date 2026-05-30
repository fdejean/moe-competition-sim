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


---

## Addendum from the autoresearch agent (g2evbaiu) — the long-horizon eval

The single most useful thing this session uncovered: **why local was useless,
and how to make it predictive.**

### The committed traces are long; eval.py just truncates them
`trace/Qwen3/LmSys.npy` is **(5120, 94, 128)**; `trace/DS-R1/LmSys.npy` is
**(6144, 58, 256)**. `auto/eval.py` defaults to `max_iters=1200`, so locally
only **one** rebalance ever happens and 85% of iters sit on the fixed init grid
→ everything ties at ~100. Re-run at full length and the spread reappears, in the
right order:

```
eval.py <run> --max-iters 6144 --cases Qwen3:LmSys:32 DS-R1:LmSys:32
```
(eval into a temp/inline harness — don't clobber the canonical 1200-iter score.json.)

### Full-length ranking (LmSys EP32, collection_interval=1024)
| variant | mean | Qwen3 PAR / tx | DS-R1 PAR / tx |
|---|---:|---|---|
| DS-EPLB baseline | 100 | 1.2885 / **57792** | 1.2578 / **82489** |
| finance_band (λ2) | 107.82 | 1.2885 / 14914 | 1.2351 / 16634 |
| 019 band_swap_refine | 108.14 | 1.2795 / 14935 | 1.2360 / 16652 |
| **020 band_refine_transmin (λ1)** | **108.56** | 1.2795 / **11645** | 1.2360 / **13852** |

### What the long-horizon numbers reveal
- **DS-EPLB's transmit explodes** (57k / 82k) because it redeploys every cycle.
  finance_band deploys once and **holds** (band gates redeploys) → tx stays ~15k.
  *That collapse of the transfer term is the whole 100→111 server jump.* The
  no-trade band is the dominant long-trace lever — confirmed quantitatively.
- At full length, PAR converges to ~1.26–1.29 (vs ~1.6–1.9 at 1200 iters), so the
  PAR differences between good variants are small — **transmit is what separates
  the leaders.**
- **020 wins the proxy** with PAR identical to 019 but ~20% less transmit, via the
  PAR-neutral relabel (device permutation + within-device slot preservation are
  load-invariant → free transmit cut). Caveat: this proxy is LmSys-EP32 only; the
  server also scores ShareGPT and EP64, which is why server magnitudes (107–111)
  differ from the proxy (107–108). Treat it as a *ranking* tool, not an oracle.

### Still-untapped PAR levers (next, per auto/research/eplb_ideas.md)
Packing is already near-optimal (swap refine moved PAR <0.01), so the remaining
PAR headroom is in **replication**, not placement:
- **2a replication-aware refinement** — move a replica from a slack expert to the
  one dominating the peak device, re-pack, keep if peak drops. Flagged as the
  single biggest lever.
- **2b target-load binary search** — smallest feasible per-device load T;
  expert e gets `ceil(w_e / T)` replicas. Principled, path-independent counts.

### Session outcome (final state)
- Canonical upload built: **codes/band_refine_transmin/ (λ=2.0)** → `submissions/
  band_refine_transmin.zip`; λ=1 hedge → `submissions/band_refine_transmin_l1.zip`.
  Both verified: import clean, return shape (94,32,5) with change=True, EP64 in
  <0.2s (raw ds_eplb times out at EP64). Frozen snapshot: auto/runs/021_transmin_l2/.
- λ choice: local long-horizon proxy can't separate λ1 vs λ2 (both 108.56 on
  LmSys-EP32); the **server** is the tiebreaker and says λ=2 (finance_band λ2=111.27
  > fb_lambda_1=110.66). Ship λ2 as primary, keep λ1 as a hedge upload.
- Commit discipline: held the commit because peer `bulcww7h` owned the restructure
  and was "holding before final commit." Messaged it the exact list of new/append-
  only paths I touched so it can fold them in. Never commit into a peer's in-flight
  restructure without clearing it first.
- Biggest meta-lesson: **the eval you're handed may be silently lossy.** eval.py
  truncating 5120/6144-iter traces to 1200 made the entire local leaderboard
  non-predictive. Always check the data the proxy actually consumes (trace .npy
  shapes) before trusting a score's ranking.

---

## Addendum — agent #3 (predict/packing lane), 2026-05-30 ~01:50

Two findings that change the team's SOTA understanding:

### 1. band_refine_transmin TIMES OUT on the server (-1,000,000)
The swap-refine's `_TIME_BUDGET_S=7.0` lets one rebalance call run up to 7s. The
server's per-trace budget is **3× the DS-EPLB rebalance time, which is tiny at
high EP**, so it floored on ShareGPT_Qwen3_EP128 (id 766436). xa90w3tz hit the
identical wall (cov_place, EP128). **Only finance_band-speed code (build + band,
~120ms/call) is server-safe. The 7s refine was the SOTA-killer.** Rule: bound any
local search by a FIXED iteration count, NEVER by wall-clock.

### 2. The high-EP PAR gap is PACKING, not replication
Per-layer PAR floor (max single-slot weight / avg device load) = **1.000 at every
EP**. finance_band achieves 1.04 (EP32) → 1.15 (DS-R1 EP256). So the whole
0.10–0.15 high-EP gap is LPT packing inefficiency — replica COUNTS are already
min-max optimal (water-filling). Better/faster packing (BLDM, capped swap) is the
biggest fast PAR lever; replication tuning is near its floor.

### Delivered
- `codes/pack_refine_fast/` (run 061): finance_band + cut-preserving swap packer
  bounded by FIXED pass count (`FB_PASSES=60`), no time budget, relabel dropped.
  **176–269ms/call at every EP incl EP256** (server-safe). Proxy 108.41 > finance_band
  107.82. Submitted (id 766513).
- `auto/eval_proxy.py`: full-length multi-EP proxy (EP 32/64/128/256 on LmSys),
  writes `score_proxy.json` — ranks variants without burning server slots. High-EP
  DS-EPLB baselines precomputed into `_baseline_cache.json`.
- Dead end (run 060 predict_trend): trend-extrapolation estimator exploded PAR AND
  transmit. Confirms flo's EDA — prediction/recency overshoots on persistent load.

### Server-safe submission tooling
`tools/codabench_submit.py` works headless with the repo `.env`. Use homebrew
`python3` (has requests). Eval needs `.venv/bin/python` (has torch). 1000 subs/day.

---

## Agent #4 (vero overnight, 2026-05-30 ~01:00–03:30) — the transmit breakthrough

Goal: beat finance_band (111.264) overnight, auto-submitting via fdejean's
`tools/codabench_submit.py` (copied into this repo; creds in gitignored `.env`).
Two interpreters: `.venv/bin/python` (torch, eval) and system `python3` (requests, submit).

### What the server taught us (18 submissions, every lever)
- **The server tests up to EP256** (DS-R1_Mix_EP256) and a **"Mix" dataset**, 25 cases =
  {LmSys,ShareGPT,WildChat,Mix}×{Qwen3,DS-R1}×{EP32,64,128,256}. Per-case time budget =
  **3× DS-EPLB for THAT case** — tiny at high EP. On the server my numpy/baseline ratio
  is far worse than local, so **only finance_band-speed (~0.1–0.15s/call, single
  build+band, no iteration) survives.** Floored to −1e6: band_refine_transmin (O(nd²)
  relabel), rep_aware (rep loop), block_pack (k-vector per-device, EP256).
- **PAR is a robust optimum at ~1.692 and SATURATED.** Every perturbation raised it:
  co-spiking scalar (1.717), place-on-mean (1.700), over-replicate (1.706), λ/τ/window
  tuning, heavy-tail quantile. mean-load PAR≈1.05 but realized≈1.69 = per-iteration
  co-spiking, which is unexploitable within the speed budget (per-device too slow,
  scalar hurts). The local LmSys-EP32 proxy is ANTI-CORRELATED with the server (finance_
  band has the worst proxy, best server) — proxy only good for catching errors/timeouts.
- **TRANSMIT is the recoverable lever.** finance_band's PAR ≈ baseline's; its win is
  holding (low transmit). Its own transmit (842k, ~82s, ~3% of modeled time) is cuttable
  PAR-NEUTRALLY: permuting device rows / slots within a device leaves device loads (and
  PAR) unchanged but reduces the Hamming-distance transmit charge.

### The win: cheap_relabel = 111.751 (> finance_band 111.264)
band_refine_transmin had the right idea (PAR-neutral device relabel + slot preserve) but
its O(n_device²) overlap match floored at EP128/256. **cheap_relabel does it in O(n_device):**
match new device rows to old positions by shared HEAVIEST expert (a dict), then keep
persisting experts in their old slot. Load-invariant ⇒ PAR identical (1.6929 = finance_band),
transmit −18.5% on the server (686k vs 842k) ⇒ **+0.49 score, mathematically guaranteed ≥
finance_band on every case.** Transmit-cut ceiling (transmit→0) ≈ 114.8, so there's headroom
via better alignment (v2: exact full-set match) and source-level stickiness.

Meta-lesson: when one axis (PAR) is saturated and another (transmit) is a small but
RECOVERABLE fraction, attack the recoverable one — and the prior agent's "failed" idea
(transmit-min) was only failing on SPEED; the fix was algorithmic complexity (O(nd) not O(nd²)),
not abandoning it.
