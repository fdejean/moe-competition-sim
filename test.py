print("Start")
import sys, importlib.util, numpy as np
from pathlib import Path
REPO = Path(__file__).resolve().parents[0]
sys.path.insert(0, str(REPO))
from experiments.benchmark_submissions import load_trace, run
print("Loaded")
