#!/usr/bin/env bash
# newsub.sh <name> — freeze + named-zip + submit one architecture, fully traceable.
# Requires codes/<name>/submission.py to already exist.
# Produces:
#   submissions/<name>.zip            (Codabench shows THIS name)
#   auto/runs/NNN_<name>/submission.py + <name>.py (uniquely-named copy) + score.json
#   appends id to auto/pending_subs.txt and a row to SUBMISSIONS.md
set -euo pipefail
cd "$(dirname "$0")/.."
NAME="$1"
SRC="codes/$NAME/submission.py"
[ -f "$SRC" ] || { echo "missing $SRC"; exit 1; }

# named zip (submission.py at root — Codabench requirement)
( cd "codes/$NAME" && zip -j -q "../../submissions/$NAME.zip" submission.py )

# frozen snapshot with a UNIQUELY-NAMED copy alongside submission.py
N=$(printf "%03d" $(( $(ls auto/runs | sed -nE 's/^([0-9]{3})_.*/\1/p' | sort -n | tail -1) + 1 )))
DIR="auto/runs/${N}_${NAME}"
mkdir -p "$DIR"
cp "$SRC" "$DIR/submission.py"
cp "$SRC" "$DIR/${NAME}.py"

# submit (no-wait → instant id)
OUT=$(python3 tools/codabench_submit.py "submissions/$NAME.zip" --no-wait -q 2>&1)
ID=$(echo "$OUT" | grep -oE "id=[0-9]+" | head -1 | cut -d= -f2)
echo "{\"variant\":\"$NAME\",\"id\":\"$ID\",\"server\":\"pending\"}" > "$DIR/score.json"
echo "$NAME $ID" >> auto/pending_subs.txt
[ -f SUBMISSIONS.md ] || echo "# Submissions index (id · name · score · idea)" > SUBMISSIONS.md
echo "- $ID · **$NAME** · _pending_ · run $DIR" >> SUBMISSIONS.md
echo "SUBMITTED $NAME -> id=$ID  (snapshot $DIR, zip submissions/$NAME.zip)"
