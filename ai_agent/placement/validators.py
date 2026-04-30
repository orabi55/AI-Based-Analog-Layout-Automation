"""
Placement Validators
====================
Provides post-placement validation utilities to ensure structural integrity and 
adherence to design constraints in the AI-generated layout.

Functions:
- _validate_placement: Performs structural sanity checks on the placement results.
  - Inputs: original_nodes (list), result (list or dict)
  - Outputs: list of error strings.
- validate_finger_integrity: Verifies that all finger devices are preserved.
  - Inputs: original_nodes (list), processed_nodes (list)
  - Outputs: dict containing validation status and details.
"""

from collections import Counter, defaultdict


def _validate_placement(original_nodes: list, result) -> list:
    """Run quick structural sanity checks on the returned AI placement."""
    errors = []
    orig_ids  = {n["id"] for n in original_nodes}
    orig_type = {n["id"]: n.get("type") for n in original_nodes}

    if isinstance(result, list):
        placed_nodes = result
    elif isinstance(result, dict):
        placed_nodes = result.get("nodes", [])
    else:
        errors.append(f"Unexpected placement result type: {type(result).__name__}")
        return errors

    if not placed_nodes:
        errors.append("Response has no 'nodes' array.")
        return errors

    placed_ids = {n.get("id") for n in placed_nodes if isinstance(n, dict) and n.get("id")}
    missing = orig_ids - placed_ids
    extra   = placed_ids - orig_ids
    if missing:
        errors.append(f"MISSING devices: {sorted(missing)}")
    if extra:
        errors.append(f"EXTRA (invented) devices: {sorted(extra)}")

    id_counts = Counter(n.get("id") for n in placed_nodes if isinstance(n, dict) and n.get("id"))
    duplicates = [dev_id for dev_id, count in id_counts.items() if count > 1]
    if duplicates:
        errors.append(f"DUPLICATE devices: {sorted(duplicates)}")

    rows = defaultdict(list)
    for n in placed_nodes:
        if not isinstance(n, dict): continue
        y = n.get("geometry", {}).get("y", 0)
        rows[round(float(y), 3)].append(n)

    for y, row_nodes in rows.items():
        sorted_row = sorted(row_nodes, key=lambda n: n.get("geometry", {}).get("x", 0))
        for i in range(len(sorted_row) - 1):
            n1, n2 = sorted_row[i], sorted_row[i+1]
            x1 = n1.get("geometry", {}).get("x", 0)
            x2 = n2.get("geometry", {}).get("x", 0)
            w1 = n1.get("geometry", {}).get("width", 0.294)
            dx = x2 - x1
            abut1 = n1.get("abutment", {})
            abut2 = n2.get("abutment", {})
            is_abutted = abut1.get("abut_right") and abut2.get("abut_left")

            if is_abutted:
                if abs(dx - 0.070) > 0.005:
                    errors.append(f"Abutment spacing error between {n1['id']} and {n2['id']}")
            else:
                min_x2 = round(x1 + w1, 4)
                if round(x2, 4) < min_x2 - 0.001:
                    errors.append(f"Overlap in row y={y} between {n1['id']} and {n2['id']}")

    for n in placed_nodes:
        if not isinstance(n, dict): continue
        dev_id = n.get("id", "?")
        expected = orig_type.get(dev_id)
        actual = n.get("type")
        if expected and actual and expected != actual:
            errors.append(f"Device {dev_id} changed type: was {expected}, now {actual}")

    return errors


def validate_finger_integrity(
    original_nodes: list[dict],
    processed_nodes: list[dict],
) -> dict:
    """Verify that all finger devices are preserved after aggregation/expansion."""
    original_ids  = {n["id"] for n in original_nodes  if not n.get("is_dummy")}
    processed_ids = {n["id"] for n in processed_nodes if not n.get("is_dummy")}

    missing = sorted(original_ids  - processed_ids)
    extra   = sorted(processed_ids - original_ids)
    passed  = (len(missing) == 0 and len(extra) == 0)

    if passed:
        summary = f"Finger integrity OK — all {len(original_ids)} device(s) preserved"
    else:
        summary = f"Finger integrity FAILED — {len(missing)} missing, {len(extra)} extra"

    return {
        "pass": passed,
        "missing": missing,
        "extra": extra,
        "original_count": len(original_ids),
        "processed_count": len(processed_ids),
        "summary": summary,
    }
