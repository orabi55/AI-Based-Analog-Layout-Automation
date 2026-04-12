"""
hierarchy.py
============
Device hierarchy modeling for array, multiplier (m), and finger (nf) parameters.

SPICE device lines may encode replication in three ways:
  1. Array suffix:  MM9<7>  → this is the 8th copy (0-based index 7) of parent MM9
  2. Multiplier:    m=8     → 8 multiplier copies (MM3_m1 … MM3_m8)
  3. Finger:        nf=10   → 10 fingers per instance (MM10_f1 … MM10_f10)

Naming convention for expanded devices:
  - Array/multiplier children:  {parent}_m{N}   (N is 1-based)
  - Finger children:            {parent}_f{N}   (N is 1-based)
  - Mixed:                      {parent}_m{M}_f{F}

This module provides:
  - parse_array_suffix()       — extract <N> index from device names
  - HierarchyNode / DeviceHierarchy — hierarchy tree data structures
  - build_hierarchy_for_device() — build hierarchy tree from params
  - build_device_hierarchy()   — reconstruct hierarchies from expanded devices
  - expand_hierarchy_devices()   — generate leaf Device objects from a hierarchy
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

# Import Device lazily to avoid circular dependency
# (netlist_reader.py imports from hierarchy.py)
def _Device():
    from .netlist_reader import Device
    return Device


# ---------------------------------------------------------------------------
# Array suffix parsing
# ---------------------------------------------------------------------------

_ARRAY_RE = re.compile(r'^(.+?)<(\d+)>\s*$')


def parse_array_suffix(device_name: str) -> Tuple[str, Optional[int]]:
    """
    Parse a device name that may have an array index suffix <N>.

    The <N> is a 0-based index indicating which copy of the parent this is.
    For example, in a schematic you might draw MM9<0>, MM9<1>, ..., MM9<7>
    to indicate 8 separate instances of the same transistor.

    Parameters
    ----------
    device_name : str
        e.g. "MM9<7>", "MM10", "XI0_MM3<4>"

    Returns
    -------
    (base_name, array_index) : tuple
        base_name    — the device name without the suffix (e.g. "MM9")
        array_index  — 0-based integer index if a suffix was found, else None

    Examples
    --------
    >>> parse_array_suffix("MM9<7>")
    ('MM9', 7)
    >>> parse_array_suffix("MM10")
    ('MM10', None)
    """
    m = _ARRAY_RE.match(device_name)
    if m:
        return m.group(1), int(m.group(2))
    return device_name, None


def parse_net_array_suffix(net_name: str) -> Tuple[str, Optional[int]]:
    """
    Parse a net name that may have an array index suffix <N>.

    In hierarchical SPICE, nets connecting array instances can also carry
    an index:  net2<3>  means the net for array copy #3.

    Returns
    -------
    (base_net, index) : tuple
        base_net — the net name without the suffix (e.g. "net2")
        index    — integer if a suffix was found, else None
    """
    m = _ARRAY_RE.match(net_name)
    if m:
        return m.group(1), int(m.group(2))
    return net_name, None


# ---------------------------------------------------------------------------
# Parameter extraction helpers
# ---------------------------------------------------------------------------

def _extract_int_param(params: dict, key: str, default: int = 1) -> int:
    """
    Safely extract an integer parameter from a params dict.
    Handles floats, strings, negative values, and non-numeric strings.
    """
    raw = params.get(key, default)
    if isinstance(raw, int):
        val = raw
    elif isinstance(raw, float):
        val = int(round(raw))
    elif isinstance(raw, str):
        try:
            val = int(raw)
        except ValueError:
            try:
                val = int(round(float(raw)))
            except ValueError:
                print(f"[hierarchy] Warning: non-integer {key}='{raw}' "
                      f"for device, using default {default}")
                val = default
    else:
        val = default

    if val < 1:
        print(f"[hierarchy] Warning: {key}={val} < 1, clamping to {default}")
        val = default

    return val


# ---------------------------------------------------------------------------
# Hierarchy node dataclass
# ---------------------------------------------------------------------------

@dataclass
class HierarchyNode:
    """
    Represents one node in the device hierarchy tree.

    Attributes
    ----------
    name : str
        Fully qualified instance name (e.g. "MM6", "MM6_m1", "MM6_m1_f3")
    level : int
        0 = parent device, 1 = multiplier/array child, 2 = finger grandchild
    children : list[HierarchyNode]
        Child nodes at the next level down
    multiplier_index : int | None
        Which multiplier/array copy this is (1-based)
    finger_index : int | None
        Which finger this is (1-based)
    device : Device | None
        The underlying Device object (populated for leaf nodes)
    """
    name: str
    level: int = 0
    children: List['HierarchyNode'] = field(default_factory=list)
    multiplier_index: Optional[int] = None
    finger_index: Optional[int] = None
    device: Optional['Device'] = None  # type: ignore  # noqa: F821

    def is_leaf(self) -> bool:
        return len(self.children) == 0

    def leaf_count(self) -> int:
        """Total number of leaf (physical finger) nodes in this subtree."""
        if self.is_leaf():
            return 1
        return sum(c.leaf_count() for c in self.children)

    def all_leaves(self) -> List['HierarchyNode']:
        """Return all leaf nodes in left-to-right order."""
        if self.is_leaf():
            return [self]
        leaves = []
        for c in self.children:
            leaves.extend(c.all_leaves())
        return leaves


# ---------------------------------------------------------------------------
# Main hierarchy builder
# ---------------------------------------------------------------------------

@dataclass
class DeviceHierarchy:
    """
    Represents the full hierarchy of a single logical device.

    Structure for multiplier + fingers (m=3, nf=5):
        MM6                          ← root (level 0)
        ├── MM6_m1                   ← level 1: multiplier child 1
        │   ├── MM6_m1_f1            ← level 2: fingers 1..5
        │   ├── MM6_m1_f2
        │   └── ...
        ├── MM6_m2
        │   └── ...
        └── MM6_m3
            └── ...

    Structure for fingers only (nf=10):
        MM10                         ← root (level 0)
        ├── MM10_f1                  ← level 1: fingers directly under parent
        ├── MM10_f2
        └── ...

    Structure for single device (m=1, nf=1):
        MM1                          ← root with no children
    """
    root: HierarchyNode
    multiplier: int = 1          # 1 means no multiplier expansion
    fingers: int = 1             # 1 means no finger expansion
    is_array: bool = False       # True if children came from array indexing
    total_leaves: int = 1

    def needs_expansion(self) -> bool:
        """True if this device has more than one physical instance."""
        return self.total_leaves > 1


def build_hierarchy_for_device(
        name: str,
        pins: dict,
        params: dict,
        dtype: str,
) -> DeviceHierarchy:
    """
    Build a DeviceHierarchy for a single logical device.

    Parameters
    ----------
    name : str
        Parent device name (e.g. "MM6", "MM9" — WITHOUT array suffix)
    pins : dict
        Pin-to-net mapping
    params : dict
        Device parameters (m, nf, array_count, etc.)
    dtype : str
        Device type ("nmos", "pmos", etc.)

    Returns
    -------
    DeviceHierarchy
    """
    m = _extract_int_param(params, 'm', default=1)
    nf = _extract_int_param(params, 'nf', default=1)
    array_count = params.get('array_count', 0)
    is_array = params.get('is_array', False)

    # Determine effective m-level count:
    # - If array_count > 1 and m == 1: array copies are the m-level
    # - If array_count > 1 and m > 1: combined (array_count * m)
    # - If m > 1 and no array: multiplier copies are the m-level
    # - If m == 1 and no array: no m-level (fingers directly under parent)
    if array_count > 1 and m <= 1:
        effective_m = array_count
    elif array_count > 1 and m > 1:
        effective_m = array_count * m
    else:
        effective_m = m

    root = HierarchyNode(name=name, level=0)
    total = effective_m * nf

    # --- Single device ---
    if effective_m == 1 and nf == 1:
        return DeviceHierarchy(
            root=root, multiplier=1, fingers=1,
            is_array=False, total_leaves=1,
        )

    # --- Fingers only (no multiplier/array at top level) ---
    if effective_m == 1 and nf > 1:
        for fi in range(1, nf + 1):
            child = HierarchyNode(
                name=f"{name}_f{fi}", level=1, finger_index=fi,
            )
            root.children.append(child)
        return DeviceHierarchy(
            root=root, multiplier=1, fingers=nf,
            is_array=False, total_leaves=nf,
        )

    # --- Multiplier/array only (no fingers) ---
    if effective_m > 1 and nf == 1:
        for mi in range(1, effective_m + 1):
            child = HierarchyNode(
                name=f"{name}_m{mi}", level=1, multiplier_index=mi,
            )
            root.children.append(child)
        return DeviceHierarchy(
            root=root, multiplier=effective_m, fingers=1,
            is_array=is_array, total_leaves=effective_m,
        )

    # --- Multiplier/array + fingers (two-level tree) ---
    for mi in range(1, effective_m + 1):
        level1 = HierarchyNode(
            name=f"{name}_m{mi}", level=1, multiplier_index=mi,
        )
        for fi in range(1, nf + 1):
            level2 = HierarchyNode(
                name=f"{name}_m{mi}_f{fi}", level=2,
                multiplier_index=mi, finger_index=fi,
            )
            level1.children.append(level2)
        root.children.append(level1)
    return DeviceHierarchy(
        root=root, multiplier=effective_m, fingers=nf,
        is_array=is_array, total_leaves=effective_m * nf,
    )


# ---------------------------------------------------------------------------
# Bulk hierarchy builder (reconstructs hierarchy from expanded devices)
# ---------------------------------------------------------------------------

def build_device_hierarchy(devices: List['Device']) -> Dict[str, DeviceHierarchy]:
    """
    Reconstruct DeviceHierarchy objects from a list of expanded Device objects.

    parse_mos() expands devices with m>1 or nf>1 into individual children named
    with _mN and _fN suffixes, with params['parent'] set to the original name.
    Array-indexed devices from separate SPICE lines are also grouped by their
    base_name.

    Returns one DeviceHierarchy per logical device (parent name).
    """
    # Group devices by their logical parent name
    parent_buckets: Dict[str, List['Device']] = {}
    standalone: List['Device'] = []

    for dev in devices:
        parent_name = dev.params.get('parent')
        if parent_name:
            parent_buckets.setdefault(parent_name, []).append(dev)
        else:
            standalone.append(dev)

    hierarchies: Dict[str, DeviceHierarchy] = {}

    # --- Reconstruct hierarchies for expanded devices ---
    for parent_name, children in parent_buckets.items():
        if not children:
            continue

        rep = children[0]
        total_children = len(children)

        # Determine the type of expansion by inspecting children
        has_array = any(c.params.get('array_index') is not None for c in children)
        has_mult = any(c.params.get('multiplier_index') is not None for c in children)
        has_finger = any(c.params.get('finger_index') is not None for c in children)

        orig_m = rep.params.get('m', 1)
        orig_nf = rep.params.get('nf', 1)
        array_count = rep.params.get('array_count', 0)

        # If array-indexed, the effective multiplier is the array count
        if has_array and array_count > 0:
            effective_m = array_count
            is_array = True
        elif has_mult and orig_m > 1:
            effective_m = orig_m
            is_array = False
        else:
            # Children are just a flat list — count unique multiplier indices
            mult_indices = set()
            for c in children:
                mi = c.params.get('multiplier_index')
                if mi is not None:
                    mult_indices.add(mi)
            if len(mult_indices) > 1:
                effective_m = len(mult_indices)
                is_array = has_array
            else:
                effective_m = total_children
                is_array = has_array

        # Recover original nf
        if effective_m > 1:
            recovered_nf = total_children // effective_m
        else:
            recovered_nf = total_children

        h = build_hierarchy_for_device(
            name=parent_name,
            pins=rep.pins,
            params={
                'm': effective_m,
                'nf': recovered_nf,
                'array_count': array_count,
                'is_array': is_array,
            },
            dtype=rep.type,
        )

        # Attach actual expanded Device objects to leaf nodes
        # Sort children by their name for correct ordering
        children_sorted = sorted(children, key=lambda d: _device_sort_key(d.name))
        leaves = h.root.all_leaves()
        for i, leaf in enumerate(leaves):
            if i < len(children_sorted):
                leaf.device = children_sorted[i]

        hierarchies[parent_name] = h

    # --- Build hierarchies for standalone devices ---
    for dev in standalone:
        h = build_hierarchy_for_device(
            name=dev.name,
            pins=dev.pins,
            params=dev.params,
            dtype=dev.type,
        )
        if not h.needs_expansion():
            h.root.device = dev
        hierarchies[dev.name] = h

    return hierarchies


def _device_sort_key(name: str) -> tuple:
    """Sort key for device names — handles _mN, _fN, _mN_fM patterns."""
    m_m = re.search(r'_m(\d+)', name)
    m_f = re.search(r'_f(\d+)', name)
    mi = int(m_m.group(1)) if m_m else 0
    fi = int(m_f.group(1)) if m_f else 0
    return (mi, fi)


# ---------------------------------------------------------------------------
# Child device generation
# ---------------------------------------------------------------------------

def expand_hierarchy_devices(
        hierarchy: DeviceHierarchy,
        dtype: str,
        pins: dict,
        params: dict,
) -> List['Device']:
    """
    Generate all leaf Device objects from a DeviceHierarchy.

    Each leaf device has:
      - A unique name matching its HierarchyNode name
      - nf=1 (each physical finger is a single-finger device)
      - A 'parent' parameter pointing to the root device name
    """
    leaves = hierarchy.root.all_leaves()
    devices = []

    for leaf in leaves:
        # Resolve pin names — replace array-indexed nets with leaf-specific nets
        resolved_pins = {}
        for pin_name, net_name in pins.items():
            net_base, net_idx = parse_net_array_suffix(net_name)
            if net_idx is not None and leaf.multiplier_index is not None:
                # Use the multiplier index as the array index for net resolution
                resolved_pins[pin_name] = f"{net_base}<{leaf.multiplier_index - 1}>"
            else:
                resolved_pins[pin_name] = net_name

        leaf_params = params.copy()
        leaf_params['nf'] = 1
        leaf_params['parent'] = hierarchy.root.name
        if leaf.multiplier_index is not None:
            leaf_params['multiplier_index'] = leaf.multiplier_index
        if leaf.finger_index is not None:
            leaf_params['finger_index'] = leaf.finger_index

        Device = _Device()
        dev = Device(leaf.name, dtype, resolved_pins, leaf_params)
        devices.append(dev)

    return devices
