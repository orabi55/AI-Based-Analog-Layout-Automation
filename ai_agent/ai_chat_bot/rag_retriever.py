"""
ai_agent/rag_retriever.py
=========================
RAG Retriever — formats retrieved examples as few-shot context
to inject into the Placement Specialist prompt.

Usage (called inside orchestrator.py at Stage 2):
    from ai_agent.rag_retriever import build_rag_context
    rag_context = build_rag_context(nodes, edges, terminal_nets)
    # then inject rag_context into specialist_user string
"""

from ai_agent.ai_chat_bot.rag_store import RAGStore

# Singleton store — loaded once, reused across all runs
_store: RAGStore | None = None


def get_store() -> RAGStore:
    """Return the singleton RAGStore, initialising it on first call."""
    global _store
    if _store is None:
        _store = RAGStore()
    return _store


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def build_rag_context(
    nodes:         list,
    edges:         list,
    terminal_nets: dict,
    top_k:         int = 3,
) -> str:
    """Retrieve similar past examples and format them as few-shot prompt context.

    Args:
        nodes:         current device list (pre-placement)
        edges:         net edges
        terminal_nets: {device_id: {gate, drain, source}} dict
        top_k:         max number of examples to inject

    Returns:
        A formatted string ready to prepend to the Placement Specialist prompt.
        Returns empty string if no examples are available yet.
    """
    store    = get_store()
    examples = store.retrieve_similar(nodes, terminal_nets, edges=edges, top_k=top_k)

    if not examples:
        return ""  # no examples yet — RAG is silent, no impact on prompt

    lines = []
    lines.append("=" * 60)
    lines.append("📚 RAG: SIMILAR PAST PLACEMENTS (USE AS REFERENCE)")
    lines.append("=" * 60)
    lines.append(
        "The following are real successful placements from similar circuits.\n"
        "Use them as guidance — adapt the x-positions to your current device list.\n"
        "DO NOT copy IDs blindly — check that each ID exists in YOUR inventory.\n"
    )

    for i, ex in enumerate(examples, 1):
        drc   = ex["drc_violations"]
        cross = ex["routing_score"]
        wire  = ex["wire_length"]
        sim   = ex["similarity"]
        score = ex["quality_score"]

        lines.append(f"--- Example {i}: '{ex['label']}' "
                     f"(similarity={sim:.2f}, quality={score:.0f}/100) ---")
        lines.append(f"Scores: DRC violations={drc}, "
                     f"routing crossings={cross}, wire length={wire:.3f}µm")

        if drc == 0:
            lines.append("✅ This example passed DRC with zero violations.")
        else:
            lines.append(f"⚠ This example had {drc} DRC violation(s) — use with caution.")

        lines.append("Placement used in this example:")

        # Separate PMOS and NMOS for clarity
        pmos = sorted(
            [p for p in ex["placement"] if p["type"] == "pmos" and not p["is_dummy"]],
            key=lambda p: p["x"]
        )
        nmos = sorted(
            [p for p in ex["placement"] if p["type"] == "nmos" and not p["is_dummy"]],
            key=lambda p: p["x"]
        )
        dummies = [p for p in ex["placement"] if p["is_dummy"]]

        if pmos:
            lines.append("  PMOS row (left → right):")
            for p in pmos:
                lines.append(f"    {p['id']:10s}  x={p['x']:.3f}  orient={p['orientation']}")

        if nmos:
            lines.append("  NMOS row (left → right):")
            for p in nmos:
                lines.append(f"    {p['id']:10s}  x={p['x']:.3f}  orient={p['orientation']}")

        if dummies:
            lines.append(f"  Dummies: {', '.join(p['id'] for p in dummies)}")

        lines.append("")

    lines.append("=" * 60)
    lines.append("END OF RAG EXAMPLES — Now apply to YOUR current circuit below.")
    lines.append("=" * 60)
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Save helper (called at end of orchestrator run)
# ---------------------------------------------------------------------------
def save_run_as_example(
    nodes:          list,
    edges:          list,
    terminal_nets:  dict,
    drc_result:     dict,
    routing_result: dict,
    label:          str = "",
) -> str:
    """Save a completed run to the RAG store.

    Call this at the end of orchestrator.continue_placement() to
    accumulate examples automatically over time.

    Returns:
        example_id (str)
    """
    store = get_store()
    return store.save_example(
        nodes, edges, terminal_nets,
        drc_result, routing_result,
        label=label,
    )
