"""
Device Tree Panel — left sidebar showing device hierarchy and
terminal connectivity.
"""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont

from icons import icon_panel_toggle


class DeviceTreePanel(QWidget):
    """Left panel showing devices, hierarchy, and terminal connectivity."""

    device_selected = Signal(str)
    connection_selected = Signal(str, str, str)  # (dev_id, net_name, other_dev_id)
    toggle_requested = Signal()  # emitted when the user clicks the panel-toggle button

    def __init__(self, parent=None):
        super().__init__(parent)
        self._terminal_nets = {}  # dev_id -> {"D": net, "G": net, "S": net}
        self._edges = []
        self._conn_map = {}  # dev_id -> [(other_id, net), ...]
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QFrame()
        header.setFixedHeight(48)
        header.setStyleSheet(
            "background-color: #1a1f2b;"
            "border-bottom: 1px solid #2d3548;"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 0, 14, 0)
        title = QLabel("📋 Device Hierarchy")
        title.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
        title.setStyleSheet("color: #c8d0dc;")
        header_layout.addWidget(title)

        header_layout.addStretch()

        # Panel toggle button
        toggle_btn = QPushButton()
        toggle_btn.setIcon(icon_panel_toggle())
        toggle_btn.setFixedSize(28, 28)
        toggle_btn.setToolTip("Hide panel")
        toggle_btn.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: rgba(255,255,255,0.12);
            }
            QPushButton:pressed {
                background-color: rgba(255,255,255,0.20);
            }
            """
        )
        toggle_btn.clicked.connect(self.toggle_requested.emit)
        header_layout.addWidget(toggle_btn)

        layout.addWidget(header)

        # Tree widget
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(20)
        self.tree.setAnimated(True)
        self.tree.setStyleSheet(
            """
            QTreeWidget {
                background-color: #111621;
                border: none;
                color: #c0cad8;
                font-family: 'Segoe UI', sans-serif;
                font-size: 12px;
                padding: 6px;
            }
            QTreeWidget::item {
                padding: 5px 8px;
                border-radius: 6px;
                margin: 1px 3px;
            }
            QTreeWidget::item:hover {
                background-color: #1e2a3a;
            }
            QTreeWidget::item:selected {
                background-color: rgba(74, 144, 217, 0.25);
                color: #ffffff;
            }
            QTreeWidget::branch {
                background-color: #111621;
            }
            QTreeWidget::branch:has-children:!has-siblings:closed,
            QTreeWidget::branch:closed:has-children:has-siblings {
                image: none;
                border-image: none;
            }
            QTreeWidget::branch:open:has-children:!has-siblings,
            QTreeWidget::branch:open:has-children:has-siblings {
                image: none;
                border-image: none;
            }
            QScrollBar:vertical {
                width: 8px;
                background: transparent;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #2d3548;
                border-radius: 4px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: #3d5066;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            """
        )
        self.tree.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.tree)

    def set_edges(self, edges):
        """Store edge data and build connectivity lookup."""
        self._edges = edges or []
        self._conn_map.clear()
        for edge in self._edges:
            src = edge.get("source")
            tgt = edge.get("target")
            net = edge.get("net", "")
            if src and tgt:
                self._conn_map.setdefault(src, []).append((tgt, net))
                self._conn_map.setdefault(tgt, []).append((src, net))

    def set_terminal_nets(self, terminal_nets):
        """Store terminal-to-net mapping per device.
        terminal_nets: {dev_id: {'D': net, 'G': net, 'S': net}}
        """
        self._terminal_nets = terminal_nets or {}

    def load_devices(self, nodes):
        """Populate tree from the placement JSON nodes."""
        self.tree.clear()

        # Classify devices
        nmos_real, nmos_dummy = [], []
        pmos_real, pmos_dummy = [], []
        for node in nodes:
            dev_type = node.get("type", "unknown")
            is_dummy = node.get("is_dummy", False) or str(node.get("id", "")).upper().startswith("DUMMY")
            if dev_type == "nmos":
                (nmos_dummy if is_dummy else nmos_real).append(node)
            elif dev_type == "pmos":
                (pmos_dummy if is_dummy else pmos_real).append(node)

        # NMOS group
        all_nmos = nmos_real + nmos_dummy
        if all_nmos:
            nmos_root = QTreeWidgetItem(
                self.tree, [f"⬜ NMOS Devices ({len(all_nmos)})"]
            )
            nmos_root.setFont(0, QFont("Segoe UI", 11, QFont.Weight.Bold))
            nmos_root.setForeground(0, QColor("#7ec8e3"))
            for dev in nmos_real:
                self._add_device_item(nmos_root, dev)
            if nmos_dummy:
                dummy_n_root = QTreeWidgetItem(
                    nmos_root, [f"🟪 Dummy NMOS ({len(nmos_dummy)})"]
                )
                dummy_n_root.setFont(0, QFont("Segoe UI", 10, QFont.Weight.DemiBold))
                dummy_n_root.setForeground(0, QColor("#d14d94"))
                for dev in nmos_dummy:
                    self._add_device_item(dummy_n_root, dev)
                dummy_n_root.setExpanded(True)
            nmos_root.setExpanded(True)

        # PMOS group
        all_pmos = pmos_real + pmos_dummy
        if all_pmos:
            pmos_root = QTreeWidgetItem(
                self.tree, [f"⬜ PMOS Devices ({len(all_pmos)})"]
            )
            pmos_root.setFont(0, QFont("Segoe UI", 11, QFont.Weight.Bold))
            pmos_root.setForeground(0, QColor("#e87474"))
            for dev in pmos_real:
                self._add_device_item(pmos_root, dev)
            if pmos_dummy:
                dummy_p_root = QTreeWidgetItem(
                    pmos_root, [f"🟪 Dummy PMOS ({len(pmos_dummy)})"]
                )
                dummy_p_root.setFont(0, QFont("Segoe UI", 10, QFont.Weight.DemiBold))
                dummy_p_root.setForeground(0, QColor("#d14d94"))
                for dev in pmos_dummy:
                    self._add_device_item(dummy_p_root, dev)
                dummy_p_root.setExpanded(True)
            pmos_root.setExpanded(True)

    def _add_device_item(self, parent, dev):
        """Add a device and its terminal connections as tree items."""
        dev_id = dev.get("id", "unknown")
        elec = dev.get("electrical", {})
        info = f"🔷 {dev_id}  (nf={elec.get('nf', 1)}, nfin={elec.get('nfin', '?')})"
        item = QTreeWidgetItem(parent, [info])
        item.setData(0, Qt.ItemDataRole.UserRole, dev_id)
        item.setFont(0, QFont("Segoe UI", 11))

        term_nets = self._terminal_nets.get(dev_id, {})
        connections = self._conn_map.get(dev_id, [])

        # Build net -> [connected devices] map
        net_to_devs = {}
        for other_id, net in connections:
            net_to_devs.setdefault(net, []).append(other_id)

        # Show each terminal with its net and connected devices
        for term_label, term_key, icon in [
            ("Gate", "G", "🟦"),
            ("Drain", "D", "🟩"),
            ("Source", "S", "🟨"),
        ]:
            net_name = term_nets.get(term_key, "?")
            connected = net_to_devs.get(net_name, [])
            if connected:
                devs_str = ", ".join(connected)
                text = f"{icon} {term_label} ({net_name}) → {devs_str}"
            else:
                text = f"{icon} {term_label} ({net_name})"

            sub = QTreeWidgetItem(item, [text])
            sub.setForeground(0, QColor("#8899aa"))
            sub.setFont(0, QFont("Segoe UI", 10))
            # Store data for click-to-highlight
            sub.setData(0, Qt.ItemDataRole.UserRole, None)  # not a device
            sub.setData(0, Qt.ItemDataRole.UserRole + 1, dev_id)
            sub.setData(0, Qt.ItemDataRole.UserRole + 2, net_name)

    def highlight_device(self, dev_id):
        """Highlight the tree item matching the given device id (recursive)."""
        self.tree.blockSignals(True)
        self.tree.clearSelection()

        def _search(parent):
            for i in range(parent.childCount()):
                child = parent.child(i)
                if child.data(0, Qt.ItemDataRole.UserRole) == dev_id:
                    child.setSelected(True)
                    self.tree.scrollToItem(child)
                    return True
                if _search(child):
                    return True
            return False

        _search(self.tree.invisibleRootItem())
        self.tree.blockSignals(False)

    def _on_item_clicked(self, item, column):
        dev_id = item.data(0, Qt.ItemDataRole.UserRole)
        if dev_id:
            # Device item clicked
            self.device_selected.emit(dev_id)
        else:
            # Connection sub-item clicked
            parent_dev = item.data(0, Qt.ItemDataRole.UserRole + 1)
            net_name = item.data(0, Qt.ItemDataRole.UserRole + 2)
            if parent_dev and net_name:
                self.device_selected.emit(parent_dev)
                self.connection_selected.emit(parent_dev, net_name, "")
