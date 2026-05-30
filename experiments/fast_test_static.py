import numpy as np
import dynamic_lb_simulator as sim
from eplb_algorithms import deepseek
import os

for trace_name in ["DS-R1", "Qwen3"]:
    trace_path = f"trace/{trace_name}/LmSys.npy"
    if not os.path.exists(trace_path): continue
    
    trace = np.load(trace_path)
    n_device = 32 if trace_name == "DS-R1" else 64
    n_red_expert = 0
    sim_obj = sim.MoESimulator(n_device=n_device, n_red_expert=n_red_expert)

    # Base
    eplb = deepseek.DeepSeekEPLB()
    _, d_par, d_trans = sim_obj.forward(eplb, trace)
    ds_score = d_par + 0.001 * d_trans

    class Static:
        def __init__(self):
            import submissions.finance_band.submission as fb
            self.fb = fb
            self.done = False
            self.deploy = None
            
        def reset(self):
            pass

        def rebalance(self, hotness, n_device, n_red_expert):
            if not self.done:
                # Use the ENTIRE trace mean to calculate the offline PERFECT static deploy
                global trace
                mean = trace.mean(axis=0)
                n_layers = mean.shape[0]
                n_exp_per_dev = (mean.shape[1] + n_red_expert) // n_device
                candidate = np.zeros((n_layers, n_device, n_exp_per_dev), dtype=np.int64)
                for l in range(n_layers):
                    candidate[l] = self.fb._build_layer(mean[l], n_device, n_exp_per_dev)
                
                self.done = True
                self.deploy = candidate
                return True, np.arange(n_layers, dtype=np.int64), candidate, None
            else:
                return False, np.array([], dtype=np.int64), self.deploy, None
                
    st = Static()
    _, m_par, m_trans = sim_obj.forward(st, trace)
    
    my_score = m_par + 0.001 * m_trans
    final = 100.0 * (ds_score / my_score)

    print(f"{trace_name} (Global Offline Static) | PAR: {m_par:.4f} Transmit: {m_trans} Score: {final:.2f}")
