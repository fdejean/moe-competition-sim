#!/usr/bin/env bash
# Long-horizon proxy eval + EP64 smoke for one codes/<slug> dir.
# Usage: auto/proxy_eval.sh <slug>            (reads codes/<slug>/)
#        auto/proxy_eval.sh <slug> <src_dir>  (reads <src_dir>/)
# Prints a compact JSON-ish summary. Uses the torch venv. Does NOT touch
# codes/ or auto/runs/ canonical score.json — evals into auto/_proxy/<slug>/.
set -euo pipefail
cd "$(dirname "$0")/.."
SLUG="$1"
SRC="${2:-codes/$SLUG}"
PY=.venv/bin/python
SCRATCH="auto/_proxy/$SLUG"
rm -rf "$SCRATCH"; mkdir -p "$SCRATCH"
cp "$SRC"/*.py "$SCRATCH"/ 2>/dev/null || { echo "no .py in $SRC"; exit 1; }

echo "### LH proxy (EP32, 6144 iters, both LmSys) — the ranking number ###"
$PY auto/eval.py "$SCRATCH" --max-iters 6144 --cases Qwen3:LmSys:32 DS-R1:LmSys:32 2>&1 | tail -25

echo "### EP64 smoke (1500 iters) — must NOT error/timeout; checks shape+budget ###"
$PY auto/eval.py "$SCRATCH" --max-iters 1500 --cases Qwen3:LmSys:64 DS-R1:LmSys:64 2>&1 | tail -25

echo "### EP128 smoke (1500 iters) — THE SERVER TESTS EP128. n_device=128 must NOT"
echo "### timeout. Time each rebalance call; --per-call-budget mirrors the server. ###"
$PY auto/eval.py "$SCRATCH" --max-iters 1500 --cases Qwen3:LmSys:128 DS-R1:LmSys:128 --per-call-budget 10 2>&1 | tail -25

echo "### EP256 smoke (1500 iters) — SERVER TESTS DS-R1 EP256 (n_device=256). This is"
echo "### where block-vector packers FLOOR. Per-call time MUST be ~finance_band. ###"
$PY auto/eval.py "$SCRATCH" --max-iters 1500 --cases DS-R1:LmSys:256 --per-call-budget 10 2>&1 | tail -15
