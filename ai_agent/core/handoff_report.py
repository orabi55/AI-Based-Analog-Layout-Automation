"""
Handoff Report Generator
========================
Makes the 75/25 split explicit: which placement decisions the pipeline made
with confidence, and which still need human review via chatbot, MCP server,
or manual editing in Custom Compiler.

Public API:
    generate_handoff_report(state, pdk) -> dict
    render_handoff_report_html(report) -> str

The report is intended to be:
  1. Stored under state["handoff_report"] before save_layout_state(state).
  2. Rendered in the Qt chat panel via _append_bubble(..., is_html=True).
"""

from __future__ import annotations

import logging
from typing import Any, List, Tuple

from ai_agent.placement.quality_metrics import score_placement
from ai_agent.pdks.loader import _SAED14_DEFAULTS, _YIELD_CRITICAL

logger = logging.getLogger("ai_agent")

# Confidence levels
_HIGH:   str = "high"
_MEDIUM: str = "medium"
_LOW:    str = "low"

# Rule sources
_CONFIRMED_PDK:    str = "confirmed_pdk"
_LITERATURE_PRIOR: str = "literature_prior"
_HEURISTIC:        str = "heuristic"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _rule_lookup(pdk: dict, rule_name: str) -> Tuple[Any, str]:
    """Return (value, source) where source is one of the three rule_source tokens.

    Source mapping:
      - confirmed_pdk:    explicitly present in the supplied pdk dict (top or nested)
      - literature_prior: served from _SAED14_DEFAULTS, not yield-critical
      - heuristic:        served from _SAED14_DEFAULTS and IS yield-critical,
                          OR rule unknown (value None)
    """
    if pdk is None:
        pdk = {}

    # 1. Top-level explicit override → confirmed
    if rule_name in pdk:
        return pdk[rule_name], _CONFIRMED_PDK

    # 2. Nested dict override → confirmed
    for subval in pdk.values():
        if isinstance(subval, dict) and rule_name in subval:
            return subval[rule_name], _CONFIRMED_PDK

    # 3. Default fallback
    if rule_name in _SAED14_DEFAULTS:
        if rule_name in _YIELD_CRITICAL:
            return _SAED14_DEFAULTS[rule_name], _HEURISTIC
        return _SAED14_DEFAULTS[rule_name], _LITERATURE_PRIOR

    # 4. Unknown rule
    return None, _HEURISTIC


def _confidence_for(source: str, value: Any) -> str:
    """Spec rule: heuristic source OR null value → confidence is always 'low'."""
    if value is None:
        return _LOW
    if source == _HEURISTIC:
        return _LOW
    if source == _LITERATURE_PRIOR:
        return _MEDIUM
    return _HIGH  # confirmed_pdk


def _to_pct(score: Any) -> float:
    """Convert a [0,1] score to a percent. None → 0.0."""
    if score is None:
        return 0.0
    try:
        return round(float(score) * 100.0, 1)
    except (TypeError, ValueError):
        return 0.0


def _compute_area_utilization(nodes: list) -> float:
    """Active-device area / bounding-box area, expressed as a percentage."""
    valid = [n for n in nodes if isinstance(n.get("geometry"), dict)]
    if not valid:
        return 0.0

    xs  = [float(n["geometry"].get("x", 0)) for n in valid]
    ys  = [float(n["geometry"].get("y", 0)) for n in valid]
    xes = [float(n["geometry"].get("x", 0)) + float(n["geometry"].get("width", 0))
           for n in valid]
    yes = [float(n["geometry"].get("y", 0)) + float(n["geometry"].get("height", 0))
           for n in valid]

    bbox_area = (max(xes) - min(xs)) * (max(yes) - min(ys))
    if bbox_area <= 0:
        return 0.0

    active = sum(
        float(n["geometry"].get("width", 0)) * float(n["geometry"].get("height", 0))
        for n in valid
        if not n.get("is_dummy")
        and not str(n.get("id", "")).startswith(
            ("EDGE_DUMMY", "FILLER_DUMMY", "DUMMY_matrix_")
        )
    )
    return round(100.0 * active / bbox_area, 1)


