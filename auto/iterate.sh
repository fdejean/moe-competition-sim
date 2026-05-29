#!/usr/bin/env bash
# auto/iterate.sh <slug> <NNN> [extra eval args...]
# Build a variant from codes/<slug>/ into submissions/<slug>.zip + auto/runs/<NNN>_<slug>/,
# then full-length proxy-eval (6144 iters, LmSys EP32 both models, baseline cached).
# Writes score.json INTO the new run dir (safe: it's this agent's own fresh snapshot).
set -euo pipefail
ROOT="/Users/veron/Desktop/moe-competition-sim"
PY="$ROOT/.venv_auto/bin/python"
slug="$1"; NNN="$2"; shift 2
cd "$ROOT"
test -f "codes/$slug/submission.py" || { echo "missing codes/$slug/submission.py"; exit 1; }

# zip (submission.py must be at zip root)
( cd "codes/$slug" && rm -f "../../submissions/$slug.zip" && zip -j -q "../../submissions/$slug.zip" *.py )

# snapshot
run="auto/runs/${NNN}_${slug}"
mkdir -p "$run"
cp codes/"$slug"/*.py "$run"/

# full-length proxy eval (does not touch other runs)
"$PY" auto/eval.py "$run" --max-iters 6144 "$@"
