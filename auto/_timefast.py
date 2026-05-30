import sys, time, importlib.util, numpy as np
from pathlib import Path
sys.path.insert(0, str(Path('.').resolve()))
from quickstart import load_trace
def lf(p):
    spec=importlib.util.spec_from_file_location("m_"+Path(p).parent.name,p); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    if hasattr(m,'reset'): m.reset()
    return m.rebalance
hot=load_trace(Path('.'),'DS-R1','LmSys',1100); win=hot[-1024:].astype(np.float64)
for s in ['finance_band','block_pack','block_pack_fast']:
    fn=lf(f'codes/{s}/submission.py')
    line=f"{s}: "
    for ep in [128,256]:
        fn=lf(f'codes/{s}/submission.py')
        t=time.time(); fn(win,ep,ep); line+=f"DS-R1_EP{ep}={time.time()-t:.2f}s "
    print(line)
