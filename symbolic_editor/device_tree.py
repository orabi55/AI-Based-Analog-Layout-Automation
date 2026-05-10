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
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QMenu,
)

try:
    from .icons import icon_empty_layers, icon_filter, icon_list_view
except ImportError:
    from icons import icon_empty_layers, icon_filter, icon_list_view
try:
    from .icons import icon_group
except ImportError:
    try:
        from icons import icon_group
    except Exception:
        icon_group = None


class DeviceTreePanel(QWidget):
    """Left sidebar showing design hierarchy, nets, and groups."""
    group_delete_requested = Signal(str) # Signal to pass the group name/ID
    device_selected = Signal(str)
    connection_selected = Signal(str, str, str)
    block_selected = Signal(str)
    toggle_requested = Signal()
    net_view_toggled = Signal(bool)  # True when Nets tab is active
    net_colorize_toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._terminal_nets = {}
        self._edges = []
        self._conn_map = {}
        self._nodes = []
        self._blocks = {}
        self._custom_groups = []
        self._placement_groups = {}
        self._active_tab = "all"
        self._tree_expanded = False
        self._init_ui()
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)

    def _init_ui(self):
        self.setMinimumWidth(280)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        tab_bar = QFrame()
        tab_bar.setFixedHeight(52)
        tab_bar.setStyleSheet(
            "background-color: #070c0f; border-bottom: 1px solid #142127;"
        )
        tab_layout = QHBoxLayout(tab_bar)
        tab_layout.setContentsMargins(12, 8, 12, 8)
        tab_layout.setSpacing(4)

        tab_style = """
        QPushButton {
            background-color: transparent;
            color: #8c9aa0;
            border: 1px solid transparent;
            border-radius: 6px;
            padding: 7px 11px;
            font-family: 'Segoe UI';
            font-size: 9pt;
            font-weight: 600;
        }
        QPushButton:hover {
            background-color: #101b20;
            color: #dceef2;
        }
        QPushButton:checked {
            background-color: #082331;
            border: 1px solid #00e5ff;
            color: #00e5ff;
        }
        """

        self.tab_all = QPushButton("All")
        self.tab_all.setCheckable(True)
        self.tab_all.setChecked(True)
        self.tab_all.setStyleSheet(tab_style)
        self.tab_all.clicked.connect(lambda: self._switch_tab("all"))
        tab_layout.addWidget(self.tab_all)

        self.tab_instances = QPushButton("Instances")
        self.tab_instances.setCheckable(True)
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
        self._filter_btn = self._make_icon_button(icon_filter(), "Filter hierarchy view")
        self._filter_btn.clicked.connect(self._show_filter_menu)
        tab_layout.addWidget(self._filter_btn)

        from PySide6.QtWidgets import QCheckBox
        self.check_colorize_nets = QCheckBox("Colorize")
        self.check_colorize_nets.setStyleSheet(
            "QCheckBox { color: #8c9aa0; font-size: 9pt; margin-right: 4px; }"
            "QCheckBox::indicator { width: 14px; height: 14px; }"
        )
        self.check_colorize_nets.setVisible(False)
        self.check_colorize_nets.toggled.connect(self.net_colorize_toggled.emit)
        tab_layout.addWidget(self.check_colorize_nets)

        layout.addWidget(tab_bar)

        search_bar = QFrame()
        search_bar.setFixedHeight(48)
        search_bar.setStyleSheet(
            "background-color: #070c0f; border-bottom: 1px solid #101b20;"
        )
        search_layout = QHBoxLayout(search_bar)
        search_layout.setContentsMargins(12, 6, 12, 6)
        search_layout.setSpacing(8)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search hierarchy...")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setStyleSheet(
            """
            QLineEdit {
                background-color: #05090b;
                color: #d7e4e8;
                border: 1px solid #142127;
                border-radius: 6px;
                padding: 7px 10px;
                font-family: 'Segoe UI';
                font-size: 9pt;
            }
            QLineEdit:focus {
                border-color: #00e5ff;
            }
            QLineEdit::placeholder {
                color: #64777e;
            }
            """
        )
        self._search_edit.textChanged.connect(self._on_search_changed)
        search_layout.addWidget(self._search_edit, 1)

        self._list_btn = self._make_icon_button(icon_list_view(), "Expand/collapse hierarchy")
        self._list_btn.clicked.connect(self._toggle_tree_expanded)
        search_layout.addWidget(self._list_btn)
        layout.addWidget(search_bar)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.tree.setAnimated(True)
        self.tree.setStyleSheet(
            """
            QTreeWidget {
                background-color: #070c0f;
                border: none;
                color: #bfd0d6;
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
                background-color: #101b20;
            }
            QTreeWidget::item:selected {
                background-color: rgba(0, 229, 255, 0.18);
                color: #f4fbfd;
            }
            QTreeWidget::branch {
                background-color: #070c0f;
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
                background: #1b3038;
                border-radius: 3px;
                min-height: 24px;
            }
            QScrollBar::handle:vertical:hover {
                background: #00a9bc;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            """
        )
        self.tree.itemClicked.connect(self._on_item_clicked)

        self._empty_state = self._build_empty_state()
        self._content_stack = QStackedWidget()
        self._content_stack.addWidget(self.tree)
        self._content_stack.addWidget(self._empty_state)
        layout.addWidget(self._content_stack, 1)

    def _make_icon_button(self, icon, tooltip):
        btn = QToolButton()
        btn.setIcon(icon)
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedSize(30, 30)
        btn.setStyleSheet(
            """
            QToolButton {
                background-color: #05090b;
                border: 1px solid #142127;
                border-radius: 6px;
                padding: 5px;
            }
            QToolButton:hover {
                background-color: #101b20;
                border-color: #00e5ff;
            }
            QToolButton:pressed {
                background-color: #092531;
            }
            """
        )
        return btn

    def _build_empty_state(self):
        widget = QWidget()
        widget.setStyleSheet("background-color: #070c0f;")
        empty_layout = QVBoxLayout(widget)
        empty_layout.setContentsMargins(24, 24, 24, 36)
        empty_layout.setSpacing(10)
        empty_layout.addStretch(1)

        self._empty_icon = QLabel()
        self._empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_icon.setPixmap(icon_empty_layers().pixmap(48, 48))
        empty_layout.addWidget(self._empty_icon)

        self._empty_title = QLabel("No hierarchy loaded")
        self._empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_title.setStyleSheet(
            "color: #f4fbfd; font-family: 'Segoe UI'; font-size: 11pt; font-weight: 600;"
        )
        empty_layout.addWidget(self._empty_title)

        self._empty_hint = QLabel(
            "Open a layout or import a netlist\n"
            "to populate the hierarchy."
        )
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint.setWordWrap(True)
        self._empty_hint.setStyleSheet(
            "color: #c0ccd1; font-family: 'Segoe UI'; font-size: 9.5pt; line-height: 145%;"
        )
        empty_layout.addWidget(self._empty_hint)
        empty_layout.addStretch(2)
        return widget

    def _show_context_menu(self, position):
        item = self.tree.itemAt(position)
        if not item:
            return
            
        role = item.data(0, Qt.ItemDataRole.UserRole)
        # Check if the clicked item is a Group header
        if role == "__group_header__": # Use your specific group role key
            menu = QMenu()
            delete_action = menu.addAction("Ungroup")
            action = menu.exec(self.tree.viewport().mapToGlobal(position))
            
            if action == delete_action:
                group_id = item.data(0, Qt.ItemDataRole.UserRole + 3)
                if not group_id:
                    group_id = item.text(0).split("|", 1)[0].strip()
                self.group_delete_requested.emit(group_id)

    def _switch_tab(self, tab_name):
        if tab_name not in {"all", "instances", "nets", "groups"}:
            tab_name = "all"
        self._active_tab = tab_name
        self.tab_all.setChecked(tab_name == "all")
        self.tab_instances.setChecked(tab_name == "instances")
        self.tab_nets.setChecked(tab_name == "nets")
        self.tab_groups.setChecked(tab_name == "groups")
        
        self.check_colorize_nets.setVisible(False)
        
        self.load_devices(self._nodes, blocks=self._blocks)
        self.net_view_toggled.emit(tab_name == "nets")

    def _show_filter_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(
            """
            QMenu {
                background-color: #070c0f;
                color: #d7e4e8;
                border: 1px solid #1b3038;
                font-family: 'Segoe UI';
                font-size: 9pt;
            }
            QMenu::item {
                padding: 6px 24px;
            }
            QMenu::item:selected {
                background-color: #0d2a35;
                color: #00e5ff;
            }
            """
        )
        for label, mode in (
            ("All", "all"),
            ("Instances", "instances"),
            ("Nets", "nets"),
            ("Groups", "groups"),
        ):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self._active_tab == mode)
            action.triggered.connect(lambda _checked=False, m=mode: self._switch_tab(m))
        menu.exec(self._filter_btn.mapToGlobal(self._filter_btn.rect().bottomLeft()))

    def _on_search_changed(self, _text):
        self._apply_search_filter()
        self._update_empty_state()

    def _toggle_tree_expanded(self):
        self._tree_expanded = not self._tree_expanded
        if self._tree_expanded:
            self.tree.expandAll()
        else:
            self.tree.collapseAll()

    def _apply_search_filter(self):
        query = self._search_edit.text().strip().lower()

        def visit(item):
            own_match = query in item.text(0).lower() if query else True
            child_match = False
            for idx in range(item.childCount()):
                if visit(item.child(idx)):
                    child_match = True
            visible = own_match or child_match
            item.setHidden(not visible)
            if query and child_match:
                item.setExpanded(True)
            return visible

        root = self.tree.invisibleRootItem()
        for idx in range(root.childCount()):
            visit(root.child(idx))

    def _has_visible_items(self):
        root = self.tree.invisibleRootItem()
        for idx in range(root.childCount()):
            if not root.child(idx).isHidden():
                return True
        return False

    def _update_empty_state(self):
        has_items = self.tree.topLevelItemCount() > 0
        has_visible = self._has_visible_items()
        if has_items and has_visible:
            self._content_stack.setCurrentWidget(self.tree)
            return

        query = self._search_edit.text().strip()
        if query and has_items:
            self._empty_title.setText("No matching items")
            self._empty_hint.setText("Try a different hierarchy search.")
        else:
            self._empty_title.setText("No hierarchy loaded")
            self._empty_hint.setText(
                "Open a layout or import a netlist\n"
                "to populate the hierarchy."
            )
        self._content_stack.setCurrentWidget(self._empty_state)

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

    def set_custom_groups(self, groups):
        """Set runtime custom groups to show in the Groups tab.

        groups: list of dicts {'name': str, 'devices': [dev_id,...]}
        """
        self._custom_groups = groups or []

    def set_groups(self, groups):
        """Set placement groups (dict of group_id -> [device_ids])."""
        if not isinstance(groups, dict):
            groups = {}
        self._placement_groups = groups

    def load_devices(self, nodes, blocks=None):
        self.tree.clear()
        self._nodes = nodes or []
        self._blocks = blocks or {}

        if self._active_tab == "all":
            self._populate_all_tab()
        elif self._active_tab == "instances":
            self._populate_instances_tab()
        elif self._active_tab == "nets":
            self._populate_nets_tab()
        else:
            self._populate_groups_tab()
        self._apply_search_filter()
        self._update_empty_state()

    def _populate_all_tab(self):
        self._populate_instances_tab()
        self._populate_nets_tab()
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

        if self._placement_groups:
            placement_root = QTreeWidgetItem(
                self.tree,
                [f"Placement Groups  |  {len(self._placement_groups)}"],
            )
            placement_root.setFont(0, QFont("Segoe UI", 10, QFont.Weight.Bold))
            placement_root.setForeground(0, QColor("#8fa8d8"))
            for group_name, dev_ids in sorted(self._placement_groups.items()):
                members = [d for d in dev_ids if isinstance(d, str) and d.strip()]
                if not members:
                    continue
                group_item = QTreeWidgetItem(
                    placement_root,
                    [f"{group_name}  |  {len(members)} devices"],
                )
                group_item.setFont(0, QFont("Segoe UI", 10, QFont.Weight.DemiBold))
                group_item.setForeground(0, QColor("#8fa8d8"))
                if icon_group:
                    group_item.setIcon(0, icon_group())
                for dev_id in sorted(members):
                    child = QTreeWidgetItem(group_item, [f"  {dev_id}"])
                    child.setForeground(0, QColor("#6f7d98"))
                    child.setFont(0, QFont("Segoe UI", 9))
                    child.setData(0, Qt.ItemDataRole.UserRole, dev_id)
                group_item.setExpanded(False)
            placement_root.setExpanded(True)

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
                # Set group icon if available
                if icon_group:
                    group_item.setIcon(0, icon_group())
                for dev_id in sorted(dev_ids):
                    child = QTreeWidgetItem(group_item, [f"  {dev_id}"])
                    child.setForeground(0, QColor("#9a8eb8"))
                    child.setFont(0, QFont("Segoe UI", 9))
                    child.setData(0, Qt.ItemDataRole.UserRole, dev_id)
                group_item.setExpanded(False)
            groups_root.setExpanded(True)

        # Show custom (user-created) groups if present
        if self._custom_groups:
            custom_root = QTreeWidgetItem(self.tree, [f"Custom Groups  |  {len(self._custom_groups)}"])
            custom_root.setFont(0, QFont("Segoe UI", 10, QFont.Weight.Bold))
            custom_root.setForeground(0, QColor("#f0d07a"))
            for grp in sorted(self._custom_groups, key=lambda g: g.get('name', '')):
                name = grp.get('name', 'group')
                dev_ids = grp.get('devices', [])
                group_item = QTreeWidgetItem(custom_root, [f"{name}  |  {len(dev_ids)} devices"])
                group_item.setFont(0, QFont("Segoe UI", 10, QFont.Weight.DemiBold))
                group_item.setForeground(0, QColor("#f0d07a"))
                group_item.setData(0, Qt.ItemDataRole.UserRole, "__group_header__")
                group_item.setData(0, Qt.ItemDataRole.UserRole + 3, name)
                if icon_group:
                    group_item.setIcon(0, icon_group())
                for dev_id in sorted(dev_ids):
                    child = QTreeWidgetItem(group_item, [f"  {dev_id}"])
                    child.setForeground(0, QColor("#8f7a44"))
                    child.setFont(0, QFont("Segoe UI", 9))
                    child.setData(0, Qt.ItemDataRole.UserRole, dev_id)
                group_item.setExpanded(False)
            custom_root.setExpanded(True)

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
