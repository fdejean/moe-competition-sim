import numpy as np
import torch

import deepseek


def rebalance(hotness, n_device, n_red_expert):
    """DeepSeek EPLB submission: wraps the official DeepSeek balancer."""
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
    n_layers = deployment.shape[0]
    layers_priority = np.arange(n_layers, dtype=np.int64)

    return True, layers_priority, deployment, None
