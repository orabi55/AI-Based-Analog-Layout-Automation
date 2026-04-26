"""
Test suite for the AI Initial Placement Pipeline.
Tests each step independently so you can debug exactly where failures occur.

Usage:
    python -m pytest tests/test_placement_pipeline.py -v
    python tests/test_placement_pipeline.py            # run directly
"""
import sys, os, copy, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest

# ── Sample test data ──────────────────────────────────────────────────
def _make_test_nodes():
    """Create a minimal but realistic set of finger nodes for a comparator."""
    nodes = []
    # 4 NMOS fingers: MM1_f0..MM1_f3
    for i in range(4):
        nodes.append({
            "id": f"MM1_f{i}", "type": "nmos",
            "electrical": {"l": 1.4e-8, "nfin": 2, "w": 0},
            "geometry": {"x": i * 0.070, "y": 0.0, "width": 0.294, "height": 0.568, "orientation": "R0"},
        })
    # 4 NMOS fingers: MM2_f0..MM2_f3
    for i in range(4):
        nodes.append({
            "id": f"MM2_f{i}", "type": "nmos",
            "electrical": {"l": 1.4e-8, "nfin": 2, "w": 0},
            "geometry": {"x": 1.5 + i * 0.070, "y": 0.0, "width": 0.294, "height": 0.568, "orientation": "R0"},
        })
    # 4 PMOS fingers: MM3_f0..MM3_f3
    for i in range(4):
        nodes.append({
            "id": f"MM3_f{i}", "type": "pmos",
            "electrical": {"l": 1.4e-8, "nfin": 2, "w": 0},
            "geometry": {"x": i * 0.070, "y": 0.668, "width": 0.294, "height": 0.568, "orientation": "R0"},
        })
    # 4 PMOS fingers: MM4_f0..MM4_f3
    for i in range(4):
        nodes.append({
            "id": f"MM4_f{i}", "type": "pmos",
            "electrical": {"l": 1.4e-8, "nfin": 2, "w": 0},
            "geometry": {"x": 1.5 + i * 0.070, "y": 0.668, "width": 0.294, "height": 0.568, "orientation": "R0"},
        })
    return nodes

def _make_test_edges():
    return [
        {"source": "MM1_f0", "target": "MM3_f0", "net": "VOUTP"},
        {"source": "MM2_f0", "target": "MM4_f0", "net": "VOUTN"},
    ]

def _make_terminal_nets():
    nets = {}
    for i in range(4):
        nets[f"MM1_f{i}"] = {"G": "VINP", "D": "VOUTP", "S": "TAIL"}
        nets[f"MM2_f{i}"] = {"G": "VINN", "D": "VOUTN", "S": "TAIL"}
        nets[f"MM3_f{i}"] = {"G": "VOUTP", "D": "VOUTP", "S": "VDD"}
        nets[f"MM4_f{i}"] = {"G": "VOUTN", "D": "VOUTN", "S": "VDD"}
    return nets


class Test01_FingerGrouping(unittest.TestCase):
    """Step 1: Finger aggregation -> collapse fingers to logical groups."""

    def test_aggregate_edges_none(self):
        from ai_agent.placement.finger_grouper import aggregate_to_logical_devices
        nodes = _make_test_nodes()
        result = aggregate_to_logical_devices(nodes)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 4, f"Expected 4 groups, got {len(result)}")
        ids = {g["id"] for g in result}
        self.assertEqual(ids, {"MM1", "MM2", "MM3", "MM4"})
        print(f"  [PASS] aggregate (edges=None): {len(result)} groups")

    def test_aggregate_with_edges(self):
        from ai_agent.placement.finger_grouper import aggregate_to_logical_devices
        nodes = _make_test_nodes()
        edges = _make_test_edges()
        result = aggregate_to_logical_devices(nodes, edges)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 3)
        grp_nodes, grp_edges, finger_map = result
        self.assertEqual(len(grp_nodes), 4)
        self.assertGreater(len(grp_edges), 0)
        self.assertEqual(len(finger_map), 4)
        for gid, members in finger_map.items():
            self.assertEqual(len(members), 4, f"{gid} should have 4 fingers")
        print(f"  [PASS] aggregate (with edges): {len(grp_nodes)} groups, {len(grp_edges)} edges")

    def test_total_fingers_preserved(self):
        from ai_agent.placement.finger_grouper import aggregate_to_logical_devices
        nodes = _make_test_nodes()
        _, _, finger_map = aggregate_to_logical_devices(nodes, edges=[])
        total = sum(len(v) for v in finger_map.values())
        self.assertEqual(total, len(nodes), "All fingers must be in finger_map")
        print(f"  [PASS] finger conservation: {total} fingers preserved")


