from types import SimpleNamespace

import gdstk
import pytest

from ai_agent.matching.universal_pattern_generator import (
    SymmetryError,
    generate_placement_grid,
)
from parser.device_matcher import match_devices
from parser.layout_reader import extract_layout_instances_from_library


def test_orchestrated_request_propagates_selected_model(monkeypatch):
    from ai_agent.llm.workers import OrchestratorWorker

    seen = {}

    def fake_classify_intent(user_message, selected_model):
        seen["classifier_model"] = selected_model
        return "question"

    def fake_invoke_with_retry(messages, selected_model, task_weight, stage_tag):
        seen["reply_model"] = selected_model
        seen["task_weight"] = task_weight
        # Create a mock response object
        class MockResponse:
            content = "ok"
        return MockResponse()

    monkeypatch.setattr(
        "ai_agent.agents.classifier.classify_intent",
        fake_classify_intent,
    )
    monkeypatch.setattr(
        "ai_agent.graph.builder.classify_intent",
        fake_classify_intent,
    )
    monkeypatch.setattr(
        "ai_agent.nodes.general_chatbot._invoke_with_retry",
        fake_invoke_with_retry,
    )

    worker = OrchestratorWorker()
    replies = []
    worker.response_ready.connect(replies.append)
    worker.process_orchestrated_request("what is this?", "{}", [], "Alibaba", "light")

    assert replies == ["ok"]
    assert seen == {
        "classifier_model": "Alibaba",
        "reply_model": "Alibaba",
        "task_weight": "light",
    }


def test_common_centroid_2d_handles_small_even_groups():
    coords = generate_placement_grid(
        {"M0": 2, "M1": 2},
        "COMMON_CENTROID_2D",
        2,
    )

    grid = {(entry["x_index"], entry["y_index"]): entry["device"] for entry in coords}
    assert len(coords) == 4
    assert grid[(0, 0)] == "M0"
    assert grid[(1, 0)] == "M1"
    assert grid[(0, 1)] == "M1"
    assert grid[(1, 1)] == "M0"


def test_common_centroid_2d_rejects_odd_device_counts():
    with pytest.raises(SymmetryError):
        generate_placement_grid({"M0": 3, "M1": 1}, "COMMON_CENTROID_2D", 2)


def test_matcher_collapses_expanded_children_onto_shared_layout_instance():
    devices = [
        SimpleNamespace(
            name="MM0_f1",
            type="nmos",
            params={"parent": "MM0", "finger_index": 1},
        ),
        SimpleNamespace(
            name="MM0_f2",
            type="nmos",
            params={"parent": "MM0", "finger_index": 2},
        ),
    ]
    netlist = SimpleNamespace(devices={device.name: device for device in devices})
    layout_devices = [{"cell": "nfet_pcell_test", "x": 0.0, "y": 0.0}]

    mapping = match_devices(netlist, layout_devices)

    assert mapping == {"MM0_f1": 0, "MM0_f2": 0}


def test_package_import_of_main_window():
    from symbolic_editor.main import MainWindow

    assert MainWindow is not None


def test_layout_reader_can_return_reference_metadata_for_export():
    import tempfile
    import os

    # Create a dummy library with a cell and a reference
    lib = gdstk.Library()
    cell = lib.new_cell("TEST_CELL")
    ref_cell = lib.new_cell("nfet_pcell")
    ref_cell.add(gdstk.rectangle((0, 0), (1, 1)))
    cell.add(gdstk.Reference(ref_cell, (0, 0)))

    temp_fd, temp_path = tempfile.mkstemp(suffix=".oas")
    os.close(temp_fd)
    try:
        lib.write_oas(temp_path)
        read_lib = gdstk.read_oas(temp_path)
        devices = extract_layout_instances_from_library(read_lib, include_references=True)

        assert devices
        assert "reference" in devices[0]
        assert "parent_transform" in devices[0]
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
