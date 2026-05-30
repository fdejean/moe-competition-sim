# MoE Competition Sim — Navigation

**Canonical working folder:** `~/Desktop/moe-competition-sim/` ← you are here.
**Do not touch** `~/Desktop/moe-competition-sim-fdejean/` — that's a read-only
clone of flo's fork kept around for diffing; everything in it is already in this
repo's `vero-flo` branch.

**Remotes** on this repo:
- `origin`   → `huawei-cpl-zurich/moe-competition-sim` (upstream / Codabench)
- `fdejean`  → `fdejean/moe-competition-sim` (flo's fork — push branches here)

**Active branch**: `vero-flo` (combined work: flo's `finance_band` + vero's
autoresearch loop + variant sweeps).

## Folder layout

```
moe-competition-sim/
├── CLAUDE.md                     ← read first
├── README.md, USAGE.md, REPO_MAP.md, requirements.txt   (upstream)
├── quickstart.py                 ← sample-trace runner (upstream)
├── dynamic_lb_simulator.py       ← full simulator (upstream)
├── eplb_algorithms/              ← DeepSeek EPLB algorithm (upstream)
├── trace/                        ← committed sample traces (LFS)
│
├── codes/                        ← every algorithm variant lives here
│   ├── smoke/                      no-op baseline
│   ├── hot_expert_baseline/        existing upstream baseline
│   ├── finance_band/               flo's algo (current SOTA, 111.27 server)
│   ├── ds_eplb/                    DeepSeek EPLB wrapper
│   ├── ds_eplb_throttled/          DS-EPLB but recompute every 4th call
│   ├── ds_eplb_hierarchical/       DS-EPLB hierarchical mode
│   ├── hot_expert_par_priority/    hot_expert + priority by load std
│   ├── hot_expert_top2_redundant/  hot_expert + top-K replication
│   ├── ema_hot_expert/             EWMA hotness weighting (dead end)
│   ├── global_swap_refine/         LPT + swap local search (peer 018)
│   ├── band_swap_refine/           finance_band + swap refine (peer 019)
│   └── fb_tau_005/ … fb_conservative/   9 finance_band parameter sweeps
│
├── submissions/                  ← upload-ready zips (one per codes/ entry)
│   └── <name>.zip                  always regenerated from codes/<name>/
│
├── auto/                         ← autoresearch loop
│   ├── eval.py                     score one folder against committed traces
│   ├── leaderboard.py              regenerate LEADERBOARD.md from score.json's
│   ├── program.md                  agent brief (Karpathy autoresearch style)
│   ├── LEADERBOARD.md              ranked results (regenerated)
│   ├── NOTES.md                    append-only hypothesis log
│   ├── _baseline_cache.json        cached DS-EPLB baseline (re-evals are fast)
│   ├── research/eplb_ideas.md      peer's literature sweep + roadmap
│   └── runs/                       frozen historical snapshots — NEVER deleted
│       └── NNN_<name>/             submission.py, score.json, stdout.txt
│
└── extra/                        ← old/auxiliary stuff, never edited
    ├── benchmark_submissions.py    flo's local benchmark harness
    ├── explore_traces.py           flo's EDA
    ├── trailing_window_analysis.py flo's "EWMA vs SMA" finding
    ├── ds_eplb_sweep.py            upstream cadence sweep
    ├── results/                    DS-EPLB sweep CSV/MD
    ├── prediction_result*/         flo's server metrics dumps
    ├── scoring_result*/            flo's server scores
    └── flo_session_dump.txt        flo's chat session export
```

## Workflows

### Add a new algorithm variant
1. Create `codes/<short_slug>/submission.py` (+ helpers like `deepseek.py` if used).
2. Generate the zip:
   `cd codes/<slug> && zip -j ../../submissions/<slug>.zip *.py`
3. Snapshot for the autoresearch log:
   - Pick next `NNN` from `auto/runs/`, create `auto/runs/NNN_<slug>/`
   - Copy `codes/<slug>/submission.py` (and helpers) into it
4. Evaluate: `python auto/eval.py auto/runs/NNN_<slug>`
5. Regenerate leaderboard: `python auto/leaderboard.py`
6. Append one line to `auto/NOTES.md` describing the hypothesis.
7. Commit and push to `vero-flo`.

### Run the overnight loop
In a fresh Claude Code session inside this repo (permissions disabled), paste:
> Read `auto/program.md` and start iterating. Aim for 100 variants or 8 hours,
> whichever comes first.

### Submit a variant to Codabench
1. Confirm the zip exists: `ls submissions/<name>.zip`
2. Upload at https://www.codabench.org/competitions/16778/?secret_key=…#/participate-tab
3. Wait for status to flip from `Running` → `Finished` (~1–5 min per case).
4. Record the server score back in `auto/NOTES.md`.

## Server vs local

Local scoring (1200 iters Qwen3/DS-R1 LmSys, EP32) **clusters everything around
100** because ~85% of iterations run on the fixed init deployment, which is
shared between the submission and the DS-EPLB baseline.

The Codabench server runs longer + more diverse traces (ShareGPT, EP64), so the
spread is much wider. Calibration from 2026-05-29:

| variant            | local | server |
|--------------------|-----:|-------:|
| finance_band       | 100.04 | **111.27** |
| fb_lambda_1        | 100.05 | 110.66 |
| fb_tau_040         | 100.04 | 110.63 |
| fb_tau_080         | 100.04 | 109.44 |
| fb_conservative    | 100.00 | 109.32 |
| fb_lambda_0        | 100.00 | 108.13 |
| fb_aggressive      | 99.95  | 107.52 |
| ds_eplb            | 100.00 | **fails on EP64 (timeout)** |
| smoke              | 97.41  | (running) |

**Implication**: optimize for the long-trace regime (variance-aware replication,
no-trade band), but verify locally only that the code runs without error.
Local score ranking is unreliable.

## Scoring formula (server)

```
modeled_time = 60.0 * mean_par + transmit_amount * 88_080_384 / 900_000_000_000
case_score   = 100 * ds_eplb_modeled_time / submission_modeled_time
score        = mean(case_score over all evaluated cases)
```

A PAR drop of 0.01 saves ~0.6s; one transmit costs ~9.79e-5 s.
**PAR is worth ~6000× a transmit unit.** Always optimize PAR first.

## Peer coordination

There can be more than one Claude Code instance running in this repo at once
(via `mcp__claude-peers`). Conventions:
- Use `set_summary` to declare what you're working on.
- Don't edit files in `auto/runs/NNN_*` after another agent has written them —
  they're meant to be frozen snapshots.
- When you create a new variant, write to both `codes/<name>/` AND
  `auto/runs/NNN_<name>/`.
- Coordinate big restructures via `send_message` before committing.

## Overnight loop operating rules (READ EVERY ITERATION)

1. **Commit AND push continuously.** After every new variant, eval result, server
   score, or doc update, `git add -A && git commit && git push fdejean vero-flo`.
   Never let work sit uncommitted. (Never sign commits as Claude.)
2. **Always read the tried-log before proposing anything**, so you never repeat a
   dead end. The log lives in:
   - `auto/NOTES.md` — append-only, one line per attempt + every server score.
   - `auto/loop_queue.md` — idea backlog with status ([x]/[!]/[S]).
   - `auto/loop_state.json` — best proxy/server, submission ids, timing calibration.
3. **Pivot hard when stuck.** If 2+ consecutive variants don't beat the best
   *server* score, STOP refining that family and jump to a completely new,
   crazy, literature/science/finance-rooted idea. Log the pivot in NOTES.md.
4. **Hard constraints learned the hard way (do NOT relitigate):**
   - Server tests {ShareGPT,WildChat,LmSys}×{Qwen3,DS-R1}×{EP32,EP64,EP128}.
     Per-case time budget = **3× DS-EPLB time for THAT case** — tiny at EP128.
   - **Only finance_band-speed (~0.1–0.15s/call, single build+band, NO iterative
     refine/relabel/search) survives.** Every iterative method floored to −1e6.
   - mean-load PAR≈1.05 but **realized PAR≈1.69**; the gap is **co-spiking**
     (per-iteration variance), not mean packing or replication budget. Replication
     /λ/τ/window tuning is TAPPED OUT (finance_band λ2/τ0.2/full-window is the peak).
   - Smaller TAU and shorter WINDOW both HURT (transmit blow-up / drift-chasing).
   - Always EP128-smoke (`auto/proxy_eval.sh`) before submitting; local LmSys
     proxy CANNOT catch EP128/ShareGPT timeouts.

## Research directive (2026-05-30, user) — NOVELTY OVER TUNING

**Do NOT get stuck fine-tuning hyperparameters** (block-k, lambda, TAU, THETA,
PEAKFRAC sweeps) of an existing architecture. Converging to an optimal hyperparam
config for one architecture is LOW value. The strong score jumps come from
**genuinely new MECHANISMS** (e.g. joint-peak placement +0.35, co-spiking block
placement +1.0), not from sweeps. Every loop cycle should test a **completely new
idea** drawn from literature / science / finance / OR — bold, divergent, high-ceiling
— not a parameter variant of the last one. Sweep a winner only briefly to confirm,
then move on to the next novel mechanism. Target strong jumps, not 0.05 increments.

## ⛔ ABSOLUTE RULE (2026-05-30, user, RESTATED & STRENGTHENED): NEVER EVER FINE-TUNE

**NEVER EVER fine-tune.** Do NOT sweep block-k, lambda, TAU, THETA, PEAKFRAC, STAY,
window, or ANY hyperparameter of an existing architecture. Not even "just to confirm."
That is forbidden.

**ONLY look for new strategies / bigger steps.** Every single loop cycle MUST introduce
a genuinely NEW MECHANISM (a different algorithm / objective / representation / lever),
drawn from literature, science, finance, or OR — pull the next unticked idea from
`auto/novel_ideas.md` (and keep adding bolder ones). The goal is STRONG JUMPS, not
0.05 increments. If an idea works, log it and move to the NEXT new idea — never tune it.
Hyperparameters get ONE sensible default and are left alone.

## ⚡ HACKATHON DIRECTIVE (vero builder lane, 2026-05-30) — GOAL: SCORE 120+

**Mandate: explore NOVEL ideas for big jumps. Do NOT fine-tune hyperparameters.**
- This is a hackathon. The objective is a strong jump to **120+**, not converging to the
  optimal hyperparam config of an existing architecture.
- STOP small fine-tuning (θ/k/λ/TAU sweeps, ±0.1 tweaks). If an idea only promises a
  fraction of a point, skip it. Chase mechanisms that could move PAR by a lot.
- Go in COMPLETELY new directions: new objectives, new estimators, new structure
  (hierarchical/node-aware, regime/cluster decomposition, robust statistics, control
  theory, RMT/random-matrix, optimal transport, etc.). Crazy is good.
- WORK IN PARALLEL and IN FRONT of the other peers — own run #s **100+**, submit
  aggressively and continuously via fdejean's `tools/codabench_submit.py`. The server
  is the oracle (local LmSys proxy is non-predictive for the non-stationary cases).
- Hard invariant still holds: must be finance_band-speed / EP256-safe (per-call budget
  = 3× DS-EPLB; iterative refine/relabel-O(nd²) floor to −1e6). Always EP256 smoke-test
  before submit.
- LOG every submission + rationale in memo.md (§9). Never sign commits as Claude.
