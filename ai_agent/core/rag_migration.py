"""
RAG-based Layout Migration/Adaptation
======================================
Replicates interdigitation or common-centroid layout styles using retrieved ChromaDB vectors.
Operates 100% offline using a deterministic keyword embedding function.
"""

from __future__ import annotations

import os
import copy
import logging
import chromadb
from chromadb.api.types import Documents, Embeddings

from ai_agent.core.interfaces import LayoutToolResult, wrap_tool
import ai_agent.core.common_centroid as _cc

logger = logging.getLogger("ai_agent")


class SimpleEmbeddingFunction(chromadb.EmbeddingFunction):
    """Deterministic, zero-dependency keyword embedding function.
    Runs 100% offline with zero network latency or model download overhead.
    """
    def __call__(self, input: Documents) -> Embeddings:
        vocab = [
            "current", "mirror", "interdigitated", "differential", "pair",
            "common", "centroid", "1d", "2d", "matrix", "abba", "baab", "abba/baab"
        ]
        embeddings = []
        for text in input:
            text_lower = text.lower()
            vec = []
            for w in vocab:
                vec.append(float(text_lower.count(w)))
            norm = sum(v*v for v in vec) ** 0.5
            if norm > 0:
                vec = [v / norm for v in vec]
            embeddings.append(vec)
        return embeddings


@wrap_tool
def apply_rag_style_migration(
    nodes: list,
    pdk: dict,
    style_query: str,
    target_device_ids: list,
) -> LayoutToolResult:
    """Query ChromaDB for the closest golden layout style and apply it to the target devices."""
    pdk = pdk or {}
    
    if not target_device_ids:
        return LayoutToolResult(
            success=False,
            message="RAG migration failed: target_device_ids is empty.",
            nodes=list(nodes),
        )

    # 1. Initialize local ChromaDB vector store in the workspace
    db_path = os.path.join(os.getcwd(), "rag_examples_db")
    try:
        client = chromadb.PersistentClient(path=db_path)
        embedding_func = SimpleEmbeddingFunction()
        collection = client.get_or_create_collection(
            name="layout_templates",
            embedding_function=embedding_func
        )
        
        # Seed database if empty
        if collection.count() == 0:
            collection.add(
                documents=[
                    "current mirror interdigitated placement ABBA",
                    "differential pair common centroid 1D placement ABBA",
                    "differential pair common centroid 2D matrix placement ABBA/BAAB"
                ],
                metadatas=[
                    {"style": "interdigitated", "pattern": "ABBA"},
                    {"style": "common_centroid", "pattern": "ABBA"},
                    {"style": "common_centroid_2d", "pattern": "ABBA/BAAB"}
                ],
                ids=["cm_interdigitated_seed", "diff_pair_cc_seed", "diff_pair_cc_2d_seed"]
            )
            
        # 2. Query database for closest template match
        results = collection.query(
            query_texts=[style_query],
            n_results=1
        )
        
        if not results or not results["metadatas"] or not results["metadatas"][0]:
            raise ValueError("No matching template found in ChromaDB.")
            
        match_meta = results["metadatas"][0][0]
        style = match_meta.get("style", "interdigitated")
        pattern = match_meta.get("pattern", "ABBA")
        doc_text = results["documents"][0][0]
        
    except Exception as exc:
        logger.error(f"[RAG Migration] ChromaDB failed: {exc}", exc_info=True)
        # Fallback in case of system-level DB failures
        style = "common_centroid"
        pattern = "ABBA"
        doc_text = f"Fallback (error: {exc})"

    # 3. Apply style migration
    nodes_copy = copy.deepcopy(nodes)
    id_map = {n["id"]: n for n in nodes_copy}
    
    # Filter target devices that actually exist
    valid_targets = [did for did in target_device_ids if did in id_map]
    if not valid_targets:
        return LayoutToolResult(
            success=False,
            message="RAG migration failed: none of the target devices exist in the layout.",
            nodes=list(nodes),
        )
        
    # Get current bounds / starting coordinates of target devices
    xs = [float(id_map[did]["geometry"]["x"]) for did in valid_targets if "geometry" in id_map[did]]
    ys = [float(id_map[did]["geometry"]["y"]) for did in valid_targets if "geometry" in id_map[did]]
    start_x = min(xs) if xs else 0.0
    row_y = ys[0] if ys else 0.0
    
    n_targets = len(valid_targets)
    
    # Split valid targets in half for Group A and Group B
    group_a_ids = valid_targets[:n_targets//2]
    group_b_ids = valid_targets[n_targets//2:]
    
    if not group_a_ids or not group_b_ids:
        # Not enough fingers to CC; place sequentially
        from ai_agent.core.layout_ops import place_sequence
        return place_sequence(nodes, row_y=row_y, device_ids=valid_targets, start_x=start_x)
        
    group_a = [id_map[did] for did in group_a_ids]
    group_b = [id_map[did] for did in group_b_ids]
    
    if style == "common_centroid_2d" and n_targets >= 4:
        # 2D common centroid placement
        devices = [
            {"id": "A", "fingers": len(group_a), "nodes": group_a},
            {"id": "B", "fingers": len(group_b), "nodes": group_b}
        ]
        result = _cc.place_common_centroid_2d(devices, start_x=start_x, row_y=row_y, pdk=pdk)
        if result.success:
            # Splicing back
            placed_ids = set(valid_targets)
            non_placed = [n for n in nodes_copy if n["id"] not in placed_ids]
            full_nodes = non_placed + result.nodes
            return LayoutToolResult(
                success=True,
                message=f"RAG migrated style: '{doc_text}' applied in 2D Centroid matrix.",
                changed=True,
                nodes=full_nodes,
                metrics={"rag_style": style, "pattern": pattern},
            )
            
    # Default/1D common centroid/interdigitated placement
    result = _cc.place_common_centroid(
        group_a, group_b,
        start_x=start_x,
        row_y=row_y,
        pdk=pdk,
        pattern=pattern
    )
    
    if result.success:
        return LayoutToolResult(
            success=True,
            message=f"RAG migrated style: '{doc_text}' applied in 1D Centroid pattern.",
            changed=True,
            nodes=nodes_copy,
            metrics={"rag_style": style, "pattern": pattern},
        )
        
    return LayoutToolResult(
        success=False,
        message=f"RAG style migration failed to apply layout: {result.message}",
        nodes=list(nodes),
    )
