# The Journey: finance_band 111.27 → cov_iter_dro 114.76

*The full experimental arc with the actual estimators and the reason each step moved
(or didn't) the live grader. Companion to [`methods.md`](methods.md), which states the
final algorithm formally. Read top to bottom.*

---

## 0. The objective, precisely

Per layer, window $H\in\mathbb{Z}_{\ge0}^{T\times E}$; place $E+R$ slots on $D$ devices,
$S=(E+R)/D$ slots/device, replica counts $c_e$. Simulator load model: a copy of expert
$e$ carries $x_e/c_e$; device load $L_d(x)=\sum_{(d,s)}x_{\pi(d,s)}/c_{\pi(d,s)}$;
$\mathrm{PAR}=\max_d L_d/\bar L$.

- **$\bar L=\frac1D\sum_e x_e$ is placement-invariant** ⇒ $\min_\pi\mathrm{PAR}\equiv$ **makespan** $\min_\pi\max_d L_d$.
- Grader scores $\frac1T\sum_t\mathrm{PAR}(x_t;\pi)$ with **one** committed $\pi$ ⇒ true objective is

$$\min_\pi \mathbb{E}_t\big[\max_d L_d(x_t;\pi)\big]\;\;\text{(expectation of a max)},\quad\text{not}\quad \min_\pi\max_d L_d(\bar x;\pi).$$

Jensen gap $=$ within-device cross-expert covariance (co-spiking). DS-EPLB and the
baseline optimize the latter; we build surrogates for the former.

- Score $=100\cdot t_{\text{DS-EPLB}}/t_{\text{ours}}$, $t=60\cdot\overline{\mathrm{PAR}}+\text{transmit}\cdot 88{,}080{,}384/9\!\times\!10^{11}$, mean over 25 cases. PAR : transmit $\approx 6000:1$ per unit.
- **Budget**: per-call compute $\le\rho\cdot t_{\text{DS-EPLB}}$ for that case ($\rho:3\to6$ late), CPU vs their GPU. At $D=256,S=2$, any $O(\text{slots})$ Python loop floors the run to $-10^6$.

---

## 1. finance_band (111.27): no-trade band as impulse control

**Estimator.** Scalar LPT on $w^{\text{risk}}_e=\mu_e+\lambda\sigma_e$ ($\lambda=2$),
water-filled replicas on the same weight, then a **no-trade band**: redeploy layer $\ell$
only if $\Delta_\ell=\mathrm{PAR}(\bar w;\pi^{\text{cur}})-\mathrm{PAR}(\bar w;\pi^{\text{cand}})>\tau$, $\tau=0.2$.

**Why it wins.** This is an $(s,S)$ / transaction-cost impulse-control policy. DS-EPLB
redeploys *unconditionally* — its transmit term is huge (≈840k–150k slot-moves);
holding collapses ours to ≈15k at statistically identical PAR ($\overline{\mathrm{PAR}}\approx1.69$ either way).
The full 100→111 is the transfer term, not balance. $\lambda=2$ is the replication
sweet spot on the server ($\lambda=0$: 108.1, $\lambda=4$: 107.5).

---

## 2. Transmit on the symmetry orbit (→ 111.8)

$L_d$ is invariant under (a) **device relabeling** and (b) **intra-device slot
permutation** — a stabilizer subgroup of the placement. Minimize Hamming distance to
$\pi^{\text{cur}}$ *within that orbit*:

- **cheap_relabel (111.751):** match new device rows to old by shared head expert
  (argmax-load slot) via an $O(D)$ dict; −18% transmit, provably PAR-neutral.
- **sticky_relabel (111.802):** add stay-bias $-\text{stay}\cdot[e\text{ on }d\text{ last time}]$
  to the LPT score (source-side stickiness); −26% cumulative.

> **Budget rule, learned by flooring.** The first relabel matched rows with an $O(D^2)$
> set-overlap loop → floored at EP128. The fix was the $O(D)$ head-expert dict. *Bound
> work by fixed operation count, never wall-clock.*

---

## 3. The ceiling decomposition: packing is solved (the reframe)

`experiments/oracle_gap.py`, `ceiling_decomp.py`: feed the *same* packer the true
**next**-window load.

| | EP32 | EP64 | EP128 | EP256 |
|---|---:|---:|---:|---:|
| realized | 1.235 | 1.383 | 1.616 | 1.998 |
| **oracle (true future)** | **1.011** | **1.032** | **1.035** | **1.025** |

Near-perfect at every $D$ ⇒ the realized 1.24–2.0 is **forecasting + static-hold error**,
not packing. The gap grows super-linearly in $D$: at $S\to2$ there is no within-device
averaging to absorb a forecast miss (fragmentation). Worst case **Mix·DS-R1·EP256 = 3.42**
is a non-stationary source mixture — the window mean is a biased predictor of the next
regime. **The score is a forecasting problem.**

> The local proxy (EP32 LmSys only) is **anti-correlated** with the server for these
> levers. All subsequent ranking is on the live grader.

---

## 4. Breaking "PAR is saturated": EP-adaptive dispatch (→ 112.5)

The "PAR saturated at 1.69" verdict was a **budget artifact** — the co-spiking-aware
build only *timed out at EP256*; at EP32/64 it ran in ~0.13s. Fix: dispatch the
expensive method where $\rho\cdot t_{\text{DS-EPLB}}$ allows, fall back to scalar where it
doesn't.

- **epadaptive_algo (112.079):** block-balanced placement at $D\le64$, scalar at $D\ge128$. **First $\overline{\mathrm{PAR}}<1.692$.**
- **epadaptive_ep128 (112.364):** raise threshold to $D\le128$ (only EP256 floors).
- **epadaptive_k4 (112.522):** $k=4$ coarse temporal blocks beat $k=32$ (which overfit window noise).

---

## 5. Multi-scenario L∞ packing: peakblock (→ 114.1)

The co-spiking lever, done as **vector bin packing**. Each slot carries
$b_e=[\,b^{(1)}_e,\dots,b^{(k)}_e,\ \mu^{\text{peak}}_e\,]\in\mathbb{R}^{k+1}$:
$b^{(j)}$ = mean over the $j$-th of $k=6$ temporal blocks;
$\mu^{\text{peak}}_e=\mathbb{E}[x_e\mid t\in\text{top-}10\%\text{ by total load}]$ (CVaR-style
joint-spike conditioning). LPT places each slot to $\arg\min_d\|B_d+b_{\text{slot}}\|_\infty$
— the worst device load **across all scenarios at once**.

- peak_balance 112.159 → cospike_hybrid 113.189 → peak_cospike 113.895 / _k4 113.995 → **peakblock 114.132**.
- Realized PAR 1.696 → 1.663.
- **The lever is the scenario column, not the norm:** L2 (113.1) and dot-product VBP
  (112.7) both lost. Appending an informative scenario to the L∞ greedy wins; reshaping
  the objective doesn't.

---

## 6. drift_router (114.181): gated recency for non-stationarity

Per layer, $\delta_\ell=\|\mu^{\text{recent}}_\ell-\mu_\ell\|_1/\|\mu_\ell\|_1$ (recent = last
25%), mixing weight $a_\ell=\mathrm{clip}(\gamma\delta_\ell,0,1)$, $\hat\mu_\ell=(1-a_\ell)\mu_\ell+a_\ell\mu^{\text{recent}}_\ell$.
Stationary ⇒ $\delta\approx0\Rightarrow\hat\mu=\mu$ (byte-identical to peakblock, can't
regress wins); Mix ⇒ leans recent. A **gated** recency — always-on EWMA was neutral
(load is persistent, lag-1 autocorr ≈ 0.5; recency only helps under a measured regime
shift). The forecasting lever finally paying on the server.

---

## 7. dro_ambiguity (114.32): robust replication, decoupled from placement

Replica water-fill on a **robust** target $\tilde w_e=\mu_e+\kappa\sigma_e$, $\kappa=3$,
*decoupled* from the placement risk ($\lambda\in\{1.5,2\}$). $\mu+\kappa\sigma$ is a
one-sided **Cantelli** bound, $\Pr(x_e\ge\mu+\kappa\sigma)\le 1/(1+\kappa^2)$ — a
moment-ambiguity (DRO) sizing. A replica is a coarse integer hedge that persists across
windows, so it's sized to higher one-sided confidence than the continuously-corrected
placement target.

> **dro_cvar (114.253):** distribution-free version (size copies by empirical worst-10%
> per-expert load) — slightly *worse*. The Gaussian/Cantelli sizing is near-optimal;
> replication sizing is tapped out at this formulation.

---

## 8. The EP256 endgame: min–max matching (cov_match → cov_iter, 114.35 → 114.61)

At $D=256,S=2$: placement is a **cardinality-2 matching**; device load $=h_d[t]+\ell_{j(d)}[t]$.
Fix the $D$ heaviest slots one-per-device; match light slots to minimize the realized
peak $\max_d\max_t(h_d[t]+\ell_j[t])$ = $L^\infty$ of the summed series.
$\mathrm{var}(h+\ell)=\mathrm{var}(h)+\mathrm{var}(\ell)+2\mathrm{cov}(h,\ell)$ ⇒ optimum pairs
**anti-correlated** experts.

- **cov_match (114.35):** $O(E\log E)$ spectral proxy — dominant co-spike temporal mode
  $u$ via 5 power iterations, slot phase $\langle\text{sig}_j,u\rangle$, heavy/light fold,
  pair opposite phase. Vectorized, no floor risk at $\rho=3$.
- **cov_iter (114.61):** the **exact greedy** on the realized-peak objective,
  $d^\*=\arg\min_d\max_{t\in\text{subsample}}(h_d[t]+\ell_j[t])$, light slots heaviest-first,
  $s=48$. $O(D^2 s)$ — **floored at $\rho=3$, feasible at $\rho=6$.** This jump is *purely*
  the budget relaxation, not a new idea.

EP≤128 path is byte-identical to peakblock; matching fires only when $S=2$.

---

## 9. cov_iter_dro (114.76): the orthogonal stack

The two strongest levers act on **disjoint decision variables**:

| Lever | Variable | Estimator |
|---|---|---|
| cov_iter | slot→device **assignment** ($S=2$) | min–max realized-peak greedy |
| dro_ambiguity | integer **replica counts** $c_e$ | water-fill on $\mu+3\sigma$ |

Assignment (given counts) ⟂ counts ⇒ stacking is near-additive (+0.04 confirmed on the
cov_match base). Pipeline: drift-gated $\hat\mu$ + joint-peak $\mu^{\text{peak}}$ + $\sigma$ →
$w^{\text{place}}=(1-\theta)\hat\mu+\theta\mu^{\text{peak}}$, risk $+\lambda\sigma$, rep $+3\sigma$ →
water-fill counts → L∞ multi-scenario LPT ($D\le128$) / min–max matching ($D=256$) →
PAR-neutral align → no-trade band gate. **= 114.76.**

---

## 10. The graveyard (and the precise reason each died)

| Idea | Result | Mechanism of failure |
|---|---|---|
| smaller $\tau$ / redeploy more often | 106–109 | transmit $\uparrow$ (1.9M); PAR flat — load is persistent, not staleness-limited |
| shorter window | 105.3 | recency loses to persistence; short window = noise (PAR 1.74) |
| always-on EWMA base | ~neutral | lag-1 autocorr 0.5 ⇒ nothing to gain on stationary data |
| per-expert tail replication p95/p99 | local win, **server loss** | realized peak is a *joint* event; marginal tails miss the covariance |
| robust *location* (median / trimmed mean) | ~neutral | regimes genuinely unpredictable from the window; location ≈ mean here |
| L2 / dot-product packing objective | 113.1 / 112.7 | makespan is $L^\infty$; other norms don't minimize the max device |
| spectral "snake" placement | +1–2% only | at $S=2$ the peak is one expert's *own* variance, not pairwise anti-corr ordering |
| scalar co-spiking weight | 108.9 | a single scalar premium distorts the mean balance |
| over-replication $\mu+4\sigma$ | 110.6 | steals copy budget from the experts that actually peak |
| pure joint-peak replication ($\theta_{\text{rep}}=1$) | 113.4 | over-concentrates copies on spikers, starves the rest |
| OT / Sinkhorn assignment | = greedy | homogeneous devices ⇒ identical device potential ⇒ collapses to LPT |
| peak-iteration placement | **floored EP256** | $O(\text{slots})$ Python loop over 512 slots too slow on server CPU |
| any wall-clock-bounded refine/swap | **floored** | per-case budget is tiny at high $D$; bound by fixed step count instead |

---

## 11. Transferable conclusions

1. **The objective was mis-specified by the baseline.** $\mathbb{E}_t[\max_d L_d]\ne\max_d\mathbb{E}_t[L_d]$;
   the entire gain is surrogates for the expectation-of-max (multi-scenario L∞, joint-peak
   conditioning, anti-correlated matching, robust replication).
2. **Decompose the ceiling first.** The oracle redirected effort from packing (solved) to
   forecasting (the real residual, concentrated in one non-stationary case).
3. **The constraint is the design.** "Saturated" meant "too slow where it helps"; the
   wins were EP-adaptive dispatch and the $\rho:3\to6$ unlock, not new objectives.
4. **Stack orthogonal levers.** Placement ⟂ replication ⟂ redeploy-policy; the SOTA is a
   composition, and each composes only when it's PAR-neutral or variable-disjoint.
5. **Pick the right validation oracle.** The convenient proxy was anti-correlated with
   reality; the live grader was the only honest signal.
