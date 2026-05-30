import sys, importlib.util
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from dynamic_lb_simulator import modeled_runtime_seconds
from experiments.benchmark_submissions import load_trace, MODELS, run

fb_spec = importlib.util.spec_from_file_location("sb", REPO / "submissions/finance_band/submission.py")
fb = importlib.util.module_from_spec(fb_spec)
fb_spec.loader.exec_module(fb)

ep = 32; n_red = 32
hotness_ds = load_trace("DS-R1")
mean_par, transmit = run(hotness_ds, 58, 256, ep, n_red, lambda w: fb.rebalance(w, ep, n_red), False)
total_ds = modeled_runtime_seconds(mean_par, transmit)
print(f"DS-R1 | PAR: {mean_par:.4f} Transmit: {transmit} Score: {100 * 84.155 / total_ds:.2f}")

hotness_qw = load_trace("Qwen3")
fb.reset()
mean_par, transmit = run(hotness_qw, 94, 128, ep, n_red, lambda w: fb.rebalance(w, ep, n_red), False)
total_qw = modeled_runtime_seconds(mean_par, transmit)
print(f"Qwen3 | PAR: {mean_par:.4f} Transmit: {transmit} Score: {100 * 83.144 / total_qw:.2f}")
