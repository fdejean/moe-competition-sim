# 4-Minute Talk Script — MoE Dynamic Expert Placement

*Audience: ML PhDs. ~150 wpm → ~600 words ≈ 4 min. Bracketed notes are slide cues.
Lead with the objective reformulation; it's the contribution.*

---

## The script

**[Slide: Title — "Dynamic Expert Placement as Robust Makespan Minimization · 114.76"]**

We're given expert-hotness traces from a DeepSeek-MoE inference run and must place
experts — and a budget of replicas — onto expert-parallel devices, re-deciding each
window. We're scored against DeepSeek's own EPLB; 100 is parity. We reach 114.76.

**[Slide: Formulation — the two equations]**

Here's the formulation that everything follows from. A replicated expert's load splits
evenly across copies, so the mean device load is the total load over the device count —
**invariant to placement.** Minimizing peak-to-average ratio is therefore exactly
**makespan minimization.** But here's the subtlety: the grader averages PAR *over
iterations*, while we commit *one* placement per window. So the real objective is the
**expected max device load**, the expectation of a maximum — not the max of the
expectation. By Jensen those differ, and the gap is exactly the **within-device
cross-expert covariance**: experts that spike in the same iteration. DeepSeek's EPLB
optimizes the max of the mean. We build surrogates for the expectation of the max. That
reframing is the whole talk.

**[Slide: The oracle decomposition]**

First, where are the points? We ran a ceiling decomposition: give the *same* packer the
true next-window load, and PAR collapses to about 1.03 at *every* device count — even
256. So packing is solved. The entire realized gap, up to PAR 3.4, is **forecasting and
static-hold error**, and it grows super-linearly with device count: at EP256 each device
holds two experts, so there's no within-device averaging to absorb a forecast miss. The
worst case — a non-stationary source mixture at EP256 — is a *prediction* problem, not a
packing one.

**[Slide: Three surrogates for E[max]]**

So we attack the expectation-of-max with three orthogonal surrogates. **One:
multi-scenario L-infinity packing.** Longest-processing-time greedy, but each slot
carries a vector of scenario loads — temporal sub-window means plus a joint-peak column,
the expert profile conditioned on the top-decile busiest iterations, a CVaR-style
conditioning. The greedy minimizes the L-infinity over scenarios, so it refuses to
co-locate experts that are hot together. **Two: drift-gated recency** — an L1
relative-drift statistic gates a convex blend toward the recent sub-window, identically
zero on stationary data so we never regress the cases we already win. **Three: robust
replication** — replica counts water-filled on a mean-plus-three-sigma target, a
one-sided Cantelli bound, decoupled from the gentler placement risk. A replica is a
coarse integer hedge, so it's sized to higher confidence.

**[Slide: The EP256 endgame — min–max matching]**

At EP256, two slots per device, placement collapses to a **cardinality-two matching**:
device load is the sum of two series, and the variance of a sum is minimized by pairing
**anti-correlated** experts. We fix the heavy slots one per device and greedily match
each light slot to minimize the realized peak — the L-infinity of the summed series.
That exact greedy floored under the original 3x compute budget; when it relaxed to 6x it
became feasible. That's the last jump.

**[Slide: The stack — 114.76]**

The winner stacks the two strongest levers because they act on **disjoint variables** —
the matching sets the assignment, the robust water-fill sets the replica counts — so the
gains are near-additive. All of it wrapped in a no-trade band, which is an impulse-control
policy that holds placement unless the balance gain clears a threshold, plus a
PAR-neutral relabeling that cuts transmit using the symmetry of the load functional.

**[Slide: Takeaway]**

Three messages: the objective is expectation-of-max, not max-of-expectation; the ceiling
analysis says the residual is forecasting, not packing; and every method had to be a
single vectorized build because the compute budget floors anything iterative at scale.
Thank you.

---

## Sample Q&A (PhD-level)

**Q: Why is mean device load placement-invariant?**
A replicated expert's per-copy load is its total load over its replica count, summed
over its copies it returns the total. So $\sum_d L_d = \sum_e x_e$ regardless of
assignment; $\bar L$ is fixed and minimizing PAR is pure makespan minimization.

**Q: Quantify the Jensen gap you're exploiting.**
$\mathbb{E}_t[\max_d L_d] - \max_d \mathbb{E}_t[L_d]$. The first term is what's scored;
the second is what mean-based packing optimizes. The difference is governed by the
within-device covariance $\mathrm{cov}(x_a,x_b)$ for experts $a,b$ co-located on $d$.
Measured realized-minus-mean PAR: +0.19 at EP128, +0.27 at EP256 on DS-R1.

**Q: The joint-peak column — why conditioning on total load rather than per-expert tails?**
Per-expert tail replication (p95/p99) validated locally but did *not* transfer to the
server. The realized peak is a *joint* event — which experts are hot in the same
iteration — so conditioning on the globally-busy iterations ($\mathbb{E}[x_e \mid t \in
\text{top-}p]$) captures the covariance structure that marginal tails miss.

**Q: Why $\kappa=3$ for replication but $\lambda\approx2$ for placement?**
Placement is continuous and corrected every window; replication is an integer hedge that
persists. Sizing copies to a higher one-sided confidence ($\Pr(x \ge \mu+\kappa\sigma)
\le 1/(1+\kappa^2)$) is the robust choice. Empirically $\kappa=3$ beat the
distribution-free CVaR target, so the moment bound is near-tight here.

**Q: Is the EP256 matching optimal?**
No — it's a greedy on the exact realized-peak objective, $O(D^2 s)$. Optimal
anti-correlated b-matching is more expensive; the greedy captures most of it and stays
inside the 6x budget. At $S=2$ the oracle shows placement is near-saturated anyway; the
residual is replica sizing and forecasting.

**Q: Why is the local proxy anti-correlated with the server?**
It contains only EP32 LmSys — stationary and already near the packing floor. The
co-spiking/forecasting levers help precisely on the non-stationary high-EP cases it
doesn't contain. The joint-peak target is *worse* than $\mu+2\sigma$ on LmSys yet wins
on the grader. We ranked everything on the live server.

**Q: Where's the remaining headroom?**
The oracle says ~+1.0 PAR at EP256 is recoverable, entirely a next-window forecasting
problem. Placement and replication are saturated. The budget-legal lever is forecaster
ensembling — a convex combination of $O(E)$ predictors (multi-horizon EWMA, last-window,
joint-peak) — which survives EP256 where no iterative placement does.
