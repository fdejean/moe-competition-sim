# Dynamic expert placement for MoE inference

An expert-placement algorithm for distributed Mixture-of-Experts models: decide which GPU hosts each expert, which hot experts to replicate, and when a better placement is worth the migration cost.

The recorded **`cov_iter_dro` competition score is 114.76 versus 100 for DeepSeek's DS-EPLB baseline**: a **14.76% higher normalized score** across the 25-case evaluation. This is a trace-driven simulator result, not a measured end-to-end GPU serving speedup.

[Algorithm](codes/cov_iter_dro/submission.py) · [Method and objective](docs/methods.md) · [Experimental progression](docs/journey.md) · [Competition overview](docs/overview.md)

## The idea

Balancing average expert load misses experts that spike together. A deployment is held for a window, so the expensive quantity is the realized load on the slowest device—not simply the largest average device load.

The implementation combines robust replica allocation, co-spike-aware placement, and migration control. At high expert-parallelism, vectorized matching keeps the algorithm within the grader's per-call compute budget.

| Decision | Mechanism |
|---|---|
| Which experts to replicate | Water-filling on uncertainty-adjusted load targets |
| Which experts share a device | Multi-scenario peak-aware placement; min–max matching at EP256 |
| When to migrate | A no-trade band that requires enough load improvement |
| How to reduce migration cost | Align new placements with the existing layout without changing their load balance |

The [method note](docs/methods.md) and [journey](docs/journey.md) record the 114.76 result and the competing approaches. Failed and neutral experiments remain in the submission history; the small public sample is a smoke test, not a reproduction of the private 25-case score.

## Run the public sample

```bash
python -m pip install -r requirements.txt
git lfs pull
python quickstart.py
```

This evaluates a bounded **Qwen3 / LmSys / EP32** trace and reports peak-to-average load, migration cost, modeled runtime, and normalized score for the static placement and DS-EPLB baselines.

```bash
python quickstart.py --all-samples
```

The second command also runs the included DeepSeek-R1 sample. Full-grid experiments use `dynamic_lb_simulator.py` and require the additional trace set, which is not committed.

## Code and evidence

- [codes/cov_iter_dro/submission.py](codes/cov_iter_dro/submission.py): the result-bearing placement stack.
- [dynamic_lb_simulator.py](dynamic_lb_simulator.py): deployment, load, and transmission simulation.
- [eplb_algorithms/](eplb_algorithms/): reference load balancer.
- [experiments/](experiments/): oracle-gap analysis, forecasting comparisons, and placement ablations.
- [SUBMISSIONS.md](SUBMISSIONS.md): submission identifiers and recorded outcomes.

The score is the mean of `100 × baseline modeled time / candidate modeled time` over cases. A higher score should not be described as the same percentage reduction in runtime.

Built with teammates; see the [contributor history](https://github.com/fdejean/moe-competition-sim/graphs/contributors).