class Test02_MatchingDetection(unittest.TestCase):
    """Step 2: Matching detection — find symmetric pairs."""

    def test_detect_matching(self):
        from ai_agent.placement.finger_grouper import (
            aggregate_to_logical_devices, detect_matching_groups,
        )
        nodes = _make_test_nodes()
        grp_nodes, grp_edges, _ = aggregate_to_logical_devices(nodes, edges=_make_test_edges())
        info = detect_matching_groups(grp_nodes, grp_edges)
        self.assertIn("matched_pairs", info)
        self.assertIn("matched_clusters", info)
        # MM1+MM2 (same type, same params) should be matched
        # MM3+MM4 (same type, same params) should be matched
        self.assertGreaterEqual(len(info["matched_clusters"]), 2)
        print(f"  [PASS] matching: {len(info['matched_clusters'])} clusters, {len(info['matched_pairs'])} pairs")


class Test03_MergeMatchedGroups(unittest.TestCase):
    """Step 3: Merge matched groups into interdigitated blocks."""

    def test_merge(self):
        from ai_agent.placement.finger_grouper import (
            aggregate_to_logical_devices, detect_matching_groups, merge_matched_groups,
        )
        nodes = _make_test_nodes()
        edges = _make_test_edges()
        terminal_nets = _make_terminal_nets()
        grp_nodes, grp_edges, finger_map = aggregate_to_logical_devices(nodes, edges)
        matching_info = detect_matching_groups(grp_nodes, grp_edges)

        # Build group terminal nets
        group_terminal_nets = {}
        for gid, members in finger_map.items():
            g_nets = [terminal_nets.get(m["id"], {}).get("G", "") for m in members]
            d_nets = [terminal_nets.get(m["id"], {}).get("D", "") for m in members]
            s_nets = [terminal_nets.get(m["id"], {}).get("S", "") for m in members]
            group_terminal_nets[gid] = {
                "G": g_nets[0] if g_nets else "",
                "D": d_nets[0] if d_nets else "",
                "S": s_nets[0] if s_nets else "",
            }

        merged_nodes, merged_edges, merged_fmap, merged_blocks = merge_matched_groups(
            grp_nodes, grp_edges, finger_map,
            matching_info, group_terminal_nets, terminal_nets,
        )
        print(f"  [PASS] merge: {len(grp_nodes)} groups -> {len(merged_nodes)} after merge")
        print(f"         merged blocks: {list(merged_blocks.keys())}")
        # Verify total finger count >= original (merge may add edge guard dummies)
        total = sum(len(v) for v in merged_fmap.values())
        self.assertGreaterEqual(total, len(nodes),
                                f"Total fingers should be >= original: {total} vs {len(nodes)}")


class Test04_RowAssignment(unittest.TestCase):
    """Step 4: Row assignment — bin-pack into rows."""

    def test_pre_assign_rows(self):
        from ai_agent.placement.finger_grouper import (
            aggregate_to_logical_devices, pre_assign_rows,
        )
        nodes = _make_test_nodes()
        grp_nodes, _, _ = aggregate_to_logical_devices(nodes, edges=[])
        updated, summary = pre_assign_rows(grp_nodes)
        self.assertEqual(len(updated), len(grp_nodes))
        self.assertIsInstance(summary, str)

        # Check PMOS above NMOS
        nmos_ys = [n["geometry"]["y"] for n in updated if n["type"] == "nmos"]
        pmos_ys = [n["geometry"]["y"] for n in updated if n["type"] == "pmos"]
        if nmos_ys and pmos_ys:
            self.assertGreater(min(pmos_ys), max(nmos_ys),
                               "PMOS must be above NMOS")
        print(f"  [PASS] row assignment: NMOS y={nmos_ys}, PMOS y={pmos_ys}")
        print(f"         summary:\n{summary}")