def _infer_circuit_type(state: dict, groups: list, nodes: list) -> str:
    """Best-effort circuit type from explicit state, constraint_text, or device mix."""
    if state.get("circuit_type"):
        return str(state["circuit_type"])

    ct = state.get("constraint_text", "") or ""
    for line in ct.split("\n"):
        if line.strip().startswith("CIRCUIT_TYPE:"):
            value = line.split(":", 1)[1].strip()
            if value:
                return value

    if groups:
        types = sorted({str(g.get("type", "")) for g in groups if g.get("type")})
        if types:
            return ", ".join(types)

    n_pmos = sum(1 for n in nodes if str(n.get("type", "")).lower() == "pmos")
    n_nmos = sum(1 for n in nodes if str(n.get("type", "")).lower() == "nmos")
    if n_pmos and n_nmos:
        return "Mixed PMOS/NMOS"
    if n_pmos:
        return "PMOS-only"
    if n_nmos:
        return "NMOS-only"
    return "unknown"


def _format_review_item(decision: dict) -> str:
    """Convert a low-confidence decision into a specific actionable review string."""
    label  = decision.get("decision",   "?")
    reason = decision.get("reason",     "")
    return f"{label} — {reason}"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_handoff_report(state: dict, pdk: dict) -> dict:
    """Build the five-section handoff report.

    Sections:
      1. summary                   — totals + circuit type + DRC pass
      2. scores                    — symmetry / interdig / utilization / flow / DRC
      3. confidence_per_decision   — per-decision confidence + rule source
      4. needs_human_review        — actionable items requiring manual verification
      5. suggested_next_actions    — concrete next steps in chatbot/MCP/CC
    """
    state = state or {}
    pdk   = pdk   or {}

    nodes  = state.get("placement_nodes") or state.get("nodes") or []
    groups = state.get("groups") or []
    drc_pass = bool(state.get("drc_pass", False))

    # ── 1. summary ──────────────────────────────────────────────────────
    summary = {
        "total_devices": len(nodes),
        "total_groups":  len(groups),
        "circuit_type":  _infer_circuit_type(state, groups, nodes),
        "pdk_name":      str(state.get("pdk_name", "saed14nm")),
        "drc_pass":      drc_pass,
    }

    # ── 2. scores ───────────────────────────────────────────────────────
    quality = state.get("placement_quality") or {}
    if not quality and nodes:
        try:
            quality = score_placement(nodes)
        except Exception as exc:
            logger.warning("score_placement failed in handoff_report: %s", exc)
            quality = {}

    routing = state.get("routing_result") or {}
    signal_flow_cost = 0.0
    for key in ("weighted_cost", "score", "estimated_crossings"):
        if key in routing and routing[key] is not None:
            try:
                signal_flow_cost = float(routing[key])
                break
            except (TypeError, ValueError):
                pass

    scores = {
        "symmetry":         _to_pct(quality.get("matching_x_score") or quality.get("layout_y_score")),
        "interdigitation":  _to_pct(quality.get("interdigitation_score")),
        "area_utilization": _compute_area_utilization(nodes),
        "signal_flow_cost": round(signal_flow_cost, 4),
        "drc_pass":         drc_pass,
    }

    # ── 3. confidence_per_decision ──────────────────────────────────────
    decisions: List[dict] = []

    # PDK-rule-driven decisions
    pdk_rules = [
        ("Fin grid snap",     "fin_pitch_um",
         "Snap-to-grid pitch for tap and filler placement"),
        ("Tap cell spacing",  "tap_max_distance_um",
         "Maximum distance between substrate tap cells"),
        ("Endcap cell name",  "endcap_cell_names",
         "Cell name(s) used at row boundaries"),
        ("Endcap width",      "endcap_width_um",
         "Physical width of endcap cells"),
    ]
    for label, rule_key, base_reason in pdk_rules:
        value, source = _rule_lookup(pdk, rule_key)
        confidence    = _confidence_for(source, value)

        if value is None:
            reason = f"{base_reason} — value missing in PDK (null)"
        elif source == _CONFIRMED_PDK:
            reason = f"{base_reason} (PDK value: {value!r})"
        elif source == _HEURISTIC:
            reason = f"{base_reason} — using heuristic fallback ({value!r}); verify against PDK DRC runset"
        else:  # literature_prior
            reason = f"{base_reason} — using SAED14 literature default ({value!r})"

        decisions.append({
            "decision":    label,
            "confidence":  confidence,
            "reason":      reason,
            "rule_source": source,
        })

    # Algorithmic / quality-metric driven decisions
    decisions.append({
        "decision":    "DRC overlap resolution",
        "confidence":  _HIGH if drc_pass else _MEDIUM,
        "reason":      ("All overlaps resolved cleanly"
                        if drc_pass else
                        f"{len(state.get('drc_flags', []))} unresolved violation(s)"),
        "rule_source": _CONFIRMED_PDK,
    })

    sym_score = quality.get("matching_x_score")
    if sym_score is not None:
        if sym_score >= 0.85:
            sym_conf = _HIGH
        elif sym_score >= 0.5:
            sym_conf = _MEDIUM
        else:
            sym_conf = _LOW
        decisions.append({
            "decision":    "Matched-pair X mirror symmetry",
            "confidence":  sym_conf,
            "reason":      f"X mirror score = {_to_pct(sym_score)}%",
            "rule_source": _LITERATURE_PRIOR,
        })

    cc_score = quality.get("centroid_score")
    if cc_score is not None:
        if cc_score >= 0.85:
            cc_conf = _HIGH
        elif cc_score >= 0.5:
            cc_conf = _MEDIUM
        else:
            cc_conf = _LOW
        decisions.append({
            "decision":    "2D common-centroid accuracy",
            "confidence":  cc_conf,
            "reason":      f"Centroid coincidence score = {_to_pct(cc_score)}%",
            "rule_source": _LITERATURE_PRIOR,
        })

    # ── 4. needs_human_review ───────────────────────────────────────────
    needs_review: List[str] = []

    # DRC failures jump to the top (most actionable issue)
    if not drc_pass:
        n = len(state.get("drc_flags", []))
        needs_review.append(
            f"{n} DRC violation(s) unresolved — re-run legalizer or fix manually in Custom Compiler"
        )

    # Every low-confidence decision becomes a review item
    for d in decisions:
        if d["confidence"] == _LOW:
            needs_review.append(_format_review_item(d))

    # Known unimplemented features
    needs_review.append("Guard ring not inserted — not yet implemented")

    # ── 5. suggested_next_actions ──────────────────────────────────────
    actions: List[str] = []
    actions.append("Run 'check_overlaps' to verify final DRC state")

    if not drc_pass:
        actions.append("Run 'run_legalizer' to apply prescriptive DRC fixes")

    if any(d["confidence"] == _LOW and "Tap" in d["decision"] for d in decisions):
        actions.append("Use chatbot: 'verify tap spacing for each row against PDK runset'")

    if any(d["confidence"] == _LOW and "centroid" in d["decision"].lower() for d in decisions):
        actions.append("Manually inspect 2D common-centroid groups in Custom Compiler / KLayout")

    if scores["area_utilization"] > 0 and scores["area_utilization"] < 50.0:
        actions.append(
            f"Area utilization is {scores['area_utilization']}% — consider tightening row spacing"
        )

    actions.append("Run 'score_layout' after manual edits to re-score the layout")

    return {
        "summary":                 summary,
        "scores":                  scores,
        "confidence_per_decision": decisions,
        "needs_human_review":      needs_review,
        "suggested_next_actions":  actions,
    }


