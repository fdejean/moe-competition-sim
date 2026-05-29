"""Hypothesis: recent iterations are more predictive than uniform sum. EMA over
the hotness window (alpha=0.6 on the last frame, decaying back) should track
sudden hot-expert shifts faster.
"""

import numpy as np


def _ema_load(hotness, alpha=0.6):
    n_iters = hotness.shape[0]
    weights = np.array([(1 - alpha) ** (n_iters - 1 - t) for t in range(n_iters)])
    weights *= alpha if n_iters > 1 else 1.0
    weights[0] = (1 - alpha) ** (n_iters - 1)  # tail mass goes to oldest
    weights /= weights.sum()
    return (hotness * weights[:, None, None]).sum(axis=0)


def rebalance(hotness, n_device, n_red_expert):
    load = _ema_load(hotness)
    n_layers, n_experts = load.shape
    n_exp_per_dev = (n_experts + n_red_expert) // n_device
    base_slots = n_exp_per_dev - 1

    deployment = np.zeros((n_layers, n_device, n_exp_per_dev), dtype=np.int64)

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
