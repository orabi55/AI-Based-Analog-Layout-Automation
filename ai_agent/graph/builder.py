"""
Unified LangGraph Builder
=========================
Constructs the LangGraph StateGraph for the layout automation pipeline.
Supports "initial" auto-run and "chat" interactive modes.

Functions:
- build_layout_graph: Constructs and compiles the LayoutState graph.
  - Inputs: mode (str: "initial" or "chat")
  - Outputs: tuple (compiled_app, memory)
"""

import warnings

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from ai_agent.agents.classifier import classify_intent
from ai_agent.graph.state import LayoutState
from ai_agent.graph.edges import (
    route_after_drc,
    route_after_human,
    route_by_mode,
    route_after_session_chat,
    route_after_command_validator,
    route_after_session_drc,
    route_after_layout_session_agent,
    route_after_deterministic_tool_runner,
)
from ai_agent.utils.logging import vprint
from ai_agent.nodes import (
    node_topology_analyst,
    node_strategy_selector,
    node_placement_specialist,
    node_placement_specialist_chatbot,
    node_finger_expansion,
    node_symmetry_enforcer,
    node_drc_critic,
    node_routing_previewer,
    node_human_viewer,
    node_save_to_rag,
    node_session_chat,
    node_session_finalizer,
    node_command_validator,
    node_drc_checker,
)
from ai_agent.nodes.layout_session_agent import node_layout_session_agent
from ai_agent.nodes.deterministic_tool_runner import node_deterministic_tool_runner
from ai_agent.nodes.session_synthesizer import node_session_synthesizer


def _route_after_router(state: LayoutState):
    """Route the chat workflow to the selected analysis node."""
    target = str(state.get("router_target", "topology_analyst"))
    if target in {
        "topology_analyst",
        "strategy_selector",
        "placement_specialist",
        "drc_critic",
        "routing_previewer",
    }:
        return target
    return "topology_analyst"


def _node_router(state: LayoutState):
    """Classify user intent and store the downstream routing target."""
    user_message = str(state.get("user_message", ""))
    selected_model = str(state.get("selected_model", "Gemini"))
    target = classify_intent(user_message, selected_model)
    intent = target

    preview = user_message.replace("\n", " ").strip()
    if len(preview) > 120:
        preview = preview[:117] + "..."
    vprint(
        "[ROUTER] intent={} | target={} | model={} | msg={!r}".format(
            intent,
            target,
            selected_model,
            preview,
        )
    )

    if target not in {
        "topology_analyst",
        "strategy_selector",
        "placement_specialist",
        "drc_critic",
        "routing_previewer",
    }:
        target = "topology_analyst"

    return {
        "intent": intent,
        "router_target": target,
    }


def build_layout_graph(mode: str = "initial"):
    """Build a fresh LangGraph with its own MemorySaver per run.

    .. note:: This is the **full-pipeline** layout graph, not the chatbot.

       * For the **session chatbot** (layout-aware interactive chat),
         use :func:`build_session_chat_graph` / ``session_chat_app``.
       * For the **legacy chatbot**, use :func:`build_chat_graph` /
         ``chat_app``.
       * The historical ``mode="chat"`` value here means
         *“interactive full pipeline with human-in-loop”*, **not** the
         session chatbot flow.  It is deprecated — prefer
         ``mode="interactive_full"`` to avoid confusion (Fix 10).

    Args:
        mode: ``"initial"`` for full auto-run, ``"interactive_full"``
              (or deprecated ``"chat"``) for interactive mode.

    Returns:
        (compiled_app, memory) tuple.
    """
    if mode == "chat":
        warnings.warn(
            'build_layout_graph(mode="chat") is deprecated; '
            'use mode="interactive_full" to avoid confusion with the '
            'session chatbot.  For the new chatbot, use '
            'build_session_chat_graph().',
            DeprecationWarning,
            stacklevel=2,
        )
        mode = "interactive_full"
    memory = MemorySaver()
    builder = StateGraph(LayoutState)

    # ── Register all Nodes ──
    builder.add_node("node_topology_analyst", node_topology_analyst)
    builder.add_node("node_strategy_selector", node_strategy_selector)
    builder.add_node("node_placement_specialist", node_placement_specialist)
    builder.add_node("node_finger_expansion", node_finger_expansion)
    builder.add_node("node_symmetry_enforcer", node_symmetry_enforcer)
    builder.add_node("node_drc_critic", node_drc_critic)
    builder.add_node("node_routing_previewer", node_routing_previewer)
    builder.add_node("node_human_viewer", node_human_viewer)
    builder.add_node("node_save_to_rag", node_save_to_rag)

    # ── Mode-based entry routing ──
    builder.add_conditional_edges(START, route_by_mode, {
        "full_pipeline": "node_topology_analyst",
        "interactive":   "node_topology_analyst",
    })

    # ── Linear flow (shared by both modes) ──
    builder.add_edge("node_topology_analyst", "node_strategy_selector")
    builder.add_edge("node_strategy_selector", "node_placement_specialist")
    builder.add_edge("node_placement_specialist", "node_finger_expansion")
    builder.add_edge("node_finger_expansion", "node_symmetry_enforcer")
    builder.add_edge("node_symmetry_enforcer", "node_routing_previewer")
    builder.add_edge("node_routing_previewer", "node_drc_critic")

    # ── Conditional / cyclic flows ──
    builder.add_conditional_edges("node_drc_critic", route_after_drc)

    # ── Terminal ──
    if mode == "initial":
        # Initial placement: go directly to END after human viewer
        # (no interactive review, no RAG save)
        builder.add_edge("node_human_viewer", END)
    else:
        # Interactive-full mode (was "chat"): human viewer routes to
        # save or back to placement.
        builder.add_conditional_edges("node_human_viewer", route_after_human)
        builder.add_edge("node_save_to_rag", END)

    return builder.compile(checkpointer=memory), memory


