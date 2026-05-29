import numpy as np
import torch

import deepseek

_RECOMPUTE_EVERY = 4
_state = {"calls": 0, "deployment": None, "priority": None}


def _compute(hotness, n_device, n_red_expert):
    n_experts = hotness.shape[-1]
    num_replicas = n_experts + n_red_expert
    num_local = num_replicas // n_device

    weight = torch.from_numpy(hotness).sum(dim=0)
    physical_to_logical_map, _, _ = deepseek.rebalance_experts(
        weight=weight,
        num_replicas=num_replicas,
        num_groups=1,
        num_nodes=1,
        num_gpus=n_device,
        enable_hierarchical=False,
    )
    deployment = physical_to_logical_map.numpy().reshape(-1, n_device, num_local)
    priority = np.arange(deployment.shape[0], dtype=np.int64)
    return deployment, priority


def rebalance(hotness, n_device, n_red_expert):
    """Throttled DeepSeek EPLB: only recompute every Nth call."""
    _state["calls"] += 1

    if _state["deployment"] is None:
        deployment, priority = _compute(hotness, n_device, n_red_expert)
        _state["deployment"] = deployment
        _state["priority"] = priority
        return True, priority, deployment, None

    if _state["calls"] % _RECOMPUTE_EVERY == 1:
        deployment, priority = _compute(hotness, n_device, n_red_expert)
        _state["deployment"] = deployment
        _state["priority"] = priority
        return True, priority, deployment, None

    return False, _state["priority"], _state["deployment"], None
