# Methods — what the code actually computes

*Precise formulation and the exact estimator/algorithm behind each mechanism, as
implemented in `codes/cov_iter_dro/submission.py` (the 114.76 submission). Written
for a reader who wants the math, not the analogy.*

---

## 1. Problem, stated exactly

Per layer (layers are independent — verified: cross-layer hotness correlation
≈ 0.002), we are given a window $H \in \mathbb{Z}_{\ge 0}^{T \times E}$ of per-iteration
expert loads ($T$ iterations, $E$ experts), and must emit a placement of
$E + R$ **physical slots** onto $D$ devices, $S = (E+R)/D$ slots per device. Let
$c_e \ge 1$ be the replica count of expert $e$ ($\sum_e c_e = E + R$), and let the
assignment put copy loads on devices.

**The simulator's load model.** For a per-iteration load vector $x \in \mathbb{R}^E$,
each expert's load is split evenly across its replicas, so a single copy carries
$x_e / c_e$. Device $d$'s load is

$$L_d(x;\pi) = \sum_{(d,s)} \frac{x_{\pi(d,s)}}{c_{\pi(d,s)}}, \qquad
\mathrm{PAR}(x;\pi) = \frac{\max_d L_d}{\bar L}, \quad \bar L = \tfrac1D\textstyle\sum_d L_d.$$

**Key invariant.** Each expert's total load is conserved across its copies, so
$\bar L = \tfrac1D \sum_e x_e$ is **independent of the placement $\pi$**. Therefore

$$\arg\min_\pi \mathrm{PAR} \;\equiv\; \arg\min_\pi \max_d L_d(x;\pi)$$

— minimizing PAR is exactly **makespan minimization** (min–max load).

**The objective we actually face.** The grader scores the *time-averaged* PAR,
$\frac1T\sum_t \mathrm{PAR}(x_t;\pi)$, but we commit **one** placement $\pi$ for the
whole window. So we minimize an **expected makespan under per-iteration uncertainty**,

$$\min_\pi \; \mathbb{E}_t\!\big[\max_d L_d(x_t;\pi)\big],$$

**not** the makespan of the mean $\max_d L_d(\bar x;\pi)$. By Jensen
$\mathbb{E}_t[\max_d L_d] \ge \max_d \mathbb{E}_t[L_d]$, and the gap is precisely
**within-device cross-expert covariance** — co-spiking. The naïve baseline (and
DS-EPLB) optimize the right-hand side; the whole project is surrogates for the
left-hand side.

**Two coupled decisions**, which we factorize: (i) the integer **replica counts**
$c_e$, and (ii) the **slot→device assignment**. Plus a temporal layer: when to
redeploy (transaction-cost / no-trade band) and how to minimize transmit.

**The binding constraint.** Per-call compute budget $= \rho \times$ DS-EPLB time for
that case ($\rho$ started at 3, later relaxed to ~6), on CPU vs DS-EPLB's GPU. At
$D=256$, $S=2$: any $O(\text{slots})$ Python loop floors the run to $-10^6$. This is
why almost everything must be a single vectorized build.

---

## 2. Replication: robust upper-confidence water-filling (`_replicas_and_order`)

Greedy water-filling on a target weight $\tilde w$: initialize $c_e = 1$; repeat $R$
times $c_{e^\*} \mathrel{+}= 1$ with $e^\* = \arg\max_e \tilde w_e / c_e$. This greedily
minimizes $\max_e \tilde w_e / c_e$ (the heaviest single-copy load) — the per-slot PAR
floor. Identical structure to DS-EPLB's replication.

**dro_ambiguity — what's new is the target.** We size copies for a *worse* case than
we place for:

$$\tilde w_e \;=\; \underbrace{(1-\theta)\mu_e + \theta\,\mu^{\text{peak}}_e}_{w^{\text{place}}_e} \;+\; \kappa\,\sigma_e,\qquad \kappa = 3,$$

while the **placement** target uses $\lambda \in \{1.5, 2\}$ (`LAMBDA_HI`/`LAMBDA`):
$w^{\text{risk}}_e = w^{\text{place}}_e + \lambda\sigma_e$. The decoupling $\kappa = 3 > \lambda$
is the mechanism: $\mu_e + \kappa\sigma_e$ is a **one-sided (Cantelli) upper-confidence
load**, $\Pr(x_e \ge \mu_e + \kappa\sigma_e) \le 1/(1+\kappa^2)$ — i.e. a distributionally
robust (moment-ambiguity) bound on the per-iteration load. A replica is a *coarse,
integer* hedge against a spike, so it should be sized to a higher confidence than the
continuous placement target. Empirically $\kappa=3$ beat the distribution-free CVaR
target (dro_cvar 114.25 < 114.32), so the Gaussian/Cantelli sizing is near-optimal on
this axis.

---

## 3. Placement targets (the per-expert weights)

