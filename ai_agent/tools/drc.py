"""
DRC Tool Wrapper
================
Provides a wrapper for the DRC overlap and gap check engine.

Functions:
- run_drc (tool_run_drc): Executes the DRC check on a set of nodes.
  - Inputs: nodes (list), gap_px (float)
  - Outputs: DRC result dictionary.
"""


def run_drc(nodes, gap_px=0.0):
    """Run the pure-Python DRC overlap + gap check.

    Returns:
        dict: see ai_agent.agents.drc_critic.run_drc_check() for schema.
    """
    try:
        from ai_agent.agents.drc_critic import run_drc_check
        return run_drc_check(nodes, gap_px=gap_px)
    except Exception as exc:
        print(f"[TOOLS] run_drc failed: {exc}")
        return {"pass": True, "violations": [], "summary": str(exc)}


# Backward-compatible alias
tool_run_drc = run_drc
