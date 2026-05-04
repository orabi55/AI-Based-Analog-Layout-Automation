# tests/schematic/conftest.py
"""
Pytest fixtures for schematic regression tests.
Requires QT_QPA_PLATFORM=offscreen for headless CI.
"""
import os
import sys
import pytest

# Make sure the project root is on the path
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_EXAMPLES = os.path.join(os.path.dirname(__file__), "examples")


def _parse_spice(path: str) -> dict[str, dict[str, str]]:
    """
    Minimal SPICE parser: returns terminal_nets dict
    {device_id: {"D": net, "G": net, "S": net}}.
    Also returns nodes list [{id, type, terminal_nets}].
    """
    nodes = []
    terminal_nets = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("*") or line.startswith("."):
                continue
            tokens = line.split()
            if len(tokens) >= 6 and tokens[0].upper().startswith("M"):
                dev_id = tokens[0]
                d_net, g_net, s_net = tokens[1], tokens[2], tokens[3]
                # tokens[4] = bulk/body terminal, tokens[5] = model name
                dev_type_raw = tokens[5].lower()
                dev_type = "pmos" if "p" in dev_type_raw else "nmos"
                tn = {"D": d_net, "G": g_net, "S": s_net}
                terminal_nets[dev_id] = tn
                nodes.append({
                    "id": dev_id,
                    "type": dev_type,
                    "terminal_nets": tn,
                    "electrical": {},
                })
    return nodes, terminal_nets


@pytest.fixture(scope="session")
def comparator_data():
    return _parse_spice(os.path.join(_EXAMPLES, "dynamic_comparator.sp"))


@pytest.fixture(scope="session")
def ota_data():
    return _parse_spice(os.path.join(_EXAMPLES, "five_t_ota.sp"))


@pytest.fixture(scope="session")
def mirror_data():
    return _parse_spice(os.path.join(_EXAMPLES, "cm_basic.sp"))


@pytest.fixture(scope="session")
def qt_app():
    """Shared QApplication for all GUI tests."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    return app
