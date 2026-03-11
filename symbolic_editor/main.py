import sys
import os
import json
import copy
import glob

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
    QToolButton,
    QFileDialog,
    QSpinBox,
    QLabel,
    QWidgetAction,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QCheckBox,
    QDoubleSpinBox,
)
from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QFont, QAction, QKeySequence, QColor, QPalette

# Local GUI modules (same directory)
from chat_panel import ChatPanel
from device_tree import DeviceTreePanel
from editor_view import SymbolicEditor
from klayout_panel import KLayoutPanel
from icons import (
    icon_undo, icon_redo, icon_fit_view,
    icon_zoom_in, icon_zoom_out, icon_zoom_reset,
    icon_select_all, icon_delete, icon_swap,
    icon_flip_h, icon_flip_v,
    icon_merge_ss, icon_merge_dd, icon_add_dummy,
)


# -------------------------------------------------
# Main Window
# -------------------------------------------------
class MainWindow(QMainWindow):

    def __init__(self, placement_file):
        super().__init__()
        self.setWindowTitle("Symbolic Layout Editor")
        self.resize(1500, 950)

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
        self.klayout_panel = KLayoutPanel()

        # --- Toolbar ---
        self._create_menu_bar()
        self._create_toolbar()

        # --- Right-side vertical splitter (Chat + KLayout Preview) ---
        self._right_splitter = QSplitter(Qt.Orientation.Vertical)
        self._right_splitter.addWidget(self.chat_panel)
        self._right_splitter.addWidget(self.klayout_panel)
        self._right_splitter.setStretchFactor(0, 1)
        self._right_splitter.setStretchFactor(1, 1)
        self._right_splitter.setSizes([480, 380])
        self._right_splitter.setStyleSheet(
            """
            QSplitter::handle {
                background-color: #2d3548;
                height: 2px;
            }
            QSplitter::handle:hover {
                background-color: #4a90d9;
            }
            """
        )

        # --- Splitter layout ---
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self.device_tree)
        self._splitter.addWidget(self.editor)
        self._splitter.addWidget(self._right_splitter)

        # Set proportions: left ~200px, center stretches, right ~320px
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setStretchFactor(2, 0)
        self._splitter.setSizes([220, 860, 320])

        # Remember default sizes for restore-after-collapse
        self._tree_default_width = 220
        self._chat_default_width = 320

        # --- Collapsed-panel reopen strips ---
        self._tree_reopen_strip = self._make_reopen_strip("▶", "Show Device Hierarchy")
        self._tree_reopen_strip.clicked.connect(self._toggle_device_tree)
        self._tree_reopen_strip.setVisible(False)

        self._chat_reopen_strip = self._make_reopen_strip("◀", "Show AI Chat")
        self._chat_reopen_strip.clicked.connect(self._toggle_chat_panel)
        self._chat_reopen_strip.setVisible(False)

        # Insert strips into splitter: strip | tree | editor | chat | strip
        # We rearrange: use a wrapper layout
        from PySide6.QtWidgets import QFrame
        container = QFrame()
        container_layout = QHBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        container_layout.addWidget(self._tree_reopen_strip)
        container_layout.addWidget(self._splitter, 1)
        container_layout.addWidget(self._chat_reopen_strip)

        self._splitter.setStyleSheet(
            """
            QSplitter::handle {
                background-color: #2d3548;
                width: 2px;
            }
            QSplitter::handle:hover {
                background-color: #4a90d9;
            }
            """
        )

        self.setCentralWidget(container)

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

        # Connect panel toggle buttons (in each panel header)
        self.device_tree.toggle_requested.connect(self._toggle_device_tree)
        self.chat_panel.toggle_requested.connect(self._toggle_chat_panel)

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
                background-color: #1a1f2b;
                color: #c8d0dc;
                border-bottom: 1px solid #2d3548;
                padding: 2px 6px;
                font-family: 'Segoe UI';
                font-size: 9pt;
            }
            QMenuBar::item {
                background: transparent;
                padding: 4px 10px;
                border-radius: 4px;
            }
            QMenuBar::item:selected {
                background-color: #2d3f54;
                color: #ffffff;
            }
            QMenu {
                background-color: #1e2636;
                border: 1px solid #3d5066;
                border-radius: 6px;
                padding: 4px;
                font-family: 'Segoe UI';
                font-size: 9pt;
                color: #c8d0dc;
            }
            QMenu::item {
                padding: 6px 24px 6px 12px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #4a90d9;
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background: #2d3548;
                margin: 4px 8px;
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

        self._act_file_export = QAction("Export JSON", self)
        self._act_file_export.setShortcut(QKeySequence("Ctrl+E"))
        self._act_file_export.triggered.connect(self._on_export)
        file_menu.addAction(self._act_file_export)

        self._act_export_oas = QAction("Export to OAS", self)
        self._act_export_oas.setShortcut(QKeySequence("Ctrl+Shift+E"))
        self._act_export_oas.triggered.connect(self._on_export_oas)
        file_menu.addAction(self._act_export_oas)

        file_menu.addSeparator()

        self._act_view_klayout = QAction("View in KLayout", self)
        self._act_view_klayout.triggered.connect(self._on_view_in_klayout)
        file_menu.addAction(self._act_view_klayout)

        design_menu = mb.addMenu("Design")
        a = QAction("Design Placeholder", self)
        a.setEnabled(False)
        design_menu.addAction(a)

        view_menu = mb.addMenu("View")
        a = QAction("View Placeholder", self)
        a.setEnabled(False)
        view_menu.addAction(a)

        # --- Edit menu (functional) ---
        edit_menu = mb.addMenu("Edit")

        self._act_close_row_gap = QCheckBox("Close PMOS–NMOS gap")
        self._act_close_row_gap.setStyleSheet(
            "QCheckBox { color: #c8d0dc; font-family: 'Segoe UI'; font-size: 9pt; padding: 4px 8px; }"
            "QCheckBox::indicator { width: 14px; height: 14px; }"
        )
        self._act_close_row_gap.toggled.connect(self._on_close_row_gap_toggled)
        wa_gap_check = QWidgetAction(self)
        wa_gap_check.setDefaultWidget(self._act_close_row_gap)
        edit_menu.addAction(wa_gap_check)

        # Gap distance spin
        gap_widget = QWidget()
        gap_layout = QHBoxLayout(gap_widget)
        gap_layout.setContentsMargins(24, 4, 8, 4)
        gap_lbl = QLabel("Gap (px):")
        gap_lbl.setStyleSheet("color: #8899aa; font-family: 'Segoe UI'; font-size: 9pt;")
        self._row_gap_spin = QDoubleSpinBox()
        self._row_gap_spin.setRange(0.0, 200.0)
        self._row_gap_spin.setSingleStep(1.0)
        self._row_gap_spin.setValue(0.0)
        self._row_gap_spin.setSuffix(" px")
        self._row_gap_spin.setEnabled(False)
        self._row_gap_spin.setStyleSheet(
            "QDoubleSpinBox { background: #232a38; color: #c8d0dc; border: 1px solid #2d3548;"
            " border-radius: 4px; padding: 2px 6px; font-family: 'Segoe UI'; font-size: 9pt; }"
        )
        self._row_gap_spin.valueChanged.connect(self._on_row_gap_changed)
        gap_layout.addWidget(gap_lbl)
        gap_layout.addWidget(self._row_gap_spin)
        wa_gap_spin = QWidgetAction(self)
        wa_gap_spin.setDefaultWidget(gap_widget)
        edit_menu.addAction(wa_gap_spin)

        edit_menu.addSeparator()

        # View panel toggles in Edit too
        act_toggle_tree = QAction("Toggle Device Tree", self)
        act_toggle_tree.triggered.connect(self._toggle_device_tree)
        edit_menu.addAction(act_toggle_tree)

        act_toggle_chat = QAction("Toggle AI Chat", self)
        act_toggle_chat.triggered.connect(self._toggle_chat_panel)
        edit_menu.addAction(act_toggle_chat)

        act_toggle_klayout = QAction("Toggle KLayout Preview", self)
        act_toggle_klayout.triggered.connect(self._toggle_klayout_panel)
        edit_menu.addAction(act_toggle_klayout)

        for name in ["Options", "Window", "Help"]:
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
        toolbar.setIconSize(QSize(22, 22))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        toolbar.setStyleSheet(
            """
            QToolBar {
                background-color: #1a1f2b;
                border: none;
                border-bottom: 1px solid #2d3548;
                spacing: 2px;
                padding: 4px 8px;
            }
            QToolBar::separator {
                width: 1px;
                background-color: #2d3548;
                margin: 4px 6px;
            }
            QToolButton {
                color: #c8d0dc;
                background: transparent;
                border: 1px solid transparent;
                border-radius: 6px;
                padding: 4px;
                min-width: 28px;
                min-height: 28px;
            }
            QToolButton:hover {
                background-color: #2a3345;
                border-color: #3d5066;
            }
            QToolButton:pressed {
                background-color: #4a90d9;
                border-color: #4a90d9;
            }
            QToolButton:checked {
                background-color: rgba(74, 144, 217, 0.25);
                border-color: #4a90d9;
                color: #ffffff;
            }
            QToolButton:disabled {
                opacity: 0.35;
            }
            QSpinBox {
                font-family: 'Segoe UI';
                font-size: 11px;
                padding: 2px 4px;
                min-height: 24px;
                background-color: #232a38;
                color: #c8d0dc;
                border: 1px solid #2d3548;
                border-radius: 6px;
                selection-background-color: #4a90d9;
            }
            QSpinBox:focus {
                border-color: #4a90d9;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 16px;
                background: transparent;
                border: none;
            }
            QSpinBox::up-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-bottom: 5px solid #7b8a9c;
                width: 0; height: 0;
            }
            QSpinBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #7b8a9c;
                width: 0; height: 0;
            }
            QLabel {
                color: #8899aa;
                font-family: 'Segoe UI';
                font-size: 11px;
            }
            """
        )
        self.addToolBar(toolbar)

        toolbar.addSeparator()

        # Undo
        self._act_undo = QAction(icon_undo(), "Undo", self)
        self._act_undo.setShortcuts([QKeySequence("Ctrl+Z")])
        self._act_undo.setToolTip("Undo  (Ctrl+Z)")
        self._act_undo.setEnabled(False)
        self._act_undo.triggered.connect(self._on_undo)
        toolbar.addAction(self._act_undo)

        # Redo
        self._act_redo = QAction(icon_redo(), "Redo", self)
        self._act_redo.setShortcuts(
            [QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")]
        )
        self._act_redo.setToolTip("Redo  (Ctrl+Y)")
        self._act_redo.setEnabled(False)
        self._act_redo.triggered.connect(self._on_redo)
        toolbar.addAction(self._act_redo)

        toolbar.addSeparator()

        # Fit to View
        act_fit = QAction(icon_fit_view(), "Fit View", self)
        act_fit.setShortcut(QKeySequence("F"))
        act_fit.setToolTip("Fit all devices in view  (F)")
        act_fit.triggered.connect(self.editor.fit_to_view)
        toolbar.addAction(act_fit)

        toolbar.addSeparator()

        # Zoom In
        act_zoom_in = QAction(icon_zoom_in(), "Zoom In", self)
        act_zoom_in.setShortcut(QKeySequence("Ctrl+="))
        act_zoom_in.setToolTip("Zoom In  (Ctrl++)")
        act_zoom_in.triggered.connect(self.editor.zoom_in)
        toolbar.addAction(act_zoom_in)

        # Zoom Out
        act_zoom_out = QAction(icon_zoom_out(), "Zoom Out", self)
        act_zoom_out.setShortcut(QKeySequence("Ctrl+-"))
        act_zoom_out.setToolTip("Zoom Out  (Ctrl+-)")
        act_zoom_out.triggered.connect(self.editor.zoom_out)
        toolbar.addAction(act_zoom_out)

        # Zoom Reset
        act_zoom_reset = QAction(icon_zoom_reset(), "Zoom Reset", self)
        act_zoom_reset.setShortcut(QKeySequence("Ctrl+0"))
        act_zoom_reset.setToolTip("Reset Zoom  (Ctrl+0)")
        act_zoom_reset.triggered.connect(self.editor.zoom_reset)
        toolbar.addAction(act_zoom_reset)

        toolbar.addSeparator()

        # Select All
        act_select_all = QAction(icon_select_all(), "Select All", self)
        act_select_all.setShortcut(QKeySequence("Ctrl+A"))
        act_select_all.setToolTip("Select All  (Ctrl+A)")
        act_select_all.triggered.connect(self._select_all_devices)
        toolbar.addAction(act_select_all)

        # Delete
        act_delete = QAction(icon_delete(), "Delete", self)
        act_delete.setShortcut(QKeySequence("Delete"))
        act_delete.setToolTip("Delete Selected  (Del)")
        act_delete.triggered.connect(self._delete_selected)
        toolbar.addAction(act_delete)

        # Swap selected (need exactly 2)
        act_swap = QAction(icon_swap(), "Swap", self)
        act_swap.setShortcut(QKeySequence("Ctrl+W"))
        act_swap.setToolTip("Swap 2 Selected  (Ctrl+W)")
        act_swap.triggered.connect(self._swap_selected_devices)
        toolbar.addAction(act_swap)

        # Flip selected
        act_flip_h = QAction(icon_flip_h(), "Flip H", self)
        act_flip_h.setShortcut(QKeySequence("H"))
        act_flip_h.setToolTip("Flip Horizontally  (H)")
        act_flip_h.triggered.connect(self._flip_selected_h)
        toolbar.addAction(act_flip_h)

        act_flip_v = QAction(icon_flip_v(), "Flip V", self)
        act_flip_v.setShortcut(QKeySequence("V"))
        act_flip_v.setToolTip("Flip Vertically  (V)")
        act_flip_v.triggered.connect(self._flip_selected_v)
        toolbar.addAction(act_flip_v)

        # Merge helpers
        act_merge_ss = QAction(icon_merge_ss(), "Merge S-S", self)
        act_merge_ss.setShortcut(QKeySequence("G"))
        act_merge_ss.setToolTip("Merge by S-S  (G)")
        act_merge_ss.triggered.connect(self._merge_selected_ss)
        toolbar.addAction(act_merge_ss)

        act_merge_dd = QAction(icon_merge_dd(), "Merge D-D", self)
        act_merge_dd.setShortcut(QKeySequence("Shift+G"))
        act_merge_dd.setToolTip("Merge by D-D  (Shift+G)")
        act_merge_dd.triggered.connect(self._merge_selected_dd)
        toolbar.addAction(act_merge_dd)

        toolbar.addSeparator()

        self._sel_label = QLabel("  Sel: 0  ", self)
        toolbar.addWidget(self._sel_label)

        toolbar.addSeparator()

        # Row / Col controls
        self._row_spin = QSpinBox(self)
        self._row_spin.setRange(0, 9999)
        self._row_spin.setPrefix("Row ")
        self._row_spin.setFixedWidth(100)
        self._row_spin.valueChanged.connect(self._on_row_target_changed)
        toolbar.addWidget(self._row_spin)

        self._col_spin = QSpinBox(self)
        self._col_spin.setRange(0, 9999)
        self._col_spin.setPrefix("Col ")
        self._col_spin.setFixedWidth(100)
        self._col_spin.valueChanged.connect(self._on_col_target_changed)
        toolbar.addWidget(self._col_spin)

        toolbar.addSeparator()

        # Add Dummy mode
        self._act_add_dummy = QAction(icon_add_dummy(), "Dummy", self)
        self._act_add_dummy.setCheckable(True)
        self._act_add_dummy.setShortcut(QKeySequence("D"))
        self._act_add_dummy.setToolTip(
            "Toggle dummy placement mode (D)\nHover a row and click to place."
        )
        self._act_add_dummy.toggled.connect(self._on_toggle_add_dummy)
        toolbar.addAction(self._act_add_dummy)

    # -------------------------------------------------
    # Panel collapse / expand
    # -------------------------------------------------
    @staticmethod
    def _make_reopen_strip(arrow_text, tooltip):
        """Create a narrow vertical button that sits at the collapsed edge."""
        btn = QToolButton()
        btn.setText(arrow_text)
        btn.setToolTip(tooltip)
        btn.setFixedWidth(18)
        btn.setStyleSheet(
            """
            QToolButton {
                background-color: #1a1f2b;
                color: #7b8a9c;
                border: none;
                font-size: 11px;
                padding: 0;
            }
            QToolButton:hover {
                background-color: #2d3f54;
                color: #e0e8f0;
            }
            """
        )
        return btn

    def _toggle_device_tree(self):
        """Collapse or expand the device hierarchy panel."""
        if self.device_tree.isVisible():
            self.device_tree.setVisible(False)
            self._tree_reopen_strip.setVisible(True)
        else:
            self.device_tree.setVisible(True)
            self._tree_reopen_strip.setVisible(False)
            sizes = self._splitter.sizes()
            sizes[0] = self._tree_default_width
            self._splitter.setSizes(sizes)

    def _toggle_chat_panel(self):
        """Collapse or expand the AI chat panel."""
        if self.chat_panel.isVisible():
            self.chat_panel.setVisible(False)
            self._chat_reopen_strip.setVisible(True)
        else:
            self.chat_panel.setVisible(True)
            self._chat_reopen_strip.setVisible(False)
            sizes = self._splitter.sizes()
            sizes[2] = self._chat_default_width
            self._splitter.setSizes(sizes)

    def _toggle_klayout_panel(self):
        """Collapse or expand the KLayout preview panel."""
        self.klayout_panel.setVisible(not self.klayout_panel.isVisible())

    def _on_view_in_klayout(self):
        """Find the sibling OAS file and open it in KLayout."""
        if not self._current_file:
            return
        json_dir = os.path.dirname(os.path.abspath(self._current_file))
        oas_files = glob.glob(os.path.join(json_dir, "*.oas"))
        if oas_files:
            self.klayout_panel._oas_path = oas_files[0]
            self.klayout_panel._on_open_klayout()

    def keyPressEvent(self, event):
        """Esc releases active modes and selection.  M enters move mode."""
        if event.key() == Qt.Key.Key_Escape:
            released = False
            if hasattr(self, "_act_add_dummy") and self._act_add_dummy.isChecked():
                self._act_add_dummy.setChecked(False)
                released = True
            # Exit move mode if active
            if getattr(self, '_move_mode', False):
                self._exit_move_mode()
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
        # M key → toggle move mode (pick up selected device)
        if event.key() == Qt.Key.Key_M and not event.modifiers():
            self._toggle_move_mode()
            event.accept()
            return
        super().keyPressEvent(event)

    # -------------------------------------------------
    # Move mode (M key)
    # -------------------------------------------------
    def _toggle_move_mode(self):
        """Toggle move mode: pressing M picks up the selected device;
        the user drags it freely, then clicks or presses M/Esc to drop."""
        if getattr(self, '_move_mode', False):
            self._exit_move_mode()
            return
        selected = self.editor.selected_device_ids()
        if len(selected) != 1:
            self.chat_panel._append_message(
                "AI", "Select exactly 1 device to move (M).", "#fde8e8", "#a00"
            )
            return
        self._move_mode = True
        self._move_dev_id = selected[0]
        self._sync_node_positions()
        self._push_undo()
        self.chat_panel._append_message(
            "AI",
            f"Move mode: drag {self._move_dev_id} to new position. Press M or Esc to finish.",
            "#e8f4fd", "#1a1a2e",
        )

    def _exit_move_mode(self):
        self._move_mode = False
        self._move_dev_id = None
        self._sync_node_positions()

    # -------------------------------------------------
    # Row-gap (Edit menu)
    # -------------------------------------------------
    def _on_close_row_gap_toggled(self, checked):
        self._row_gap_spin.setEnabled(checked)
        if checked:
            gap_px = self._row_gap_spin.value()
            self.editor.set_custom_row_gap(gap_px)
        else:
            self.editor.set_custom_row_gap(None)  # revert to default
        self._refresh_panels(compact=True)

    def _on_row_gap_changed(self, value):
        if self._act_close_row_gap.isChecked():
            self.editor.set_custom_row_gap(value)
            self._refresh_panels(compact=True)

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

    def _refresh_panels(self, compact=True):
        """Refresh all panels from self.nodes.

        Args:
            compact: passed to editor.load_placement. Set False to
                     preserve exact node positions (after AI swap/move).
        """
        edges = self._original_data.get("edges")
        self.device_tree.set_edges(edges)
        self.device_tree.set_terminal_nets(self._terminal_nets)
        self.device_tree.load_devices(self.nodes)
        self.editor.load_placement(self.nodes, compact=compact)
        self.editor.set_edges(edges)
        self.editor.set_terminal_nets(self._terminal_nets)
        self.chat_panel.set_layout_context(
            self.nodes, self._original_data.get("edges"),
            self._terminal_nets,
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
        self._row_spin.setMinimum(max(actual_rows, 1))
        self._col_spin.setMinimum(max(actual_cols, 1))
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
        self.editor.set_virtual_extents(self._rows_virtual_min, cols)
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
        self.editor.set_virtual_extents(rows, self._cols_virtual_min)
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

        while row_type_count(candidate["y"]) > col_capacity:
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
        self._refresh_panels(compact=False)
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

    def _on_export_oas(self):
        """Export the current placement back into an OAS layout file."""
        # Locate the original .oas and .sp files next to the loaded JSON
        if not self._current_file:
            self.chat_panel._append_message(
                "AI", "No layout loaded. Load a JSON first.",
                "#fde8e8", "#a00",
            )
            return

        json_dir = os.path.dirname(os.path.abspath(self._current_file))

        # Find sibling .oas file
        oas_files = glob.glob(os.path.join(json_dir, "*.oas"))
        if not oas_files:
            self.chat_panel._append_message(
                "AI",
                "No .oas file found next to the loaded JSON.",
                "#fde8e8", "#a00",
            )
            return
        oas_path = oas_files[0]

        # Find sibling .sp file
        sp_files = glob.glob(os.path.join(json_dir, "*.sp"))
        if not sp_files:
            self.chat_panel._append_message(
                "AI",
                "No .sp netlist file found next to the loaded JSON.",
                "#fde8e8", "#a00",
            )
            return
        sp_path = sp_files[0]

        # Ask user where to save
        default_name = os.path.splitext(os.path.basename(oas_path))[0] + "_updated.oas"
        default_path = os.path.join(json_dir, default_name)
        output_path, _ = QFileDialog.getSaveFileName(
            self, "Export to OAS", default_path,
            "OASIS Files (*.oas);;GDS Files (*.gds);;All Files (*)",
        )
        if not output_path:
            return

        # Sync current canvas positions into self.nodes
        self._sync_node_positions()

        try:
            from export.oas_writer import update_oas_placement
            update_oas_placement(
                oas_path=oas_path,
                sp_path=sp_path,
                nodes=self.nodes,
                output_path=output_path,
            )
            self.chat_panel._append_message(
                "AI",
                f"Layout exported to **{os.path.basename(output_path)}**",
                "#e8f4fd", "#1a1a2e",
            )
            # Auto-refresh KLayout preview
            self.klayout_panel.refresh_preview(output_path)
        except Exception as e:
            self.chat_panel._append_message(
                "AI",
                f"Export to OAS failed: {e}",
                "#fde8e8", "#a00",
            )
            import traceback
            traceback.print_exc()

    # -------------------------------------------------
    # AI command execution
    # -------------------------------------------------
    def _resolve_device_id(self, raw_id):
        """Resolve a device id from AI text (case-insensitive)."""
        if raw_id is None:
            return None
        candidate = str(raw_id).strip()
        if not candidate:
            return None
        if candidate in self.editor.device_items:
            return candidate

        lookup = {str(dev_id).lower(): dev_id for dev_id in self.editor.device_items.keys()}
        resolved = lookup.get(candidate.lower())
        if resolved:
            return resolved

        # Support shorthand numeric ids from chat, e.g. "28" -> "MM28".
        if candidate.isdigit():
            suffix_matches = [
                dev_id
                for dev_id in self.editor.device_items.keys()
                if str(dev_id).lower().endswith(candidate.lower())
            ]
            if len(suffix_matches) == 1:
                return suffix_matches[0]

        return None

    def _handle_ai_command(self, cmd):
        """Execute a command dict from the AI on the canvas."""
        print(f"[AI CMD] Received command: {cmd}")

        if not isinstance(cmd, dict):
            self.chat_panel._append_message(
                "AI", "Could not execute command: invalid command format.", "#fde8e8", "#a00"
            )
            return

        action = str(cmd.get("action", "")).strip().lower()
        try:
            if action in {"swap", "swap_devices"}:
                raw_a = cmd.get("device_a", cmd.get("a"))
                raw_b = cmd.get("device_b", cmd.get("b"))
                id_a = self._resolve_device_id(raw_a)
                id_b = self._resolve_device_id(raw_b)
                print(f"[AI CMD] Swap: raw=({raw_a},{raw_b}) resolved=({id_a},{id_b})")

                if not id_a or not id_b:
                    self.chat_panel._append_message(
                        "AI",
                        f"Swap failed: device not found ({raw_a}, {raw_b}).",
                        "#fde8e8",
                        "#a00",
                    )
                    return

                # Sync current canvas state into self.nodes
                self._sync_node_positions()
                self._push_undo()

                # --- Swap at data level: exchange geometry in self.nodes ---
                node_a = next((n for n in self.nodes if n.get("id") == id_a), None)
                node_b = next((n for n in self.nodes if n.get("id") == id_b), None)
                if node_a and node_b:
                    geo_a = node_a["geometry"]
                    geo_b = node_b["geometry"]
                    # Swap x, y, and orientation
                    geo_a["x"], geo_b["x"] = geo_b["x"], geo_a["x"]
                    geo_a["y"], geo_b["y"] = geo_b["y"], geo_a["y"]
                    orient_a = geo_a.get("orientation", "R0")
                    orient_b = geo_b.get("orientation", "R0")
                    geo_a["orientation"] = orient_b
                    geo_b["orientation"] = orient_a
                    print(f"[AI CMD] Data swap done: {id_a}→({geo_a['x']},{geo_a['y']}), {id_b}→({geo_b['x']},{geo_b['y']})")
                    # Rebuild canvas WITHOUT re-compaction so positions stick
                    self._refresh_panels(compact=False)
                    self.chat_panel._append_message(
                        "AI",
                        f"✅ Swapped {id_a} ↔ {id_b}",
                        "#e8f4fd",
                        "#1a1a2e",
                    )
                else:
                    print(f"[AI CMD] Swap failed: node_a={node_a is not None}, node_b={node_b is not None}")
                    self.chat_panel._append_message(
                        "AI",
                        f"Swap failed for {id_a} and {id_b}.",
                        "#fde8e8",
                        "#a00",
                    )

            elif action in {"move", "move_device"}:
                raw_dev = cmd.get("device", cmd.get("device_id", cmd.get("id")))
                dev_id = self._resolve_device_id(raw_dev)
                x = cmd.get("x")
                y = cmd.get("y")
                print(f"[AI CMD] Move: raw={raw_dev} resolved={dev_id} x={x} y={y}")

                if dev_id is None:
                    self.chat_panel._append_message(
                        "AI",
                        f"Move failed: device not found ({raw_dev}).",
                        "#fde8e8",
                        "#a00",
                    )
                    return
                if x is None or y is None:
                    self.chat_panel._append_message(
                        "AI",
                        "Move failed: missing x or y in command.",
                        "#fde8e8",
                        "#a00",
                    )
                    return

                self._sync_node_positions()
                self._push_undo()

                # --- Move at data level ---
                node = next((n for n in self.nodes if n.get("id") == dev_id), None)
                if node:
                    node["geometry"]["x"] = float(x)
                    node["geometry"]["y"] = float(y)
                    print(f"[AI CMD] Data move done: {dev_id}→({x},{y})")
                    self._refresh_panels(compact=False)
                    self.chat_panel._append_message(
                        "AI",
                        f"✅ Moved {dev_id} to ({x}, {y})",
                        "#e8f4fd",
                        "#1a1a2e",
                    )
                else:
                    self.chat_panel._append_message(
                        "AI",
                        f"Move failed for {dev_id}.",
                        "#fde8e8",
                        "#a00",
                    )

            elif action in {"add_dummy", "add_dummies", "dummy"}:
                dev_type = str(cmd.get("type", "nmos")).strip().lower()
                count = int(cmd.get("count", 1))
                if dev_type not in ("nmos", "pmos"):
                    self.chat_panel._append_message(
                        "AI",
                        f"Invalid dummy type: {dev_type}. Use 'nmos' or 'pmos'.",
                        "#fde8e8",
                        "#a00",
                    )
                    return
                print(f"[AI CMD] Add dummy: type={dev_type}, count={count}")
                self._sync_node_positions()
                self._push_undo()
                added = []
                for _ in range(count):
                    template = next(
                        (n for n in self.nodes
                         if str(n.get('type', '')).strip().lower() == dev_type),
                        None,
                    )
                    if not template:
                        self.chat_panel._append_message(
                            "AI",
                            f"No {dev_type} device to use as template.",
                            "#fde8e8",
                            "#a00",
                        )
                        return
                    tgeo = template["geometry"]
                    w = tgeo.get("width", 1) * self.editor.scale_factor
                    h = tgeo.get("height", 0.5) * self.editor.scale_factor
                    row_y = None
                    for it in self.editor.device_items.values():
                        if getattr(it, 'device_type', None) == dev_type:
                            row_y = self.editor._snap_row(it.pos().y())
                            break
                    if row_y is None:
                        row_y = 0
                    # Determine target_x from "side" hint (left / right)
                    side = str(cmd.get("side", "left")).strip().lower()
                    if side == "right":
                        # Start search from rightmost occupied slot + 1
                        row_items = [
                            it for it in self.editor.device_items.values()
                            if self.editor._snap_row(it.pos().y()) == row_y
                        ]
                        if row_items:
                            max_x = max(it.pos().x() + it.rect().width() for it in row_items)
                            target_x = self.editor._snap_value(max_x)
                        else:
                            target_x = 0
                    else:
                        # Start search from leftmost occupied slot - 1
                        row_items = [
                            it for it in self.editor.device_items.values()
                            if self.editor._snap_row(it.pos().y()) == row_y
                        ]
                        if row_items:
                            min_x = min(it.pos().x() for it in row_items)
                            target_x = self.editor._snap_value(min_x - w)
                        else:
                            target_x = 0
                    free_x = self.editor.find_nearest_free_x(
                        row_y=row_y, width=w, target_x=target_x, exclude_id=None,
                    )
                    candidate = {
                        "type": dev_type,
                        "x": free_x,
                        "y": row_y,
                        "width": w,
                        "height": h,
                    }
                    dummy = self._build_dummy_node(candidate)
                    self.nodes.append(dummy)
                    self._original_data["nodes"] = self.nodes
                    added.append(dummy["id"])
                    self._refresh_panels(compact=False)
                    self._sync_node_positions()
                names = ", ".join(added)
                self.chat_panel._append_message(
                    "AI",
                    f"✅ Added {count} {dev_type} dummy(s): {names}",
                    "#e8f4fd",
                    "#1a1a2e",
                )

            else:
                print(f"[AI CMD] Unsupported action: '{action}'")
                self.chat_panel._append_message(
                    "AI",
                    f"Unsupported AI action: {action or '(empty)'}",
                    "#fde8e8",
                    "#a00",
                )
        except (KeyError, TypeError, ValueError) as e:
            print(f"[AI CMD] Exception: {e}")
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
            self.nodes, self._original_data.get("edges"),
            self._terminal_nets,
        )
        self._update_grid_counts()
        self._on_selection_count_changed()


# -------------------------------------------------
# Main Entry
# -------------------------------------------------
if __name__ == "__main__":

    app = QApplication(sys.argv)

    # Global application style — modern dark Fusion
    app.setStyle("Fusion")

    # Dark palette
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#12161f"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#c8d0dc"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#111621"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#1a1f2b"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#1e2636"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#d0d8e0"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#c8d0dc"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#1a1f2b"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#c8d0dc"))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Link, QColor("#4a90d9"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#4a90d9"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#556677"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#556677"))
    app.setPalette(palette)

    # Global tooltip styling
    app.setStyleSheet("""
        QToolTip {
            background-color: #1e2636;
            color: #d0d8e0;
            border: 1px solid #3d5066;
            border-radius: 6px;
            padding: 6px 10px;
            font-family: 'Segoe UI';
            font-size: 11px;
        }
    """)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    placement_path = os.path.join(script_dir, "..", "Xor_initial_placement.json")

    window = MainWindow(placement_path)
    window.show()

    sys.exit(app.exec())
