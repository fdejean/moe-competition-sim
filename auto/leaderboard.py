#!/usr/bin/env python3
"""Walk experiments/auto/runs/ and regenerate LEADERBOARD.md.

Reads every score.json and ranks runs by mean_score descending.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNS_DIR = ROOT / "runs"
LEADERBOARD = ROOT / "LEADERBOARD.md"


def collect() -> list[dict]:
    rows: list[dict] = []
    for run_dir in sorted(RUNS_DIR.iterdir()):
        if not run_dir.is_dir():
            continue
        score_path = run_dir / "score.json"
        if not score_path.exists():
            rows.append({
                "run": run_dir.name,
                "mean_score": None,
                "error": "no score.json (not evaluated)",
                "cases": [],
            })
            continue
        try:
            rows.append(json.loads(score_path.read_text()))
        except json.JSONDecodeError as exc:
            rows.append({
                "run": run_dir.name,
                "mean_score": None,
                "error": f"unreadable score.json: {exc}",
                "cases": [],
            })
    return rows


def render(rows: list[dict]) -> str:
    def sort_key(row: dict) -> tuple[int, float]:
        score = row.get("mean_score")
        if score is None or row.get("error"):
            return (1, 0.0)
        return (0, -float(score))

    rows_sorted = sorted(rows, key=sort_key)
    lines = [
        "# Auto Leaderboard",
        "",
        "Ranked by mean score across all evaluated cases (higher is better).",
        "Score of 100 = matches DS-EPLB baseline. Failed runs sink to the bottom.",
        "",
        "| rank | run | mean_score | cases | error |",
        "|---:|:---|---:|:---|:---|",
    ]
    for i, row in enumerate(rows_sorted, start=1):
        score = row.get("mean_score")
        score_str = f"{score:.4f}" if isinstance(score, (int, float)) else "—"
        cases = row.get("cases", [])
        case_parts = []
        for c in cases:
            tag = f"{c.get('model')}/{c.get('dataset')}/EP{c.get('ep')}"
            cs = c.get("score")
            cs_str = f"{cs:.2f}" if isinstance(cs, (int, float)) else "—"
            case_parts.append(f"{tag}={cs_str}")
        err = row.get("error") or ""
        lines.append(
            f"| {i} | `{row.get('run', '?')}` | {score_str} | {', '.join(case_parts) or '—'} | {err} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    rows = collect()
    LEADERBOARD.write_text(render(rows))
    print(f"Wrote {LEADERBOARD} with {len(rows)} runs.")


if __name__ == "__main__":
    main()
