import sys, time, importlib.util, numpy as np
from pathlib import Path
sys.path.insert(0, str(Path('.').resolve()))
from quickstart import MODEL_SHAPES, load_trace
def load_fn(p):
    spec = importlib.util.spec_from_file_location("m_"+Path(p).parent.name, p)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    if hasattr(m,'reset'): m.reset()
    return m.rebalance
for name,p in [('finance_band','codes/finance_band/submission.py'),
               ('block_pack','codes/block_pack/submission.py')]:
    line=f"{name}: "
    for model in ['Qwen3','DS-R1']:
        hot = load_trace(Path('.'), model, 'LmSys', 1100); win = hot[-1024:].astype(np.float64)
        for ep in [32,128]:
            fn = load_fn(p)
            t=time.time(); fn(win, ep, ep); dt=time.time()-t
            line += f"{model}EP{ep}={dt:.2f}s "
    print(line)
