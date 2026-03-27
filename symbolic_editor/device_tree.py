"""
Device Tree Panel — left sidebar showing design hierarchy with
Instances, Nets, and Groups sections.
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
    """Left panel showing design hierarchy: Instances, Nets, Groups."""

    device_selected = Signal(str)
    connection_selected = Signal(str, str, str)  # (dev_id, net_name, other_dev_id)
    net_selected = Signal(str)  # net_name - for highlighting all connections of a net
    toggle_requested = Signal()  # emitted when the user clicks the panel-toggle button

    def __init__(self, parent=None):
        super().__init__(parent)
        self._terminal_nets = {}  # dev_id -> {"D": net, "G": net, "S": net}
        self._edges = []
        self._conn_map = {}  # dev_id -> [(other_id, net), ...]
        self._nodes = []  # Store nodes for group analysis
        self._groups = {}  # group_name -> [dev_ids]
        self._active_tab = "instances"  # Track which tab is active
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
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
        title.setStyleSheet("color: #c8d0dc;")
        header_layout.addWidget(title)

        header_layout.addStretch()

        # Panel toggle button
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

        # Tab bar
        tab_bar = QFrame()
        tab_bar.setFixedHeight(40)
        tab_bar.setStyleSheet("background-color: #151a23; border-bottom: 1px solid #2d3548;")
        tab_layout = QHBoxLayout(tab_bar)
        tab_layout.setContentsMargins(8, 4, 8, 4)
        tab_layout.setSpacing(4)

        # Tab button style
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

        # Instances tab
        self.tab_instances = QPushButton("Instances")
        self.tab_instances.setCheckable(True)
        self.tab_instances.setChecked(True)
        self.tab_instances.setStyleSheet(tab_style)
        self.tab_instances.clicked.connect(lambda: self._switch_tab("instances"))
        tab_layout.addWidget(self.tab_instances)

        # Nets tab
        self.tab_nets = QPushButton("Nets")
        self.tab_nets.setCheckable(True)
        self.tab_nets.setStyleSheet(tab_style)
        self.tab_nets.clicked.connect(lambda: self._switch_tab("nets"))
        tab_layout.addWidget(self.tab_nets)

        # Groups tab
        self.tab_groups = QPushButton("Groups")
        self.tab_groups.setCheckable(True)
        self.tab_groups.setStyleSheet(tab_style)
        self.tab_groups.clicked.connect(lambda: self._switch_tab("groups"))
        tab_layout.addWidget(self.tab_groups)

        tab_layout.addStretch()

        layout.addWidget(tab_bar)

        # Tree widget
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
                font-family: 'Segoe UI', 'Consolas', monospace;
                font-size: 11px;
                padding: 4px;
            }
            QTreeWidget::item {
                padding: 3px 6px;
                border-radius: 3px;
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
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.tree)

    def _switch_tab(self, tab_name):
        """Switch between Instances, Nets, and Groups tabs."""
        self._active_tab = tab_name

        # Update button states
        self.tab_instances.setChecked(tab_name == "instances")
        self.tab_nets.setChecked(tab_name == "nets")
        self.tab_groups.setChecked(tab_name == "groups")

        # Reload the tree with only the active tab's content
        self.load_devices(self._nodes)

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

    def set_groups(self, groups):
        """Set device groups for the Groups section.
        groups: {group_name: [dev_ids], ...}
        """
        self._groups = groups or {}

    def load_devices(self, nodes):
        """Populate tree from the placement JSON nodes.
        Only shows content for the active tab (Instances, Nets, or Groups).
        """
        self.tree.clear()
        self._nodes = nodes

        # ═══════════════════════════════════════════════════════════
        # INSTANCES TAB
        # ═══════════════════════════════════════════════════════════
        if self._active_tab == "instances":
            # Classify devices
            nmos_real, nmos_dummy = [], []
            pmos_real, pmos_dummy = [], []
            for node in nodes:
                dev_type = str(node.get("type", "unknown")).lower()
                is_dummy = node.get("is_dummy", False) or str(node.get("id", "")).upper().startswith("DUMMY")
                if "nmos" in dev_type or dev_type == "nmos":
                    (nmos_dummy if is_dummy else nmos_real).append(node)
                elif "pmos" in dev_type or dev_type == "pmos":
                    (pmos_dummy if is_dummy else pmos_real).append(node)

            # NMOS folder
            if nmos_real or nmos_dummy:
                nmos_folder = QTreeWidgetItem(self.tree, [f"NMOS ({len(nmos_real)})"])
                nmos_folder.setFont(0, QFont("Segoe UI", 10, QFont.Weight.Bold))
                nmos_folder.setForeground(0, QColor("#5dade2"))
                nmos_folder.setData(0, Qt.ItemDataRole.UserRole, "__nmos__")

                for dev in sorted(nmos_real, key=lambda d: d.get("id", "")):
                    self._add_instance_item(nmos_folder, dev, "nmos")

                # NMOS Dummies subfolder
                if nmos_dummy:
                    dummy_folder = QTreeWidgetItem(nmos_folder, [f"Dummies ({len(nmos_dummy)})"])
                    dummy_folder.setFont(0, QFont("Segoe UI", 9))
                    dummy_folder.setForeground(0, QColor("#888888"))
                    for dev in sorted(nmos_dummy, key=lambda d: d.get("id", "")):
                        self._add_instance_item(dummy_folder, dev, "dummy")

                nmos_folder.setExpanded(True)

            # PMOS folder
            if pmos_real or pmos_dummy:
                pmos_folder = QTreeWidgetItem(self.tree, [f"PMOS ({len(pmos_real)})"])
                pmos_folder.setFont(0, QFont("Segoe UI", 10, QFont.Weight.Bold))
                pmos_folder.setForeground(0, QColor("#e74c3c"))
                pmos_folder.setData(0, Qt.ItemDataRole.UserRole, "__pmos__")

                for dev in sorted(pmos_real, key=lambda d: d.get("id", "")):
                    self._add_instance_item(pmos_folder, dev, "pmos")

                # PMOS Dummies subfolder
                if pmos_dummy:
                    dummy_folder = QTreeWidgetItem(pmos_folder, [f"Dummies ({len(pmos_dummy)})"])
                    dummy_folder.setFont(0, QFont("Segoe UI", 9))
                    dummy_folder.setForeground(0, QColor("#888888"))
                    for dev in sorted(pmos_dummy, key=lambda d: d.get("id", "")):
                        self._add_instance_item(dummy_folder, dev, "dummy")

                pmos_folder.setExpanded(True)

        # ═══════════════════════════════════════════════════════════
        # NETS TAB
        # ═══════════════════════════════════════════════════════════
        elif self._active_tab == "nets":
            all_nets = self._collect_all_nets()
            if all_nets:
                # Separate supply nets from signal nets
                supply_nets = {"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS", "DVDD", "DVSS"}
                signal_nets = []
                power_nets = []

                for net_name, devices in sorted(all_nets.items()):
                    if net_name.upper() in supply_nets:
                        power_nets.append((net_name, devices))
                    else:
                        signal_nets.append((net_name, devices))

                # Signal nets folder
                if signal_nets:
                    signals_folder = QTreeWidgetItem(self.tree, [f"Signals ({len(signal_nets)})"])
                    signals_folder.setFont(0, QFont("Segoe UI", 10, QFont.Weight.Bold))
                    signals_folder.setForeground(0, QColor("#77aa77"))
                    for net_name, devices in signal_nets:
                        self._add_net_item(signals_folder, net_name, devices)
                    signals_folder.setExpanded(True)

                # Power nets folder
                if power_nets:
                    power_folder = QTreeWidgetItem(self.tree, [f"Power ({len(power_nets)})"])
                    power_folder.setFont(0, QFont("Segoe UI", 10, QFont.Weight.Bold))
                    power_folder.setForeground(0, QColor("#cc8844"))
                    for net_name, devices in power_nets:
                        self._add_net_item(power_folder, net_name, devices)
                    power_folder.setExpanded(True)

        # ═══════════════════════════════════════════════════════════
        # GROUPS TAB
        # ═══════════════════════════════════════════════════════════
        elif self._active_tab == "groups":
            if self._groups:
                for group_name, dev_ids in sorted(self._groups.items()):
                    self._add_group_item(self.tree, group_name, dev_ids)
            else:
                # Auto-detect groups from device naming (e.g., MM0_f1, MM0_f2)
                auto_groups = self._detect_device_groups(nodes)
                if auto_groups:
                    for group_name, dev_ids in sorted(auto_groups.items()):
                        self._add_group_item(self.tree, group_name, dev_ids)

    def _collect_all_nets(self):
        """Collect all nets and which devices connect to them."""
        net_to_devices = {}

        for dev_id, terminals in self._terminal_nets.items():
            for term, net_name in terminals.items():
                if net_name:
                    if net_name not in net_to_devices:
                        net_to_devices[net_name] = []
                    if dev_id not in net_to_devices[net_name]:
                        net_to_devices[net_name].append(dev_id)

        return net_to_devices

    def _detect_device_groups(self, nodes):
        """Auto-detect device groups from naming patterns like MM0_f1, MM0_f2."""
        import re
        groups = {}

        for node in nodes:
            dev_id = node.get("id", "")
            if node.get("is_dummy") or dev_id.upper().startswith("DUMMY"):
                continue

            # Match patterns like MM0_f1, M1_f2, etc.
            match = re.match(r'^([A-Za-z]+\d+)_f\d+$', dev_id)
            if match:
                base_name = match.group(1)
                if base_name not in groups:
                    groups[base_name] = []
                groups[base_name].append(dev_id)

        # Only return groups with 2+ devices
        return {k: v for k, v in groups.items() if len(v) >= 2}

    def _add_instance_item(self, parent, dev, dev_type):
        """Add a device instance with its terminal info."""
        dev_id = dev.get("id", "unknown")
        elec = dev.get("electrical", {})

        # Compact display with better formatting
        nf = elec.get('nf', 1)
        if dev_type == "dummy":
            text = f"  {dev_id}"
            color = QColor("#999999")
        else:
            # Show device ID with finger count in a clean format
            text = f"  {dev_id}  │  nf={nf}"
            color = QColor("#5dade2") if dev_type == "nmos" else QColor("#e74c3c")

        item = QTreeWidgetItem(parent, [text])
        item.setData(0, Qt.ItemDataRole.UserRole, dev_id)
        item.setFont(0, QFont("Segoe UI", 10))
        item.setForeground(0, color)

        # Add terminal sub-items with better visual hierarchy
        term_nets = self._terminal_nets.get(dev_id, {})
        for term in ["S", "G", "D"]:
            net_name = term_nets.get(term, "—")
            # Use bullet point for visual hierarchy
            sub_text = f"    • {term}: {net_name}"
            sub = QTreeWidgetItem(item, [sub_text])
            sub.setFont(0, QFont("Segoe UI", 9))
            sub.setForeground(0, QColor("#7799bb"))
            sub.setData(0, Qt.ItemDataRole.UserRole, None)
            sub.setData(0, Qt.ItemDataRole.UserRole + 1, dev_id)
            sub.setData(0, Qt.ItemDataRole.UserRole + 2, net_name)

    def _add_net_item(self, parent, net_name, devices):
        """Add a net with its connected devices."""
        # Clean formatting with device count
        text = f"  {net_name}  │  {len(devices)} devices"
        item = QTreeWidgetItem(parent, [text])
        item.setFont(0, QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        item.setForeground(0, QColor("#8fbc8f"))
        item.setData(0, Qt.ItemDataRole.UserRole, "__net__")
        item.setData(0, Qt.ItemDataRole.UserRole + 1, net_name)

        # Add connected devices as children with terminal info
        for dev_id in sorted(devices):
            # Find which terminal connects to this net
            term_nets = self._terminal_nets.get(dev_id, {})
            terms = [t for t, n in term_nets.items() if n == net_name]
            term_str = "/".join(terms) if terms else "?"

            sub_text = f"    • {dev_id}.{term_str}"
            sub = QTreeWidgetItem(item, [sub_text])
            sub.setFont(0, QFont("Segoe UI", 9))
            sub.setForeground(0, QColor("#88aa99"))
            sub.setData(0, Qt.ItemDataRole.UserRole, dev_id)

    def _add_group_item(self, parent, group_name, dev_ids):
        """Add a device group item."""
        text = f"  {group_name}  │  {len(dev_ids)} fingers"
        item = QTreeWidgetItem(parent, [text])
        item.setFont(0, QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        item.setForeground(0, QColor("#dda0dd"))
        item.setData(0, Qt.ItemDataRole.UserRole, "__group__")
        item.setData(0, Qt.ItemDataRole.UserRole + 1, group_name)

        for dev_id in sorted(dev_ids):
            sub = QTreeWidgetItem(item, [f"    • {dev_id}"])
            sub.setFont(0, QFont("Segoe UI", 9))
            sub.setForeground(0, QColor("#bb99bb"))
            sub.setData(0, Qt.ItemDataRole.UserRole, dev_id)

        for dev_id in sorted(dev_ids):
            sub = QTreeWidgetItem(item, [dev_id])
            sub.setFont(0, QFont("Consolas", 9))
            sub.setForeground(0, QColor("#aa77aa"))
            sub.setData(0, Qt.ItemDataRole.UserRole, dev_id)

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
        role = item.data(0, Qt.ItemDataRole.UserRole)

        if role == "__net__":
            # Net item clicked - emit net_selected for highlighting
            net_name = item.data(0, Qt.ItemDataRole.UserRole + 1)
            if net_name:
                self.net_selected.emit(net_name)
        elif role in ("__instances__", "__nmos__", "__pmos__", "__nets__", "__groups__", "__group__"):
            # Folder clicked - just expand/collapse
            pass
        elif role is None:
            # Terminal sub-item clicked
            parent_dev = item.data(0, Qt.ItemDataRole.UserRole + 1)
            net_name = item.data(0, Qt.ItemDataRole.UserRole + 2)
            if parent_dev:
                self.device_selected.emit(parent_dev)
                if net_name and net_name != "—":
                    self.connection_selected.emit(parent_dev, net_name, "")
        elif role:
            # Device item clicked
            self.device_selected.emit(role)

    def _on_item_double_clicked(self, item, column):
        """Double-click expands/collapses folders or selects and highlights devices."""
        role = item.data(0, Qt.ItemDataRole.UserRole)
        if role and not str(role).startswith("__"):
            # Device - emit selection
            self.device_selected.emit(role)
