import sys, time, numpy as np
from pathlib import Path
sys.path.insert(0, str(Path('.').resolve()))
from quickstart import MODEL_SHAPES, load_trace
from eplb_algorithms import rebalance as ds_rebalance
for model in ['Qwen3','DS-R1']:
    hot = load_trace(Path('.'), model, 'LmSys', 1100)
    win = hot[-1024:]
    for ep in [32,64,128]:
        fn = ds_rebalance(ep, ep, "deepseek")
        t=time.time(); fn(win); a=time.time()-t
        t=time.time(); fn(win); b=time.time()-t
        print(f"DS-EPLB baseline {model} EP{ep}: {a:.3f}/{b:.3f}s  -> 3x budget ~= {3*max(a,b):.2f}s/call")
