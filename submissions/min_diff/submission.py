import os
import numpy as np

TAU = float(os.environ.get("MD_TAU", "0.2"))
LAMBDA = float(os.environ.get("MD_LAMBDA", "2.0"))

_STATE = {}

def reset():
    _STATE.clear()
    
def get_initial_deploy(n_device, n_experts, n_red_expert):
    n_exp_per_dev = (n_experts + n_red_expert) // n_device
    d = np.zeros((n_device, n_exp_per_dev), dtype=np.int64)
    for dev in range(n_device):
        for s in range(n_exp_per_dev - 1):
            d[dev, s] = (dev * (n_exp_per_dev - 1) + s) % n_experts
        d[dev, -1] = d[dev, -2]
    for dev in range(n_device):
        d[dev] = np.sort(d[dev])
    return d

def _par(load, deploy):
    cut = np.bincount(deploy.reshape(-1), minlength=load.shape[0])
    weights = load / cut
    loads = weights[deploy.reshape(-1)].reshape(deploy.shape).sum(-1)
    mean_load = loads.mean()
    if mean_load == 0: return 1.0
    return float(loads.max() / mean_load)

def get_target_replicas(load, n_slots):
    n_experts = load.shape[0]
    target_replicas = np.ones(n_experts, dtype=np.int64)
    for _ in range(n_slots - n_experts):
        target_replicas[int(np.argmax(load / target_replicas))] += 1
    return target_replicas

def local_search(load, deploy, target_replicas, iters=50):
    n_device, n_exp_per_dev = deploy.shape
    best_deploy = deploy.copy()
    
    for _ in range(iters):
        # Calculate current loads
        cut = np.bincount(best_deploy.reshape(-1), minlength=load.shape[0])
        weights = load / np.maximum(cut, 1)
        loads = weights[best_deploy.reshape(-1)].reshape(best_deploy.shape).sum(-1)
        
        d_max = int(np.argmax(loads))
        d_min = int(np.argmin(loads))
        
        # Try all swaps between d_max and d_min
        best_swap = None
        best_val = loads[d_max]
        
        for i in range(n_exp_per_dev):
            for j in range(n_exp_per_dev):
                e_max = best_deploy[d_max, i]
                e_min = best_deploy[d_min, j]
                if e_max == e_min:
                    continue
                # If d_min already has e_max, swapping introduces a duplicate. 
                # Duplicates on same device are generally bad for par, but handled.
                # Actually, simulator handles them fine. But let's avoid if possible unless necessary.
                
                # Check new max load if we did this swap
                # We only need to recompute loads for d_max and d_min
                load_d_max_new = loads[d_max] - weights[e_max] + weights[e_min]
                load_d_min_new = loads[d_min] - weights[e_min] + weights[e_max]
                
                max_of_two = max(load_d_max_new, load_d_min_new)
                if max_of_two < best_val - 1e-9: # strict improvement
                    best_val = max_of_two
                    best_swap = (i, j)
                    
        if best_swap:
            i, j = best_swap
            # execute swap
            best_deploy[d_max, i], best_deploy[d_min, j] = best_deploy[d_min, j], best_deploy[d_max, i]
        else:
            break
            
    # sort internally
    for d in range(n_device):
        best_deploy[d] = np.sort(best_deploy[d])
    return best_deploy

def _build_layer_from_cur(load, cur_deploy):
    n_device, n_exp_per_dev = cur_deploy.shape
    n_slots = n_device * n_exp_per_dev
    n_experts = load.shape[0]
    
    target_replicas = get_target_replicas(load, n_slots)
    cur_replicas = np.bincount(cur_deploy.reshape(-1), minlength=n_experts)
    
    # 1. Match replicas
    diff = target_replicas - cur_replicas
    to_add = []
    for e in range(n_experts):
        if diff[e] > 0:
            to_add.extend([e] * diff[e])
            
    deploy = cur_deploy.copy()
    if len(to_add) > 0:
        # We need to remove items where diff < 0
        removals_needed = []
        for e in range(n_experts):
            if diff[e] < 0:
                removals_needed.extend([e] * (-diff[e]))
                
        # Just replace removals_needed with to_add
        for add_e, rem_e in zip(to_add, removals_needed):
            # Find the device that has rem_e. 
            # Prefer device that has the largest load, to maybe help offload?
            # Or just any device.
            found = False
            for d in range(n_device):
                for s in range(n_exp_per_dev):
                    if deploy[d, s] == rem_e:
                        deploy[d, s] = add_e
                        found = True
                        break
                if found:
                    break
    
    # 2. Local search to balance
    deploy = local_search(load, deploy, target_replicas, iters=100)
    return deploy

# Re-use finance_band _build_layer for initial cold start
def _greedy_build_layer(load, n_device, n_exp_per_dev):
    n_experts = load.shape[0]
    n_slots = n_device * n_exp_per_dev
    target_replicas = get_target_replicas(load, n_slots)

    deploy = np.full((n_device, n_exp_per_dev), -1, dtype=np.int64)
    dev_load = np.zeros(n_device, dtype=np.float64)
    dev_count = np.zeros(n_device, dtype=np.int64)
    
    to_place = []
    for e in range(n_experts):
        to_place.extend([e] * target_replicas[e])
    to_place = np.array(to_place)
    weights = load[to_place] / target_replicas[to_place]
    to_place = to_place[np.argsort(weights)[::-1]]

    big = load.sum() + 1.0
    for e in to_place:
        score = dev_load + np.where((deploy == e).any(axis=1), big, 0.0)
        score[dev_count >= n_exp_per_dev] = np.inf
        d = int(np.argmin(score))
        deploy[d, int(dev_count[d])] = e
        dev_load[d] += load[e] / target_replicas[e]
        dev_count[d] += 1
        
    for d in range(n_device):
        deploy[d] = np.sort(deploy[d])
    return deploy

def rebalance(hotness, n_device, n_red_expert):
    hotness = hotness.astype(np.float64)
    mean = hotness.mean(axis=0)
    n_layers, n_experts = mean.shape
    n_exp_per_dev = (n_experts + n_red_expert) // n_device
    risk = mean + LAMBDA * hotness.std(axis=0) if LAMBDA else mean

    sig = (n_layers, n_experts, n_device, n_exp_per_dev)
    cur = _STATE.get("deploy")
    if cur is None or _STATE.get("sig") != sig:
        candidate = np.zeros((n_layers, n_device, n_exp_per_dev), dtype=np.int64)
        for l in range(n_layers):
            candidate[l] = _greedy_build_layer(risk[l], n_device, n_exp_per_dev)
        _STATE["sig"] = sig
        _STATE["deploy"] = candidate.copy()
        return True, np.arange(n_layers, dtype=np.int64), candidate, None

    candidate = np.zeros((n_layers, n_device, n_exp_per_dev), dtype=np.int64)
    for l in range(n_layers):
        candidate[l] = _build_layer_from_cur(risk[l], cur[l])

    improve = np.array([_par(mean[l], cur[l]) - _par(mean[l], candidate[l])
                        for l in range(n_layers)])
    sel = np.where(improve > TAU)[0]
    if sel.size == 0:
        return False, np.array([], dtype=np.int64), candidate, None

    order = sel[np.argsort(improve[sel])[::-1]]
    new_state = cur.copy()
    new_state[order] = candidate[order]
    _STATE["deploy"] = new_state
    
    return True, order.astype(np.int64), candidate, None
