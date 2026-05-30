import numpy as np
import os
import sys

from eplb_algorithms import deepseek
import submissions.finance_band.submission as my_sub

import dynamic_lb_simulator as sim

for trace_name in ["DS-R1", "Qwen3"]:
    trace_path = f"trace/{trace_name}/LmSys.npy"
    if not os.path.exists(trace_path): continue
    
    trace = np.load(trace_path)
    n_device = 32 if trace_name == "DS-R1" else 64
    n_red_expert = 0
    sim_obj = sim.MoESimulator(n_device=n_device, n_red_expert=n_red_expert)

    # Base DeepSeek
    eplb = deepseek.DeepSeekEPLB()
    _, d_par, d_trans = sim_obj.forward(eplb, trace)

    my_sub.reset()
    _, m_par, m_trans = sim_obj.forward(my_sub, trace)

    ds_score = d_par + 0.001 * d_trans
    my_score = m_par + 0.001 * m_trans
    final = 100.0 * (ds_score / my_score)

    print(f"{trace_name} (FB) | PAR: {m_par:.4f} Transmit: {m_trans} Score: {final:.2f}")
