# program.md — MoE Submission Autoresearch

You are an autonomous agent. Your job: propose new `submission.py` variants for the
MoE dynamic load-balancing competition, evaluate them with the local simulator,
and never throw a variant away. Every attempt is kept on disk forever.

## Hard rules

1. **Never edit or delete an existing run folder.** Each variant lives in its own
   directory and stays there. Past failures are data too.
2. **Never run forever.** Each `submission.rebalance()` call has a per-call time
   budget (default 10s). The evaluator aborts variants that exceed it.
3. **Never break the API.** `rebalance(hotness, n_device, n_red_expert)` must
   return `(change: bool, layers_priority: np.ndarray, deployment: np.ndarray, _)`
   where `deployment.shape == (n_layers, n_device, (n_experts + n_red_expert) // n_device)`.
   **Even when `change=False`**, return a deployment array of the correct shape
   (cache the last good one).

## Repository layout you will use

```
auto/
  program.md          ← this file (do not modify)
  eval.py             ← evaluates one run folder, writes score.json + stdout.txt
  leaderboard.py      ← regenerates LEADERBOARD.md from every score.json
  LEADERBOARD.md      ← regenerated; rank of all runs
  runs/
    000_seed_smoke/
      submission.py
      score.json      ← {mean_score, cases:[...], error, elapsed_seconds, ...}
      stdout.txt
    001_seed_hot/
      submission.py
      deepseek.py     ← optional helper
      score.json
      ...
```

## Loop

1. **Read** `LEADERBOARD.md` and the top 3 runs' `submission.py` to understand what
   works and what doesn't.
2. **Pick a hypothesis** based on past results. Examples:
   - "Hot-expert duplication helps but the priority order is naive — sort layers
     by max-PAR not by index."
   - "DS-EPLB is too slow at EP64 — throttle recompute and cache deployment."
   - "Smoothed EMA over hotness window is more stable than raw sum."
3. **Create a new folder** `auto/runs/NNN_<short_slug>/` where `NNN`
   is one greater than the highest existing index. Keep the slug ≤30 chars.
4. **Write `submission.py`** (+ any helper modules) into that folder. The agent
   may copy `eplb_algorithms/deepseek.py` next to it if needed — the evaluator
   adds the run folder to `sys.path` first.
5. **Run the evaluator:**
   ```
   python auto/eval.py auto/runs/NNN_<slug>
   ```
6. **Append** a one-line note to `auto/NOTES.md` describing the
   hypothesis and the outcome.
7. **Regenerate the leaderboard:**
   ```
   python auto/leaderboard.py
   ```
8. Go to step 1.

## Evaluation details

- Two cases by default: `Qwen3:LmSys:32` and `DS-R1:LmSys:32` (the committed sample
  traces).
- `max_iters = 1200`, `collection_interval = 1024`.
- Score for each case = `100 * ds_eplb_modeled_time / submission_modeled_time`.
  100 means equal to DS-EPLB baseline; higher is faster.
- `mean_score` is the simple mean across cases.
- A case that errors (timeout, bad shape, exception) gets `-1_000_000` and the
  remaining cases are skipped.

## Useful priors

- `quickstart.py` and `dynamic_lb_simulator.py` define the API the simulator uses.
- `eplb_algorithms/deepseek.py` is the official DS-EPLB algorithm; it's slow on
  large EP — wrap it with throttling/caching to stay within budget.
- `submissions/hot_expert_baseline/submission.py` is a cheap, fast baseline.
- The Codabench server evaluates on *more* traces than the local sample set
  (ShareGPT, EP64, etc.). Local PAR is a proxy, not ground truth.

## What to write down

In `auto/NOTES.md` keep one line per attempt:

```
2026-05-30 02:14  005_layer_priority_par   mean_score=104.31  hypothesis="sort priority by per-layer PAR descending"
2026-05-30 02:31  006_throttle_8           mean_score= 98.02  hypothesis="recompute every 8 calls (was 4); saves time but quality drop"
```

## Stop conditions

- 100 successful variants written, or
- 8 hours elapsed since the loop started, or
- 10 consecutive variants score lower than the current best.

Then stop and let the human pick the winner to upload to Codabench.
