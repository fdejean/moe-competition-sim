# 4-Minute Talk Script — MoE Dynamic Load Balancing

*Spoken pace ~140 wpm → ~560 words ≈ 4 min. Square-bracketed notes are stage
directions / which slide to be on. Speak it, don't read it.*

---

## The script

**[Slide: Title — "MoE Load Balancing · score 114.76"]**

A model like DeepSeek-R1 doesn't push every word through the whole network. Each
layer has 256 little sub-networks — "experts" — and a router sends each word to
just a handful. That's what makes it fast. But the router plays favorites: a few
experts get swamped while most sit idle.

**[Slide: The Problem — diagram / hotness-over-time graph]**

Now spread those experts across GPUs. A layer can't finish until its *slowest* GPU
finishes. So if the popular experts all pile onto one GPU, every other GPU sits and
waits. Our job: given a recent history of how busy each expert was, decide which
GPU each expert — and any spare copies — should live on. And re-decide as the load
shifts. We're graded against DeepSeek's own balancer. A hundred means we tied it.
We hit **114.76** — about fifteen percent faster.

**[Slide: Scoring — PAR + the speed budget]**

The number we minimize is **PAR** — how overloaded the busiest GPU is versus the
average. One is perfect. And there's a villain: we get almost no compute time per
decision. Anything too slow is disqualified outright — score goes to minus a
million. That time limit shaped every single thing we built.

**[Slide: Finance analogy / finance_band]**

Our starting point treated each expert's load like a stock price. DeepSeek
re-shuffles experts constantly, which is expensive. Instead, we put a **no-trade
band** around each expert: only move it if its load really changed. Same balance,
far less shuffling. That alone got us to 111 — almost entirely from *not* churning.

**[Slide: The oracle finding — realized vs perfect-foresight bars]**

Then the key experiment. We asked: if we could see the future perfectly, how good
could balance get? Answer — *nearly perfect, at every scale.* So the placement math
is basically solved. **Our whole loss is that we place experts on a stale snapshot,
and the load moves out from under us.** It's a forecasting problem, not a packing
problem — and it gets brutal at scale, because with 256 GPUs each one holds only
about *two* experts. No room to absorb a surprise.

**[Slide: The climb — score-climb graph]**

So we attacked the forecasting gap with a series of new mechanisms. Don't put
experts that *spike together* on the same GPU. Lean on recent data when the traffic
**drifts**. Give hot experts an extra copy sized for the *worst* case, not the
average. And at the largest scale — two experts per GPU — placement is literally a
*pairing* problem, so we pair *anti-correlated* experts: when one spikes the other
is quiet, and the GPU stays flat.

**[Slide: cov_iter_dro — the winner]**

The winner stacks the two best independent ideas. One decides *which* two experts
share each GPU. The other decides *how many copies* each hot expert gets. They don't
interfere — so the gains add up. That's **cov_iter_dro: 114.76**.

**[Slide: Takeaway]**

The recurring lesson: the speed budget is everything. Every "smarter" idea that
needed a loop got disqualified at scale. The wins came from doing smart things
*cheaply*, and from spending the expensive computation only where there's time for
it. Thank you.

---

## Sample Q&A (likely judge questions)

**Q: What exactly is PAR, in one sentence?**
The most-loaded GPU's work divided by the average GPU's work. 1.0 is perfect
balance; we're fighting to push it down toward 1.0.

**Q: Why not just re-balance every step to track the load?**
We tried — it's a trap. Re-balancing costs "transmit" (moving expert weights between
GPUs), and re-shuffling constantly explodes that cost while barely improving PAR,
because the load is *persistent* — chasing every wiggle overshoots. Holding still
and only moving on real change wins.

**Q: You said packing is "solved" — then where do the points come from?**
From a perfect-foresight experiment: with the true future load, the same packer hits
near-perfect balance at every scale. So the gap isn't packing, it's *prediction*. The
points come from making one placement robust to being wrong about the future —
co-spiking-aware placement, drift-aware recency, and worst-case-sized replicas.

**Q: What's the hardest case?**
"Mix" — a dataset that blends different traffic types, on the biggest model at the
largest GPU count. Its popular set keeps shifting, so any snapshot is stale. Its PAR
is 2–3× everything else, so it basically decides the score.

**Q: What does the winning algorithm (cov_iter_dro) actually do?**
Three layers. (1) Base: balance the *moments experts spike together* as extra
columns the packer must level, plus drift-aware recency. (2) At the largest scale
(2 experts per GPU) it's a pairing problem — greedily pair each light expert with the
heavy expert whose combined peak is lowest, i.e. pair anti-correlated experts. (3)
Replica counts are sized by the worst case (mean + 3 standard deviations), decoupled
from placement. Layers (2) and (3) are independent, so stacking them compounds.

**Q: Why did the score jump at the very end?**
The competition relaxed the per-decision time budget from 3× to ~6× DeepSeek's time.
That finally let the richer pairing search (cov_iter) run at the largest scale
without being disqualified — it had floored before purely on speed, not merit.

**Q: What surprised you / what didn't work?**
Lots. Per-expert tail-replication looked great locally but didn't transfer to the
server. Fancier "location" estimators (median, trimmed mean) were neutral. Any
iterative refinement timed out at scale. And the local test set was actively
*misleading* — it only contained the easy cases — so the live server was our only
honest signal.

**Q: Is there headroom left?**
Yes — the oracle says ~1.0 PAR is recoverable at the largest scale, and we're still
near 1.65. That's a *forecasting* prize: the next step is ensembling cheap predictors
of the next window, not better packing.