All computed once per window, vectorized over $(L, E)$:

- **Mean** $\mu_e = \frac1T\sum_t x_{t,e}$, optionally drift-adapted (§5).
- **Joint-peak** $\mu^{\text{peak}}_e = \mathbb{E}\!\big[x_e \mid t \in \mathcal{P}\big]$,
  $\mathcal{P} = $ top-$\lceil pT\rceil$ iterations ranked by **total** load
  $\sum_e x_{t,e}$ ($p = 0.10$). This is the expert-load profile **conditioned on the
  system being in a globally-busy (joint-spike) state** — a CVaR-style tail
  conditioning that captures *which experts are hot together*, in a single scalar
  per expert.
- **Dispersion** $\sigma_e = \mathrm{sd}_t(x_{t,e})$.
- Placement target $w^{\text{place}} = (1-\theta)\mu + \theta\mu^{\text{peak}}$, $\theta = 0.5$;
  risk target $w^{\text{risk}} = w^{\text{place}} + \lambda\sigma$.

---

## 4. Placement algorithm: multi-scenario L∞ LPT (peakblock)

Slots are expanded ($c_e$ copies of $e$, each with per-copy weight $w^{\text{risk}}_e/c_e$)
and sorted **heaviest-first** (LPT). Each slot is placed on the device minimizing a
score; two variants:

**Scalar** (`_build_scalar`, used at $D \ge 128$ in the EP≤128 path is *block*, see
below): score $= L_d + \infty\!\cdot\![\,e \text{ already on } d\,] - \text{stay}\cdot[\text{e on } d \text{ last time}]$.

**Block / multi-scenario** (`_build_block`, the peakblock core, $D \le 128$): each slot
carries a **vector** of scenario loads
$b_e = \big[b^{(1)}_e,\dots,b^{(k)}_e,\ \mu^{\text{peak}}_e\big] \in \mathbb{R}^{k+1}$,
where $b^{(j)}_e$ is the mean of $x_e$ over the $j$-th of $k=6$ contiguous temporal
blocks. Device $d$ accumulates $B_d \in \mathbb{R}^{k+1}$, and the placement score is

$$\text{score}(d) = \big\| B_d + b_{\text{slot}} \big\|_\infty
= \max_{j}\,(B_d + b_{\text{slot}})_j .$$

So LPT greedily minimizes the **worst-case device load across all $k+1$ scenarios at
once** — a vector-bin-packing / robust-makespan greedy. The temporal blocks enforce
balance under *within-window drift* (each sub-window stays level); the appended
**joint-peak column** makes the *placement itself* co-spiking-aware (it refuses to
co-locate experts that are both heavy in the spike scenario) — distinct from merely
shaping the scalar target. This "append an informative scenario column" was the single
most reliable PAR lever; changing the *norm* (L2, dot-product) instead lost.

---

## 5. Drift-gated recency target (drift_router, `_adaptive_mean`)

Per layer, an L1 relative-drift statistic between the last quarter-window and the full
window:

$$\delta_\ell = \frac{\big\|\mu^{\text{recent}}_\ell - \mu_\ell\big\|_1}{\|\mu_\ell\|_1},\qquad
a_\ell = \mathrm{clip}(\gamma\,\delta_\ell, 0, 1),\qquad
\hat\mu_\ell = (1-a_\ell)\,\mu_\ell + a_\ell\,\mu^{\text{recent}}_\ell .$$

$\gamma = 1$, recent fraction $= 0.25$. Stationary ⇒ $\delta_\ell \approx 0 \Rightarrow \hat\mu = \mu$
(byte-identical to peakblock — cannot regress the cases we already win); non-stationary
(Mix) ⇒ lean recent and track the regime. A *gated* recency (vs always-on EWMA, which
was neutral-to-harmful on stationary data because load is persistent, lag-1 autocorr
≈ 0.5).

---

## 6. EP256: min–max pairing (cov_match → cov_iter)

At $D = 256$, $S = 2$: each device holds exactly two slots, so placement is a
**cardinality-2 matching** and the device time series is $h_d[t] + \ell_{j(d)}[t]$.
Fix the $D$ heaviest slots one-per-device (heavy partners $h_d$). The objective is to
match the $D$ light slots to devices minimizing the realized peak
$\max_d \max_t\big(h_d[t] + \ell_{j}[t]\big)$, i.e. the $L^\infty$ norm of the summed
series. Since $\mathrm{var}(h+\ell) = \mathrm{var}(h)+\mathrm{var}(\ell)+2\,\mathrm{cov}(h,\ell)$,
the peak is minimized by pairing **anti-correlated** slots.

- **cov_match (114.35)** — cheap $O(E\log E)$ spectral proxy: dominant co-spiking
  temporal mode $u$ via 5 power iterations on the subsampled slot signal; phase
  $\langle \text{sig}_j, u\rangle$ per slot; heavy/light fold by load, paired opposite
  phase. Fully vectorized → no floor risk even at $\rho=3$.