def build_chat_graph():
    """Build the chat-bot LangGraph with intent-based routing."""
    memory = MemorySaver()
    builder = StateGraph(LayoutState)

    builder.add_node("router", _node_router)
    builder.add_node("topology_analyst", node_topology_analyst)
    builder.add_node("strategy_selector", node_strategy_selector)
    builder.add_node("placement_specialist", node_placement_specialist_chatbot)
    builder.add_node("drc_critic", node_drc_critic)
    builder.add_node("routing_previewer", node_routing_previewer)
    builder.add_node("human_viewer", node_human_viewer)

    builder.add_edge(START, "router")
    builder.add_conditional_edges(
        "router",
        _route_after_router,
        {
            "topology_analyst": "topology_analyst",
            "strategy_selector": "strategy_selector",
            "placement_specialist": "placement_specialist",
            "drc_critic": "drc_critic",
            "routing_previewer": "routing_previewer",
        },
    )
    builder.add_edge("topology_analyst", "human_viewer")
    builder.add_edge("strategy_selector", "human_viewer")
    builder.add_edge("placement_specialist", "human_viewer")
    builder.add_edge("drc_critic", "human_viewer")
    builder.add_edge("routing_previewer", "human_viewer")
    builder.add_edge("human_viewer", END)

    return builder.compile(checkpointer=memory), memory

