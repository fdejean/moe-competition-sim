import sys, time, importlib.util, numpy as np
from pathlib import Path
sys.path.insert(0, str(Path('.').resolve()))
from quickstart import load_trace
def lf(p):
    spec=importlib.util.spec_from_file_location("m_"+Path(p).parent.name,p); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    if hasattr(m,'reset'): m.reset()
    return m.rebalance
for s in ['block_pack','block_pack_k16','block_pack_k32']:
    line=f"{s}: "
    for model in ['Qwen3','DS-R1']:
        hot=load_trace(Path('.'),model,'LmSys',1100); win=hot[-1024:].astype(np.float64)
        fn=lf(f'codes/{s}/submission.py'); t=time.time(); fn(win,128,128); line+=f"{model}EP128={time.time()-t:.2f}s "
    print(line)
