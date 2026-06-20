import pytest
import os
import shutil
from ai_agent.core.physical_cells import insert_guard_ring
from ai_agent.core.rag_migration import apply_rag_style_migration
from ai_agent.core.layout_ops import reconfigure_floorplan, shield_net
from ai_agent.pdks.loader import load_pdk


@pytest.fixture
def dummy_nodes():
    return [
        {
            "id": "MM0_f1",
            "type": "nmos",
            "net_s": "GND",
            "net_d": "net_x",
            "net_g": "clk",
            "geometry": {
                "x": 0.0,
                "y": 0.0,
                "width": 0.294,
                "height": 0.568,
                "orientation": "R0"
            }
        },
        {
            "id": "MM0_f2",
            "type": "nmos",
            "net_s": "net_x",
            "net_d": "VDD",
            "net_g": "clk",
            "geometry": {
                "x": 0.294,
                "y": 0.0,
                "width": 0.294,
                "height": 0.568,
                "orientation": "R0"
            }
        }
    ]


@pytest.fixture
def dummy_pdk():
    return {
        "fin_pitch_um": 0.014,
        "tap_width_um": 0.294,
        "tap_height_um": 0.568,
        "tap_max_distance_um": 2.5
    }


def test_insert_guard_ring(dummy_nodes, dummy_pdk):
    # Surround both nodes with ptap ring
    result = insert_guard_ring(
        dummy_nodes,
        pdk=dummy_pdk,
        group_node_ids=["MM0_f1", "MM0_f2"],
        ring_type="ptap",
        spacing_um=0.5
    )
    assert result.success
    assert result.changed
    
    # Check that guard ring tap nodes are added
    taps = [n for n in result.nodes if n.get("type") == "tap" and n.get("subtype") == "ptap"]
    assert len(taps) > 0
    
    # Confirm guard ring taps have correct keys
    for tap in taps:
        assert str(tap["id"]).startswith("GUARDRING_")
        assert tap["physical_only"] is True
        assert "x" in tap["geometry"]
        assert "y" in tap["geometry"]


def test_reconfigure_floorplan(dummy_nodes):
    # Add another node in a second row
    nodes = dummy_nodes + [
        {
            "id": "MM1_f1",
            "type": "pmos",
            "geometry": {
                "x": 0.0,
                "y": 0.8,
                "width": 0.294,
                "height": 0.568,
                "orientation": "R0"
            }
        }
    ]
    
    # Reconfigure heights and vertical row pitches
    result = reconfigure_floorplan(
        nodes,
        row_height=0.6,
        row_pitch=0.3
    )
    assert result.success
    assert result.changed
    
    # Row 0 should stay at 0.0, Row 1 should shift to 0.0 + 0.6 + 0.3 = 0.9
    mm0_f1 = next(n for n in result.nodes if n["id"] == "MM0_f1")
    mm1_f1 = next(n for n in result.nodes if n["id"] == "MM1_f1")
    
    assert mm0_f1["geometry"]["y"] == 0.0
    assert mm1_f1["geometry"]["y"] == 0.9


def test_shield_net_dummy(dummy_nodes):
    # Shield net clk which is connected to MM0_f1 and MM0_f2
    result = shield_net(
        dummy_nodes,
        net_name="clk",
        shield_type="dummy",
        width_um=0.294
    )
    assert result.success
    assert result.changed
    
    shields = [n for n in result.nodes if n.get("is_shield")]
    assert len(shields) == 4  # 2 shields per matched node (MM0_f1 left/right, MM0_f2 left/right)


def test_shield_net_empty_space(dummy_nodes):
    # Shield net clk by shifting adjacent layout cells to create spacing channels
    result = shield_net(
        dummy_nodes,
        net_name="clk",
        shield_type="empty_space",
        width_um=0.2
    )
    assert result.success
    assert result.changed
    
    # Confirm coordinate horizontal shifts
    mm0_f1 = next(n for n in result.nodes if n["id"] == "MM0_f1")
    mm0_f2 = next(n for n in result.nodes if n["id"] == "MM0_f2")
    
    # MM0_f1 is matching and shifted right by width_um = 0.2
    # MM0_f2 is matching and shifted right by another width_um = 0.2 + left spacing + right spacing
    assert mm0_f1["geometry"]["x"] == pytest.approx(0.2)
    assert mm0_f2["geometry"]["x"] == pytest.approx(0.294 + 0.6)  # 0.294 + shift (0.2 left shield + 0.2 right shield MM0_f1 + 0.2 left shield MM0_f2)


def test_rag_style_migration(dummy_pdk):
    # Cleanup any existing test DB directory
    test_db_path = os.path.join(os.getcwd(), "rag_examples_db")
    if os.path.exists(test_db_path):
        try:
            shutil.rmtree(test_db_path)
        except Exception:
            pass

    # Define a 4-finger dummy nodes structure to satisfy ABBA pattern
    four_nodes = [
        {
            "id": "MM0_f1",
            "type": "nmos",
            "geometry": {
                "x": 0.0,
                "y": 0.0,
                "width": 0.294,
                "height": 0.568,
                "orientation": "R0"
            }
        },
        {
            "id": "MM0_f2",
            "type": "nmos",
            "geometry": {
                "x": 0.294,
                "y": 0.0,
                "width": 0.294,
                "height": 0.568,
                "orientation": "R0"
            }
        },
        {
            "id": "MM0_f3",
            "type": "nmos",
            "geometry": {
                "x": 0.588,
                "y": 0.0,
                "width": 0.294,
                "height": 0.568,
                "orientation": "R0"
            }
        },
        {
            "id": "MM0_f4",
            "type": "nmos",
            "geometry": {
                "x": 0.882,
                "y": 0.0,
                "width": 0.294,
                "height": 0.568,
                "orientation": "R0"
            }
        }
    ]

    # Run style migration using ChromaDB query
    result = apply_rag_style_migration(
        four_nodes,
        pdk=dummy_pdk,
        style_query="current mirror matching ABBA",
        target_device_ids=["MM0_f1", "MM0_f2", "MM0_f3", "MM0_f4"]
    )
    assert result.success
    assert result.changed


def test_device_clean_display_name():
    from symbolic_editor.device_item import DeviceItem
    
    # NTAP / Vdd tap
    item = DeviceItem.__new__(DeviceItem)
    item.device_name = "TAP_VDD"
    item._is_tap = True
    item._is_dummy = False
    assert item._get_clean_display_name() == "Vdd"
    
    # GND tap
    item = DeviceItem.__new__(DeviceItem)
    item.device_name = "TAP_GND"
    item._is_tap = True
    item._is_dummy = False
    assert item._get_clean_display_name() == "Gnd"
    
    # Dummy device
    item = DeviceItem.__new__(DeviceItem)
    item.device_name = "DUMMY_2"
    item._is_tap = False
    item._is_dummy = True
    assert item._get_clean_display_name() == "D2"
    
    # Multiplier device
    item = DeviceItem.__new__(DeviceItem)
    item.device_name = "MM1_m1"
    item._is_tap = False
    item._is_dummy = False
    assert item._get_clean_display_name() == "MM1.m1"
    
    # Finger device
    item = DeviceItem.__new__(DeviceItem)
    item.device_name = "MM1_f2"
    item._is_tap = False
    item._is_dummy = False
    assert item._get_clean_display_name() == "MM1.f2"
    
    # Multiplier + Finger device
    item = DeviceItem.__new__(DeviceItem)
    item.device_name = "MM1_m1_f3"
    item._is_tap = False
    item._is_dummy = False
    assert item._get_clean_display_name() == "MM1.m1.f3"
