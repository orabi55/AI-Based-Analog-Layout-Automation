"""
ai_agent/rag_indexer.py
=======================
RAG Indexer — seeds the ChromaDB vector store with your existing
layout JSON files so RAG works from the very first run.

Run this ONCE from your project root:
    python -m ai_agent.ai_chat_bot.rag_indexer

It will scan for all *_placement.json and *_layout_graph.json files
and add them to the RAG database as seed examples.

You can also call index_example_file() programmatically to add
individual files at any time.
"""

import json
import glob
from pathlib import Path

from ai_agent.ai_chat_bot.rag_store import RAGStore


# ---------------------------------------------------------------------------
# Seed files config — add more patterns here as you collect examples
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SEED_PATTERNS = [
    str(_PROJECT_ROOT / "*_placement.json"),
    str(_PROJECT_ROOT / "*initial*.json"),
    str(_PROJECT_ROOT / "examples" / "*.json"),   # future examples folder
]


# ---------------------------------------------------------------------------
# Index a single JSON file
# ---------------------------------------------------------------------------
def index_example_file(
    file_path:     str,
    store:         RAGStore = None,
    label:         str      = "",
    drc_violations: int     = 0,
    routing_score:  int     = 0,
    wire_length:    float   = 0.0,
) -> bool:
    """Load a layout JSON file and add it to the RAG store.

    The JSON must have at least a "nodes" key. "edges" is optional.

    Args:
        file_path:      path to the JSON file
        store:          RAGStore instance (created if None)
        label:          human-readable label for this example
        drc_violations: known DRC violations for this layout (0 = perfect)
        routing_score:  known routing crossings (0 = perfect)
        wire_length:    known total wire length in µm

    Returns:
        True if indexed successfully, False otherwise.
    """
    path = Path(file_path)
    if not path.exists():
        print(f"[INDEXER] File not found: {file_path}")
        return False

    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"[INDEXER] Failed to read {file_path}: {e}")
        return False

    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    if not nodes:
        print(f"[INDEXER] No nodes found in {file_path} — skipping")
        return False

    # Build terminal_nets from edges if not provided
    terminal_nets = _build_terminal_nets_from_edges(nodes, edges)

    # Build mock drc/routing results from provided scores
    drc_result = {
        "pass":       drc_violations == 0,
        "violations": ["placeholder"] * drc_violations,
    }
    routing_result = {
        "score":             routing_score,
        "total_wire_length": wire_length,
    }

    if store is None:
        store = RAGStore()

    file_label = label or path.stem
    example_id = store.save_example(
        nodes, edges, terminal_nets,
        drc_result, routing_result,
        label=file_label,
    )
    print(f"[INDEXER] ✅ Indexed '{file_label}' → id={example_id}")
    return True


# ---------------------------------------------------------------------------
# Seed all existing JSON files
# ---------------------------------------------------------------------------
def seed_from_project(store: RAGStore = None) -> int:
    """Scan project directory and index all layout JSON files found.

    Returns:
        Number of files successfully indexed.
    """
    if store is None:
        store = RAGStore()

    indexed = 0
    seen    = set()

    for pattern in _SEED_PATTERNS:
        for file_path in glob.glob(pattern):
            if file_path in seen:
                continue
            seen.add(file_path)

            label = Path(file_path).stem
            print(f"[INDEXER] Indexing: {file_path}")
            ok = index_example_file(
                file_path,
                store=store,
                label=label,
                # These are seed examples — assume decent quality
                drc_violations=0,
                routing_score=2,
                wire_length=3.0,
            )
            if ok:
                indexed += 1

    print(f"\n[INDEXER] Done — {indexed} file(s) indexed.")
    print(f"[INDEXER] Total examples in DB: {store.count()}")
    return indexed


# ---------------------------------------------------------------------------
# Helper: build terminal_nets from edges
# ---------------------------------------------------------------------------
def _build_terminal_nets_from_edges(nodes: list, edges: list) -> dict:
    """Infer terminal_nets dict from edge list.

    This is a best-effort reconstruction — not as accurate as reading
    from a SPICE netlist, but good enough for fingerprinting.
    """
    terminal_nets = {n["id"]: {} for n in nodes}

    for edge in edges:
        src = edge.get("source", "")
        tgt = edge.get("target", "")
        net = edge.get("net", "")
        if not net:
            continue
        # Heuristic: assign net to gate/drain/source based on net name
        role = _guess_terminal_role(net)
        if src in terminal_nets:
            terminal_nets[src].setdefault(role, net)
        if tgt in terminal_nets:
            terminal_nets[tgt].setdefault(role, net)

    return terminal_nets


def _guess_terminal_role(net_name: str) -> str:
    """Heuristic: guess if a net is gate, drain, or source."""
    n = net_name.lower()
    if any(k in n for k in ("vdd", "vss", "gnd", "tail", "source")):
        return "source"
    if any(k in n for k in ("out", "vout", "drain", "pun", "pdn")):
        return "drain"
    return "gate"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 50)
    print("RAG Indexer — Seeding example database")
    print("=" * 50)
    count = seed_from_project()
    if count == 0:
        print("\n⚠ No JSON files found to index.")
        print("  Place layout JSON files in your project root or ai_agent/examples/")
    else:
        print(f"\n✅ Successfully seeded {count} example(s) into RAG database.")
        print("   RAG is now active and will improve with each run!")
