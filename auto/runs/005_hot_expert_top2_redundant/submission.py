"""Hypothesis: when n_red_expert allows >1 redundant slot per device, spreading
the top-K hottest experts across redundant slots gives a flatter load than
cycling only the single hottest.
"""

import numpy as np


def rebalance(hotness, n_device, n_red_expert):
    load = hotness.sum(axis=0)
    n_layers, n_experts = load.shape
    n_exp_per_dev = (n_experts + n_red_expert) // n_device
    redundant_per_dev = n_exp_per_dev - (n_experts // n_device)
    base_slots = n_exp_per_dev - redundant_per_dev

    deployment = np.zeros((n_layers, n_device, n_exp_per_dev), dtype=np.int64)

    for layer in range(n_layers):
        for device in range(n_device):
            for slot in range(base_slots):
                deployment[layer, device, slot] = (
                    device * base_slots + slot
                ) % n_experts

        hottest = np.argsort(load[layer])[::-1]
        top_k = max(redundant_per_dev * n_device, 1)
        pool = hottest[:top_k] if top_k <= len(hottest) else np.tile(
            hottest, (top_k // len(hottest)) + 1
        )[:top_k]
        for idx, device in enumerate(range(n_device)):
            for r in range(redundant_per_dev):
                deployment[layer, device, base_slots + r] = pool[
                    (idx * redundant_per_dev + r) % len(pool)
                ]

    imbalance = load.std(axis=1)
    layers_priority = np.argsort(imbalance)[::-1].astype(np.int64)
    return True, layers_priority, deployment, None
