# Learnings from Claude — Codabench auto-submit session

_Session: 2026-05-30. Goal: understand fdejean's submission tooling and submit something._

## What the repo's submission automation actually is

`tools/codabench_submit.py` is a self-contained script that drives the **entire
Codabench submission round-trip over the REST API** — exactly what the web UI does
when you drag-drop a zip, but headless. No browser needed.

Flow (competition `16778`, "MoE Dynamic Load Balancing Competition"):

1. `POST /api/api-token-auth/` → token (or skip with `CODABENCH_TOKEN`)
2. `GET /api/competitions/16778/` → find the `Current` phase
3. `POST /api/datasets/` → register the file, get `{key, sassy_url}`
4. `PUT <sassy_url>` → raw zip bytes to presigned storage (**no auth header on this PUT**)
5. `PUT /api/datasets/completed/<key>/` → finalize
6. `POST /api/submissions/` → create submission (`{data: key, phase: id}`)
7. Poll `GET /api/submissions/<id>/` until `Finished` / `Failed` / `Cancelled`
8. `GET .../get_details/` → download + pretty-print `scores.json` + `metrics.json` + logs

Default entry submitted is `submissions/finance_band/submission.py`. Other entries:
`hot_expert_baseline`, `smoke`.

## Concrete lessons / gotchas

- **Auth is required even for the read-only `--list-phases` check.** You can't probe
  the competition anonymously. `--list-phases` is still the right safe first step —
  it confirms credentials work without consuming a slot.
- **Every upload consumes a real leaderboard submission slot.** Don't run it to test
  plumbing; use `--list-phases` for that. (README warns about this explicitly.)
- **Credentials are not in the repo.** No `.env` is committed (it's gitignored), and
  no `CODABENCH_*` env vars were set. You must supply username+password or a token
  before anything runs. Writing them into a gitignored `.env` at the repo root is the
  intended workflow and keeps them out of shell history / the process list.
- **Env over the local interpreter mismatch:** the README references a Linux conda
  python (`/home/wli/miniconda3/bin/python`), but on this macOS box the system
  `python3` (3.14.4, homebrew) already has `requests 2.33.1`, so `python3 tools/codabench_submit.py …`
  works directly.
- **`tasks` is intentionally omitted** from the create-submission call — the phase
  payload only exposes `PhaseTaskInstance` ids / task UUIDs, not the integer `Task`
  PKs the endpoint wants. Omitting it makes the server grade all phase tasks (correct
  for this single-task competition).
- **Grading takes ~4 minutes.** Run the submit in the background and let it poll
  (10s cadence, 1800s timeout). If polling is interrupted, resume with
  `--submission-id <id>` — the id is printed right after upload.

## The submission we actually ran

- Submission **`766390`**, `finance_band` entry → status **`Finished`**.
- Leaderboard **score / composite_score = `111.264`** (mean_par 1.69205,
  transmit_amount 842,299, total_time 2620.51s).
- All 24 hidden cases graded (Qwen3 + DS-R1 × ShareGPT/WildChat/LmSys/Mix, EP 32–256).
  Hardest: `DS-R1 / Mix / EP256` (mean_par 3.42). Easiest: small-EP Qwen3 (~1.29).
- Artifacts saved under `results/20260530-003331/` (submission.zip, scoring_result/,
  prediction_result/, logs/, submission_scores.json).

## Quick command reference

```bash
PY=python3   # this machine; README's conda path is Linux-only

# read-only sanity check (auth required, no slot used)
$PY tools/codabench_submit.py --list-phases

# submit the default finance_band entry, poll, print scores (uses a slot)
$PY tools/codabench_submit.py

# submit a specific entry
$PY tools/codabench_submit.py submissions/hot_expert_baseline/submission.py

# re-fetch results for an already-graded submission (no new upload)
$PY tools/codabench_submit.py --submission-id 766390

# fire-and-forget (create, don't poll)
$PY tools/codabench_submit.py --no-wait
```
