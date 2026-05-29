# tools/

Helper scripts for working with the competition. Run everything with the conda
interpreter (`/home/wli/miniconda3/bin/python`) — it has `numpy`/`torch`/`requests`;
the system `python3` does not. See `../USAGE.md` for the simulator itself.

---

## `codabench_submit.py` — upload a submission and pull back the scores

Automates the **full Codabench round-trip** for the MoE competition
(`competition 16778`): it zips `submission.py`, uploads it through the Codabench
REST API exactly the way the website does, waits for grading, then downloads and
prints the scores. No browser needed.

### One-time setup: credentials

Put your Codabench login in a `.env` at the repo root (it is gitignored):

```bash
cp .env.example .env
# then edit .env:
#   CODABENCH_USERNAME=you@example.com   # email works as the username
#   CODABENCH_PASSWORD=your-password
```

Credential resolution order (first match wins):
1. `--token` flag or `CODABENCH_TOKEN` env/.env (a DRF token — skips the login call)
2. `--username` / `--password` flags
3. `CODABENCH_USERNAME` / `CODABENCH_PASSWORD` from the environment or `.env`

The script ships its own tiny `.env` reader (no `python-dotenv` dependency).
**Real environment variables override `.env`.** Point at a different file with
`CODABENCH_ENV_FILE=/path/to/file`.

### Common commands

```bash
PY=/home/wli/miniconda3/bin/python

# Sanity check: confirm auth + see the phases (read-only, no submission used)
$PY tools/codabench_submit.py --list-phases

# Submit the default entry (submissions/finance_band/submission.py), wait, print scores
$PY tools/codabench_submit.py

# Submit a specific file / directory / prebuilt zip
$PY tools/codabench_submit.py submissions/finance_band/submission.py
$PY tools/codabench_submit.py path/to/submission.zip      # validated: submission.py must be at root

# Re-fetch results for a submission that already ran (no new upload)
$PY tools/codabench_submit.py --submission-id 766344

# Fire-and-forget: create the submission and exit without polling
$PY tools/codabench_submit.py --no-wait
```

A submission upload **consumes a real leaderboard submission slot** — don't run it
just to test plumbing; use `--list-phases` for that.

### Useful flags

| Flag | Meaning |
|------|---------|
| `--competition ID` | competition id (default `16778`) |
| `--phase-id` / `--phase-index` / `--phase-name` | pick the phase explicitly; default auto-picks the `Current` phase |
| `--submission-id ID` | skip upload; just poll + fetch results for an existing submission |
| `--no-wait` | create the submission and exit (don't poll) |
| `--timeout SECS` | max wait for grading (default `1800`) |
| `--poll-interval SECS` | status poll cadence (default `10`) |
| `--out DIR` | where to save the zip + results (default `results/<timestamp>/`) |
| `--list-phases` | print phases and exit |
| `-q` / `--quiet` | less chatter |

### What it produces

Artifacts land in `results/<timestamp>/` (or `--out`):
- `submission.zip` — the bundle that was uploaded (`submission.py` at the zip root)
- `scoring_result.zip` + extracted `scoring_result/` (`scores.json`, `scores.txt`, `metadata`) —
  `score`, `mean_par`, `transmit_amount`, …
- `prediction_result.zip` + extracted `prediction_result/` (`metrics.json`, `metadata`) —
  per-case `mean_par` / `transmit_amount` for all 24 hidden cases
- `logs/` — per-step program + ingestion stdout/stderr
  (`prediction_stdout.txt`, `prediction_ingestion_stdout.txt`, `scoring_stdout.txt`, …).
  The ingestion stdout has a handy `completed <dataset>_<model>_EP<n>: PAR=.. transmit=..` line per case.
- `submission_scores.json` — the leaderboard scores straight from the API

and prints a summary table to stdout. (This competition has no `detailed_result`, so none is saved.)

### Long runs / background

Grading takes a few minutes. To avoid blocking, run it in the background and
collect the result later; if a poll is interrupted, resume with
`--submission-id <id>` (the id is printed right after upload).

### How it works (for maintainers)

REST flow against `https://www.codabench.org/api/`, mirroring the web UI:
`POST api-token-auth/` → `GET competitions/<id>/` (find phase) →
`POST datasets/` (`{type:'submission', competition, file_name, file_size, request_sassy_file_name}`
→ `{key, sassy_url}`) → `PUT <sassy_url>` (raw zip bytes to presigned storage, **no auth header**) →
`PUT datasets/completed/<key>/` → `POST submissions/` (`{data:key, phase:id}`) →
poll `GET submissions/<id>/` until `Finished/Failed/Cancelled` →
`GET submissions/<id>/get_details/` for the `prediction_result` / `scoring_result` URLs.

Note: `tasks` is intentionally **omitted** from the create call — the phase payload
only exposes `PhaseTaskInstance` ids / task UUIDs, not the integer `Task` PKs the
endpoint expects, and omitting it makes the server evaluate all phase tasks (correct
for this single-task competition).
