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
    
    # Max retries exhausted — proceed to human viewer even if DRC fails.
    # (Previously this re-routed to node_placement_specialist on >5 violations,
    # which caused an infinite loop because drc_retry_count was never reset.)
    return "node_human_viewer"

# def route_after_routing(state: LayoutState):
#     if state.get("routing_pass_count", 0) < MAX_ROUTING_PASSES:
#         return "node_routing_previewer" # Loop for more hill-climbing
#     return "node_human_viewer"

def route_after_human(state: LayoutState):
    if state.get("approved", False):
        return "node_save_to_rag"  # Proceed to save if approved
    return "node_placement_specialist" # Loop back to placement with edits