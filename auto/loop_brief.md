# Overnight loop brief — one iteration (vero agent)

You are iterating autonomously to beat the MoE EPLB leaderboard. Read
`auto/loop_state.json` and `auto/loop_queue.md` first. **PAR is ~97% of the
server score — optimize PAR, not transmit** (see memory `moe-par-dominates-score`).

## Per-iteration steps
1. **Pick** the highest-priority `[ ]` idea from `auto/loop_queue.md`. Mark `[~]`.
   If a whole family has stopped improving (2+ consecutive non-beaters), pivot to a
   different tier / invent a new literature-rooted idea and add it to the queue.
2. **Implement** it. Build on `codes/band_refine_transmin/submission.py` (copy it
   to `codes/<slug>/submission.py`, modify the relevant stage). Keep the hard
   contract: `rebalance(hotness,n_device,n_red_expert) -> (change, layers_priority,
   deployment, None)`, `deployment.shape==(n_layers,n_device,(n_experts+n_red)//n_device)`
   ALWAYS (cache last good for change=False), per-call wall-clock < ~7s (degrade
   gracefully), robust across EP32/EP64 + Qwen3(94L/128E) + DS-R1(58L/256E).
   You MAY dispatch a subagent to implement from a precise spec to save context.
3. **Snapshot**: pick next NNN from `auto/loop_state.json.next_nnn` (mine = 050+),
   create `auto/runs/NNN_<slug>/` with the submission.py (+ helpers). Increment next_nnn.
4. **Eval**: `bash auto/proxy_eval.sh <slug>`. Read the LH proxy mean_score (EP32,
   6144 iters) and confirm the EP64 smoke shows no error/timeout and correct shape.
5. **Record**: update `auto/loop_queue.md` (mark `[x]` + proxy score + 1-line finding),
   append a line to `auto/NOTES.md`, regenerate `.venv/bin/python auto/leaderboard.py`.
6. **Gate + submit**: if proxy mean_score > `best_proxy.mean_score` AND EP64 smoke
   clean AND submit_budget allows AND >= min_minutes_between since last submit:
   - `cp codes/<slug>/submission.py submissions/<slug>/submission.py` (mkdir),
     then `cd codes/<slug> && zip -j ../../submissions/<slug>.zip *.py`.
   - Submit: `python3 tools/codabench_submit.py submissions/<slug>.zip --no-wait`
     (system python3, NOT venv). Capture the printed submission id.
   - Record in `loop_state.json.submissions` [{nnn,slug,submission_id,ts,proxy}],
     bump used, set last_submit_ts. Update `best_proxy` if beaten.
   - Poll later with `python3 tools/codabench_submit.py --submission-id <id>` and
     record server score in loop_state + NOTES + loop_queue (`[S]`).
7. **Commit** occasionally (every few iterations) to `vero-flo`: never sign as Claude.
8. **Schedule** the next iteration (ScheduleWakeup) and repeat until backlog
   exhausted or out of submission budget or morning.

## Guardrails
- A bad submission wastes a real leaderboard slot — the gate above is mandatory.
- Don't edit `auto/runs/NNN_*` snapshots owned by peers (<=049). Don't touch the
  `-fdejean/` clone. Use `set_summary` to keep peers aware; answer peer messages.
- If proxy eval errors, FIX the variant (it would score -1e6 on the server) before
  moving on; don't submit it.
