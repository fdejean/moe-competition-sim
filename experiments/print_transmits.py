import sys, importlib.util, numpy as np
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from experiments.benchmark_submissions import load_trace, run
md_spec = importlib.util.spec_from_file_location("md", REPO / "submissions/min_diff/submission.py")
md = importlib.util.module_from_spec(md_spec)
md_spec.loader.exec_module(md)
hotness = load_trace("DS-R1")
n_layers, n_experts = 58, 256
n_device, n_red = 32, 32
n_exp_per_dev = (n_experts + n_red) // n_device

last_prio = 0
total_calls = 0

def wrapper(w):
    global total_calls, last_prio
    change, lp, deploy, _ = md.rebalance(w, n_device, n_red)
    total_calls += 1
    if change:
        print(f"Call {total_calls} returned change=True with {len(lp)} layers")
        last_prio += len(lp)
    return change, lp, deploy, _

mean_par, transmit = run(hotness, n_layers, n_experts, n_device, n_red, wrapper, False)
print("Transmit:", transmit)
print("Sum of len(lp):", last_prio)
