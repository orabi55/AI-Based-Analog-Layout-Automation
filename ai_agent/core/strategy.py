"""
Strategy Selection Core Logic
==============================
Canonical implementation of placement mode parsing and strategy helpers.
Extracted from ai_agent/agents/strategy_selector.py — zero logic changes.

Functions:
- parse_placement_mode: Parses user feedback into a specific placement mode.
- _normalize_chat_history: Normalizes chat history for LLM consumption.
- _mirror_fallback_strategies: Provides static fallback strategies for mirrors.
"""


def _normalize_chat_history(chat_history):
    normalized = []
    if not isinstance(chat_history, list):
        return normalized

    for msg in chat_history:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "")).strip()
        content = str(msg.get("content", "")).strip()
        if not role or not content:
            continue
        normalized.append({"role": role, "content": content})

    return normalized


def _mirror_fallback_strategies() -> str:
    """Fallback strategies when mirrors are detected."""
    return (
        "Here are the recommended placement strategies for your current mirror:\n\n"
        "1. **Interdigitated Placement** — Single-row ABAB pattern. "
        "Fingers of matched devices are alternated in one row for 1D gradient cancellation. "
        "Compact layout, good for moderate matching requirements.\n\n"
        "2. **Common Centroid Placement** — Multi-row 2D symmetric placement. "
        "Fingers are distributed across multiple rows, symmetric about the center. "
        "Best matching performance — cancels gradients in both X and Y directions.\n\n"
        "3. **Auto (recommended)** — System automatically selects interdigitated "
        "(≤16 fingers) or common centroid (>16 fingers) based on total finger count.\n\n"
        "4. **Two-Half (Vertical Axis Symmetry)** — For fully-differential circuits. "
        "All matched pairs are placed symmetrically around a single vertical axis. "
        "Diff pair (rank 1) sits innermost, load mirror (rank 2) sits outermost, "
        "tail/axis device is centered exactly on the axis. "
        "Best for comparators and 5T-OTAs.\n\n"
        "Type **1** for interdigitated, **2** for common centroid, "
        "**3** for auto, **4** for two-half vertical axis, "
        "or describe a custom approach."
    )


def parse_placement_mode(user_message: str, constraint_text: str = "") -> str:
    """Parse the user's strategy choice into a placement mode.

    Args:
        user_message:    User's reply (e.g., "1", "2", "common centroid", "4", etc.)
        constraint_text: Topology constraints (used to check for mirrors/symmetry)

    Returns:
        "interdigitated" | "common_centroid" | "auto" | "two_half"
    """
    has_mirror = "MIRROR" in (constraint_text or "").upper()
    has_symmetry = "[SYMMETRY]" in (constraint_text or "")

    # If [SYMMETRY] block detected, default to two_half
    if has_symmetry and not (constraint_text or ""):
        return "two_half"

    if not has_mirror and not has_symmetry:
        return "auto"

    msg = user_message.strip().lower()

    # Two-half keywords
    if any(k in msg for k in ("two-half", "two half", "2 halves", "axis symmetry",
                               "vertical axis", "two_half")):
        return "two_half"
    if msg in ("4", "4."):
        return "two_half"

    # Explicit keyword matching
    if "common centroid" in msg or "common_centroid" in msg or "common-centroid" in msg:
        return "common_centroid"
    if "interdigit" in msg:  # matches "interdigitated", "interdigitation"
        return "interdigitated"

    # Strategy number matching (only if using mirror fallback strategies)
    # 1 = interdigitated, 2 = common centroid, 3 = auto, 4 = two_half
    if msg in ("1", "1."):
        return "interdigitated"
    if msg in ("2", "2."):
        return "common_centroid"
    if msg in ("3", "3.", "auto", "all", "yes"):
        return "auto"

    # If [SYMMETRY] block is present and user didn't override, default to two_half
    if has_symmetry:
        return "two_half"

    # Default: auto
    return "auto"
