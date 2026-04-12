"""
Device Tree Panel — left sidebar showing device hierarchy and
terminal connectivity.
"""

import re

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
    block_selected = Signal(str)  # (instance_name) — emitted when block group clicked
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

    def load_devices(self, nodes, blocks=None):
        """Populate tree from the placement JSON nodes.

        Devices are grouped hierarchically:
          Level 1: Parent device (e.g. MM3, MM6)
          Level 2: Multiplier/array children (e.g. MM3_1..MM3_8, or MM9<1>..MM9<7>)
          Level 3: Finger children (e.g. MM3_1_f1..MM3_1_fN)

        Single devices (m=1, nf=1, no array) appear directly without nesting.

        Args:
            nodes: list of node dicts
            blocks: optional {inst: {"subckt": str, "devices": [str, ...]}}
        """
        self.tree.clear()

        # ── Block groups (if available) ──
        if blocks:
            block_colors = [
                "#ffa500", "#00bfff", "#32cd32", "#ff69b4",
                "#8a2be2", "#ffd700", "#00ced1", "#ff6347",
            ]
            blocks_root = QTreeWidgetItem(
                self.tree, [f"🧩 Blocks ({len(blocks)})"]
            )
            blocks_root.setFont(0, QFont("Segoe UI", 11, QFont.Weight.Bold))
            blocks_root.setForeground(0, QColor("#e0c97f"))

            for idx, (inst_name, info) in enumerate(blocks.items()):
                subckt = info.get("subckt", "?")
                devices = info.get("devices", [])
                color = block_colors[idx % len(block_colors)]

                block_item = QTreeWidgetItem(
                    blocks_root,
                    [f"📦 {inst_name}: {subckt}  ({len(devices)} devices)"]
                )
                block_item.setFont(0, QFont("Segoe UI", 10, QFont.Weight.DemiBold))
                block_item.setForeground(0, QColor(color))
                # Store block instance name for click handling
                block_item.setData(0, Qt.ItemDataRole.UserRole, None)
                block_item.setData(0, Qt.ItemDataRole.UserRole + 3, inst_name)

                # Add member devices under this block
                for dev_id in devices:
                    dev_node = next(
                        (n for n in nodes if n.get("id") == dev_id), None
                    )
                    if dev_node:
                        self._add_device_item(block_item, dev_node)

                block_item.setExpanded(True)

            blocks_root.setExpanded(True)

        # ── Group devices by parent (hierarchical grouping) ──
        parent_groups = self._group_by_parent(nodes)

        # ── Separate into NMOS / PMOS ──
        nmos_groups, pmos_groups = {}, {}
        for parent_name, (children, meta) in parent_groups.items():
            if children:
                dev_type = children[0].get("type", "nmos")
            else:
                dev_type = "nmos"
            if dev_type == "pmos":
                pmos_groups[parent_name] = (children, meta)
            else:
                nmos_groups[parent_name] = (children, meta)

        # NMOS group
        if nmos_groups:
            nmos_root = QTreeWidgetItem(
                self.tree, [f"⬜ NMOS Devices ({len(nmos_groups)} groups)"]
            )
            nmos_root.setFont(0, QFont("Segoe UI", 11, QFont.Weight.Bold))
            nmos_root.setForeground(0, QColor("#7ec8e3"))
            for parent_name in sorted(nmos_groups.keys()):
                children, meta = nmos_groups[parent_name]
                self._add_hierarchy_group(nmos_root, parent_name, children, meta)
            nmos_root.setExpanded(True)

        # PMOS group
        if pmos_groups:
            pmos_root = QTreeWidgetItem(
                self.tree, [f"⬜ PMOS Devices ({len(pmos_groups)} groups)"]
            )
            pmos_root.setFont(0, QFont("Segoe UI", 11, QFont.Weight.Bold))
            pmos_root.setForeground(0, QColor("#e87474"))
            for parent_name in sorted(pmos_groups.keys()):
                children, meta = pmos_groups[parent_name]
                self._add_hierarchy_group(pmos_root, parent_name, children, meta)
            pmos_root.setExpanded(True)

    # ── Hierarchical grouping helpers ─────────────────────────────────

    def _group_by_parent(self, nodes):
        """Group expanded finger nodes by their logical parent device.

        Each node's electrical dict contains:
          - parent: original device name (set by parse_mos on each child)
          - m: original multiplier count
          - array_size: not used anymore; array count = number of children
          - multiplier_index: m-level index (for array/multiplier copies)
          - finger_index: f-level index (for finger children)

        Returns dict: parent_name -> (child_nodes, meta_dict)
        """
        groups = {}
        for node in nodes:
            elec = node.get("electrical", {})
            parent = elec.get("parent")

            if parent:
                if parent not in groups:
                    groups[parent] = {
                        "children": [],
                        "m": elec.get("m", 1),
                        "type": node.get("type", "nmos"),
                    }
                groups[parent]["children"].append(node)
            else:
                # Standalone device (not expanded)
                name = node.get("id", "unknown")
                groups[name] = {
                    "children": [node],
                    "m": elec.get("m", 1),
                    "type": node.get("type", "nmos"),
                }

        return {
            name: (info["children"], {
                "m": info["m"],
                "type": info["type"],
            })
            for name, info in groups.items()
        }

    def _add_hierarchy_group(self, parent, name, children, meta):
        """Add a hierarchical device group to the tree.

        Display hierarchy:
          🔷 MM9 (array=8)          ← Level 0: parent
            ├── 🔸 MM9_m1 (nf=1)     ← Level 1: array/multiplier copy
            │   └── 🔹 MM9_m1        ← Level 2: finger (if nf>1)
            ├── 🔸 MM9_m2 (nf=1)
            └── ...

          🔷 MM6 (m=3, nf=5)        ← Level 0: parent
            ├── 🔸 MM6_m1 (nf=5)     ← Level 1: multiplier copy
            │   ├── 🔹 MM6_m1_f1     ← Level 2: fingers
            │   └── ...
            └── ...

          🔷 MM1 (nf=1, nfin=4)     ← Single device, no nesting
        """
        m = meta.get("m", 1)
        dev_type = meta.get("type", "nmos")
        total_children = len(children)

        # Determine structure from children's indices
        has_mult = any(
            n.get("electrical", {}).get("multiplier_index") is not None
            for n in children
        )
        has_finger = any(
            n.get("electrical", {}).get("finger_index") is not None
            for n in children
        )
        has_array = any(
            n.get("electrical", {}).get("array_index") is not None
            for n in children
        )

        # Compute m-level and f-level counts
        if has_mult:
            mult_indices = set()
            for n in children:
                mi = n.get("electrical", {}).get("multiplier_index")
                if mi is not None:
                    mult_indices.add(mi)
            m_count = len(mult_indices)
        else:
            m_count = 1

        if has_finger:
            # Count fingers per multiplier group
            fingers_per_mult = {}
            for n in children:
                mi = n.get("electrical", {}).get("multiplier_index", 0)
                fi = n.get("electrical", {}).get("finger_index")
                if fi is not None:
                    fingers_per_mult.setdefault(mi, set()).add(fi)
            # All multiplier groups should have the same finger count
            nf_per_mult = max(len(v) for v in fingers_per_mult.values()) if fingers_per_mult else 1
        else:
            nf_per_mult = 1

        # For finger-only devices (no m-level), nf = total_children
        if not has_mult:
            nf_per_mult = total_children

        # --- Single device ---
        if m_count == 1 and nf_per_mult == 1:
            assert len(children) == 1
            self._add_device_item(parent, children[0])
            return

        # --- Build parent label ---
        label_parts = [f"🔷 {name}"]
        if has_array and m_count > 1:
            label_parts.append(f"(array={m_count})")
        elif m_count > 1 and nf_per_mult > 1:
            label_parts.append(f"(m={m_count}, nf={nf_per_mult})")
        elif m_count > 1:
            label_parts.append(f"(m={m_count})")
        elif nf_per_mult > 1:
            label_parts.append(f"(nf={nf_per_mult})")

        parent_label = " ".join(label_parts)
        parent_item = QTreeWidgetItem(parent, [parent_label])
        parent_item.setFont(0, QFont("Segoe UI", 11, QFont.Weight.DemiBold))

        if dev_type == "pmos":
            parent_item.setForeground(0, QColor("#e87474"))
        else:
            parent_item.setForeground(0, QColor("#7ec8e3"))

        parent_item.setData(0, Qt.ItemDataRole.UserRole, None)
        parent_item.setData(0, Qt.ItemDataRole.UserRole + 1, children[0].get("id", ""))
        parent_item.setExpanded(False)

        # --- Sort children by multiplier index, then finger index ---
        def _sort_key(node):
            elec = node.get("electrical", {})
            mi = elec.get("multiplier_index") or 0
            fi = elec.get("finger_index") or 0
            return (mi, fi)
        children.sort(key=_sort_key)

        # --- Two-level tree: multiplier groups with finger children ---
        if m_count > 1 and nf_per_mult > 1:
            # Group children by multiplier index
            mult_groups = {}
            for child in children:
                elec = child.get("electrical", {})
                mi = elec.get("multiplier_index", 1)
                mult_groups.setdefault(mi, []).append(child)

            for mi in sorted(mult_groups.keys()):
                m_children = mult_groups[mi]
                m_children.sort(key=lambda n: n.get("electrical", {}).get("finger_index", 0))

                m_label = f"🔸 {name}_m{mi} (nf={len(m_children)})"
                m_item = QTreeWidgetItem(parent_item, [m_label])
                m_item.setFont(0, QFont("Segoe UI", 10, QFont.Weight.DemiBold))
                m_item.setForeground(0, QColor("#c0a060"))
                m_item.setData(0, Qt.ItemDataRole.UserRole, None)
                m_item.setData(0, Qt.ItemDataRole.UserRole + 1, m_children[0].get("id", ""))
                m_item.setExpanded(False)

                for child in m_children:
                    self._add_finger_item(m_item, child)

        # --- One-level tree: multiplier/array copies only ---
        elif m_count > 1 and nf_per_mult == 1:
            for child in children:
                self._add_device_item(parent_item, child)

        # --- One-level tree: fingers only ---
        elif m_count == 1 and nf_per_mult > 1:
            for child in children:
                self._add_device_item(parent_item, child)

    def _add_finger_item(self, parent, dev):
        """Add a finger device item with its terminal connections."""
        dev_id = dev.get("id", "unknown")
        elec = dev.get("electrical", {})
        nf = elec.get("nf", 1)
        nfin = elec.get("nfin", "?")
        info = f"🔹 {dev_id}  (nf={nf}, nfin={nfin})"
        item = QTreeWidgetItem(parent, [info])
        item.setData(0, Qt.ItemDataRole.UserRole, dev_id)
        item.setFont(0, QFont("Segoe UI", 11))

        self._add_terminal_connections(item, dev_id)

    def _add_device_item(self, parent, dev):
        """Add a device and its terminal connections as tree items."""
        dev_id = dev.get("id", "unknown")
        elec = dev.get("electrical", {})
        info = f"🔹 {dev_id}  (nf={elec.get('nf', 1)}, nfin={elec.get('nfin', '?')})"
        item = QTreeWidgetItem(parent, [info])
        item.setData(0, Qt.ItemDataRole.UserRole, dev_id)
        item.setFont(0, QFont("Segoe UI", 11))

        self._add_terminal_connections(item, dev_id)

    def _add_terminal_connections(self, item, dev_id):
        """Add terminal net and connection sub-items under a device item."""
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
            sub.setData(0, Qt.ItemDataRole.UserRole, None)
            sub.setData(0, Qt.ItemDataRole.UserRole + 1, dev_id)
            sub.setData(0, Qt.ItemDataRole.UserRole + 2, net_name)

    def highlight_device(self, dev_id):
        """Highlight the tree item matching the given device id (recursive)."""
        self.tree.blockSignals(True)
        self.tree.clearSelection()

        def _search(parent):
            for i in range(parent.childCount()):
                child = parent.child(i)
                # Check direct device role
                if child.data(0, Qt.ItemDataRole.UserRole) == dev_id:
                    child.setSelected(True)
                    self.tree.scrollToItem(child)
                    return True
                # Check fallback role (hierarchy groups)
                if child.data(0, Qt.ItemDataRole.UserRole + 1) == dev_id:
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
            # Check if this is a block group item
            block_inst = item.data(0, Qt.ItemDataRole.UserRole + 3)
            if block_inst:
                self.block_selected.emit(block_inst)
                return

            # Check if this is a hierarchy group item (parent/mult/array)
            # These store the first child's device id in UserRole+1
            fallback_dev = item.data(0, Qt.ItemDataRole.UserRole + 1)
            if fallback_dev:
                self.device_selected.emit(fallback_dev)
                return

            # Connection sub-item clicked
            parent_dev = item.data(0, Qt.ItemDataRole.UserRole + 1)
            net_name = item.data(0, Qt.ItemDataRole.UserRole + 2)
            if parent_dev and net_name:
                self.device_selected.emit(parent_dev)
                self.connection_selected.emit(parent_dev, net_name, "")

