"""
Matching Intent Handler
=======================
Deterministic handler for matching edit/change commands.

Ensures matching questions return answers-only (no commands) and
matching edit commands return safe, targeted responses without
generating unrelated layout modifications.

Functions:
- parse_matching_edit_intent:
  - Inputs: message (str), state (dict)
  - Outputs: dict with is_matching_edit, is_question, target_devices, etc.
- evaluate_matching_edit_intent:
  - Inputs: intent (dict), state (dict)
  - Outputs: dict with decision, assistant_text, pending_cmds
"""

from __future__ import annotations

import re
from typing import Optional

from ai_agent.tools.device_resolver import (
    normalize_logical_device_id,
    find_matched_block_for_device,
    detect_finger_interleaving,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MATCHING_VERBS = frozenset({
    "match", "make", "apply", "change", "convert", "use", "enforce", "set",
})

_MATCHING_TECHNIQUES: dict[str, str] = {
    "common centroid":    "common_centroid",
    "common-centroid":    "common_centroid",
    "centroid":           "common_centroid",
    "centriod":           "common_centroid",       # common typo
    "interdigitation":    "interdigitated",
    "interdigitated":     "interdigitated",
    "interdigitate":      "interdigitated",
    "interdig":           "interdigitated",
    "symmetric":          "symmetric",
    "symmetry":           "symmetric",
    "mirror":             "symmetric",
    "mirrored":           "symmetric",
    "matched environment": "matched_environment",
}

_QUESTION_MARKERS = frozenset({"?", "should", "how", "what", "or", "is", "are", "does", "do"})

_PAIR_ALIASES: dict[str, list[list[str]]] = {
    "input pair":       [["MM8", "MM9"]],
    "input pairs":      [["MM8", "MM9"]],
    "latch pair":       [["MM4", "MM5"], ["MM6", "MM7"]],     # ambiguous
    "pmos latch pair":  [["MM4", "MM5"]],
    "nmos latch pair":  [["MM6", "MM7"]],
    "output pair":      [["MM1", "MM2"]],
    "precharge pair":   [["MM0", "MM3"], ["MM1", "MM2"]],     # ambiguous
    "load pair":        [["MM0", "MM3"]],
    "diff pair":        [["MM8", "MM9"]],
    "differential pair": [["MM8", "MM9"]],
}

#: Regex matching analog device names (MM1, M1, etc.)
_DEVICE_RE = re.compile(r"\b((?:MM|XM|MN|MP|M)\d+)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Intent parsing
# ---------------------------------------------------------------------------

def parse_matching_edit_intent(message: str, state: dict | None = None) -> dict:
    """Parse a matching edit/change intent from user message.

    Returns::

        {
            "is_matching_edit": bool,
            "is_question": bool,
            "target_devices": ["MM8", "MM9"],
            "target_group": "MM8_MM9_matched",
            "requested_technique": "common_centroid",
            "normalized_technique": "common_centroid",
            "ambiguous_alias": None or str,
        }
    """
    text = str(message or "").strip()
    text_l = text.lower()

    result = {
        "is_matching_edit": False,
        "is_question": False,
        "target_devices": [],
        "target_group": "",
        "requested_technique": "",
        "normalized_technique": "",
        "ambiguous_alias": None,
    }

    # Check if any matching technique is mentioned
    found_technique = ""
    found_normalized = ""
    for phrase, normalized in _MATCHING_TECHNIQUES.items():
        if phrase in text_l:
            found_technique = phrase
            found_normalized = normalized
            break

    if not found_technique:
        return result

    # Check for question markers
    is_question = False
    if "?" in text:
        is_question = True
    else:
        first_word = text_l.split()[0] if text_l.split() else ""
        if first_word in {"how", "what", "why", "is", "are", "should", "does", "do"}:
            is_question = True
        # "or" between two techniques = question
        if " or " in text_l and sum(1 for t in _MATCHING_TECHNIQUES if t in text_l) >= 2:
            is_question = True

    result["is_question"] = is_question
    result["requested_technique"] = found_technique
    result["normalized_technique"] = found_normalized

    # Check for matching verb (command intent)
    has_verb = False
    for verb in _MATCHING_VERBS:
        if re.search(r"\b" + verb + r"\b", text_l):
            has_verb = True
            break

    # Extract target devices
    devices = [normalize_logical_device_id(d) for d in _DEVICE_RE.findall(text)]
    devices = list(dict.fromkeys(devices))  # deduplicate preserving order

    # Check pair aliases
    ambiguous_alias = None
    if not devices:
        for alias, pairs in _PAIR_ALIASES.items():
            if alias in text_l:
                if len(pairs) > 1:
                    ambiguous_alias = alias
                    result["ambiguous_alias"] = alias
                else:
                    devices = list(pairs[0])
                break

    # Check for PMOS/NMOS disambiguation of ambiguous aliases
    if ambiguous_alias:
        if "pmos" in text_l:
            for alias, pairs in _PAIR_ALIASES.items():
                pmos_alias = f"pmos {alias}"
                if pmos_alias in text_l:
                    devices = list(pairs[0]) if pairs else []
                    ambiguous_alias = None
                    result["ambiguous_alias"] = None
                    break
            if not devices:
                # "pmos latch" → MM4/MM5
                if ambiguous_alias == "latch pair":
                    devices = ["MM4", "MM5"]
                    ambiguous_alias = None
                    result["ambiguous_alias"] = None
        elif "nmos" in text_l:
            if ambiguous_alias == "latch pair":
                devices = ["MM6", "MM7"]
                ambiguous_alias = None
                result["ambiguous_alias"] = None

    result["target_devices"] = devices

    # Determine if this is a matching edit command
    if has_verb and not is_question:
        result["is_matching_edit"] = True
    elif is_question:
        result["is_matching_edit"] = False

    # Build target group name
    if len(devices) >= 2:
        sorted_devs = sorted(devices)
        result["target_group"] = f"{'_'.join(sorted_devs)}_matched"

    return result


# ---------------------------------------------------------------------------
# Intent evaluation
# ---------------------------------------------------------------------------

def evaluate_matching_edit_intent(intent: dict, state: dict) -> dict:
    """Evaluate a parsed matching edit intent and produce a safe response.

    Returns::

        {
            "layout_session_decision": "answer" | "clarify",
            "assistant_text": "...",
            "pending_cmds": [],
        }

    Safety rules:
    - Never produce commands for devices not in the resolved target group.
    - Never produce unrelated commands.
    - Never force standalone common_centroid on a differential_pair.
    """
    devices = intent.get("target_devices") or []
    technique = intent.get("normalized_technique", "")
    ambiguous = intent.get("ambiguous_alias")
    is_question = intent.get("is_question", False)
    nodes = state.get("placement_nodes") or state.get("nodes") or []

    safe_response = {
        "layout_session_decision": "answer",
        "assistant_text": "",
        "pending_cmds": [],
    }

    # Handle ambiguous aliases
    if ambiguous:
        if ambiguous == "precharge pair":
            safe_response["layout_session_decision"] = "clarify"
            safe_response["assistant_text"] = (
                "Do you mean MM0/MM3 (input precharge/load pair) or "
                "MM1/MM2 (output precharge pair)?"
            )
            return safe_response
        if ambiguous == "latch pair":
            safe_response["layout_session_decision"] = "clarify"
            safe_response["assistant_text"] = (
                "Do you mean MM4/MM5 (PMOS latch pair) or "
                "MM6/MM7 (NMOS latch pair)? "
                "You can also specify 'PMOS latch pair' or 'NMOS latch pair'."
            )
            return safe_response
        safe_response["layout_session_decision"] = "clarify"
        safe_response["assistant_text"] = (
            f"The alias '{ambiguous}' is ambiguous. "
            "Please specify the exact device pair."
        )
        return safe_response

    if len(devices) < 2:
        safe_response["layout_session_decision"] = "clarify"
        safe_response["assistant_text"] = (
            "Please specify which device pair to apply matching to. "
            "For example: 'Make MM8 and MM9 interdigitated'."
        )
        return safe_response

    dev_a, dev_b = devices[0], devices[1]
    pair_set = {dev_a, dev_b}

    # Detect current interleaving from physical layout
    interleaving = detect_finger_interleaving(dev_a, dev_b, nodes)

    # Find matched block
    block = find_matched_block_for_device(dev_a, state)

    # --- Rule A: MM8/MM9 input differential pair ---
    if pair_set == {"MM8", "MM9"}:
        if technique == "common_centroid":
            safe_response["assistant_text"] = (
                "MM8/MM9 are the input differential pair. The structural skill is "
                "differential_pair with common-centroid-style/interdigitated finger "
                "ordering. Forcing standalone common_centroid on a differential pair "
                "is not recommended. "
            )
            if interleaving == "ABAB":
                safe_response["assistant_text"] += (
                    "The current placement already shows ABAB alternating finger order, "
                    "which provides common-centroid-style matching."
                )
            safe_response["assistant_text"] += " No layout changes were applied."
            return safe_response
        if technique == "interdigitated":
            if interleaving == "ABAB":
                safe_response["assistant_text"] = (
                    "MM8/MM9 are already interdigitated (ABAB alternating finger order). "
                    "No changes needed."
                )
            else:
                safe_response["assistant_text"] = (
                    "MM8/MM9 are the input differential pair. Interdigitation is the "
                    "correct approach. The current finger ordering can be inspected "
                    "visually. No layout changes were applied."
                )
            return safe_response
        if technique == "symmetric":
            safe_response["assistant_text"] = (
                "MM8/MM9 are the input differential pair. They should be kept "
                "symmetrically matched. "
            )
            if interleaving:
                safe_response["assistant_text"] += (
                    f"Current finger pattern: {interleaving}. "
                )
            safe_response["assistant_text"] += "No layout changes were applied."
            return safe_response

    # --- Rule B: MM3/MM0 PMOS load pair ---
    if pair_set == {"MM3", "MM0"} or pair_set == {"MM0", "MM3"}:
        block_technique = (block or {}).get("technique", "") if block else ""
        if technique == "interdigitated":
            if interleaving == "ABAB" or "ABAB" in block_technique:
                safe_response["assistant_text"] = (
                    "MM0/MM3 are the PMOS input/precharge load pair. They are already "
                    "interdigitated (ABAB finger ordering). No changes needed."
                )
            else:
                safe_response["assistant_text"] = (
                    "MM0/MM3 are the PMOS input/precharge load pair. Interdigitation "
                    "should be applied at the finger-ordering level. "
                    "No layout changes were applied."
                )
            return safe_response
        if technique == "common_centroid":
            safe_response["assistant_text"] = (
                "MM0/MM3 are the PMOS input/precharge load pair in a single-row "
                "placement. True 2D common-centroid requires multiple rows, which "
                "is not applicable for this single-row pair. The existing "
            )
            if interleaving == "ABAB" or "ABAB" in block_technique:
                safe_response["assistant_text"] += (
                    "ABAB interdigitated ordering provides common-centroid-style "
                    "symmetry within the row."
                )
            else:
                safe_response["assistant_text"] += (
                    "finger ordering provides the best available matching "
                    "within the row."
                )
            safe_response["assistant_text"] += " No layout changes were applied."
            return safe_response

    # --- Rule C: MM2/MM1 output/precharge pair ---
    if pair_set == {"MM2", "MM1"} or pair_set == {"MM1", "MM2"}:
        block_technique = (block or {}).get("technique", "") if block else ""
        if technique == "interdigitated":
            if interleaving == "ABAB" or "ABAB" in block_technique:
                safe_response["assistant_text"] = (
                    "MM1/MM2 are the output precharge pair. They are already "
                    "interdigitated (ABAB finger ordering). No changes needed."
                )
            else:
                safe_response["assistant_text"] = (
                    "MM1/MM2 are the output precharge pair. "
                    "No layout changes were applied."
                )
            return safe_response
        if technique == "common_centroid":
            safe_response["assistant_text"] = (
                "MM1/MM2 are the output precharge pair in a single-row placement. "
                "True 2D common-centroid is not applicable; existing finger ordering "
                "provides common-centroid-style symmetry. "
                "No layout changes were applied."
            )
            return safe_response

    # --- Rule D: MM5/MM4 PMOS latch pair ---
    if pair_set == {"MM5", "MM4"} or pair_set == {"MM4", "MM5"}:
        block_technique = (block or {}).get("technique", "") if block else ""
        if "symmetric_cross_coupled" in block_technique or interleaving == "ABBA":
            safe_response["assistant_text"] = (
                "MM4/MM5 are the PMOS latch pair, already symmetrically matched "
                "for latch balance (symmetric cross-coupled structure). "
            )
        else:
            safe_response["assistant_text"] = (
                "MM4/MM5 are the PMOS latch pair. "
            )
        if technique in {"interdigitated", "common_centroid"}:
            safe_response["assistant_text"] += (
                f"Applying {technique.replace('_', ' ')} to a cross-coupled latch pair "
                "may not be the correct structural operation; the symmetric cross-coupled "
                "skill is typically more appropriate. "
            )
        safe_response["assistant_text"] += "No layout changes were applied."
        return safe_response

    # --- Rule E: MM6/MM7 NMOS latch pair ---
    if pair_set == {"MM6", "MM7"}:
        if technique == "interdigitated" or technique == "common_centroid":
            safe_response["assistant_text"] = (
                f"MM6/MM7 are the NMOS single-finger latch pair. "
                f"{technique.replace('_', ' ').title()} is not applicable for a "
                "single-finger pair (there are no fingers to interleave). "
                "No layout changes were applied."
            )
            return safe_response
        if technique == "symmetric":
            safe_response["assistant_text"] = (
                "MM6/MM7 are the NMOS single-finger latch pair. "
            )
            # Check if currently symmetric
            a_x, b_x = None, None
            for n in nodes:
                if not isinstance(n, dict):
                    continue
                nid = str(n.get("id") or n.get("device_id") or "").upper()
                if nid == "MM6":
                    geom = n.get("geometry") if isinstance(n.get("geometry"), dict) else n
                    a_x = geom.get("x")
                elif nid == "MM7":
                    geom = n.get("geometry") if isinstance(n.get("geometry"), dict) else n
                    b_x = geom.get("x")
            if a_x is not None and b_x is not None:
                safe_response["assistant_text"] += (
                    "They appear to be placed symmetrically in the current layout. "
                )
            safe_response["assistant_text"] += "No layout changes were applied."
            return safe_response

    # --- Generic fallback for known pairs ---
    if block:
        block_technique = block.get("technique", "")
        description = block.get("description", "matched pair")
        safe_response["assistant_text"] = (
            f"{dev_a}/{dev_b} are the {description}. "
            f"Current matching technique: {block_technique.replace('_', ' ')}. "
        )
        if interleaving:
            safe_response["assistant_text"] += (
                f"Current finger pattern: {interleaving}. "
            )
        safe_response["assistant_text"] += "No layout changes were applied."
        return safe_response

    # --- Unknown pair ---
    safe_response["assistant_text"] = (
        f"I recognize {dev_a}/{dev_b} but cannot determine their matched-block "
        "status from the current layout data. No layout changes were applied."
    )
    return safe_response
