import sys, time, importlib.util, numpy as np
from pathlib import Path
sys.path.insert(0, str(Path('.').resolve()))
from quickstart import MODEL_SHAPES, load_trace, run_ds_eplb

def load_fn(p):
    spec = importlib.util.spec_from_file_location("m_"+Path(p).parent.name, p)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    if hasattr(m,'reset'): m.reset()
    return m.rebalance

variants = {
  'finance_band': 'codes/finance_band/submission.py',
  'rep_aware_refine': 'codes/rep_aware_refine/submission.py',
  'cvar_replication': 'codes/cvar_replication/submission.py',
}
for model in ['Qwen3','DS-R1']:
    nL,nE = MODEL_SHAPES[model]
    hot = load_trace(Path('.'), model, 'LmSys', 1100)
    win = hot[-1024:].astype(np.float64)
    for ep in [32,64,128]:
        # baseline DS-EPLB total time on a short trace, per-call estimate
        line=f"{model} EP{ep}: "
        for name,p in variants.items():
            fn = load_fn(p)
            t=time.time(); fn(win, ep, ep); dt=time.time()-t  # first call (deploy)
            t=time.time(); fn(win, ep, ep); dt2=time.time()-t  # second call (band)
            line += f"{name}={dt:.2f}/{dt2:.2f}s  "
        print(line)