- **cov_iter (114.61)** — exact greedy on the *true* objective, affordable at $\rho\approx6$:
  for each light slot $j$ (heaviest first), assign to the available device
  $d^\* = \arg\min_d \max_{t\in \text{subsample}}\big(h_d[t] + \ell_j[t]\big)$.
  Subsample $s=48$ iterations. $O(D^2 s)$, vectorized per step. Floored at $\rho=3$ —
  this lever is *purely* a budget-relaxation unlock, not a new idea.

EP≤128 path is byte-identical to peakblock; the matching is invoked only when $S=2$.

---

## 7. Temporal policy: no-trade band + PAR-neutral transmit (finance_band + relabel)

**No-trade band (transaction-cost control).** With cached deployment $\pi^{\text{cur}}$,
redeploy layer $\ell$ only if the balance gain exceeds a threshold:

$$\Delta_\ell = \mathrm{PAR}(\bar w_\ell;\pi^{\text{cur}}_\ell) - \mathrm{PAR}(\bar w_\ell;\pi^{\text{cand}}_\ell) > \tau,\quad \tau = 0.2,$$

else hold (return `change=False`). This is an $(s,S)$-style impulse-control / no-trade
band: act only when the expected benefit clears the transaction cost. It is the entire
100→111 jump — DS-EPLB redeploys unconditionally (transmit ~840k–150k), we hold
(~15k), at near-identical PAR.

**PAR-neutral transmit reduction (`_align`).** The load functional $L_d$ is invariant
under (a) device relabeling and (b) intra-device slot permutation — a stabilizer
subgroup of the placement. Within that orbit we minimize Hamming distance to
$\pi^{\text{cur}}$: match new device rows to old by **shared head expert** (the argmax-load
slot), then permute slots within each device to keep persisting experts in place.
Zero PAR cost, −18% transmit. A **stay-bias** term in the LPT score
($-\text{stay}\cdot[e \text{ on } d \text{ last time}]$) additionally biases the *build*
toward the previous layout (source-side stickiness), −26% transmit cumulative.

---

## 8. The winning stack (cov_iter_dro = 114.76) and why it composes

The two strongest levers act on **disjoint decision variables**:

| Lever | Decision variable | Estimator |
|---|---|---|
| **cov_iter** | slot→device **assignment** at $S=2$ | min–max realized-peak matching |
| **dro_ambiguity** | integer **replica counts** $c_e$ | water-fill on $\mu + 3\sigma$ |

Because assignment (given counts) and counts are orthogonal, stacking is near-additive
(confirmed: +0.04 on the cov_match base). Full pipeline per window:

1. targets: drift-gated $\hat\mu$ (§5), joint-peak $\mu^{\text{peak}}$ (§3), $\sigma$;
2. $w^{\text{place}} = (1-\theta)\hat\mu + \theta\mu^{\text{peak}}$; risk $+\lambda\sigma$; rep $+\,3\sigma$;
3. replica counts: water-fill on rep target (§2);
4. placement: multi-scenario L∞ LPT at $D\le128$ (§4); min–max matching at $D=256$ (§6);
5. PAR-neutral align to previous (§7);
6. no-trade band gate per layer (§7), redeploy in order of $\Delta_\ell$.

---

## 9. The decisive empirical facts

- **Oracle / ceiling decomposition** (`experiments/oracle_gap.py`, `ceiling_decomp.py`):
  the *same* packer with the true next-window load achieves PAR $\approx 1.01$–$1.04$ at
  **every** EP. Hence packing is solved; the realized excess (1.24–2.0) is forecasting /
  static-hold error. The gap grows super-linearly in $D$ (fragmentation: $S \to 2$
  removes the within-device averaging that absorbs forecast error).
- **Hardest case is `Mix · DS-R1 · EP256`, PAR 3.42** — a non-stationary source mixture;
  its window mean is a biased predictor of the next regime. This single case dominates
  the residual score, and it is a *prediction* problem, not a packing one.
- **Local proxy is anti-correlated with the server** for the co-spiking/forecasting
  levers (it contains only the saturated EP32 LmSys case): the joint-peak target is
  *worse* than $\mu+2\sigma$ on LmSys yet *wins* on the server. All ranking was done on
  the live grader.

---

## 10. Open frontier

The oracle says ~$+1.0$ PAR is recoverable at EP256, all of it a next-window
**forecasting** problem under the budget. The placement and replication axes are
saturated. The untried high-ceiling lever is **forecaster ensembling** — a convex
combination of cheap $O(E)$ predictors (multi-horizon EWMAs, last-window, joint-peak)
to de-noise $\hat x_{t+1}$ — which survives the $D=256$ budget where no iterative
placement can.
