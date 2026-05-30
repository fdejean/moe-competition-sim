import numpy as np
import dynamic_lb_simulator as sim
from eplb_algorithms import deepseek
import submissions.min_diff.submission as my_sub
import os
import sys

from dynamic_lb_simulator import (
    transmission_time_seconds, modeled_runtime_seconds,
)

# Use the exact loop from benchmark_submissions
from experiments.benchmark_submissions import run, MODELS, INTERVAL, init_deploy, layer_counts, par_per_iter

for model, (n_layers, n_experts) in MODELS.items():
    ep = 32
    n_red = ep
    trace_path = f"trace/{model}/LmSys.npy"
    if not os.path.exists(trace_path): continue
    hotness = np.load(trace_path)
    
    # We just run DS-EPLB
    from eplb_algorithms import rebalance as ds_rebalance
    ds_fn = ds_rebalance(ep, ep, "deepseek")
    base_par, base_tx = run(hotness, n_layers, n_experts, ep, n_red, ds_fn, False)
    
    my_sub.reset()
    m_par, m_trans = run(hotness, n_layers, n_experts, ep, n_red, lambda w: my_sub.rebalance(w, ep, n_red), False)
    
    base_time = modeled_runtime_seconds(base_par, base_tx)
    total = modeled_runtime_seconds(m_par, m_trans)
    
    print(f"[{model}] PAR: {m_par:.4f} TX: {m_trans} Score: {100*base_time/total:.2f}")

