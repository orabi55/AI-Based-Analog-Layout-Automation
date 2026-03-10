import math

from .routing_previewer import _classify_net, _POWER_NETS

def compute_row_cost(order, terminal_nets, edges):
    """Evaluates the placement cost for a specific order of devices in a row.
    
    Assumes uniform pitch: the x-coordinate of order[i] is just i.
    """
    pos_x = {dev_id: float(i) for i, dev_id in enumerate(order)}
    
    # Collect nets present in this row
    net_devices = {}
    
    for edge in (edges or []):
        net = edge.get("net", "")
        src = edge.get("source", edge.get("src", ""))
        tgt = edge.get("target", edge.get("tgt", ""))
        if net and net.upper() not in _POWER_NETS:
            if src in pos_x or tgt in pos_x:
                net_devices.setdefault(net, set())
                if src in pos_x:
                    net_devices[net].add(src)
                if tgt in pos_x:
                    net_devices[net].add(tgt)
                    
    for dev_id, nets in (terminal_nets or {}).items():
        if dev_id not in pos_x:
            continue
        for _, net_name in nets.items():
            if net_name and net_name.upper() not in _POWER_NETS:
                net_devices.setdefault(net_name, set()).add(dev_id)
                
    total_cost = 0.0
    
    for net_name, devs in net_devices.items():
        devs_in_row = {d for d in devs if d in pos_x}
        if len(devs_in_row) < 2:
            continue # Needs at least 2 devices to have a span
            
        xs = [pos_x[d] for d in devs_in_row]
        span = max(xs) - min(xs)
        
        criticality = _classify_net(net_name)
        if criticality == "critical":
            total_cost += (span ** 2) * 10
        elif criticality == "signal":
            total_cost += (span ** 2) * 3
        else:
            total_cost += span * 1
            
    return total_cost

def optimize_row_order(row_devices, terminal_nets, edges=None):
    """
    Optimizes the order of a single row of devices using a greedy construction approach.
    
    Args:
        row_devices (list): List of device IDs in the row.
        terminal_nets (dict): Mapping dev_id -> {'D': net_name, 'G': net_name, 'S': net_name}
        edges (list): List of net connections if any.
    
    Returns:
        list of str: The optimized order of device IDs.
    """
    if not row_devices or len(row_devices) <= 1:
        return list(row_devices)
    
    best_order = None
    best_cost = math.inf
    
    # Try using each device as the initial "seed" for the greedy construction
    for seed in row_devices:
        order = [seed]
        remaining = set(row_devices)
        remaining.remove(seed)
        
        while remaining:
            best_candidate = None
            candidate_cost = math.inf
            
            for candidate in remaining:
                test_order = order + [candidate]
                cost = compute_row_cost(test_order, terminal_nets, edges)
                
                if cost < candidate_cost:
                    candidate_cost = cost
                    best_candidate = candidate
                    
            order.append(best_candidate)
            remaining.remove(best_candidate)
            
        final_cost = compute_row_cost(order, terminal_nets, edges)
        
        if final_cost < best_cost:
            best_cost = final_cost
            best_order = order
            
    return best_order