# ---------------------------------------------------------------------------
# HTML rendering for the Qt chat panel
# ---------------------------------------------------------------------------

# Color palette (consistent with the chat_panel.py colour scheme)
_COLOR = {
    _HIGH:   "#4ec98e",  # green
    _MEDIUM: "#e9b343",  # amber
    _LOW:    "#e25b5b",  # red
}


def _esc(text: Any) -> str:
    """Minimal HTML escaping for free-form report fields."""
    s = "" if text is None else str(text)
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))


def render_handoff_report_html(report: dict) -> str:
    """Render the report as HTML for the Qt chat panel.

    Color coding: high=green, medium=amber, low=red.
    The 'needs_human_review' section is set apart with a coloured border
    and a tinted background so it is the most prominent block.
    """
    summary    = report.get("summary", {}) or {}
    scores     = report.get("scores",  {}) or {}
    decisions  = report.get("confidence_per_decision", []) or []
    review     = report.get("needs_human_review", []) or []
    actions    = report.get("suggested_next_actions", []) or []

    parts: List[str] = [
        "<div style='border:1px solid #2d3548; padding:10px; border-radius:8px; "
        "background:#1a2230; color:#d0d8e0; font-size:12px;'>"
    ]
    parts.append("<b style='color:#7cb7ff; font-size:14px;'>Layout Handoff Report</b>")

    # Summary
    drc_color = _COLOR[_HIGH] if summary.get("drc_pass") else _COLOR[_LOW]
    drc_label = "PASS" if summary.get("drc_pass") else "FAIL"
    parts.append(
        "<div style='margin-top:8px; line-height:1.55;'>"
        f"<b>Circuit:</b> {_esc(summary.get('circuit_type', '?'))}<br>"
        f"<b>Devices:</b> {summary.get('total_devices', 0)} "
        f"&nbsp;<b>Groups:</b> {summary.get('total_groups', 0)}<br>"
        f"<b>PDK:</b> {_esc(summary.get('pdk_name', '?'))}<br>"
        f"<b>DRC:</b> <span style='color:{drc_color}; font-weight:bold;'>{drc_label}</span>"
        "</div>"
    )

    # Scores
    parts.append(
        "<div style='margin-top:10px; line-height:1.5;'><b>Scores</b><br>"
        f"&nbsp;&nbsp;Symmetry: {scores.get('symmetry', 0)}%<br>"
        f"&nbsp;&nbsp;Interdigitation: {scores.get('interdigitation', 0)}%<br>"
        f"&nbsp;&nbsp;Area utilization: {scores.get('area_utilization', 0)}%<br>"
        f"&nbsp;&nbsp;Signal flow cost: {scores.get('signal_flow_cost', 0)}<br>"
        "</div>"
    )

    # Confidence per decision
    if decisions:
        parts.append("<div style='margin-top:10px;'><b>Decisions</b>")
        for d in decisions:
            conf  = d.get("confidence", _LOW)
            color = _COLOR.get(conf, _COLOR[_LOW])
            parts.append(
                "<div style='margin:3px 0;'>"
                f"<span style='color:{color}; font-weight:bold;'>● {_esc(conf.upper())}</span> — "
                f"<i>{_esc(d.get('decision', ''))}</i>: {_esc(d.get('reason', ''))} "
                f"<span style='color:#5a6d82;'>({_esc(d.get('rule_source', ''))})</span>"
                "</div>"
            )
        parts.append("</div>")

    # Needs human review (PROMINENT)
    if review:
        parts.append(
            "<div style='margin-top:12px; padding:10px; "
            f"border-left:4px solid {_COLOR[_LOW]}; "
            "background:#2a1818; border-radius:4px;'>"
            f"<b style='color:{_COLOR[_LOW]}; font-size:13px;'>⚠ Needs Human Review</b>"
        )
        for item in review:
            parts.append(f"<div style='margin:4px 0;'>• {_esc(item)}</div>")
        parts.append("</div>")

    # Suggested next actions
    if actions:
        parts.append("<div style='margin-top:10px;'><b>Suggested Next Actions</b>")
        for a in actions:
            parts.append(f"<div style='margin:3px 0;'>→ {_esc(a)}</div>")
        parts.append("</div>")

    parts.append("</div>")
    return "".join(parts)
