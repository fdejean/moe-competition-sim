"""Hypothesis: DeepSeek's hierarchical mode is faster than the global mode at
the cost of small quality loss. May survive EP64 where global timed out.
"""

import numpy as np
import torch

import deepseek

_state = {"deployment": None, "priority": None}


def _compute(hotness, n_device, n_red_expert):
    n_experts = hotness.shape[-1]
    num_replicas = n_experts + n_red_expert
    num_local = num_replicas // n_device

    weight = torch.from_numpy(hotness).sum(dim=0)
    try:
        ptl, _, _ = deepseek.rebalance_experts(
            weight=weight,
            num_replicas=num_replicas,
            num_groups=1,
            num_nodes=2,
            num_gpus=n_device,
            enable_hierarchical=True,
        )
    except Exception:
        ptl, _, _ = deepseek.rebalance_experts(
            weight=weight,
            num_replicas=num_replicas,
            num_groups=1,
            num_nodes=1,
            num_gpus=n_device,
            enable_hierarchical=False,
        )
    deployment = ptl.numpy().reshape(-1, n_device, num_local)
    priority = np.arange(deployment.shape[0], dtype=np.int64)
    return deployment, priority


def rebalance(hotness, n_device, n_red_expert):
    if _state["deployment"] is None:
        deployment, priority = _compute(hotness, n_device, n_red_expert)
        _state["deployment"] = deployment
        _state["priority"] = priority
        return True, priority, deployment, None

    deployment, priority = _compute(hotness, n_device, n_red_expert)
    _state["deployment"] = deployment
    _state["priority"] = priority
    return True, priority, deployment, None
