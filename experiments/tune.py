import os, sys, importlib.util, time, itertools
import numpy as np
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from dynamic_lb_simulator import modeled_runtime_seconds
from experiments.benchmark_submissions import load_trace, MODELS, INTERVAL, run

def load_submission(name):
    spec = importlib.util.spec_from_file_location("sub_" + name, REPO / "submissions" / name / "submission.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def eval_params(tau, lam, window):
    os.environ["FB_TAU"] = str(tau)
    os.environ["FB_LAMBDA"] = str(lam)
    if window: os.environ["FB_WINDOW"] = str(window)
    elif "FB_WINDOW" in os.environ: del os.environ["FB_WINDOW"]
    
    fb = load_submission("finance_band")
    
    scores = []
    for model, (n_layers, n_experts) in MODELS.items():
        ep = 32
        n_red = ep
        hotness = load_trace(model)
        fb.reset()
        mean_par, transmit = run(hotness, n_layers, n_experts, ep, n_red, lambda w: fb.rebalance(w, ep, n_red), False)
        total = modeled_runtime_seconds(mean_par, transmit)
        scores.append((mean_par, transmit, total))
    return scores

taus = [0.2, 0.3, 0.4, 0.5, 0.1]
lams = [0.0, 1.0, 2.0, 3.0]
windows = [None, 32, 64, 128]

for t, l, w in itertools.product(taus, lams, windows):
    sc = eval_params(t, l, w)
    # DS-EPLB total_s: DS-R1 -> 84.155, Qwen3 -> 83.144
    score_ds = 100 * 84.155 / sc[0][2]
    score_qw = 100 * 83.144 / sc[1][2]
    print(f"TAU={t} LAM={l} WIN={w} | DS-R1: {score_ds:.2f} Qwen3: {score_qw:.2f} | Avg: {(score_ds+score_qw)/2:.2f}")