class Test05_FingerExpansion(unittest.TestCase):
    """Step 5: Expand groups back to physical fingers."""

    def test_expand(self):
        from ai_agent.placement.finger_grouper import (
            aggregate_to_logical_devices, pre_assign_rows, expand_to_fingers,
        )
        nodes = _make_test_nodes()
        grp_nodes, _, finger_map = aggregate_to_logical_devices(nodes, edges=[])
        grp_nodes, _ = pre_assign_rows(grp_nodes)
        orig_lookup = {n["id"]: n for n in grp_nodes}
        expanded = expand_to_fingers(grp_nodes, finger_map, original_group_nodes=orig_lookup)
        
        # Filter out filler/edge dummies before checking conservation
        active_expanded = [n for n in expanded if not n.get("is_dummy")]
        
        self.assertEqual(len(active_expanded), len(nodes),
                         f"Active expanded {len(active_expanded)} != original {len(nodes)}")
        exp_ids = {n["id"] for n in active_expanded}
        orig_ids = {n["id"] for n in nodes}
        self.assertEqual(exp_ids, orig_ids, "Device IDs must match after expansion")
        print(f"  [PASS] expansion: {len(grp_nodes)} groups -> {len(expanded)} fingers")


class Test06_OverlapResolution(unittest.TestCase):
    """Step 6: Overlap resolution — no two devices at same position."""

    def test_no_overlaps_after_resolution(self):
        from ai_agent.tools.overlap_resolver import resolve_overlaps
        # Create overlapping devices
        nodes = [
            {"id": "A", "type": "nmos", "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.568}},
            {"id": "B", "type": "nmos", "geometry": {"x": 0.1, "y": 0.0, "width": 0.294, "height": 0.568}},
            {"id": "C", "type": "nmos", "geometry": {"x": 0.05, "y": 0.0, "width": 0.294, "height": 0.568}},
        ]
        moved = resolve_overlaps(nodes, log_details=False)
        self.assertGreater(len(moved), 0, "Should have moved overlapping devices")
        # Verify no overlaps remain
        nodes.sort(key=lambda n: n["geometry"]["x"])
        for i in range(len(nodes) - 1):
            end_i = nodes[i]["geometry"]["x"] + nodes[i]["geometry"]["width"]
            start_next = nodes[i + 1]["geometry"]["x"]
            self.assertGreaterEqual(start_next + 0.001, end_i,
                                    f"Overlap between {nodes[i]['id']} and {nodes[i+1]['id']}")
        print(f"  [PASS] overlap resolution: moved {len(moved)} devices, no overlaps remain")

    def test_cascaded_pileup(self):
        """3 devices all at x=0 — must all be resolved."""
        from ai_agent.tools.overlap_resolver import resolve_overlaps
        nodes = [
            {"id": f"D{i}", "type": "nmos",
             "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.568}}
            for i in range(5)
        ]
        moved = resolve_overlaps(nodes, log_details=False)
        xs = sorted(n["geometry"]["x"] for n in nodes)
        for i in range(len(xs) - 1):
            self.assertGreater(xs[i+1], xs[i] + 0.29,
                               f"Position {i} and {i+1} still overlap: {xs}")
        print(f"  [PASS] cascaded pileup: 5 devices at x=0 -> resolved to {xs}")


class Test07_DRCCheck(unittest.TestCase):
    """Step 7: DRC check — verify overlap and row-error detection."""

    def test_clean_placement(self):
        from ai_agent.agents.drc_critic import run_drc_check
        nodes = [
            {"id": "A", "type": "nmos", "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.568}},
            {"id": "B", "type": "nmos", "geometry": {"x": 0.5, "y": 0.0, "width": 0.294, "height": 0.568}},
            {"id": "C", "type": "pmos", "geometry": {"x": 0.0, "y": 0.668, "width": 0.294, "height": 0.568}},
        ]
        result = run_drc_check(nodes, gap_px=0.0)
        self.assertTrue(result["pass"], f"Clean placement should pass: {result.get('violations')}")
        print(f"  [PASS] clean placement: DRC passed")

    def test_overlap_detected(self):
        from ai_agent.agents.drc_critic import run_drc_check
        nodes = [
            {"id": "A", "type": "nmos", "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.568}},
            {"id": "B", "type": "nmos", "geometry": {"x": 0.1, "y": 0.0, "width": 0.294, "height": 0.568}},
        ]
        result = run_drc_check(nodes, gap_px=0.0)
        self.assertFalse(result["pass"])
        self.assertGreater(len(result["violations"]), 0)
        print(f"  [PASS] overlap detected: {len(result['violations'])} violation(s)")

    def test_row_error_detected(self):
        from ai_agent.agents.drc_critic import run_drc_check
        # PMOS below NMOS
        nodes = [
            {"id": "N1", "type": "nmos", "geometry": {"x": 0.0, "y": 0.668, "width": 0.294, "height": 0.568}},
            {"id": "P1", "type": "pmos", "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.568}},
        ]
        result = run_drc_check(nodes, gap_px=0.0)
        has_row_error = any("ROW" in v for v in result.get("violations", []))
        self.assertTrue(has_row_error, f"Should detect row error: {result.get('violations')}")
        print(f"  [PASS] row error detected when PMOS below NMOS")


class Test08_PrescriptiveFixes(unittest.TestCase):
    """Step 8: Prescriptive fixes — mechanical corrections."""

    def test_fixes_generated(self):
        from ai_agent.agents.drc_critic import run_drc_check, compute_prescriptive_fixes
        nodes = [
            {"id": "A", "type": "nmos", "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.568}},
            {"id": "B", "type": "nmos", "geometry": {"x": 0.1, "y": 0.0, "width": 0.294, "height": 0.568}},
        ]
        drc = run_drc_check(nodes, gap_px=0.0)
        fixes = compute_prescriptive_fixes(drc, gap_px=0.0, nodes=nodes)
        self.assertGreater(len(fixes), 0, "Should generate fixes for overlapping devices")
        for fix in fixes:
            self.assertIn("action", fix)
            self.assertIn("device", fix)
            self.assertIn("x", fix)
        print(f"  [PASS] prescriptive fixes: {len(fixes)} generated")


class Test09_CMDParsing(unittest.TestCase):
    """Step 9: CMD block parsing and application."""

    def test_parse_cmd_blocks(self):
        from ai_agent.tools.cmd_parser import extract_cmd_blocks
        text = '[CMD]{"action":"move","device":"MM1","x":0.5,"y":0.0}[/CMD]'
        cmds = extract_cmd_blocks(text)
        self.assertEqual(len(cmds), 1)
        self.assertEqual(cmds[0]["device"], "MM1")
        print(f"  [PASS] CMD parsing: parsed {len(cmds)} block(s)")

    def test_apply_cmds(self):
        from ai_agent.tools.cmd_parser import apply_cmds_to_nodes
        nodes = [{"id": "MM1", "type": "nmos",
                  "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.568, "orientation": "R0"}}]
        cmds = [{"action": "move", "device": "MM1", "x": 1.5, "y": 0.668}]
        result = apply_cmds_to_nodes(nodes, cmds)
        self.assertAlmostEqual(result[0]["geometry"]["x"], 1.5)
        print(f"  [PASS] CMD application: MM1 moved to x=1.5")


class Test10_DeviceConservation(unittest.TestCase):
    """Step 10: Device conservation — no devices lost or invented."""

    def test_conservation_pass(self):
        from ai_agent.tools.inventory import validate_device_count
        orig = [{"id": "A"}, {"id": "B"}, {"id": "C"}]
        prop = [{"id": "A"}, {"id": "B"}, {"id": "C"}]
        result = validate_device_count(orig, prop)
        self.assertTrue(result["pass"])
        print(f"  [PASS] conservation: all devices preserved")

    def test_conservation_fail(self):
        from ai_agent.tools.inventory import validate_device_count
        orig = [{"id": "A"}, {"id": "B"}, {"id": "C"}]
        prop = [{"id": "A"}, {"id": "B"}]  # missing C
        result = validate_device_count(orig, prop)
        self.assertFalse(result["pass"])
        self.assertIn("C", result["missing"])
        print(f"  [PASS] conservation failure detected: missing={result['missing']}")


class Test11_GraphCompilation(unittest.TestCase):
    """Step 11: LangGraph compilation — graph builds without errors."""

    def test_initial_mode(self):
        from ai_agent.graph.builder import build_layout_graph
        graph, memory = build_layout_graph("initial")
        self.assertIsNotNone(graph)
        print(f"  [PASS] graph compilation (initial mode)")

    def test_chat_mode(self):
        from ai_agent.graph.builder import build_layout_graph
        graph, memory = build_layout_graph("chat")
        self.assertIsNotNone(graph)
        print(f"  [PASS] graph compilation (chat mode)")


class Test12_EndToEndOverlaps(unittest.TestCase):
    """Step 12: End-to-end — full pipeline produces zero overlaps."""

    def test_full_pipeline_no_overlaps(self):
        from ai_agent.placement.finger_grouper import (
            aggregate_to_logical_devices, detect_matching_groups,
            merge_matched_groups, pre_assign_rows, expand_to_fingers,
        )
        from ai_agent.tools.overlap_resolver import resolve_overlaps
        from ai_agent.agents.drc_critic import run_drc_check

        nodes = _make_test_nodes()
        edges = _make_test_edges()
        terminal_nets = _make_terminal_nets()

        # Step 1: Aggregate
        grp_nodes, grp_edges, finger_map = aggregate_to_logical_devices(nodes, edges)
        # Step 2: Match
        matching = detect_matching_groups(grp_nodes, grp_edges)
        # Step 3: Build group terminal nets
        gtn = {}
        for gid, members in finger_map.items():
            gtn[gid] = {
                "G": terminal_nets.get(members[0]["id"], {}).get("G", ""),
                "D": terminal_nets.get(members[0]["id"], {}).get("D", ""),
                "S": terminal_nets.get(members[0]["id"], {}).get("S", ""),
            }
        # Step 4: Merge
        grp_nodes, grp_edges, finger_map, merged = merge_matched_groups(
            grp_nodes, grp_edges, finger_map, matching, gtn, terminal_nets,
        )
        # Step 5: Row assign
        grp_nodes, row_str = pre_assign_rows(grp_nodes, matching_info=matching, group_terminal_nets=gtn)
        # Step 6: Expand
        orig_lookup = {n["id"]: n for n in grp_nodes}
        expanded = expand_to_fingers(grp_nodes, finger_map, original_group_nodes=orig_lookup)
        # Step 7: Overlap resolution
        resolve_overlaps(expanded, log_details=False)
        # Step 8: DRC check
        drc = run_drc_check(expanded, gap_px=0.0)

        n_violations = len(drc.get("violations", []))
        if not drc["pass"]:
            print(f"  [INFO] DRC violations ({n_violations}):")
            for v in drc["violations"][:5]:
                print(f"    {v[:100]}")

        # We expect zero overlaps between same-type devices
        overlap_violations = [v for v in drc.get("violations", []) if "OVERLAP" in v]
        self.assertEqual(len(overlap_violations), 0,
                         f"No overlaps should remain: {overlap_violations}")
        print(f"  [PASS] end-to-end: {len(expanded)} devices, {n_violations} violations, 0 overlaps")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  AI PLACEMENT PIPELINE -- STEP-BY-STEP TEST SUITE")
    print("=" * 60 + "\n")
    unittest.main(verbosity=2)
