from ai_agent.ai_chat_bot.state import LayoutState
##those can be changed for optimization of answers/ remove them if wanted a single cycle
MAX_ROUTING_PASSES = 3
MAX_DRC_RETRIES = 2
########
def route_after_drc(state: LayoutState):
    if state.get("drc_pass", False):
        return "node_human_viewer"  # If DRC passed, proceed to human viewer
    
    retry_count = state.get("drc_retry_count", 0)
    if retry_count < MAX_DRC_RETRIES:
        return "node_drc_critic"  # Loop back for another attempt
    
    # On final failure with many violations remaining, route back to
    # placement specialist which will use deterministic fallback.
    drc_flags = state.get("drc_flags", [])
    if len(drc_flags) > 5:
        from ai_agent.ai_chat_bot.pipeline_log import ip_step
        ip_step("5/5 DRC Critic",
                f"catastrophic failure ({len(drc_flags)} violations) — re-routing to placement")
        return "node_placement_specialist"
    
    # If we run out of retries, we must move forward to avoid an infinite loop
    return "node_human_viewer"  # Proceed to human viewer even if DRC fails after max retries

# def route_after_routing(state: LayoutState):
#     if state.get("routing_pass_count", 0) < MAX_ROUTING_PASSES:
#         return "node_routing_previewer" # Loop for more hill-climbing
#     return "node_human_viewer"

def route_after_human(state: LayoutState):
    if state.get("approved", False):
        return "node_save_to_rag"  # Proceed to save if approved
    return "node_placement_specialist" # Loop back to placement with edits