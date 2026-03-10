"""
ai_agent/rag_store.py
=====================
RAG Example Store — saves and retrieves successful layout examples
using ChromaDB as the vector database and sentence-transformers for embeddings.

Each stored example contains:
  - circuit fingerprint (device types, net connectivity summary)
  - final optimized placement (nodes with x, y positions)
  - scores (DRC violations, routing crossings, wire length)
  - timestamp

Usage:
    store = RAGStore()
    store.save_example(nodes, edges, terminal_nets, drc_result, routing_result)
    examples = store.retrieve_similar(nodes, terminal_nets, top_k=3)
"""

import json
import hashlib
from datetime import datetime
from pathlib import Path

import chromadb
from sentence_transformers import SentenceTransformer


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_DB_PATH      = str(Path(__file__).resolve().parent / "rag_examples_db")
_COLLECTION   = "layout_examples"
_EMBED_MODEL  = "all-MiniLM-L6-v2"   # small, fast, good quality


# ---------------------------------------------------------------------------
# RAGStore
# ---------------------------------------------------------------------------
class RAGStore:
    """Persistent vector store for successful analog layout placements."""

    def __init__(self, db_path: str = _DB_PATH):
        self._client     = chromadb.PersistentClient(path=db_path)
        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        self._embedder   = SentenceTransformer(_EMBED_MODEL)
        print(f"[RAG] Store ready — {self._collection.count()} example(s) in DB")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def save_example(
        self,
        nodes:          list,
        edges:          list,
        terminal_nets:  dict,
        drc_result:     dict,
        routing_result: dict,
        label:          str = "",
    ) -> str:
        """Save a completed layout run as a retrievable example.

        Args:
            nodes:          final placed device list (with geometry)
            edges:          net edges list
            terminal_nets:  {device_id: {gate, drain, source, bulk}} dict
            drc_result:     output of run_drc_check()
            routing_result: output of score_routing()
            label:          optional human label e.g. "xor_good_run_1"

        Returns:
            example_id (str)
        """
        fingerprint = _build_fingerprint(nodes, edges, terminal_nets)
        score       = _compute_score(drc_result, routing_result)

        # Only save if result is meaningful (at least DRC pass or low violations)
        drc_violations = len(drc_result.get("violations", []))

        doc = fingerprint  # text used for embedding & retrieval

        metadata = {
            "label":          label or f"example_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "timestamp":      datetime.now().isoformat(),
            "drc_violations": drc_violations,
            "routing_score":  routing_result.get("score", 99),
            "wire_length":    routing_result.get("total_wire_length", 99.0),
            "quality_score":  score,
            "device_count":   len([n for n in nodes if not n.get("is_dummy")]),
            "placement_json": json.dumps(_extract_placement(nodes)),
            "fingerprint":    fingerprint[:500],  # store first 500 chars for display
        }

        example_id = _make_id(fingerprint, metadata["timestamp"])

        self._collection.upsert(
            ids=[example_id],
            documents=[doc],
            metadatas=[metadata],
        )
        print(f"[RAG] Saved example '{metadata['label']}' "
              f"(DRC={drc_violations}, routing={metadata['routing_score']}, "
              f"wire={metadata['wire_length']:.3f}µm, score={score:.1f})")
        return example_id

    def retrieve_similar(
        self,
        nodes:         list,
        terminal_nets: dict,
        edges:         list = None,
        top_k:         int  = 3,
        max_violations: int = 5,
    ) -> list[dict]:
        """Find the most similar past examples to the current circuit.

        Args:
            nodes:          current device list (before placement)
            terminal_nets:  current net connectivity
            edges:          current edges (optional)
            top_k:          number of examples to retrieve
            max_violations: filter out examples with more DRC violations than this

        Returns:
            list of dicts with keys: label, placement, scores, fingerprint_preview
        """
        if self._collection.count() == 0:
            print("[RAG] No examples in DB yet — skipping retrieval")
            return []

        query_fingerprint = _build_fingerprint(nodes, edges or [], terminal_nets)
        embedding         = self._embedder.encode([query_fingerprint]).tolist()

        results = self._collection.query(
            query_embeddings=embedding,
            n_results=min(top_k * 2, self._collection.count()),  # over-fetch then filter
            include=["documents", "metadatas", "distances"],
        )

        examples = []
        for i, meta in enumerate(results["metadatas"][0]):
            if meta["drc_violations"] > max_violations:
                continue  # skip poor quality examples

            placement = json.loads(meta["placement_json"])
            examples.append({
                "label":             meta["label"],
                "timestamp":         meta["timestamp"],
                "drc_violations":    meta["drc_violations"],
                "routing_score":     meta["routing_score"],
                "wire_length":       meta["wire_length"],
                "quality_score":     meta["quality_score"],
                "similarity":        round(1 - results["distances"][0][i], 3),
                "placement":         placement,
                "fingerprint_preview": meta.get("fingerprint", "")[:300],
            })

            if len(examples) >= top_k:
                break

        print(f"[RAG] Retrieved {len(examples)} similar example(s)")
        return examples

    def count(self) -> int:
        """Return number of stored examples."""
        return self._collection.count()

    def list_examples(self) -> list[dict]:
        """List all stored examples with their metadata."""
        if self._collection.count() == 0:
            return []
        results = self._collection.get(include=["metadatas"])
        return [
            {
                "label":          m["label"],
                "timestamp":      m["timestamp"],
                "drc_violations": m["drc_violations"],
                "routing_score":  m["routing_score"],
                "wire_length":    m["wire_length"],
                "quality_score":  m["quality_score"],
                "device_count":   m["device_count"],
            }
            for m in results["metadatas"]
        ]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _build_fingerprint(nodes: list, edges: list, terminal_nets: dict) -> str:
    """Build a text description of the circuit for embedding.

    This is what gets embedded into the vector space — it captures:
    - device types and counts
    - net connectivity patterns
    - electrical parameters (W/L/nf)
    So similar circuits produce similar embeddings.
    """
    lines = []

    # Device summary
    nmos_devs = [n for n in nodes if n.get("type") == "nmos" and not n.get("is_dummy")]
    pmos_devs = [n for n in nodes if n.get("type") == "pmos" and not n.get("is_dummy")]
    lines.append(f"CIRCUIT: {len(nmos_devs)} NMOS, {len(pmos_devs)} PMOS devices")

    # Electrical parameters
    for n in nodes:
        if n.get("is_dummy"):
            continue
        elec = n.get("electrical", {})
        lines.append(
            f"DEVICE {n['id']} type={n.get('type','?')} "
            f"L={elec.get('l','?')} nf={elec.get('nf','?')} nfin={elec.get('nfin','?')}"
        )

    # Net connectivity
    net_map: dict = {}
    for e in edges:
        net = e.get("net", "?")
        net_map.setdefault(net, [])
        src, tgt = e.get("source",""), e.get("target","")
        if src and src not in net_map[net]:
            net_map[net].append(src)
        if tgt and tgt not in net_map[net]:
            net_map[net].append(tgt)

    for net, devs in sorted(net_map.items()):
        lines.append(f"NET {net}: {', '.join(sorted(devs))}")

    # Terminal nets
    for dev_id, nets in sorted(terminal_nets.items()):
        g = nets.get("gate","?")
        d = nets.get("drain","?")
        s = nets.get("source","?")
        lines.append(f"TERMINALS {dev_id}: gate={g} drain={d} source={s}")

    return "\n".join(lines)


def _extract_placement(nodes: list) -> list[dict]:
    """Extract just id + geometry from nodes for storage."""
    return [
        {
            "id":          n["id"],
            "type":        n.get("type", "?"),
            "is_dummy":    n.get("is_dummy", False),
            "x":           round(float(n["geometry"]["x"]), 4),
            "y":           round(float(n["geometry"]["y"]), 4),
            "orientation": n["geometry"].get("orientation", "R0"),
        }
        for n in nodes
    ]


def _compute_score(drc_result: dict, routing_result: dict) -> float:
    """Compute a single quality score (higher = better).

    Score = 100 - (drc_violations * 10) - (routing_crossings * 5) - (wire_length * 2)
    Clamped to [0, 100].
    """
    drc_v  = len(drc_result.get("violations", []))
    cross  = routing_result.get("score", 0)
    wire   = routing_result.get("total_wire_length", 0.0)
    score  = 100.0 - (drc_v * 10) - (cross * 5) - (wire * 2)
    return round(max(0.0, min(100.0, score)), 2)


def _make_id(fingerprint: str, timestamp: str) -> str:
    """Generate a stable unique ID from fingerprint + timestamp."""
    raw = fingerprint + timestamp
    return hashlib.md5(raw.encode()).hexdigest()[:16]