def build_session_chat_graph():
    """Build the session chatbot LangGraph with two-tier routing.

    Graph shape::

        START
          → node_session_chat
          → conditional route_after_session_chat

        answer_only / clarify → node_session_finalizer → END
        command_edit          → node_command_validator → node_human_viewer → END
        need_topology         → node_topology_analyst  → node_session_finalizer → END
        need_strategy         → node_strategy_selector → node_session_finalizer → END
        need_placement        → node_placement_specialist → node_command_validator → node_human_viewer → END
        need_drc              → node_drc_checker      → node_session_finalizer → END
        fix_drc               → node_drc_critic        → conditional → validator → viewer → END
        need_routing          → node_routing_previewer  → node_session_finalizer → END
        fix_routing           → node_routing_previewer  → node_session_finalizer → END

    Post-human-viewer behavior (Fix 8 – documentation only):
        The ``node_human_viewer`` interrupts the graph and sends ``pending_cmds``
        to the GUI via ``visual_viewer_signal``.  The GUI's
        ``_on_visual_viewer_signal`` handler applies approved commands to the
        layout canvas.  The graph does **not** contain an apply-commands node;
        command application is entirely UI-side.  After the interrupt resumes
        (``Command(resume=...)``), the graph proceeds directly to ``END``.
    """
    memory = MemorySaver()
    builder = StateGraph(LayoutState)

    # ── Register nodes ──
    builder.add_node("node_session_chat",          node_session_chat)
    builder.add_node("node_session_finalizer",     node_session_finalizer)
    builder.add_node("node_command_validator",     node_command_validator)
    builder.add_node("node_topology_analyst",      node_topology_analyst)
    builder.add_node("node_strategy_selector",     node_strategy_selector)
    builder.add_node("node_placement_specialist",  node_placement_specialist)
    builder.add_node("node_drc_critic",            node_drc_critic)
    builder.add_node("node_drc_checker",           node_drc_checker)
    builder.add_node("node_routing_previewer",     node_routing_previewer)
    builder.add_node("node_human_viewer",          node_human_viewer)

    # ── Entry ──
    builder.add_edge(START, "node_session_chat")

    # ── Conditional fan-out after session chat ──
    builder.add_conditional_edges(
        "node_session_chat",
        route_after_session_chat,
        {
            "node_session_finalizer":    "node_session_finalizer",
            "node_command_validator":    "node_command_validator",
            "node_topology_analyst":     "node_topology_analyst",
            "node_strategy_selector":    "node_strategy_selector",
            "node_placement_specialist": "node_placement_specialist",
            "node_drc_checker":          "node_drc_checker",
            "node_drc_critic":           "node_drc_critic",
            "node_routing_previewer":    "node_routing_previewer",
        },
    )

    # ── answer_only / clarify → finalizer → END ──
    builder.add_edge("node_session_finalizer", END)

    # ── command_edit → validator → conditional → human viewer / finalizer ──
    builder.add_conditional_edges(
        "node_command_validator",
        route_after_command_validator,
        {
            "node_human_viewer":      "node_human_viewer",
            "node_session_finalizer": "node_session_finalizer",
        },
    )
    # ── human_viewer → END ──
    # NOTE (Fix 8): node_human_viewer interrupts the graph and sends
    # pending_cmds to the GUI via the visual_viewer_signal.  The GUI's
    # _on_visual_viewer_signal handler is responsible for applying the
    # approved commands to the layout canvas.  The session graph ends
    # here because command application is entirely UI-side — the graph
    # does NOT contain an "apply commands" node.  On resume, the graph
    # terminates at END.
    builder.add_edge("node_human_viewer", END)

    # ── Specialist → finalizer → END ──
    builder.add_edge("node_topology_analyst",     "node_session_finalizer")
    builder.add_edge("node_strategy_selector",    "node_session_finalizer")
    builder.add_edge("node_drc_checker",          "node_session_finalizer")   # read-only DRC
    builder.add_edge("node_routing_previewer",    "node_session_finalizer")

    # ── fix_drc → drc_critic → conditional → validator/viewer or finalizer ──
    builder.add_conditional_edges(
        "node_drc_critic",
        route_after_session_drc,
        {
            "node_command_validator":  "node_command_validator",
            "node_session_finalizer": "node_session_finalizer",
        },
    )

    # ── Placement specialist → validator → human viewer → END ──
    builder.add_edge("node_placement_specialist", "node_command_validator")

    return builder.compile(checkpointer=memory), memory


