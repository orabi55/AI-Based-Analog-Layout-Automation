"""
Inventory Validation Tool
=========================
Ensures device conservation and identity integrity between original and 
proposed layout states.

Functions:
- validate_device_count (tool_validate_device_count): Verifies all active device IDs are preserved.
  - Inputs: original_nodes (list), proposed_nodes (list)
  - Outputs: dictionary with validation status and missing/extra device details.
- validate_inventory (tool_validate_inventory): Performs a strict comparison of device IDs.
  - Inputs: original_nodes (list), proposed_nodes (list)
  - Outputs: 3-tuple (is_valid, missing_ids, extra_ids).
"""


def validate_device_count(original_nodes, proposed_nodes):
    """Check that the proposed placement preserves ALL original device IDs.

    This is the "Conservation Guard" — it catches AI-induced device deletions
    BEFORE any commands reach the GUI.

    Args:
        original_nodes: list of node dicts from the original layout context.
        proposed_nodes: list of node dicts after applying proposed commands.

    Returns:
        dict: {
            "pass": bool,           True when no devices are missing
            "missing": [str],       IDs present in original but absent in proposed
            "extra": [str],         IDs present in proposed but absent in original
            "original_count": int,
            "proposed_count": int,
            "summary": str,
        }
    """
    original_ids = {n["id"] for n in original_nodes if not n.get("is_dummy")}
    proposed_ids = {n["id"] for n in proposed_nodes if not n.get("is_dummy")}

    missing = sorted(original_ids - proposed_ids)
    extra   = sorted(proposed_ids - original_ids)

    passed  = len(missing) == 0
    if passed:
        summary = (
            f"Device conservation OK — all {len(original_ids)} active device(s) present."
        )
    else:
        summary = (
            f"DEVICE CONSERVATION FAILURE: "
            f"{len(missing)} device(s) missing: {', '.join(missing)}."
            + (f"  {len(extra)} unknown device(s): {', '.join(extra)}." if extra else "")
        )

    return {
        "pass": passed,
        "missing": missing,
        "extra": extra,
        "original_count": len(original_ids),
        "proposed_count": len(proposed_ids),
        "summary": summary,
    }


def validate_inventory(original_nodes, proposed_nodes):
    """Strictly compare original device IDs to proposed IDs.

    Prevents transistor deletion or identity mixing.
    Returns a 3-tuple: (bool_pass, missing_ids, extra_ids)

    missing_ids: IDs in original but not in proposed (deletions)
    extra_ids:   IDs in proposed but not in original (hallucinated devices)
    """
    orig_set = {n["id"] for n in original_nodes}
    prop_set = {n["id"] for n in proposed_nodes}

    missing = sorted(orig_set - prop_set)
    extra   = sorted(prop_set - orig_set)

    is_valid = len(missing) == 0 and len(extra) == 0
    return is_valid, missing, extra


# Backward-compatible aliases
tool_validate_device_count = validate_device_count
tool_validate_inventory = validate_inventory
