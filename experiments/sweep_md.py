import os
import subprocess

for tau in [0.1, 0.15, 0.2, 0.25]:
    for lam in [1.0, 1.5, 2.0, 2.5]:
        env = os.environ.copy()
        env["MD_TAU"] = str(tau)
        env["MD_LAMBDA"] = str(lam)
        print(f"TAU={tau} LAMBDA={lam}")
        subprocess.run(["python", "experiments/fast_test_md.py"], env=env)
