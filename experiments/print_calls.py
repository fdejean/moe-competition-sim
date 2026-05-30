import sys, importlib.util, numpy as np
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from experiments.benchmark_submissions import load_trace, run
md_spec = importlib.util.spec_from_file_location("md", REPO / "submissions/finance_band/submission.py")
md = importlib.util.module_from_spec(md_spec)
md_spec.loader.exec_module(md)
hotness = load_trace("DS-R1")
n_layers, n_experts, n_device, n_red = 58, 256, 32, 32

total_calls = 0
changes = 0

def wrapper(w):
    global total_calls, changes
    change, lp, deploy, _ = md.rebalance(w, n_device, n_red)
    total_calls += 1
    if change: changes += 1
    return change, lp, deploy, _

run(hotness, n_layers, n_experts, n_device, n_red, wrapper, False)
print("Total calls:", total_calls)
print("Changes:", changes)
