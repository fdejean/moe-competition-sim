"""Hypothesis: fixing the most-imbalanced layers FIRST cuts cumulative PAR.

Same placement as hot_expert_baseline, but the layers_priority list is sorted by
per-layer imbalance (std of load) descending — worst layers get redeployed first.
"""

import numpy as np


def rebalance(hotness, n_device, n_red_expert):
    load = hotness.sum(axis=0)  # (n_layers, n_experts)
    n_layers, n_experts = load.shape
    n_exp_per_dev = (n_experts + n_red_expert) // n_device

    deployment = np.zeros((n_layers, n_device, n_exp_per_dev), dtype=np.int64)
    base_slots = n_exp_per_dev - 1

    for layer in range(n_layers):
        for device in range(n_device):
            for slot in range(base_slots):
                deployment[layer, device, slot] = (
                    device * base_slots + slot
                ) % n_experts
        hottest = np.argsort(load[layer])[::-1]
        for device in range(n_device):
            deployment[layer, device, -1] = hottest[device % len(hottest)]

    imbalance = load.std(axis=1)
    layers_priority = np.argsort(imbalance)[::-1].astype(np.int64)
    return True, layers_priority, deployment, None
