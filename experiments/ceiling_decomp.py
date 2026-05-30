"""(c) Realistic ceiling decomposition: how much of realized PAR is RECOVERABLE
by better forecasting vs STRUCTURAL (static-hold over a redeploy window)?

The sim holds one placement for a full collection_interval (1024 iters) and
realizes PAR per-iteration on the TRUE load. So for each 1024-block we compare
three placements, all using the SAME packer (finance_band._build_layer):

  per_iter_oracle   place fresh on EACH iteration's true load   -> absolute floor
  block_foresight   place ONCE on THIS block's TRUE mean        -> realistic ceiling
                    (best static placement if you knew the future)
  trailing          place ONCE on the PREVIOUS block's mean     -> what we do now

  gap_forecast = trailing - block_foresight   (RECOVERABLE by better prediction)
  gap_static   = block_foresight - per_iter   (STRUCTURAL: needs more redeploys)

Then a DRIFT stress-test: rotate the hot set every block (emulating Mix's
non-stationarity). If gap_forecast explodes while block_foresight stays flat,
Mix's extra PAR is a forecasting problem a drift-aware router can attack.

Run:  .venv/bin/python experiments/ceiling_decomp.py
"""
import importlib.util
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[1]
MODELS = {"DS-R1": (58, 256, [32, 64, 128, 256]),
          "Qwen3": (94, 128, [32, 64, 128])}
INTERVAL = 1024
ITER_STRIDE = 8     # subsample iters within a block for PAR (PAR is a mean)
N_LAYERS_SAMP = 12  # subsample layers for speed


def load_fb():
    spec = importlib.util.spec_from_file_location(
        "fb", REPO / "codes" / "finance_band" / "submission.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def block_par(fb, target_load, eval_block, ep, n_exp_per_dev):
    """Place once on target_load; realize PAR over eval_block's iters (true load)."""
    n_layers = target_load.shape[0]
    tot = 0.0
    cnt = 0
    for l in range(n_layers):
        deploy = fb._build_layer(target_load[l], ep, n_exp_per_dev)
        for it in range(0, eval_block.shape[0], ITER_STRIDE):
            tot += fb._par(eval_block[it, l], deploy)
            cnt += 1
    return tot / cnt


def per_iter_par(fb, eval_block, ep, n_exp_per_dev):
    n_layers = eval_block.shape[1]
    tot = 0.0
    cnt = 0
    for it in range(0, eval_block.shape[0], ITER_STRIDE):
        for l in range(n_layers):
            h = eval_block[it, l]
            deploy = fb._build_layer(h, ep, n_exp_per_dev)
            tot += fb._par(h, deploy)
            cnt += 1
    return tot / cnt


def decompose(fb, hot, ep, n_experts, drift=False):
    n_iter, n_layers, _ = hot.shape
    n_exp_per_dev = (n_experts + ep) // ep
    nb = n_iter // INTERVAL
    rng = np.random.default_rng(0)
    perms = [rng.permutation(n_experts) for _ in range(nb)] if drift else None
    blocks = []
    for b in range(nb):
        sl = hot[b * INTERVAL:(b + 1) * INTERVAL]
        blocks.append(sl[:, :, perms[b]] if drift else sl)
    oi, bf, tr = [], [], []
    for b in range(1, nb):                       # block 0 = warmup
        cur, prev = blocks[b], blocks[b - 1]
        true_mean = cur.mean(axis=0)
        prev_mean = prev.mean(axis=0)
        oi.append(per_iter_par(fb, cur, ep, n_exp_per_dev))
        bf.append(block_par(fb, true_mean, cur, ep, n_exp_per_dev))
        tr.append(block_par(fb, prev_mean, cur, ep, n_exp_per_dev))
    return np.mean(oi), np.mean(bf), np.mean(tr)


def main():
    fb = load_fb()
    print(f"{'case':16s} {'per_iter':>8} {'foresight':>9} {'trailing':>8} | "
          f"{'gap_fcst':>8} {'gap_stat':>8}  (RECOVERABLE / structural)")
    print("-" * 86)
    for model, (nl, ne, eps) in MODELS.items():
        full = np.load(REPO / "trace" / model / "LmSys.npy").astype(np.float64)
        full[full == 0] = 1.0
        lay = np.linspace(0, nl - 1, N_LAYERS_SAMP).astype(int)
        hot = full[:, lay, :]
        for ep in eps:
            oi, bf, tr = decompose(fb, hot, ep, ne)
            print(f"{model+' EP'+str(ep):16s} {oi:8.3f} {bf:9.3f} {tr:8.3f} | "
                  f"{tr-bf:+8.3f} {bf-oi:+8.3f}")
    print("\n--- DRIFT STRESS (hot set rotates every block ~ Mix non-stationarity) ---")
    for model, (nl, ne, eps) in MODELS.items():
        full = np.load(REPO / "trace" / model / "LmSys.npy").astype(np.float64)
        full[full == 0] = 1.0
        lay = np.linspace(0, nl - 1, N_LAYERS_SAMP).astype(int)
        hot = full[:, lay, :]
        for ep in eps:
            oi, bf, tr = decompose(fb, hot, ep, ne, drift=True)
            print(f"{model+' EP'+str(ep)+' [drift]':16s} {oi:8.3f} {bf:9.3f} {tr:8.3f} | "
                  f"{tr-bf:+8.3f} {bf-oi:+8.3f}")


if __name__ == "__main__":
    main()