def build_layout_session_graph():
    """Build the AI-first layout session LangGraph (``chat_v2``).

    This graph replaces the deterministic keyword router with an AI-first
    agent that understands natural language and delegates to deterministic
    tools, command validators, and specialist agents.

    Graph shape::

        START
          → node_layout_session_agent
          → conditional route_after_layout_session_agent

        answer / clarify
          → node_session_finalizer → END

        call_deterministic_tool
          → node_deterministic_tool_runner
          → conditional route_after_deterministic_tool_runner
              propose_commands → node_command_validator → viewer/finalizer
              otherwise        → node_session_finalizer → END

        propose_commands
          → node_command_validator
          → conditional route_after_command_validator
              valid   → node_human_viewer → END
              invalid → node_session_finalizer → END

        call_specialist (topology_analyst)
          → node_topology_analyst → node_session_synthesizer → END

        call_specialist (strategy_selector)
          → node_strategy_selector → node_session_synthesizer → END

        call_specialist (placement_specialist)
          → node_placement_specialist → node_command_validator → viewer/finalizer

        check_drc
          → node_drc_checker → node_session_synthesizer → END

        fix_drc
          → node_drc_critic
          → conditional route_after_session_drc
              commands → node_command_validator → viewer/finalizer
              no cmds  → node_session_finalizer → END

        check_routing / optimize_routing
          → node_routing_previewer → node_session_synthesizer → END
    """
    memory = MemorySaver()
    builder = StateGraph(LayoutState)

    # ── Register nodes ──
    builder.add_node("node_layout_session_agent",    node_layout_session_agent)
    builder.add_node("node_deterministic_tool_runner", node_deterministic_tool_runner)
    builder.add_node("node_session_synthesizer",     node_session_synthesizer)
    builder.add_node("node_session_finalizer",       node_session_finalizer)
    builder.add_node("node_command_validator",       node_command_validator)
    builder.add_node("node_topology_analyst",        node_topology_analyst)
    builder.add_node("node_strategy_selector",       node_strategy_selector)
    builder.add_node("node_placement_specialist",    node_placement_specialist)
    builder.add_node("node_drc_critic",              node_drc_critic)
    builder.add_node("node_drc_checker",             node_drc_checker)
    builder.add_node("node_routing_previewer",       node_routing_previewer)
    builder.add_node("node_human_viewer",            node_human_viewer)

    # ── Entry ──
    builder.add_edge(START, "node_layout_session_agent")

    # ── Conditional fan-out after layout session agent ──
    builder.add_conditional_edges(
        "node_layout_session_agent",
        route_after_layout_session_agent,
        {
            "node_session_finalizer":       "node_session_finalizer",
            "node_deterministic_tool_runner": "node_deterministic_tool_runner",
            "node_command_validator":        "node_command_validator",
            "node_topology_analyst":         "node_topology_analyst",
            "node_strategy_selector":        "node_strategy_selector",
            "node_placement_specialist":     "node_placement_specialist",
            "node_drc_checker":              "node_drc_checker",
            "node_drc_critic":               "node_drc_critic",
            "node_routing_previewer":        "node_routing_previewer",
        },
    )

    # ── answer / clarify → finalizer → END ──
    builder.add_edge("node_session_finalizer", END)

    # ── call_deterministic_tool → tool runner → conditional ──
    builder.add_conditional_edges(
        "node_deterministic_tool_runner",
        route_after_deterministic_tool_runner,
        {
            "node_routing_previewer": "node_routing_previewer",
            "node_drc_checker": "node_drc_checker",
            "node_drc_critic": "node_drc_critic",
            "node_command_validator":  "node_command_validator",
            "node_session_finalizer": "node_session_finalizer",
        },
    )

    # ── propose_commands → validator → conditional → viewer / finalizer ──
    builder.add_conditional_edges(
        "node_command_validator",
        route_after_command_validator,
        {
            "node_human_viewer":      "node_human_viewer",
            "node_session_finalizer": "node_session_finalizer",
        },
    )
    builder.add_edge("node_human_viewer", END)

    # ── Specialist → synthesizer → END ──
    builder.add_edge("node_topology_analyst",  "node_session_synthesizer")
    builder.add_edge("node_strategy_selector", "node_session_synthesizer")
    builder.add_edge("node_drc_checker",       "node_session_synthesizer")
    builder.add_edge("node_routing_previewer", "node_session_synthesizer")
    builder.add_edge("node_session_synthesizer", END)

    # ── fix_drc → drc_critic → conditional → validator/viewer or finalizer ──
    builder.add_conditional_edges(
        "node_drc_critic",
        route_after_session_drc,
        {
            "node_command_validator":  "node_command_validator",
            "node_session_finalizer": "node_session_finalizer",
        },
    )

    # ── Placement specialist → validator → viewer/finalizer ──
    builder.add_edge("node_placement_specialist", "node_command_validator")

    return builder.compile(checkpointer=memory), memory


# ── Module-level exports (backward compatibility) ─────────────────────────
# All four graph builders and their compiled apps are exported at module
# level so that existing code using ``from ai_agent.graph.builder import app``
# continues to work without changes.
#
#   app                  — initial full-placement pipeline   (mode="initial")
#   chat_app             — legacy chatbot graph              (mode="legacy_chat")
#   session_chat_app     — session chatbot with routing      (mode="chat")
#   layout_session_app   — AI-first session agent            (mode="chat_v2")
#
app, _memory = build_layout_graph()
chat_app, _chat_memory = build_chat_graph()
session_chat_app, _session_chat_memory = build_session_chat_graph()
layout_session_app, _layout_session_memory = build_layout_session_graph()
