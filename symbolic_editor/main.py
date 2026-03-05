import sys
import os
import json
import copy

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path so that cross-package imports
# (e.g. ai_agent.llm_worker from symbolic_editor/) work regardless of how
# this script is launched.
# ---------------------------------------------------------------------------
_project_root = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QSplitter,
    QToolBar,
    QFileDialog,
    QSpinBox,
    QLabel,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QAction, QKeySequence

# Local GUI modules (same directory)
from chat_panel import ChatPanel
from device_tree import DeviceTreePanel
from editor_view import SymbolicEditor


# -------------------------------------------------
# Main Window
# -------------------------------------------------
class MainWindow(QMainWindow):

    def __init__(self, placement_file):
        super().__init__()
        self.setWindowTitle("Symbolic Layout Editor")
        self.resize(1400, 900)

        # Undo / Redo stacks
        self._undo_stack = []
        self._redo_stack = []
        self._current_file = placement_file
        self._terminal_nets = {}  # {dev_id: {'D': net, 'G': net, 'S': net}}
        self._rows_virtual_min = 0
        self._cols_virtual_min = 0
        self._ignore_grid_spin_change = False

        # Load placement data
        self._load_data(placement_file)

        # --- Create panels ---
        self.device_tree = DeviceTreePanel()
        self.editor = SymbolicEditor()
        self.chat_panel = ChatPanel()

        # --- Toolbar ---
        self._create_menu_bar()
        self._create_toolbar()

        # --- Splitter layout ---
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.device_tree)
        splitter.addWidget(self.editor)
        splitter.addWidget(self.chat_panel)

        # Set proportions: left ~200px, center stretches, right ~300px
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([220, 860, 320])

        splitter.setStyleSheet(
            """
            QSplitter::handle {
                background-color: #d0d7de;
                width: 1px;
            }
            """
        )

        self.setCentralWidget(splitter)

        # Populate panels
        self._refresh_panels()

        # Fit view after initial load
        QTimer.singleShot(100, self.editor.fit_to_view)

        # Connect device tree selection to canvas highlight
        self.device_tree.device_selected.connect(self.editor.highlight_device)

        # Connect tree connection click to canvas net highlight
        self.device_tree.connection_selected.connect(self._on_connection_selected)

        # Connect canvas selection to tree highlight
        self.editor.device_clicked.connect(self.device_tree.highlight_device)
        self.editor.device_clicked.connect(self._on_canvas_device_clicked)
        self.editor.scene.selectionChanged.connect(self._on_selection_count_changed)

        # Connect AI command execution
        self.chat_panel.command_requested.connect(self._handle_ai_command)
        self.editor.set_dummy_place_callback(self._add_dummy_device)

    # -------------------------------------------------
    # QThread cleanup on close
    # -------------------------------------------------
    def closeEvent(self, event):
        """Gracefully shut down the LLM worker thread before closing."""
        self.chat_panel.shutdown()
        super().closeEvent(event)

    # -------------------------------------------------
    # Menu Bar
    # -------------------------------------------------
    def _create_menu_bar(self):
        mb = self.menuBar()
        mb.setStyleSheet(
            """
            QMenuBar {
                background: #ececec;
                color: #222;
                border-bottom: 1px solid #c8c8c8;
                padding: 1px 4px;
                font-family: 'Segoe UI';
                font-size: 9pt;
            }
            QMenuBar::item {
                background: transparent;
                padding: 3px 8px;
            }
            QMenuBar::item:selected {
                background: #d8d8d8;
            }
            QMenu {
                background: #f4f4f4;
                border: 1px solid #bdbdbd;
                font-family: 'Segoe UI';
                font-size: 9pt;
            }
            QMenu::item:selected {
                background: #d8e7ff;
            }
            """
        )

        file_menu = mb.addMenu("File")
        self._act_file_load = QAction("Load", self)
        self._act_file_load.setShortcut(QKeySequence("Ctrl+O"))
        self._act_file_load.triggered.connect(self._on_load)
        file_menu.addAction(self._act_file_load)

        self._act_file_save = QAction("Save", self)
        self._act_file_save.setShortcut(QKeySequence("Ctrl+S"))
        self._act_file_save.triggered.connect(self._on_save)
        file_menu.addAction(self._act_file_save)

        self._act_file_save_as = QAction("Save As", self)
        self._act_file_save_as.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self._act_file_save_as.triggered.connect(self._on_save_as)
        file_menu.addAction(self._act_file_save_as)

        self._act_file_export = QAction("Export", self)
        self._act_file_export.setShortcut(QKeySequence("Ctrl+E"))
        self._act_file_export.triggered.connect(self._on_export)
        file_menu.addAction(self._act_file_export)

        for name in ["Design", "View", "Edit", "Options", "Window", "Help"]:
            menu = mb.addMenu(name)
            a = QAction(f"{name} Placeholder", self)
            a.setEnabled(False)
            menu.addAction(a)

    # -------------------------------------------------
    # Toolbar
    # -------------------------------------------------
    def _create_toolbar(self):
        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(toolbar.iconSize())
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        toolbar.setStyleSheet(
            """
            QToolBar {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #1e2a3a, stop:1 #2d3f54);
                border-top: 1px solid #4a90d9;
                border-bottom: 1px solid #4a90d9;
                spacing: 3px;
                padding: 3px 6px;
            }
            QToolButton {
                color: #e0e8f0;
                background: #253445;
                border: 1px solid #3d5066;
                border-radius: 2px;
                padding: 1px 4px;
                font-family: 'Segoe UI';
                font-size: 9px;
                min-width: 18px;
            }
            QToolButton:hover {
                background-color: #2d3f54;
                border-color: #4a90d9;
            }
            QToolButton:pressed {
                background-color: #4a90d9;
            }
            QToolButton:checked {
                background-color: #4a90d9;
                border-color: #9cc7f0;
                color: #ffffff;
                font-weight: 600;
            }
            QToolButton:disabled {
                color: #7b8a9c;
            }
            QSpinBox {
                font-family: 'Segoe UI';
                font-size: 9px;
                padding: 0px 2px;
                min-height: 18px;
                background-color: #253445;
                color: #e0e8f0;
                border: 1px solid #3d5066;
                border-radius: 2px;
                selection-background-color: #4a90d9;
            }
            QSpinBox:focus {
                border: 1px solid #9cc7f0;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 14px;
                background: #2d3f54;
                border-left: 1px solid #3d5066;
            }
            """
        )
        self.addToolBar(toolbar)

        # File operations are in the File menu.
        toolbar.addSeparator()

        # Undo
        self._act_undo = QAction("↩", self)
        self._act_undo.setShortcuts([QKeySequence("Ctrl+Z")])
        self._act_undo.setToolTip("Undo last action (Ctrl+Z)")
        self._act_undo.setEnabled(False)
        self._act_undo.triggered.connect(self._on_undo)
        toolbar.addAction(self._act_undo)

        # Redo (Ctrl+Y and Ctrl+Shift+Z)
        self._act_redo = QAction("↪", self)
        self._act_redo.setShortcuts(
            [QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")]
        )
        self._act_redo.setToolTip("Redo last undone action (Ctrl+Y / Ctrl+Shift+Z)")
        self._act_redo.setEnabled(False)
        self._act_redo.triggered.connect(self._on_redo)
        toolbar.addAction(self._act_redo)

        toolbar.addSeparator()

        # Fit to View
        act_fit = QAction("▢", self)
        act_fit.setShortcut(QKeySequence("F"))
        act_fit.setToolTip("Fit all devices in view (F)")
        act_fit.triggered.connect(self.editor.fit_to_view)
        toolbar.addAction(act_fit)

        toolbar.addSeparator()

        # Zoom In
        act_zoom_in = QAction("＋", self)
        act_zoom_in.setShortcut(QKeySequence("Ctrl+="))
        act_zoom_in.setToolTip("Zoom in (Ctrl++)")
        act_zoom_in.triggered.connect(self.editor.zoom_in)
        toolbar.addAction(act_zoom_in)

        # Zoom Out
        act_zoom_out = QAction("－", self)
        act_zoom_out.setShortcut(QKeySequence("Ctrl+-"))
        act_zoom_out.setToolTip("Zoom out (Ctrl+-)")
        act_zoom_out.triggered.connect(self.editor.zoom_out)
        toolbar.addAction(act_zoom_out)

        # Zoom Reset
        act_zoom_reset = QAction("◯", self)
        act_zoom_reset.setShortcut(QKeySequence("Ctrl+0"))
        act_zoom_reset.setToolTip("Reset zoom (Ctrl+0)")
        act_zoom_reset.triggered.connect(self.editor.zoom_reset)
        toolbar.addAction(act_zoom_reset)

        toolbar.addSeparator()

        # Select All
        act_select_all = QAction("☐", self)
        act_select_all.setShortcut(QKeySequence("Ctrl+A"))
        act_select_all.setToolTip("Select all devices (Ctrl+A)")
        act_select_all.triggered.connect(self._select_all_devices)
        toolbar.addAction(act_select_all)

        # Delete
        act_delete = QAction("✖", self)
        act_delete.setShortcut(QKeySequence("Delete"))
        act_delete.setToolTip("Delete selected devices (Delete)")
        act_delete.triggered.connect(self._delete_selected)
        toolbar.addAction(act_delete)

        # Swap selected (need exactly 2)
        act_swap = QAction("⇄", self)
        act_swap.setShortcut(QKeySequence("Ctrl+W"))
        act_swap.setToolTip("Swap 2 selected devices (Ctrl+W)")
        act_swap.triggered.connect(self._swap_selected_devices)
        toolbar.addAction(act_swap)

        # Flip selected
        act_flip_h = QAction("⇋", self)
        act_flip_h.setShortcut(QKeySequence("H"))
        act_flip_h.setToolTip("Flip selected devices horizontally (H)")
        act_flip_h.triggered.connect(self._flip_selected_h)
        toolbar.addAction(act_flip_h)

        act_flip_v = QAction("⇅", self)
        act_flip_v.setShortcut(QKeySequence("V"))
        act_flip_v.setToolTip("Flip selected devices vertically (V)")
        act_flip_v.triggered.connect(self._flip_selected_v)
        toolbar.addAction(act_flip_v)

        # Merge helpers
        act_merge_ss = QAction("SS", self)
        act_merge_ss.setShortcut(QKeySequence("M"))
        act_merge_ss.setToolTip("Merge 2 devices by S-S (M)")
        act_merge_ss.triggered.connect(self._merge_selected_ss)
        toolbar.addAction(act_merge_ss)

        act_merge_dd = QAction("DD", self)
        act_merge_dd.setShortcut(QKeySequence("Shift+M"))
        act_merge_dd.setToolTip("Merge 2 devices by D-D (Shift+M)")
        act_merge_dd.triggered.connect(self._merge_selected_dd)
        toolbar.addAction(act_merge_dd)

        toolbar.addSeparator()

        self._sel_label = QLabel("Sel: 0", self)
        self._sel_label.setStyleSheet("color: #d0d9e5; font-size: 10px;")
        toolbar.addWidget(self._sel_label)

        toolbar.addSeparator()

        # Row / Col controls (counts + growth targets)
        self._row_spin = QSpinBox(self)
        self._row_spin.setRange(0, 9999)
        self._row_spin.setPrefix("Row ")
        self._row_spin.setFixedWidth(96)
        self._row_spin.valueChanged.connect(self._on_row_target_changed)
        toolbar.addWidget(self._row_spin)

        self._col_spin = QSpinBox(self)
        self._col_spin.setRange(0, 9999)
        self._col_spin.setPrefix("Col ")
        self._col_spin.setFixedWidth(96)
        self._col_spin.valueChanged.connect(self._on_col_target_changed)
        toolbar.addWidget(self._col_spin)

        toolbar.addSeparator()

        # Add Dummy mode
        self._act_add_dummy = QAction("D", self)
        self._act_add_dummy.setCheckable(True)
        self._act_add_dummy.setShortcut(QKeySequence("D"))
        self._act_add_dummy.setToolTip(
            "Toggle dummy placement mode (D). Hover a PMOS/NMOS row and click to place."
        )
        self._act_add_dummy.toggled.connect(self._on_toggle_add_dummy)
        toolbar.addAction(self._act_add_dummy)

    def keyPressEvent(self, event):
        """Esc releases active modes and selection."""
        if event.key() == Qt.Key.Key_Escape:
            released = False
            if hasattr(self, "_act_add_dummy") and self._act_add_dummy.isChecked():
                self._act_add_dummy.setChecked(False)
                released = True
            try:
                if self.editor and self.editor.scene.selectedItems():
                    self.editor.scene.clearSelection()
                    self._on_selection_count_changed()
                    released = True
            except RuntimeError:
                pass
            if released:
                event.accept()
                return
        super().keyPressEvent(event)

    # -------------------------------------------------
    # Data helpers
    # -------------------------------------------------
    def _load_data(self, filepath):
        """Load placement JSON into internal state."""
        with open(filepath) as f:
            data = json.load(f)
        if "nodes" not in data:
            raise ValueError("JSON must contain 'nodes' key")
        self._original_data = data
        self.nodes = data["nodes"]
        # Try to find and parse matching SPICE file for terminal nets
        self._terminal_nets = self._parse_spice_terminals(filepath)

    @staticmethod
    def _parse_spice_terminals(json_path):
        """Parse .sp files in the same directory to extract terminal-net mapping.
        MOSFET format: name drain gate source bulk model ...
        Returns: {dev_id: {'D': net, 'G': net, 'S': net}}
        """
        terminal_nets = {}
        sp_dir = os.path.dirname(json_path)
        sp_files = [f for f in os.listdir(sp_dir) if f.endswith('.sp')]
        for sp_file in sp_files:
            try:
                with open(os.path.join(sp_dir, sp_file)) as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('*') or line.startswith('.'):
                            continue
                        tokens = line.split()
                        if len(tokens) >= 5 and tokens[0].startswith('M'):
                            dev_name = tokens[0]
                            terminal_nets[dev_name] = {
                                'D': tokens[1],
                                'G': tokens[2],
                                'S': tokens[3],
                            }
            except Exception:
                pass
        return terminal_nets

    def _refresh_panels(self):
        """Refresh all panels from self.nodes."""
        edges = self._original_data.get("edges")
        self.device_tree.set_edges(edges)
        self.device_tree.set_terminal_nets(self._terminal_nets)
        self.device_tree.load_devices(self.nodes)
        self.editor.load_placement(self.nodes)
        self.editor.set_edges(edges)
        self.editor.set_terminal_nets(self._terminal_nets)
        self.chat_panel.set_layout_context(
            self.nodes, self._original_data.get("edges")
        )
        # Wire up drag signals on each device for undo tracking
        for item in self.editor.device_items.values():
            item.signals.drag_started.connect(self._on_device_drag_start)
            item.signals.drag_finished.connect(self._on_device_drag_end)
        self._update_grid_counts()
        self._on_selection_count_changed()

    def _on_connection_selected(self, dev_id, net_name, _other):
        """When a connection sub-item is clicked in the tree, highlight that net."""
        self.editor.highlight_device(dev_id)
        self.editor._show_net_connections(dev_id, net_name)
        self._update_row_col_for_device(dev_id)

    def _on_canvas_device_clicked(self, dev_id):
        self._update_row_col_for_device(dev_id)

    def _update_row_col_for_device(self, dev_id):
        if not hasattr(self, "_row_spin") or not hasattr(self, "_col_spin"):
            return
        self._update_grid_counts()

    def _on_selection_count_changed(self):
        if not hasattr(self, "_sel_label"):
            return
        count = len(self.editor.selected_device_ids())
        self._sel_label.setText(f"Sel: {count}")

    def _update_grid_counts(self):
        if not hasattr(self, "_row_spin") or not hasattr(self, "_col_spin"):
            return
        row_idx = {
            int(round(item.pos().y() / self.editor._row_pitch))
            for item in self.editor.device_items.values()
        }
        col_idx = {
            int(round(item.pos().x() / self.editor._snap_grid))
            for item in self.editor.device_items.values()
        }

        actual_rows = len(row_idx)
        actual_cols = len(col_idx)
        shown_rows = max(actual_rows, self._rows_virtual_min)
        shown_cols = max(actual_cols, self._cols_virtual_min)

        self._ignore_grid_spin_change = True
        self._row_spin.setValue(shown_rows)
        self._col_spin.setValue(shown_cols)
        self._ignore_grid_spin_change = False

    def _on_row_target_changed(self, value):
        if self._ignore_grid_spin_change:
            return
        row_idx = {
            int(round(it.pos().y() / self.editor._row_pitch))
            for it in self.editor.device_items.values()
        }
        col_idx = {
            int(round(it.pos().x() / self.editor._snap_grid))
            for it in self.editor.device_items.values()
        }
        actual = len(row_idx)
        self._rows_virtual_min = max(actual, value)
        cols = max(len(col_idx), self._cols_virtual_min, 1)
        self.editor.ensure_grid_extent(self._rows_virtual_min, cols)
        self._update_grid_counts()

    def _on_col_target_changed(self, value):
        if self._ignore_grid_spin_change:
            return
        col_idx = {
            int(round(it.pos().x() / self.editor._snap_grid))
            for it in self.editor.device_items.values()
        }
        row_idx = {
            int(round(it.pos().y() / self.editor._row_pitch))
            for it in self.editor.device_items.values()
        }
        actual = len(col_idx)
        self._cols_virtual_min = max(actual, value)
        rows = max(len(row_idx), self._rows_virtual_min, 1)
        self.editor.ensure_grid_extent(rows, self._cols_virtual_min)
        self._update_grid_counts()

    def _build_output_data(self):
        """Build the output dict with updated positions."""
        self._sync_node_positions()
        output = {"nodes": copy.deepcopy(self.nodes)}
        if "edges" in self._original_data:
            output["edges"] = self._original_data["edges"]
        return output

    # -------------------------------------------------
    # Undo / Redo
    # -------------------------------------------------
    def _push_undo(self):
        """Snapshot current positions onto the undo stack."""
        snapshot = copy.deepcopy(self.nodes)
        print("Pushing undo snapshot:", snapshot)
        self._undo_stack.append(snapshot)
        self._redo_stack.clear()
        self._update_undo_redo_state()

    def _update_undo_redo_state(self):
        self._act_undo.setEnabled(bool(self._undo_stack))
        self._act_redo.setEnabled(bool(self._redo_stack))

    def _on_device_drag_start(self):
        """Called when the user starts dragging a device — push undo."""
        self._sync_node_positions()
        self._push_undo()

    def _on_device_drag_end(self):
        """Called when drag ends; snap dragged items to nearest free slot."""
        try:
            for it in self.editor.scene.selectedItems():
                if not hasattr(it, "device_name"):
                    continue
                row_y = self.editor._snap_row(it.pos().y())
                target_x = self.editor._snap_value(it.pos().x())
                free_x = self.editor.find_nearest_free_x(
                    row_y=row_y,
                    width=it.rect().width(),
                    target_x=target_x,
                    exclude_id=it.device_name,
                )
                it.setPos(free_x, row_y)
        except RuntimeError:
            pass
        self._sync_node_positions()
    def _on_undo(self):
        if not self._undo_stack:
            return
        # Make sure current canvas positions are synced before saving to redo
        self._sync_node_positions()
        print("Pushing redo snapshot:", self.nodes)
        self._redo_stack.append(copy.deepcopy(self.nodes))
        # Restore previous state
        self.nodes = self._undo_stack.pop()
        print("Popped undo snapshot:", self.nodes)
        self._original_data["nodes"] = self.nodes
        self._refresh_panels()
        self._update_undo_redo_state()

    def _on_redo(self):
        if not self._redo_stack:
            return
        self._sync_node_positions()
        self._undo_stack.append(copy.deepcopy(self.nodes))
        # Restore redo state
        self.nodes = self._redo_stack.pop()
        self._original_data["nodes"] = self.nodes
        self._refresh_panels()
        self._update_undo_redo_state()

    # -------------------------------------------------
    # Select All / Delete
    # -------------------------------------------------
    def _select_all_devices(self):
        """Select all devices on the canvas."""
        for item in self.editor.device_items.values():
            item.setSelected(True)

    def _swap_selected_devices(self):
        selected = self.editor.selected_device_ids()
        if len(selected) != 2:
            self.chat_panel._append_message(
                "AI",
                "Select exactly 2 devices to swap.",
                "#fde8e8",
                "#a00",
            )
            return
        self._sync_node_positions()
        self._push_undo()
        self.editor.swap_devices(selected[0], selected[1])
        self._sync_node_positions()

    def _merge_selected_ss(self):
        self._merge_selected_devices(mode="SS")

    def _merge_selected_dd(self):
        self._merge_selected_devices(mode="DD")

    def _merge_selected_devices(self, mode="SS"):
        selected = self.editor.selected_device_ids()
        if len(selected) != 2:
            self.chat_panel._append_message(
                "AI",
                "Select exactly 2 devices to merge.",
                "#fde8e8",
                "#a00",
            )
            return

        id_a, id_b = selected[0], selected[1]
        a = self.editor.device_items.get(id_a)
        b = self.editor.device_items.get(id_b)
        if not a or not b:
            return
        if getattr(a, "device_type", None) != getattr(b, "device_type", None):
            self.chat_panel._append_message(
                "AI",
                "Merge requires same device type.",
                "#fde8e8",
                "#a00",
            )
            return

        self._sync_node_positions()
        self._push_undo()

        y = self.editor._snap_row((a.pos().y() + b.pos().y()) / 2.0)
        wa = a.rect().width()
        wb = b.rect().width()

        if mode == "SS":
            # A keeps S on left. B flips so S is on right, then sits left of A.
            if hasattr(a, "set_flip_h"):
                a.set_flip_h(False)
            if hasattr(b, "set_flip_h"):
                b.set_flip_h(True)
            ax = self.editor._snap_value(a.pos().x())
            bx = self.editor._snap_value(ax - wb)
            a.setPos(ax, y)
            b.setPos(bx, y)
        else:
            # A keeps D on right. B flips so D is on left, then sits right of A.
            if hasattr(a, "set_flip_h"):
                a.set_flip_h(False)
            if hasattr(b, "set_flip_h"):
                b.set_flip_h(True)
            ax = self.editor._snap_value(a.pos().x())
            bx = self.editor._snap_value(ax + wa)
            a.setPos(ax, y)
            b.setPos(bx, y)

        self.editor.resolve_overlaps(anchor_ids=[id_a, id_b])
        self._sync_node_positions()

    def _flip_selected_h(self):
        selected = self.editor.selected_device_ids()
        if not selected:
            return
        self._sync_node_positions()
        self._push_undo()
        self.editor.flip_devices_h(selected)
        self._sync_node_positions()

    def _flip_selected_v(self):
        selected = self.editor.selected_device_ids()
        if not selected:
            return
        self._sync_node_positions()
        self._push_undo()
        self.editor.flip_devices_v(selected)
        self._sync_node_positions()

    def _apply_row_col_to_selected(self):
        selected = self.editor.selected_device_ids()
        if len(selected) != 1:
            self.chat_panel._append_message(
                "AI",
                "Select one device to apply Row/Col.",
                "#fde8e8",
                "#a00",
            )
            return
        row = self._row_spin.value()
        col = self._col_spin.value()
        self._sync_node_positions()
        self._push_undo()
        self.editor.move_device_to_grid(selected[0], row, col)
        self._sync_node_positions()

    def _delete_selected(self):
        """Remove selected devices from the canvas and data."""
        selected = self.editor.scene.selectedItems()
        if not selected:
            return
        self._sync_node_positions()
        self._push_undo()
        for item in selected:
            if hasattr(item, 'device_name'):
                dev_id = item.device_name
                self.nodes = [
                    n for n in self.nodes if n.get('id') != dev_id
                ]
                self._original_data['nodes'] = self.nodes
                if dev_id in self.editor.device_items:
                    del self.editor.device_items[dev_id]
                self.editor.scene.removeItem(item)
        self.device_tree.load_devices(self.nodes)
        self._update_undo_redo_state()

    def _on_toggle_add_dummy(self, enabled):
        self.editor.set_dummy_mode(enabled)
        msg = (
            "Dummy mode ON: move over PMOS/NMOS row and click to place."
            if enabled
            else "Dummy mode OFF."
        )
        self.chat_panel._append_message("AI", msg, "#e8f4fd", "#1a1a2e")

    def _next_dummy_id(self, dev_type):
        prefix = "DUMMYP" if dev_type == "pmos" else "DUMMYN"
        used = {n.get("id", "") for n in self.nodes}
        i = 1
        while f"{prefix}{i}" in used:
            i += 1
        return f"{prefix}{i}"

    def _build_dummy_node(self, candidate):
        dev_type = str(candidate["type"]).strip().lower()
        template = next(
            (
                n
                for n in self.nodes
                if str(n.get("type", "")).strip().lower() == dev_type
            ),
            None,
        )
        electrical = {"l": 1.4e-08, "nf": 1, "nfin": 1}
        if template:
            electrical = copy.deepcopy(template.get("electrical", electrical))

        x = candidate["x"] / self.editor.scale_factor
        y = candidate["y"] / self.editor.scale_factor
        width = candidate["width"] / self.editor.scale_factor
        height = candidate["height"] / self.editor.scale_factor

        return {
            "id": self._next_dummy_id(dev_type),
            "type": dev_type,
            "is_dummy": True,
            "electrical": electrical,
            "geometry": {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "orientation": "R0",
            },
        }

    def _add_dummy_device(self, candidate):
        self._sync_node_positions()
        self._push_undo()
        candidate = dict(candidate)
        candidate["type"] = str(candidate.get("type", "")).strip().lower()
        candidate["y"] = self.editor._snap_row(candidate["y"])
        candidate["x"] = self.editor._snap_value(candidate["x"])

        col_capacity = max(1, int(self._col_spin.value())) if hasattr(self, "_col_spin") else 1
        dev_type = candidate.get("type")

        def row_type_count(row_y):
            return sum(
                1
                for it in self.editor.device_items.values()
                if self.editor._snap_row(it.pos().y()) == row_y
                and getattr(it, "device_type", None) == dev_type
            )

        while row_type_count(candidate["y"]) >= col_capacity:
            candidate["y"] += self.editor._row_pitch
            candidate["x"] = 0.0

        candidate["x"] = self.editor.find_nearest_free_x(
            row_y=candidate["y"],
            width=candidate["width"],
            target_x=candidate["x"],
            exclude_id=None,
        )
        dummy = self._build_dummy_node(candidate)
        self.nodes.append(dummy)
        self._original_data["nodes"] = self.nodes
        self._refresh_panels()
        self._sync_node_positions()
        self.chat_panel._append_message(
            "AI",
            f"Added dummy {dummy['id']} ({dummy['type']}).",
            "#e8f4fd",
            "#1a1a2e",
        )

    # -------------------------------------------------
    # Load / Save / Export
    # -------------------------------------------------
    def _on_load(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open Placement JSON", "", "JSON Files (*.json)"
        )
        if not file_path:
            return
        self._push_undo()
        self._current_file = file_path
        self._load_data(file_path)
        self._refresh_panels()
        self.setWindowTitle(f"Symbolic Layout Editor — {os.path.basename(file_path)}")

    def _on_save(self):
        """Save to the current file (overwrite)."""
        if not self._current_file:
            self._on_save_as()
            return
        self._write_json(self._current_file)

    def _on_save_as(self):
        """Save to a new file via dialog."""
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Layout As", "", "JSON Files (*.json)"
        )
        if not file_path:
            return
        self._current_file = file_path
        self._write_json(file_path)
        self.setWindowTitle(f"Symbolic Layout Editor — {os.path.basename(file_path)}")

    def _on_export(self):
        """Export layout as a pretty-printed JSON."""
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Layout JSON", "", "JSON Files (*.json)"
        )
        if not file_path:
            return
        self._write_json(file_path)

    def _write_json(self, file_path):
        """Write the current layout to a JSON file."""
        output = self._build_output_data()
        with open(file_path, "w") as f:
            json.dump(output, f, indent=4)
        self.chat_panel._append_message(
            "AI",
            f"Layout saved to {os.path.basename(file_path)}",
            "#e8f4fd",
            "#1a1a2e",
        )

    # -------------------------------------------------
    # AI command execution
    # -------------------------------------------------
    def _handle_ai_command(self, cmd):
        """Execute a command dict from the AI on the canvas."""
        action = cmd.get("action")
        self._push_undo()
        try:
            if action == "swap":
                id_a = cmd["device_a"]
                id_b = cmd["device_b"]
                ok = self.editor.swap_devices(id_a, id_b)
                if ok:
                    self._sync_node_positions()
            elif action == "move":
                dev_id = cmd["device"]
                x, y = cmd["x"], cmd["y"]
                ok = self.editor.move_device(dev_id, x, y)
                if ok:
                    self._sync_node_positions()
        except (KeyError, TypeError) as e:
            self.chat_panel._append_message(
                "AI", f"Could not execute command: {e}", "#fde8e8", "#a00"
            )

    def _sync_node_positions(self):
        """Sync canvas positions back to self.nodes and update layout context."""
        positions = self.editor.get_updated_positions()
        for node in self.nodes:
            dev_id = node.get("id")
            if dev_id in positions:
                node["geometry"]["x"] = positions[dev_id][0]
                node["geometry"]["y"] = positions[dev_id][1]
                item = self.editor.device_items.get(dev_id)
                if item and hasattr(item, "orientation_string"):
                    node["geometry"]["orientation"] = item.orientation_string()
        # Refresh the chat panel's context with updated positions
        self.chat_panel.set_layout_context(
            self.nodes, self._original_data.get("edges")
        )
        self._update_grid_counts()
        self._on_selection_count_changed()


# -------------------------------------------------
# Main Entry
# -------------------------------------------------
if __name__ == "__main__":

    app = QApplication(sys.argv)

    # Global application style
    app.setStyle("Fusion")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    placement_path = os.path.join(script_dir, "..", "Xor_initial_placement.json")

    window = MainWindow(placement_path)
    window.show()

    sys.exit(app.exec())
