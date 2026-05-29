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
