# -*- coding: utf-8 -*-
"""
Design hierarchy panel with Instances, Nets, and Groups tabs.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

try:
    from .icons import icon_panel_toggle
except ImportError:
    from icons import icon_panel_toggle


class DeviceTreePanel(QWidget):
    """Left sidebar showing design hierarchy, nets, and groups."""

    device_selected = Signal(str)
    connection_selected = Signal(str, str, str)
    block_selected = Signal(str)
    toggle_requested = Signal()
    net_view_toggled = Signal(bool)  # True when Nets tab is active

    def __init__(self, parent=None):
        super().__init__(parent)
        self._terminal_nets = {}
        self._edges = []
        self._conn_map = {}
        self._nodes = []
        self._blocks = {}
        self._active_tab = "instances"
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setFixedHeight(44)
        header.setStyleSheet(
            "background-color: #1a1f2b;"
            "border-bottom: 1px solid #2d3548;"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 12, 0)

        title = QLabel("Design Hierarchy")
        title.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        title.setStyleSheet("color: #d7dfeb;")
        header_layout.addWidget(title)
        header_layout.addStretch()

        toggle_btn = QPushButton()
        toggle_btn.setIcon(icon_panel_toggle())
        toggle_btn.setFixedSize(26, 26)
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

        tab_bar = QFrame()
        tab_bar.setFixedHeight(40)
        tab_bar.setStyleSheet(
            "background-color: #151a23; border-bottom: 1px solid #2d3548;"
        )
        tab_layout = QHBoxLayout(tab_bar)
        tab_layout.setContentsMargins(8, 4, 8, 4)
        tab_layout.setSpacing(4)

        tab_style = """
        QPushButton {
            background-color: transparent;
            color: #808896;
            border: none;
            border-radius: 6px;
            padding: 6px 16px;
            font-family: 'Segoe UI';
            font-size: 10pt;
            font-weight: 600;
        }
        QPushButton:hover {
            background-color: rgba(255,255,255,0.08);
            color: #a8b4c4;
        }
        QPushButton:checked {
            background-color: #2d4665;
            color: #ffffff;
        }
        """

        self.tab_instances = QPushButton("Instances")
        self.tab_instances.setCheckable(True)
        self.tab_instances.setChecked(True)
        self.tab_instances.setStyleSheet(tab_style)
        self.tab_instances.clicked.connect(lambda: self._switch_tab("instances"))
        tab_layout.addWidget(self.tab_instances)

        self.tab_nets = QPushButton("Nets")
        self.tab_nets.setCheckable(True)
        self.tab_nets.setStyleSheet(tab_style)
        self.tab_nets.clicked.connect(lambda: self._switch_tab("nets"))
        tab_layout.addWidget(self.tab_nets)

        self.tab_groups = QPushButton("Groups")
        self.tab_groups.setCheckable(True)
        self.tab_groups.setStyleSheet(tab_style)
        self.tab_groups.clicked.connect(lambda: self._switch_tab("groups"))
        tab_layout.addWidget(self.tab_groups)

        tab_layout.addStretch()
        layout.addWidget(tab_bar)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.tree.setAnimated(True)
        self.tree.setStyleSheet(
            """
            QTreeWidget {
                background-color: #0f1318;
                border: none;
                color: #b8c4d4;
                font-family: 'Segoe UI';
                font-size: 11px;
                padding: 4px;
            }
            QTreeWidget::item {
                padding: 4px 6px;
                border-radius: 4px;
                margin: 1px 2px;
            }
            QTreeWidget::item:hover {
                background-color: #1a2230;
            }
            QTreeWidget::item:selected {
                background-color: rgba(74, 144, 217, 0.30);
                color: #ffffff;
            }
            QTreeWidget::branch {
                background-color: #0f1318;
            }
            QTreeWidget::branch:has-children:!has-siblings:closed,
            QTreeWidget::branch:closed:has-children:has-siblings,
            QTreeWidget::branch:open:has-children:!has-siblings,
            QTreeWidget::branch:open:has-children:has-siblings {
                image: none;
                border-image: none;
            }
            QScrollBar:vertical {
                width: 6px;
                background: transparent;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #2d3548;
                border-radius: 3px;
                min-height: 24px;
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
        layout.addWidget(self.tree, 1)

    def _switch_tab(self, tab_name):
        self._active_tab = tab_name
        self.tab_instances.setChecked(tab_name == "instances")
        self.tab_nets.setChecked(tab_name == "nets")
        self.tab_groups.setChecked(tab_name == "groups")
        self.load_devices(self._nodes, blocks=self._blocks)
        self.net_view_toggled.emit(tab_name == "nets")

    def set_edges(self, edges):
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
        self._terminal_nets = terminal_nets or {}

    def load_devices(self, nodes, blocks=None):
        self.tree.clear()
        self._nodes = nodes or []
        self._blocks = blocks or {}

        if self._active_tab == "instances":
            self._populate_instances_tab()
        elif self._active_tab == "nets":
            self._populate_nets_tab()
        else:
            self._populate_groups_tab()

    def _populate_instances_tab(self):
        real_nmos = []
        real_pmos = []
        dummies = []
        passives = []

        for node in self._nodes:
            dev_id = str(node.get("id", ""))
            dev_type = str(node.get("type", "")).lower()
            is_dummy = node.get("is_dummy", False) or dev_id.upper().startswith("DUMMY")
            if is_dummy:
                dummies.append(node)
            elif dev_type == "pmos":
                real_pmos.append(node)
            elif dev_type == "nmos":
                real_nmos.append(node)
            else:
                passives.append(node)

        if real_nmos:
            root = QTreeWidgetItem(self.tree, [f"NMOS  |  {len(real_nmos)} devices"])
            root.setFont(0, QFont("Segoe UI", 10, QFont.Weight.Bold))
            root.setForeground(0, QColor("#7ec8e3"))
            for parent_name, (children, meta) in sorted(self._group_by_parent(real_nmos).items()):
                self._add_hierarchy_group(root, parent_name, children, meta)
            root.setExpanded(True)

        if real_pmos:
            root = QTreeWidgetItem(self.tree, [f"PMOS  |  {len(real_pmos)} devices"])
            root.setFont(0, QFont("Segoe UI", 10, QFont.Weight.Bold))
            root.setForeground(0, QColor("#e58a8a"))
            for parent_name, (children, meta) in sorted(self._group_by_parent(real_pmos).items()):
                self._add_hierarchy_group(root, parent_name, children, meta)
            root.setExpanded(True)

        if dummies:
            root = QTreeWidgetItem(self.tree, [f"Dummies  |  {len(dummies)} devices"])
            root.setFont(0, QFont("Segoe UI", 10, QFont.Weight.Bold))
            root.setForeground(0, QColor("#d28ac4"))
            for node in sorted(dummies, key=lambda item: item.get("id", "")):
                self._add_device_item(root, node)
            root.setExpanded(True)

        if passives:
            root = QTreeWidgetItem(self.tree, [f"Passives  |  {len(passives)} devices"])
            root.setFont(0, QFont("Segoe UI", 10, QFont.Weight.Bold))
            root.setForeground(0, QColor("#f0b772"))
            for node in sorted(passives, key=lambda item: item.get("id", "")):
                self._add_device_item(root, node)
            root.setExpanded(True)

    def _populate_nets_tab(self):
        all_nets = self._collect_all_nets()
        if not all_nets:
            return

        supply_nets = {"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS", "DVDD", "DVSS"}
        signal_nets = []
        power_nets = []

        for net_name, devices in sorted(all_nets.items()):
            if net_name.upper() in supply_nets:
                power_nets.append((net_name, devices))
            else:
                signal_nets.append((net_name, devices))

        if signal_nets:
            signals_root = QTreeWidgetItem(self.tree, [f"Signals  |  {len(signal_nets)}"])
            signals_root.setFont(0, QFont("Segoe UI", 10, QFont.Weight.Bold))
            signals_root.setForeground(0, QColor("#77aa77"))
            for net_name, devices in signal_nets:
                self._add_net_item(signals_root, net_name, devices)
            signals_root.setExpanded(True)

        if power_nets:
            power_root = QTreeWidgetItem(self.tree, [f"Power  |  {len(power_nets)}"])
            power_root.setFont(0, QFont("Segoe UI", 10, QFont.Weight.Bold))
            power_root.setForeground(0, QColor("#cc8844"))
            for net_name, devices in power_nets:
                self._add_net_item(power_root, net_name, devices)
            power_root.setExpanded(True)

    def _populate_groups_tab(self):
        if self._blocks:
            blocks_root = QTreeWidgetItem(self.tree, [f"Blocks  |  {len(self._blocks)}"])
            blocks_root.setFont(0, QFont("Segoe UI", 10, QFont.Weight.Bold))
            blocks_root.setForeground(0, QColor("#d9c279"))
            for block_name, info in sorted(self._blocks.items()):
                block_item = QTreeWidgetItem(
                    blocks_root,
                    [f"{block_name}  |  {len(info.get('devices', []))} devices"],
                )
                block_item.setFont(0, QFont("Segoe UI", 10, QFont.Weight.DemiBold))
                block_item.setForeground(0, QColor("#d9c279"))
                block_item.setData(0, Qt.ItemDataRole.UserRole, "__block__")
                block_item.setData(0, Qt.ItemDataRole.UserRole + 3, block_name)
                for dev_id in sorted(info.get("devices", [])):
                    child = QTreeWidgetItem(block_item, [f"  {dev_id}"])
                    child.setForeground(0, QColor("#93a4b7"))
                    child.setFont(0, QFont("Segoe UI", 9))
                    child.setData(0, Qt.ItemDataRole.UserRole, dev_id)
                block_item.setExpanded(False)
            blocks_root.setExpanded(True)

        parent_groups = {}
        for parent_name, (children, _meta) in self._group_by_parent(self._nodes).items():
            if len(children) > 1:
                parent_groups[parent_name] = [n.get("id", "") for n in children]

        if parent_groups:
            groups_root = QTreeWidgetItem(self.tree, [f"Device Groups  |  {len(parent_groups)}"])
            groups_root.setFont(0, QFont("Segoe UI", 10, QFont.Weight.Bold))
            groups_root.setForeground(0, QColor("#bc9ce0"))
            for group_name, dev_ids in sorted(parent_groups.items()):
                group_item = QTreeWidgetItem(
                    groups_root,
                    [f"{group_name}  |  {len(dev_ids)} devices"],
                )
                group_item.setFont(0, QFont("Segoe UI", 10, QFont.Weight.DemiBold))
                group_item.setForeground(0, QColor("#bc9ce0"))
                for dev_id in sorted(dev_ids):
                    child = QTreeWidgetItem(group_item, [f"  {dev_id}"])
                    child.setForeground(0, QColor("#9a8eb8"))
                    child.setFont(0, QFont("Segoe UI", 9))
                    child.setData(0, Qt.ItemDataRole.UserRole, dev_id)
                group_item.setExpanded(False)
            groups_root.setExpanded(True)

    def _group_by_parent(self, nodes):
        groups = {}
        for node in nodes:
            elec = node.get("electrical", {})
            parent = elec.get("parent")
            key = parent or node.get("id", "unknown")
            if key not in groups:
                groups[key] = {
                    "children": [],
                    "m": elec.get("m", 1),
                    "type": node.get("type", "nmos"),
                }
            groups[key]["children"].append(node)

        return {
            name: (info["children"], {"m": info["m"], "type": info["type"]})
            for name, info in groups.items()
        }

    def _add_hierarchy_group(self, parent, name, children, meta):
        m_count = meta.get("m", 1)
        dev_type = meta.get("type", "nmos")
        total_children = len(children)

        has_mult = any(
            n.get("electrical", {}).get("multiplier_index") is not None
            for n in children
        )
        has_finger = any(
            n.get("electrical", {}).get("finger_index") is not None
            for n in children
        )

        if has_mult:
            m_indices = {
                n.get("electrical", {}).get("multiplier_index")
                for n in children
                if n.get("electrical", {}).get("multiplier_index") is not None
            }
            m_groups = len(m_indices)
        else:
            m_groups = 1

        if has_finger:
            fingers_per_mult = {}
            for node in children:
                elec = node.get("electrical", {})
                mult_idx = elec.get("multiplier_index", 1)
                finger_idx = elec.get("finger_index")
                if finger_idx is not None:
                    fingers_per_mult.setdefault(mult_idx, set()).add(finger_idx)
            nf_per_group = max((len(vals) for vals in fingers_per_mult.values()), default=1)
        else:
            nf_per_group = total_children if not has_mult else 1

        if m_groups == 1 and nf_per_group == 1 and len(children) == 1:
            self._add_device_item(parent, children[0])
            return

        label = [name]
        if m_groups > 1 and nf_per_group > 1:
            label.append(f"m={m_groups}, nf={nf_per_group}")
        elif m_groups > 1:
            label.append(f"m={m_groups}")
        elif nf_per_group > 1:
            label.append(f"nf={nf_per_group}")

        parent_item = QTreeWidgetItem(parent, [f"{label[0]}  |  {label[1] if len(label) > 1 else 'group'}"])
        parent_item.setFont(0, QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        parent_item.setForeground(0, QColor("#e58a8a" if dev_type == "pmos" else "#7ec8e3"))
        parent_item.setData(0, Qt.ItemDataRole.UserRole, None)
        parent_item.setData(0, Qt.ItemDataRole.UserRole + 1, children[0].get("id", ""))
        parent_item.setExpanded(False)

        def sort_key(node):
            elec = node.get("electrical", {})
            return (
                elec.get("multiplier_index") or 0,
                elec.get("finger_index") or 0,
                node.get("id", ""),
            )

        children = sorted(children, key=sort_key)

        if m_groups > 1 and nf_per_group > 1:
            grouped = {}
            for child in children:
                mult_idx = child.get("electrical", {}).get("multiplier_index", 1)
                grouped.setdefault(mult_idx, []).append(child)
            for mult_idx, mult_children in sorted(grouped.items()):
                mult_item = QTreeWidgetItem(
                    parent_item,
                    [f"{name}_m{mult_idx}  |  {len(mult_children)} fingers"],
                )
                mult_item.setFont(0, QFont("Segoe UI", 9, QFont.Weight.DemiBold))
                mult_item.setForeground(0, QColor("#d5b46b"))
                mult_item.setData(0, Qt.ItemDataRole.UserRole, None)
                mult_item.setData(0, Qt.ItemDataRole.UserRole + 1, mult_children[0].get("id", ""))
                for child in mult_children:
                    self._add_device_item(mult_item, child)
        else:
            for child in children:
                self._add_device_item(parent_item, child)

    def _add_device_item(self, parent, node):
        dev_id = node.get("id", "unknown")
        elec = node.get("electrical", {})
        info = f"{dev_id}  |  nf={elec.get('nf', 1)}, nfin={elec.get('nfin', '?')}"
        item = QTreeWidgetItem(parent, [info])
        item.setData(0, Qt.ItemDataRole.UserRole, dev_id)
        item.setFont(0, QFont("Segoe UI", 9))
        dtype = str(node.get("type", "")).lower()
        if node.get("is_dummy") or dev_id.upper().startswith("DUMMY"):
            item.setForeground(0, QColor("#d28ac4"))
        elif dtype == "pmos":
            item.setForeground(0, QColor("#e58a8a"))
        elif dtype == "nmos":
            item.setForeground(0, QColor("#7ec8e3"))
        else:
            item.setForeground(0, QColor("#f0b772"))
        self._add_terminal_connections(item, dev_id)

    def _add_terminal_connections(self, item, dev_id):
        term_nets = self._terminal_nets.get(dev_id, {})
        connections = self._conn_map.get(dev_id, [])
        net_to_devs = {}
        for other_id, net in connections:
            net_to_devs.setdefault(net, []).append(other_id)

        for term_label, term_key in [("Gate", "G"), ("Drain", "D"), ("Source", "S")]:
            net_name = term_nets.get(term_key, "?")
            connected = net_to_devs.get(net_name, [])
            if connected:
                text = f"  {term_key}  |  {net_name} -> {', '.join(sorted(connected))}"
            else:
                text = f"  {term_key}  |  {net_name}"
            sub = QTreeWidgetItem(item, [text])
            sub.setForeground(0, QColor("#7f91a5"))
            sub.setFont(0, QFont("Segoe UI", 9))
            sub.setData(0, Qt.ItemDataRole.UserRole, None)
            sub.setData(0, Qt.ItemDataRole.UserRole + 1, dev_id)
            sub.setData(0, Qt.ItemDataRole.UserRole + 2, net_name)

    def _collect_all_nets(self):
        net_to_devices = {}
        for dev_id, terminals in self._terminal_nets.items():
            for _term, net_name in terminals.items():
                if net_name:
                    net_to_devices.setdefault(net_name, [])
                    if dev_id not in net_to_devices[net_name]:
                        net_to_devices[net_name].append(dev_id)
        return net_to_devices

    def _add_net_item(self, parent, net_name, devices):
        text = f"{net_name}  |  {len(devices)} devices"
        item = QTreeWidgetItem(parent, [text])
        item.setFont(0, QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        item.setForeground(0, QColor("#8fbc8f"))
        item.setData(0, Qt.ItemDataRole.UserRole, "__net__")
        item.setData(0, Qt.ItemDataRole.UserRole + 1, net_name)
        item.setData(0, Qt.ItemDataRole.UserRole + 4, sorted(devices))

        for dev_id in sorted(devices):
            term_nets = self._terminal_nets.get(dev_id, {})
            terms = [term for term, net in term_nets.items() if net == net_name]
            term_str = "/".join(terms) if terms else "?"
            child = QTreeWidgetItem(item, [f"  {dev_id}.{term_str}"])
            child.setFont(0, QFont("Segoe UI", 9))
            child.setForeground(0, QColor("#92b89b"))
            child.setData(0, Qt.ItemDataRole.UserRole, dev_id)
        item.setExpanded(False)

    def highlight_device(self, dev_id):
        self.tree.blockSignals(True)
        self.tree.clearSelection()

        def expand_ancestors(item):
            parent = item.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()

        def search(parent):
            for index in range(parent.childCount()):
                child = parent.child(index)
                if child.data(0, Qt.ItemDataRole.UserRole) == dev_id:
                    expand_ancestors(child)
                    child.setSelected(True)
                    self.tree.scrollToItem(child)
                    return True
                if child.data(0, Qt.ItemDataRole.UserRole + 1) == dev_id:
                    expand_ancestors(child)
                    child.setSelected(True)
                    self.tree.scrollToItem(child)
                    return True
                if search(child):
                    return True
            return False

        found = search(self.tree.invisibleRootItem())
        if not found and self._active_tab != "instances":
            self.tree.blockSignals(False)
            self._switch_tab("instances")
            self.tree.blockSignals(True)
            self.tree.clearSelection()
            search(self.tree.invisibleRootItem())
        self.tree.blockSignals(False)

    def _on_item_clicked(self, item, _column):
        role = item.data(0, Qt.ItemDataRole.UserRole)

        if role == "__block__":
            block_inst = item.data(0, Qt.ItemDataRole.UserRole + 3)
            if block_inst:
                self.block_selected.emit(block_inst)
            return

        if role == "__net__":
            net_name = item.data(0, Qt.ItemDataRole.UserRole + 1)
            devices = item.data(0, Qt.ItemDataRole.UserRole + 4) or []
            if net_name and devices:
                self.device_selected.emit(devices[0])
                self.connection_selected.emit(devices[0], net_name, "")
            item.setExpanded(not item.isExpanded())
            return

        if role:
            self.device_selected.emit(role)
            return

        parent_dev = item.data(0, Qt.ItemDataRole.UserRole + 1)
        net_name = item.data(0, Qt.ItemDataRole.UserRole + 2)
        if parent_dev:
            self.device_selected.emit(parent_dev)
            if net_name and net_name != "?":
                self.connection_selected.emit(parent_dev, net_name, "")
