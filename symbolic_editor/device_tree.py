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
    QTreeWidget,
    QTreeWidgetItem,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont


class DeviceTreePanel(QWidget):
    """Left panel showing devices, hierarchy, and terminal connectivity."""

    device_selected = Signal(str)
    connection_selected = Signal(str, str, str)  # (dev_id, net_name, other_dev_id)

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

        # Header with gradient
        header = QFrame()
        header.setFixedHeight(44)
        header.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #1e2a3a, stop:1 #2d3f54);"
            "border-bottom: 1px solid #4a90d9;"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 12, 0)
        title = QLabel("📋 Device Hierarchy")
        title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        title.setStyleSheet("color: #e0e8f0;")
        header_layout.addWidget(title)
        layout.addWidget(header)

        # Tree widget
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(18)
        self.tree.setAnimated(True)
        self.tree.setStyleSheet(
            """
            QTreeWidget {
                background-color: #1a2332;
                border: none;
                color: #c8d6e5;
                font-family: 'Segoe UI', sans-serif;
                font-size: 12px;
                padding: 4px;
            }
            QTreeWidget::item {
                padding: 4px 6px;
                border-radius: 3px;
                margin: 1px 2px;
            }
            QTreeWidget::item:hover {
                background-color: #2d3f54;
            }
            QTreeWidget::item:selected {
                background-color: #3a6fa0;
                color: white;
            }
            QTreeWidget::branch {
                background-color: #1a2332;
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
                width: 6px;
                background: transparent;
            }
            QScrollBar::handle:vertical {
                background: #3d5066;
                border-radius: 3px;
                min-height: 30px;
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

        # Group devices by type
        nmos_devices = []
        pmos_devices = []
        for node in nodes:
            dev_type = node.get("type", "unknown")
            if dev_type == "nmos":
                nmos_devices.append(node)
            elif dev_type == "pmos":
                pmos_devices.append(node)

        # NMOS group
        if nmos_devices:
            nmos_root = QTreeWidgetItem(
                self.tree, [f"⬜ NMOS Devices ({len(nmos_devices)})"]
            )
            nmos_root.setFont(0, QFont("Segoe UI", 11, QFont.Weight.Bold))
            nmos_root.setForeground(0, QColor("#7ec8e3"))
            for dev in nmos_devices:
                self._add_device_item(nmos_root, dev)
            nmos_root.setExpanded(True)

        # PMOS group
        if pmos_devices:
            pmos_root = QTreeWidgetItem(
                self.tree, [f"⬜ PMOS Devices ({len(pmos_devices)})"]
            )
            pmos_root.setFont(0, QFont("Segoe UI", 11, QFont.Weight.Bold))
            pmos_root.setForeground(0, QColor("#e87474"))
            for dev in pmos_devices:
                self._add_device_item(pmos_root, dev)
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
        """Highlight the tree item matching the given device id."""
        self.tree.blockSignals(True)
        self.tree.clearSelection()
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            group = root.child(i)
            for j in range(group.childCount()):
                child = group.child(j)
                if child.data(0, Qt.ItemDataRole.UserRole) == dev_id:
                    child.setSelected(True)
                    self.tree.scrollToItem(child)
                    self.tree.blockSignals(False)
                    return
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
