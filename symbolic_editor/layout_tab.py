# -*- coding: utf-8 -*-
"""
Layout Editor Tab — a self-contained QWidget encapsulating one
layout document with its own editor canvas, device tree, chat
panel, KLayout preview, undo/redo stack, and AI pipeline connection.

Created by extracting per-document logic from the former MainWindow
so multiple tabs can coexist independently.
"""

import sys
import os
import json
import copy
import glob
import re
import logging
import tempfile

from PySide6.QtWidgets import (
    QSplitter,
    QToolButton,
    QFileDialog,
    QLabel,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QDialog,
    QMessageBox,
    QFrame,
    QPushButton,
    QStackedWidget,
)
from PySide6.QtCore import Qt, QTimer, Signal, QPointF
from PySide6.QtGui import QColor, QKeySequence, QShortcut

try:
    from .chat_panel import ChatPanel
    from .device_tree import DeviceTreePanel
    from .editor_view import SymbolicEditor, DeleteGroupCommand
    from .hierarchy_group_item import HierarchyGroupItem
    from .klayout_panel import KLayoutPanel
    from .properties_panel import PropertiesPanel
    from .schematic_view import SchematicPanel
    from .view_toggle import SegmentedToggle
    from .widgets.generic_worker import GenericWorker
    from .widgets.loading_overlay import LoadingOverlay
    from .dialogs.import_dialog import ImportDialog
    from .dialogs.ai_model_dialog import AIModelSelectionDialog
    from .dialogs.match_dialog import _MatchDialog
except ImportError:
    from chat_panel import ChatPanel
    from device_tree import DeviceTreePanel
    from editor_view import SymbolicEditor, DeleteGroupCommand
    from hierarchy_group_item import HierarchyGroupItem
    from klayout_panel import KLayoutPanel
    from properties_panel import PropertiesPanel
    from schematic_view import SchematicPanel
    from view_toggle import SegmentedToggle
    from widgets.generic_worker import GenericWorker
    from widgets.loading_overlay import LoadingOverlay
    from dialogs.import_dialog import ImportDialog
    from dialogs.ai_model_dialog import AIModelSelectionDialog
    from dialogs.match_dialog import _MatchDialog

from ai_agent.matching.engine import MatchingEngine


# ---------------------------------------------------------------------------
# Layout Editor Tab
# ---------------------------------------------------------------------------
class LayoutEditorTab(QWidget):
    """One layout document — editor + tree + chat + KLayout preview."""

    # ── Signals for MainWindow to listen to ────────────────────────
    undo_state_changed = Signal(bool, bool)   # can_undo, can_redo
    selection_changed  = Signal(int)           # count
    grid_changed       = Signal(int, int, int, int)  # rows, cols, min_row, min_col
    title_changed      = Signal(str)           # document basename
    workspace_mode_changed = Signal(str)

    def __init__(self, placement_file=None, parent=None):
        super().__init__(parent)

        # ── Document state ─────────────────────────────────────────
        self._undo_stack = []
        self._redo_stack = []
        self._current_file = placement_file
        self._terminal_nets = {}
        self._rows_virtual_min = 0
        self._cols_virtual_min = 0
        self._original_data = None
        self.nodes = []
        self._matched_groups = []
        self._custom_groups = []
        self.groups = {}
        self._group_members_by_device = {}
        self._active_group_drag = None
        self._group_drag_last_pos = {}

        # Mode flags (toolbar communicates via setters)
        self._dummy_mode = False
        self._abutment_mode = False
        self._colorize_mode = False
        self._close_row_gap = False
        self._row_gap_value = 0.0
        # One-shot trigger: chat net-priority annotations apply to the next
        # AI initial-placement run only, then auto-reset to preserve baseline
        # behavior for general runs.
        self._apply_chat_net_priority_next_run = False

        # Load placement data
        self._load_data(placement_file)
        self._blocks = {}
        self._workspace_mode = "symbolic"
        self._both_workspace_sizes = [860, 480]
        self._pending_oas_path = None
        self._klayout_source_oas_path = None
        self._klayout_source_sp_path = None
        self._klayout_live_output_path = None
        self._klayout_live_worker = None
        self._klayout_live_pending = False
        self._klayout_live_generation = 0
        self._klayout_live_timer = QTimer(self)
        self._klayout_live_timer.setSingleShot(True)
        self._klayout_live_timer.timeout.connect(self._run_live_klayout_update)

        # ── Create panels ──────────────────────────────────────────
        self.device_tree = DeviceTreePanel()
        self.properties_panel = PropertiesPanel()
        self.editor = SymbolicEditor()
        self.schematic_panel = SchematicPanel(self)
        self.chat_panel = ChatPanel()
        self.klayout_panel = KLayoutPanel()
        self._workspace_toggle = SegmentedToggle()
        self._workspace_toggle.mode_changed.connect(self.set_workspace_mode)

        self._sidebar_shell = QFrame()
        sidebar_layout = QVBoxLayout(self._sidebar_shell)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        sidebar_tabs = QFrame()
        sidebar_tabs.setFixedHeight(44)
        sidebar_tabs.setStyleSheet(
            "background-color:#05090b; border-bottom:1px solid #142127;"
        )
        sidebar_tab_layout = QHBoxLayout(sidebar_tabs)
        sidebar_tab_layout.setContentsMargins(6, 4, 6, 4)
        sidebar_tab_layout.setSpacing(4)

        self._hierarchy_tab_btn = self._make_sidebar_tab_button("Hierarchy", 0)
        self._properties_tab_btn = self._make_sidebar_tab_button("Properties", 1)
        sidebar_tab_layout.addWidget(self._hierarchy_tab_btn, 1)
        sidebar_tab_layout.addWidget(self._properties_tab_btn, 1)

        self._sidebar_stack = QStackedWidget()
        self._sidebar_stack.addWidget(self.device_tree)
        self._sidebar_stack.addWidget(self.properties_panel)
        sidebar_layout.addWidget(sidebar_tabs)
        sidebar_layout.addWidget(self._sidebar_stack, 1)
        self._show_sidebar_panel(0)

        # ── Hook up schematic signals ──────────────────────────────
        self.schematic_panel.highlight_device.connect(self.editor.highlight_device)
        self.schematic_panel.highlight_net.connect(self.editor.highlight_net_by_name)
        self.schematic_panel.ai_layout_started.connect(self._on_schematic_ai_started)
        self.schematic_panel.ai_layout_finished.connect(self._on_schematic_ai_finished)

        # ── Right-side vertical splitter ───────────────────────────
        self._left_splitter = QSplitter(Qt.Orientation.Vertical)
        self._left_splitter.addWidget(self._sidebar_shell)
        self._left_splitter.addWidget(self.schematic_panel)
        self._left_splitter.setStretchFactor(0, 2)
        self._left_splitter.setStretchFactor(1, 1)
        self._left_splitter.setChildrenCollapsible(False)
        self._left_splitter.setSizes([390, 230])
        self._left_splitter.setStyleSheet(
            "QSplitter::handle { background-color: #142127; height: 2px; }"
            "QSplitter::handle:hover { background-color: #00e5ff; }"
        )
        self.schematic_panel.setVisible(False)

        # ── Main horizontal splitter ──────────────────────────────
        workspace_header = QFrame()
        workspace_header.setFixedHeight(36)
        workspace_header.setStyleSheet(
            "background-color: #05090b; border-bottom: 1px solid #142127;"
        )
        workspace_header_layout = QHBoxLayout(workspace_header)
        workspace_header_layout.setContentsMargins(16, 2, 16, 2)
        workspace_header_layout.setSpacing(0)
        workspace_header_layout.addStretch(1)
        self._workspace_toggle.setToolTip("Switch workspace view")
        workspace_header_layout.addWidget(
            self._workspace_toggle, 0, Qt.AlignmentFlag.AlignCenter
        )
        workspace_header_layout.addStretch(1)

        self._workspace_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._workspace_splitter.addWidget(self.editor)
        self._workspace_splitter.addWidget(self.klayout_panel)
        self._workspace_splitter.setStretchFactor(0, 1)
        self._workspace_splitter.setStretchFactor(1, 1)
        self._workspace_splitter.setSizes(self._both_workspace_sizes)
        self._workspace_splitter.setStyleSheet(
            "QSplitter::handle { background-color: #142127; width: 2px; }"
            "QSplitter::handle:hover { background-color: #00e5ff; }"
        )

        self._workspace_shell = QFrame()
        self._workspace_shell.setStyleSheet("background-color: #05090b;")
        workspace_layout = QVBoxLayout(self._workspace_shell)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        workspace_layout.addWidget(workspace_header)
        workspace_layout.addWidget(self._workspace_splitter, 1)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._left_splitter)
        self._splitter.addWidget(self._workspace_shell)
        self._splitter.addWidget(self.chat_panel)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setStretchFactor(2, 0)
        self._splitter.setSizes([300, 1000, 320])
        self._splitter.setHandleWidth(1)
        self._sidebar_default_width = 300
        self._chat_default_width = 320

        # ── Collapsed-panel reopen strips ─────────────────────────
        self._tree_reopen_strip = self._make_reopen_strip(">", "Show Hierarchy Sidebar")
        self._tree_reopen_strip.clicked.connect(self._toggle_device_tree)
        self._tree_reopen_strip.setVisible(False)

        self._chat_reopen_strip = self._make_reopen_strip("<", "Show AI Chat")
        self._chat_reopen_strip.clicked.connect(self._toggle_chat_panel)
        self._chat_reopen_strip.setVisible(False)

        container = QFrame()
        container_layout = QHBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        container_layout.addWidget(self._tree_reopen_strip)
        container_layout.addWidget(self._splitter, 1)
        container_layout.addWidget(self._chat_reopen_strip)

        self._splitter.setStyleSheet(
            "QSplitter::handle { background-color: #0f1b20; width: 1px; }"
            "QSplitter::handle:hover { background-color: #00e5ff; }"
        )

        # ── Set as this widget's layout ───────────────────────────
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(container)

        # Loading overlay (per-tab)
        self.overlay = LoadingOverlay(self)
        self.overlay.hide()
        self.overlay.cancel_requested.connect(self._cancel_ai_placement)

        # Populate panels
        self._refresh_panels(compact= placement_file is None)
        self._init_workspace_shortcuts()

        # Fit view after initial load
        QTimer.singleShot(100, self.editor.fit_to_view)

        # ── Internal signal wiring ────────────────────────────────
        self.device_tree.device_selected.connect(self._on_tree_device_selected)
        self.device_tree.connection_selected.connect(self._on_connection_selected)
        self.device_tree.block_selected.connect(self._on_tree_block_selected)
        self.device_tree.group_delete_requested.connect(self._handle_group_delete)
        self.editor.device_clicked.connect(self.device_tree.highlight_device)
        self.editor.dummy_toggle_requested.connect(self._toggle_dummy_shortcut)
        self.editor.drag_finished.connect(self._on_device_drag_end)
        self.editor.hierarchy_drag_started.connect(self._on_hierarchy_drag_start)
        self.editor.hierarchy_drag_finished.connect(self._on_hierarchy_drag_end)
        self.editor.cancel_tools_requested.connect(self._cancel_active_tools)
        self.editor.device_clicked.connect(self._on_canvas_device_clicked)
        self.editor.hierarchy_changed.connect(self._on_hierarchy_changed)
        self.editor.abutment_changed.connect(
            lambda: self._schedule_live_klayout_update(delay_ms=100)
        )
        self.editor.scene.selectionChanged.connect(self._on_editor_selection_changed)

        # AI command execution (batch for single undo)
        self._pending_cmds = []
        self._batch_flush_timer = None
        self.chat_panel.command_requested.connect(self._enqueue_ai_command)
        self.chat_panel._llm_worker.stage_completed.connect(
            self._on_pipeline_stage_completed
        )
        self._stage_highlight_timer = None
        self.editor.set_dummy_place_callback(self._add_dummy_device)

        # Panel toggles
        self.device_tree.toggle_requested.connect(self._toggle_device_tree)
        self.device_tree.net_view_toggled.connect(self.editor.set_net_labels_visible)
        self.device_tree.net_colorize_toggled.connect(self.editor.set_net_colorize_enabled)
        self.chat_panel.toggle_requested.connect(self._toggle_chat_panel)
        self.klayout_panel.refresh_requested.connect(
            lambda: self._schedule_live_klayout_update(delay_ms=0, force=True)
        )

        self.set_workspace_mode(self._workspace_mode)

    # ─────────────────────────────────────────────────────────────────
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "overlay"):
            self.overlay.resize(self.size())

    def shutdown(self):
        """Gracefully stop the AI worker thread."""
        self.chat_panel.shutdown()

    def _init_workspace_shortcuts(self):
        """Keep workspace view shortcuts local to the active editor shell."""
        self._shortcut_fit_view = QShortcut(QKeySequence("F"), self._workspace_shell)
        self._shortcut_fit_view.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._shortcut_fit_view.activated.connect(self.editor.fit_to_view)

        self._shortcut_detailed_view = QShortcut(QKeySequence("Shift+F"), self._workspace_shell)
        self._shortcut_detailed_view.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._shortcut_detailed_view.activated.connect(self.editor.show_detailed_hierarchy)

        self._shortcut_outline_view = QShortcut(QKeySequence("Ctrl+F"), self._workspace_shell)
        self._shortcut_outline_view.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._shortcut_outline_view.activated.connect(self.editor.show_symbolic_hierarchy)

        self._shortcut_show_props = QShortcut(QKeySequence("Q"), self._workspace_shell)
        self._shortcut_show_props.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._shortcut_show_props.activated.connect(self._show_selected_device_properties)

    # ── Public convenience properties ────────────────────────────
    @property
    def current_file(self):
        return self._current_file

    @property
    def document_title(self):
        if self._current_file:
            return os.path.basename(self._current_file)
        return "Untitled"

    def can_undo(self):
        return bool(self._undo_stack)

    def can_redo(self):
        return bool(self._redo_stack)

    def selection_count(self):
        try:
            return len(self.editor.selected_device_ids())
        except (AttributeError, RuntimeError):
            return 0

    # =================================================================
    #  Left sidebar stack
    # =================================================================
    def _make_sidebar_tab_button(self, text, index):
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                color: #9aa8ae;
                border: 1px solid transparent;
                border-radius: 5px;
                padding: 5px 8px;
                font-family: 'Segoe UI';
                font-size: 9pt;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #101b20;
                color: #e4f6f9;
            }
            QPushButton:checked {
                background-color: #082331;
                border-color: #00e5ff;
                color: #00e5ff;
            }
            """
        )
        btn.clicked.connect(lambda _checked=False, i=index: self._show_sidebar_panel(i))
        return btn

    def _show_sidebar_panel(self, index):
        if hasattr(self, "_sidebar_stack"):
            self._sidebar_stack.setCurrentIndex(index)
        if hasattr(self, "_hierarchy_tab_btn"):
            self._hierarchy_tab_btn.setChecked(index == 0)
        if hasattr(self, "_properties_tab_btn"):
            self._properties_tab_btn.setChecked(index == 1)

    # =================================================================
    #  Panel collapse / expand
    # =================================================================
    @staticmethod
    def _make_reopen_strip(arrow_text, tooltip):
        btn = QToolButton()
        btn.setText(arrow_text)
        btn.setToolTip(tooltip)
        btn.setFixedWidth(18)
        btn.setStyleSheet(
            "QToolButton { background-color:#071014; color:#7b8a90;"
            " border:none; font-size:11px; padding:0; }"
            "QToolButton:hover { background-color:#0d2a35; color:#00e5ff; }"
        )
        return btn

    def _toggle_device_tree(self):
        if self._left_splitter.isVisible():
            self._left_splitter.setVisible(False)
            self._tree_reopen_strip.setVisible(True)
        else:
            self._left_splitter.setVisible(True)
            self._tree_reopen_strip.setVisible(False)
            sizes = self._splitter.sizes()
            sizes[0] = self._sidebar_default_width
            self._splitter.setSizes(sizes)

    def _toggle_schematic_panel(self):
        if hasattr(self, 'schematic_panel'):
            show = not self.schematic_panel.isVisible()
            if show and not self._left_splitter.isVisible():
                self._left_splitter.setVisible(True)
                self._tree_reopen_strip.setVisible(False)
            self.schematic_panel.setVisible(show)
            if show:
                self.schematic_panel.set_editor(self.editor)
                self.schematic_panel.load(self.nodes, self._terminal_nets)

    def _toggle_chat_panel(self):
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
        if self._workspace_mode == "symbolic":
            self.set_workspace_mode("both")
        else:
            self.set_workspace_mode("symbolic")

    def _toggle_dummy_shortcut(self):
        win = self.window()
        action = getattr(win, "_act_add_dummy", None)
        if action is not None:
            action.toggle()
            return
        self.set_dummy_mode(not self._dummy_mode)

    def set_workspace_mode(self, mode):
        if mode not in {"symbolic", "klayout", "both"}:
            return

        if getattr(self, "_workspace_mode", None) == "both" and hasattr(self, "_workspace_splitter"):
            self._both_workspace_sizes = self._workspace_splitter.sizes()

        self._workspace_mode = mode
        self._workspace_toggle.set_mode(mode, emit=False)

        if mode == "symbolic":
            self.editor.setVisible(True)
            self.klayout_panel.setVisible(False)
            self._workspace_splitter.setSizes([1, 0])
        elif mode == "klayout":
            self.editor.setVisible(False)
            self.klayout_panel.setVisible(True)
            self._workspace_splitter.setSizes([0, 1])
        else:
            self.editor.setVisible(True)
            self.klayout_panel.setVisible(True)
            self._workspace_splitter.setSizes(self._both_workspace_sizes)
        if mode != "klayout":
            self.editor.setFocus(Qt.FocusReason.OtherFocusReason)
            # Auto-fit symbolic view after the splitter finishes resizing
            from PySide6.QtCore import QTimer
            QTimer.singleShot(50, self.editor.fit_to_view)
        if mode in ("klayout", "both"):
            # Auto-fit KLayout preview after the splitter finishes resizing
            from PySide6.QtCore import QTimer
            QTimer.singleShot(100, self.klayout_panel.fit_to_view)
            self._schedule_live_klayout_update(delay_ms=0, force=True)
        self.workspace_mode_changed.emit(mode)

    def workspace_mode(self):
        return self._workspace_mode

    def on_view_in_klayout(self):
        if not self._current_file:
            return
        json_dir = os.path.dirname(os.path.abspath(self._current_file))
        oas_files = glob.glob(os.path.join(json_dir, "*.oas"))
        if oas_files:
            self.klayout_panel.set_oas_path(oas_files[0])
            self.set_workspace_mode("klayout")
            self.klayout_panel._on_open_klayout()

    # =================================================================
    #  Key press – Esc / D / M
    # =================================================================
    def _set_main_action_checked(self, action_name, checked):
        action = getattr(self.window(), action_name, None)
        if action is None or not action.isCheckable():
            return
        was_blocked = action.blockSignals(True)
        try:
            action.setChecked(bool(checked))
        finally:
            action.blockSignals(was_blocked)

    def _cancel_active_tools(self):
        released = False
        dummy_action = getattr(self.window(), "_act_add_dummy", None)
        if self._dummy_mode or (dummy_action is not None and dummy_action.isChecked()):
            self._dummy_mode = False
            self.editor.set_dummy_mode(False)
            self._set_main_action_checked("_act_add_dummy", False)
            released = True

        if getattr(self, "_move_mode", False):
            self._exit_move_mode()
            released = True

        abut_action = getattr(self.window(), "_act_abutment", None)
        if getattr(self, "_abutment_mode", False) or (
            abut_action is not None and abut_action.isChecked()
        ):
            self.set_abutment_mode(False)
            self._set_main_action_checked("_act_abutment", False)
            released = True

        route_action = getattr(self.window(), "_tb_act_route", None)
        if route_action is not None and route_action.isChecked():
            self._set_main_action_checked("_tb_act_route", False)
            self._on_toggle_route(False)
            released = True

        try:
            if self.editor and self.editor.scene.selectedItems():
                self.editor.scene.clearSelection()
                self._on_editor_selection_changed()
                released = True
        except RuntimeError:
            pass

        return released

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._cancel_active_tools()
            event.accept()
            return
        if event.key() == Qt.Key.Key_D and not event.modifiers():
            self._toggle_dummy_shortcut()
            event.accept()
            return
        if event.key() == Qt.Key.Key_D and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            try:
                if self.editor:
                    self.editor.descend_nearest_hierarchy()
                    event.accept()
                    return
            except (AttributeError, RuntimeError):
                logging.debug("Ctrl+D hierarchy descend failed", exc_info=True)
        if event.key() == Qt.Key.Key_M and not event.modifiers():
            self._toggle_move_mode()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Q and not event.modifiers():
            self._show_selected_device_properties()
            event.accept()
            return
        super().keyPressEvent(event)

    # =================================================================
    #  Move mode (M key)
    # =================================================================
    def _toggle_move_mode(self):
        if getattr(self, "_move_mode", False):
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
        if self._move_dev_id:
            self._enforce_matched_group_move(self._move_dev_id)
        self._move_mode = False
        self._move_dev_id = None
        self._sync_node_positions()

    def _enforce_matched_group_move(self, moved_id):
        for group in self._matched_groups:
            if moved_id not in group["ids"]:
                continue
            moved_item = self.editor.device_items.get(moved_id)
            if not moved_item:
                return
            orig_node = None
            if self.nodes:
                for n in self.nodes:
                    if n.get("id") == moved_id:
                        orig_node = n
                        break
            if not orig_node:
                return
            orig_geo = orig_node.get("geometry", {})
            orig_x = orig_geo.get("x", 0.0)
            orig_y = orig_geo.get("y", 0.0)
            scale = self.editor._snap_grid
            dx = moved_item.pos().x() - orig_x * (scale / 0.294)
            dy = moved_item.pos().y() - orig_y * (scale / 0.668) if abs(orig_y) > 1e-9 else moved_item.pos().y()
            for gid in group["ids"]:
                if gid == moved_id:
                    continue
                item = self.editor.device_items.get(gid)
                if not item:
                    continue
                for n in self.nodes:
                    if n.get("id") == gid:
                        g = n.get("geometry", {})
                        ox = g.get("x", 0.0) * (scale / 0.294)
                        item.setPos(ox + dx, moved_item.pos().y())
                        break
            return

    # =================================================================
    #  Row-gap (Edit menu – driven by MainWindow setters)
    # =================================================================
    def set_close_row_gap(self, checked, gap_value=None):
        self._close_row_gap = checked
        if gap_value is not None:
            self._row_gap_value = gap_value
        if checked:
            self.editor.set_custom_row_gap(self._row_gap_value)
        else:
            self.editor.set_custom_row_gap(None)
        self._refresh_panels(compact=True)

    def set_row_gap_value(self, value):
        self._row_gap_value = value
        if self._close_row_gap:
            self.editor.set_custom_row_gap(value)
            self._refresh_panels(compact=True)

    # =================================================================
    #  Data helpers
    # =================================================================
    def _load_data(self, filepath):
        if filepath is None or not os.path.isfile(filepath):
            return
        with open(filepath) as f:
            data = json.load(f)
        if "nodes" not in data:
            raise ValueError("JSON must contain 'nodes' key")
        self._original_data = data
        self.nodes = data["nodes"]
        self._custom_groups = copy.deepcopy(data.get("custom_groups", []))
        self._set_groups(data.get("groups", {}))
        self._terminal_nets = self._parse_spice_terminals(filepath)

    def _set_groups(self, groups):
        if not isinstance(groups, dict):
            groups = {}
        self.groups = groups
        self._group_members_by_device = {}
        for _gid, members in groups.items():
            if not isinstance(members, (list, tuple, set)):
                continue
            cleaned = [m for m in members if isinstance(m, str) and m.strip()]
            if not cleaned:
                continue
            member_set = set(cleaned)
            for dev_id in member_set:
                self._group_members_by_device.setdefault(dev_id, set()).update(member_set)

    def _group_items_for_device(self, dev_id):
        member_ids = self._group_members_by_device.get(dev_id, set())
        return [
            self.editor.device_items.get(mid)
            for mid in member_ids
            if mid in self.editor.device_items
        ]

    @staticmethod
    def _parse_spice_terminals(json_path):
        terminal_nets = {}
        sp_dir = os.path.dirname(json_path)
        sp_files = [f for f in os.listdir(sp_dir) if f.endswith(".sp")]
        for sp_file in sp_files:
            try:
                with open(os.path.join(sp_dir, sp_file)) as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("*") or line.startswith("."):
                            continue
                        tokens = line.split()
                        if len(tokens) >= 5 and tokens[0].startswith("M"):
                            terminal_nets[tokens[0]] = {
                                "D": tokens[1], "G": tokens[2], "S": tokens[3],
                            }
            except (IOError, OSError, IndexError):
                logging.debug("Failed to parse SPICE terminal nets", exc_info=True)
        return terminal_nets

    @staticmethod
    def _preferred_oas_file(oas_files):
        if not oas_files:
            return None
        base_files = [
            path for path in oas_files
            if "_updated" not in os.path.basename(path).lower()
            and "_live" not in os.path.basename(path).lower()
        ]
        return (base_files or oas_files)[0]

    @staticmethod
    def _preferred_sp_file(sp_files, source_path=None):
        if not sp_files:
            return None
        if source_path:
            source_base = os.path.splitext(os.path.basename(source_path))[0]
            for suffix in ("_graph_compressed", "_graph"):
                if source_base.endswith(suffix):
                    source_base = source_base[:-len(suffix)]
            preferred = source_base.lower()
            for path in sp_files:
                if os.path.splitext(os.path.basename(path))[0].lower() == preferred:
                    return path
        return sp_files[0]

    def _sync_klayout_source(self, explicit_oas=None, source_path=None):
        oas_path = explicit_oas
        sp_path = None

        if source_path and os.path.isfile(source_path):
            if source_path.lower().endswith(".sp"):
                sp_path = source_path
            source_dir = os.path.dirname(os.path.abspath(source_path))
            if not oas_path:
                oas_files = sorted(
                    glob.glob(os.path.join(source_dir, "*.oas"))
                    + glob.glob(os.path.join(source_dir, "*.gds"))
                )
                oas_path = self._preferred_oas_file(oas_files)
            sp_files = sorted(glob.glob(os.path.join(source_dir, "*.sp")))
            sp_path = sp_path or self._preferred_sp_file(sp_files, source_path)

        self._klayout_source_oas_path = oas_path if oas_path and os.path.isfile(oas_path) else None
        self._klayout_source_sp_path = sp_path if sp_path and os.path.isfile(sp_path) else None
        self._klayout_live_output_path = None
        self._klayout_live_generation += 1

        self.klayout_panel.set_oas_path(self._klayout_source_oas_path)
        self.klayout_panel.refresh_preview(
            self._klayout_source_oas_path if self._klayout_source_oas_path else None
        )
        self._schedule_live_klayout_update(delay_ms=0, force=True)

    def _make_live_klayout_output_path(self):
        if self._klayout_live_output_path:
            return self._klayout_live_output_path
        source = self._klayout_source_oas_path
        if not source:
            return None
        base, ext = os.path.splitext(os.path.basename(source))
        ext = ext or ".oas"
        safe_base = re.sub(r"[^A-Za-z0-9_.-]+", "_", base).strip("._") or "layout"
        out_dir = os.path.join(tempfile.gettempdir(), "symbolic_editor_klayout")
        os.makedirs(out_dir, exist_ok=True)
        self._klayout_live_output_path = os.path.join(
            out_dir, f"{safe_base}_{id(self):x}_live{ext}"
        )
        return self._klayout_live_output_path

    def _schedule_live_klayout_update(self, delay_ms=650, force=False):
        if not self.nodes or not self._klayout_source_oas_path or not self._klayout_source_sp_path:
            return
        if not force and self._workspace_mode == "symbolic" and not self.klayout_panel.isVisible():
            return
        self._klayout_live_timer.start(max(0, int(delay_ms)))

    def _collect_live_klayout_nodes(self):
        self._sync_node_positions(schedule_live=False)
        nodes = copy.deepcopy(self.nodes)
        try:
            abut_states = self.editor.get_device_abutment_states()
        except Exception:
            abut_states = {}
        for node in nodes:
            dev_id = node.get("id")
            if dev_id in abut_states:
                node["abutment"] = abut_states[dev_id]
            else:
                node.pop("abutment", None)
        return nodes

    @staticmethod
    def _write_live_klayout_file(oas_path, sp_path, nodes, output_path):
        from export.oas_writer import update_oas_placement

        return update_oas_placement(
            oas_path=oas_path,
            sp_path=sp_path,
            nodes=nodes,
            output_path=output_path,
        )

    def _run_live_klayout_update(self):
        if not self._klayout_source_oas_path or not self._klayout_source_sp_path:
            return
        if getattr(self.editor, "_rebuilding_scene", False):
            self._schedule_live_klayout_update(delay_ms=250, force=True)
            return
        if self._klayout_live_worker and self._klayout_live_worker.isRunning():
            self._klayout_live_pending = True
            return

        output_path = self._make_live_klayout_output_path()
        if not output_path:
            return

        try:
            nodes = self._collect_live_klayout_nodes()
        except Exception as exc:
            logging.debug("Failed to collect live KLayout nodes", exc_info=True)
            if hasattr(self.klayout_panel, "set_status_text"):
                self.klayout_panel.set_status_text(f"Live preview sync failed: {exc}")
            return

        if hasattr(self.klayout_panel, "set_status_text"):
            self.klayout_panel.set_status_text("Updating live KLayout preview...")

        self._klayout_live_generation += 1
        generation = self._klayout_live_generation
        worker = GenericWorker(
            self._write_live_klayout_file,
            self._klayout_source_oas_path,
            self._klayout_source_sp_path,
            nodes,
            output_path,
        )
        worker._live_generation = generation
        self._klayout_live_worker = worker
        worker.finished.connect(self._on_live_klayout_ready)
        worker.error.connect(self._on_live_klayout_error)
        worker.start()

    def _on_live_klayout_ready(self, output_path):
        worker = self.sender()
        if getattr(worker, "_live_generation", None) != self._klayout_live_generation:
            if worker is self._klayout_live_worker:
                self._klayout_live_worker = None
            if worker is not None:
                worker.deleteLater()
            if self._klayout_live_pending:
                self._klayout_live_pending = False
                self._schedule_live_klayout_update(delay_ms=50, force=True)
            return
        if worker is self._klayout_live_worker:
            self._klayout_live_worker = None
        if output_path and os.path.isfile(output_path):
            self.klayout_panel.refresh_preview(output_path)
        if worker is not None:
            worker.deleteLater()
        if self._klayout_live_pending:
            self._klayout_live_pending = False
            self._schedule_live_klayout_update(delay_ms=50, force=True)

    def _on_live_klayout_error(self, err_msg):
        worker = self.sender()
        if getattr(worker, "_live_generation", None) != self._klayout_live_generation:
            if worker is self._klayout_live_worker:
                self._klayout_live_worker = None
            if worker is not None:
                worker.deleteLater()
            if self._klayout_live_pending:
                self._klayout_live_pending = False
                self._schedule_live_klayout_update(delay_ms=250, force=True)
            return
        if worker is self._klayout_live_worker:
            self._klayout_live_worker = None
        if hasattr(self.klayout_panel, "set_status_text"):
            self.klayout_panel.set_status_text(f"Live preview error: {err_msg}")
        logging.debug("Live KLayout preview update failed: %s", err_msg)
        if worker is not None:
            worker.deleteLater()
        if self._klayout_live_pending:
            self._klayout_live_pending = False
            self._schedule_live_klayout_update(delay_ms=250, force=True)

    def _refresh_panels(self, compact=False, force_schematic_ai=False):
        if not self._original_data:
            return
        edges = self._original_data.get("edges")
        blocks = self._original_data.get("blocks", {})
        if not blocks and self.nodes:
            for node in self.nodes:
                b = node.get("block")
                if b:
                    inst = b.get("instance", "")
                    if inst and inst not in blocks:
                        blocks[inst] = {"subckt": b.get("subckt", "?"), "devices": []}
                    if inst:
                        blocks[inst]["devices"].append(node.get("id"))
            if blocks:
                self._original_data["blocks"] = blocks
        self._blocks = blocks
        self.device_tree.set_edges(edges)
        self.device_tree.set_terminal_nets(self._terminal_nets)
        self.device_tree.set_groups(self.groups)
        self.device_tree.load_devices(self.nodes, blocks=blocks)
        self.editor.load_placement(self.nodes, compact=compact)
        self.editor.apply_custom_groups(self._custom_groups)
        self.editor.set_edges(edges)
        self.editor.set_terminal_nets(self._terminal_nets)
        self.editor.set_blocks(blocks)
        # Keep schematic data current, but only render/AI-layout it after the
        # user explicitly opens the schematic panel from the toolbar/menu.
        self.schematic_panel.set_editor(self.editor)
        self.schematic_panel.set_data(self.nodes, self._terminal_nets)
        if self.schematic_panel.isVisible():
            self.schematic_panel.refresh(force_ai=force_schematic_ai)
        self.chat_panel.set_layout_context(
            self.nodes, self._original_data.get("edges"), self._terminal_nets,
        )
        for item in self.editor.device_items.values():
            item.signals.drag_started.connect(
                lambda item=item: self._on_device_drag_start(item)
            )
            item.signals.drag_finished.connect(
                lambda item=item: self._on_device_drag_end(item)
            )
            if hasattr(item.signals, "position_changed"):
                item.signals.position_changed.connect(
                    lambda: self._schedule_live_klayout_update()
                )
                item.signals.position_changed.connect(
                    lambda item=item: self._on_device_position_changed(item)
                )
        self._update_grid_counts()
        self._on_editor_selection_changed()
        self._schedule_live_klayout_update()
        # Sync canvas positions back to self.nodes after any compaction that may
        # have run inside set_terminal_nets, so the layout context passed to the
        # AI always reflects the actual on-canvas positions.
        self._sync_node_positions(schedule_live=False)

    # =================================================================
    #  Selection / grid helpers
    # =================================================================
    def _find_node(self, dev_id):
        for node in self.nodes or []:
            if node.get("id") == dev_id:
                return node
        return None

    def _show_device_properties(self, dev_id):
        node = self._find_node(dev_id)
        block_id = ((node or {}).get("block") or {}).get("instance")
        block_data = self._blocks.get(block_id, {}) if block_id else {}
        self.properties_panel.show_device_properties(
            dev_id,
            node,
            terminal_nets=self._terminal_nets,
            block_data=block_data,
        )

    def _show_selected_device_properties(self):
        selected_ids = self.editor.selected_device_ids() if self.editor else []
        if len(selected_ids) == 1:
            self._left_splitter.setVisible(True)
            self._tree_reopen_strip.setVisible(False)
            self._show_device_properties(selected_ids[0])
            self._show_sidebar_panel(1)
            self.properties_panel.setFocus(Qt.FocusReason.OtherFocusReason)
        elif len(selected_ids) == 0:
            self.chat_panel._append_message(
                "AI",
                "Select one device first to show its properties (Q).",
                "#fde8e8",
                "#a00",
            )
        else:
            self.chat_panel._append_message(
                "AI",
                "Select a single device to open properties (Q).",
                "#fde8e8",
                "#a00",
            )

    def _sync_properties_from_selection(self):
        selected_ids = self.editor.selected_device_ids()
        if len(selected_ids) == 1:
            self._show_device_properties(selected_ids[0])
        elif len(selected_ids) > 1:
            self.properties_panel.clear_properties(
                f"{len(selected_ids)} devices selected.\nPick one device to inspect its details."
            )
        else:
            self.properties_panel.clear_properties()

    def _on_tree_device_selected(self, dev_id):
        self.editor.highlight_device(dev_id)
        self._show_device_properties(dev_id)
        self._update_grid_counts()

    def _on_tree_block_selected(self, block_id):
        block_data = self._blocks.get(block_id, {})
        self.properties_panel.show_block_properties(block_id, block_data)

    def _on_connection_selected(self, dev_id, net_name, _other):
        if net_name and net_name != "?":
            self.editor.highlight_net_by_name(net_name)
        else:
            self.editor.highlight_device(dev_id)
        self._show_device_properties(dev_id)

    def _on_canvas_device_clicked(self, dev_id):
        self._show_device_properties(dev_id)
        self._update_grid_counts()

    def _on_editor_selection_changed(self):
        self._sync_properties_from_selection()
        self.selection_changed.emit(self.selection_count())

    def _update_grid_counts(self):
        if not hasattr(self, "editor") or not self.editor:
            return
        items = self.editor.device_items.values()
        if not items:
            self.grid_changed.emit(0, 0, 1, 1)
            return
        row_idx = {int(round(it.pos().y() / self.editor._row_pitch)) for it in items}
        col_idx = {int(round(it.pos().x() / self.editor._snap_grid)) for it in items}
        actual_rows = len(row_idx)
        actual_cols = len(col_idx)
        shown_rows = max(actual_rows, self._rows_virtual_min)
        shown_cols = max(actual_cols, self._cols_virtual_min)
        self.grid_changed.emit(
            shown_rows, shown_cols, max(actual_rows, 1), max(actual_cols, 1)
        )

    def set_row_target(self, value):
        """Called by MainWindow when the row spinbox changes."""
        items = self.editor.device_items.values()
        if not items:
            return
        row_idx = {int(round(it.pos().y() / self.editor._row_pitch)) for it in items}
        col_idx = {int(round(it.pos().x() / self.editor._snap_grid)) for it in items}
        actual = len(row_idx)
        self._rows_virtual_min = max(actual, value)
        cols = max(len(col_idx), self._cols_virtual_min, 1)
        self.editor.set_virtual_extents(self._rows_virtual_min, cols)
        self.editor.ensure_grid_extent(self._rows_virtual_min, cols)
        self._update_grid_counts()

    def set_col_target(self, value):
        """Called by MainWindow when the col spinbox changes."""
        items = self.editor.device_items.values()
        if not items:
            return
        col_idx = {int(round(it.pos().x() / self.editor._snap_grid)) for it in items}
        row_idx = {int(round(it.pos().y() / self.editor._row_pitch)) for it in items}
        actual = len(col_idx)
        self._cols_virtual_min = max(actual, value)
        rows = max(len(row_idx), self._rows_virtual_min, 1)
        self.editor.set_virtual_extents(rows, self._cols_virtual_min)
        self.editor.ensure_grid_extent(rows, self._cols_virtual_min)
        self._update_grid_counts()

    # =================================================================
    #  Build output / Undo / Redo
    # =================================================================
    def _build_output_data(self):
        self._sync_node_positions()
        output = {"nodes": copy.deepcopy(self.nodes)}
        if "edges" in self._original_data:
            output["edges"] = self._original_data["edges"]
        if hasattr(self, "_routing_annotations") and self._routing_annotations:
            output["routing_annotations"] = copy.deepcopy(self._routing_annotations)
        if self._custom_groups:
            output["custom_groups"] = copy.deepcopy(self._custom_groups)
        return output

    def _push_undo(self):
        if not self.nodes:
            return
        self._undo_stack.append(copy.deepcopy(self.nodes))
        self._redo_stack.clear()
        self._update_undo_redo_state()

    def _update_undo_redo_state(self):
        self.undo_state_changed.emit(bool(self._undo_stack), bool(self._redo_stack))

    def _drag_items(self, dragged_item=None):
        try:
            items = [
                it for it in self.editor.scene.selectedItems()
                if hasattr(it, "device_name") and hasattr(it, "setPos")
            ]
        except RuntimeError:
            items = []
        if dragged_item is not None and dragged_item not in items:
            items.append(dragged_item)
        if (
            dragged_item is not None
            and getattr(self.editor, "_moving_groups_only", False)
            and hasattr(dragged_item, "device_name")
        ):
            for item in self._group_items_for_device(dragged_item.device_name):
                if item and item not in items:
                    items.append(item)
        return items

    def _capture_drag_start_positions(self, dragged_item, items):
        positions = {}
        delta = QPointF()
        if dragged_item is not None and hasattr(dragged_item, "_drag_start_pos"):
            delta = dragged_item.pos() - dragged_item._drag_start_pos

        for item in items:
            if item is dragged_item and hasattr(item, "_drag_start_pos"):
                start_pos = item._drag_start_pos
            else:
                start_pos = item.pos() - delta
            positions[item] = QPointF(start_pos)
        self._drag_start_positions = positions

    @staticmethod
    def _rects_overlap(a, b, epsilon=0.5):
        intersection = a.intersected(b)
        return intersection.width() > epsilon and intersection.height() > epsilon

    def _drag_overlaps_stationary_items(self, drag_items):
        moving = set(drag_items)
        stationary = [
            item for item in self.editor.device_items.values()
            if item not in moving
        ]
        for moving_item in drag_items:
            moving_rect = moving_item.sceneBoundingRect()
            for other in stationary:
                if self._rects_overlap(moving_rect, other.sceneBoundingRect()):
                    return True
        return False

    def _restore_drag_start_positions(self):
        positions = getattr(self, "_drag_start_positions", {})
        if not positions:
            return
        self.editor.scene.blockSignals(True)
        try:
            for item, pos in positions.items():
                try:
                    item.setPos(pos)
                except RuntimeError:
                    pass
        finally:
            self.editor.scene.blockSignals(False)

    def _on_device_drag_start(self, dragged_item=None):
        if dragged_item is not None and getattr(self.editor, "_moving_groups_only", False):
            group_items = self._group_items_for_device(dragged_item.device_name)
            if group_items:
                self._active_group_drag = {
                    "leader": dragged_item.device_name,
                    "members": [
                        it.device_name for it in group_items
                        if hasattr(it, "device_name")
                    ],
                }
                self._group_drag_last_pos = {
                    dragged_item.device_name: QPointF(dragged_item.pos())
                }
        drag_items = self._drag_items(dragged_item)
        self._capture_drag_start_positions(dragged_item, drag_items)
        try:
            for it in drag_items:
                if hasattr(it, "set_snap_grid"):
                    it.set_snap_grid(self.editor._snap_grid, self.editor._row_pitch)
        except RuntimeError:
            pass
        self._sync_node_positions()
        self._push_undo()

    def _on_device_drag_end(self, dragged_item=None):
        if getattr(self.editor, "_rebuilding_scene", False):
            self._drag_start_positions = {}
            return

        positions = getattr(self, "_drag_start_positions", {})
        drag_items = list(positions.keys())

        if positions and self._drag_overlaps_stationary_items(drag_items):
            self._restore_drag_start_positions()
            if self._undo_stack:
                self._undo_stack.pop()
                self._update_undo_redo_state()

        selected_ids = self.editor.selected_device_ids()
        if selected_ids and selected_ids[0] in self.editor.device_items:
            try:
                self.editor._show_connections(selected_ids[0])
            except RuntimeError:
                logging.debug("Skipped routing redraw for deleted selected item", exc_info=True)

        self._drag_start_positions = {}
        self._active_group_drag = None
        self._group_drag_last_pos = {}
        self._sync_node_positions()
        self._update_grid_counts()
        self.editor.refresh_hierarchy_group_geometry()

    def _on_device_position_changed(self, item):
        if not getattr(self.editor, "_moving_groups_only", False):
            return
        if not item or getattr(item, "_propagating_move", False):
            return
        active = getattr(self, "_active_group_drag", None)
        if not active:
            if not hasattr(item, "device_name"):
                return
            group_items = self._group_items_for_device(item.device_name)
            if not group_items:
                return
            active = {
                "leader": item.device_name,
                "members": [
                    it.device_name for it in group_items
                    if hasattr(it, "device_name")
                ],
            }
            self._active_group_drag = active
            start_pos = getattr(item, "_drag_start_pos", None)
            if start_pos is not None:
                dx = item.pos().x() - start_pos.x()
                dy = item.pos().y() - start_pos.y()
                if abs(dx) < 1e-9 and abs(dy) < 1e-9:
                    self._group_drag_last_pos[item.device_name] = QPointF(item.pos())
                    return
                for dev_id in active.get("members", []):
                    if dev_id == item.device_name:
                        continue
                    sibling = self.editor.device_items.get(dev_id)
                    if not sibling:
                        continue
                    sibling._propagating_move = True
                    sibling.moveBy(dx, dy)
                    sibling._propagating_move = False
                self._group_drag_last_pos[item.device_name] = QPointF(item.pos())
                return
        if active.get("leader") != getattr(item, "device_name", None):
            return
        last_pos = self._group_drag_last_pos.get(item.device_name)
        if last_pos is None:
            self._group_drag_last_pos[item.device_name] = QPointF(item.pos())
            return
        dx = item.pos().x() - last_pos.x()
        dy = item.pos().y() - last_pos.y()
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            return
        for dev_id in active.get("members", []):
            if dev_id == item.device_name:
                continue
            sibling = self.editor.device_items.get(dev_id)
            if not sibling:
                continue
            sibling._propagating_move = True
            sibling.moveBy(dx, dy)
            sibling._propagating_move = False
        self._group_drag_last_pos[item.device_name] = QPointF(item.pos())

    def _hierarchy_drag_items(self, group):
        groups = []
        try:
            groups = [
                item for item in self.editor.scene.selectedItems()
                if isinstance(item, HierarchyGroupItem)
            ]
        except RuntimeError:
            groups = []
        if group is not None and group not in groups:
            groups.append(group)

        drag_items = []
        seen = set()
        for grp in groups:
            try:
                descendants = grp.get_all_descendant_devices()
            except RuntimeError:
                continue
            for item in descendants:
                key = id(item)
                if key in seen:
                    continue
                if hasattr(item, "device_name") and hasattr(item, "setPos"):
                    drag_items.append(item)
                    seen.add(key)
        return drag_items

    def _on_hierarchy_drag_start(self, group):
        drag_items = self._hierarchy_drag_items(group)
        if not drag_items:
            return
        delta = QPointF()
        try:
            delta = group.pos() - group._drag_start_pos
        except RuntimeError:
            delta = QPointF()
        self._drag_start_positions = {
            item: QPointF(item.pos() - delta)
            for item in drag_items
        }
        self._sync_node_positions()
        self._push_undo()

    def _on_hierarchy_drag_end(self, group):
        if getattr(self.editor, "_rebuilding_scene", False):
            self._drag_start_positions = {}
            return

        positions = getattr(self, "_drag_start_positions", {})
        drag_items = list(positions.keys())
        if positions and self._drag_overlaps_stationary_items(drag_items):
            self._restore_drag_start_positions()
            if self._undo_stack:
                self._undo_stack.pop()
                self._update_undo_redo_state()

        self._drag_start_positions = {}
        self._sync_node_positions()
        self._update_grid_counts()
        self.editor.refresh_hierarchy_group_geometry()

    def _on_hierarchy_changed(self):
        """Refresh the device tree when hierarchy groups are created or deleted."""
        try:
            # Build custom groups list from editor hierarchy groups (user-created groups)
            custom_groups = []
            try:
                dev_item_to_id = {v: k for k, v in self.editor.device_items.items()}
                iter_groups = getattr(self.editor, "_iter_live_hierarchy_groups", None)
                groups = iter_groups() if callable(iter_groups) else list(
                    getattr(self.editor, '_hierarchy_groups', [])
                )
                for g in groups:
                    name = getattr(g, '_parent_name', '')
                    if not getattr(g, '_is_custom_group', False) and not name.startswith('GROUP_'):
                        continue
                    dev_ids = []
                    for dev_item in getattr(g, '_all_descendant_devices', []):
                        dev_id = dev_item_to_id.get(dev_item)
                        if dev_id:
                            dev_ids.append(dev_id)
                    if dev_ids:
                        custom_groups.append({'name': name, 'devices': dev_ids})
            except Exception:
                custom_groups = []

            self._custom_groups = custom_groups
            if self._original_data is not None:
                self._original_data["custom_groups"] = copy.deepcopy(custom_groups)

            # Provide custom groups to the device tree and reload
            try:
                self.device_tree.set_custom_groups(custom_groups)
            except Exception:
                pass
            self.device_tree.load_devices(self.nodes)
        except Exception as e:
            logging.debug(f"Failed to refresh device tree after hierarchy change: {e}")

    def do_undo(self):
        if not self._undo_stack:
            return
        self._sync_node_positions()
        self._redo_stack.append(copy.deepcopy(self.nodes))
        self.nodes = self._undo_stack.pop()
        self._original_data["nodes"] = self.nodes
        self._refresh_panels(compact=True)
        self._update_undo_redo_state()

    def do_redo(self):
        if not self._redo_stack:
            return
        self._sync_node_positions()
        self._undo_stack.append(copy.deepcopy(self.nodes))
        self.nodes = self._redo_stack.pop()
        self._original_data["nodes"] = self.nodes
        self._refresh_panels()
        self._update_undo_redo_state()
    
    def _handle_tree_group_deletion(self, group_name):
        # Find the group item in the editor by name
        group_item = self.editor.find_group_by_name(group_name)
        if group_item:
            self.editor._delete_group(group_item)

    def _handle_group_delete(self, group_name):
        target_group = self.editor.find_group_by_name(group_name)
        if target_group:
            self.editor._delete_group(target_group)
    # =================================================================
    #  Select All / Swap / Merge / Flip / Delete
    # =================================================================
    def do_select_all(self):
        for item in self.editor.device_items.values():
            item.setSelected(True)

    def do_swap(self):
        selected = self.editor.selected_device_ids()
        if len(selected) != 2:
            self.chat_panel._append_message("AI", "Select exactly 2 devices to swap.", "#fde8e8", "#a00")
            return
        self._sync_node_positions()
        self._push_undo()
        self.editor.swap_devices(selected[0], selected[1])
        self._sync_node_positions()

    def do_merge_ss(self):
        self._merge_selected_devices(mode="SS")

    def do_merge_dd(self):
        self._merge_selected_devices(mode="DD")

    def _merge_selected_devices(self, mode="SS"):
        selected = self.editor.selected_device_ids()
        if len(selected) != 2:
            self.chat_panel._append_message("AI", "Select exactly 2 devices to merge.", "#fde8e8", "#a00")
            return
        id_a, id_b = selected[0], selected[1]
        a = self.editor.device_items.get(id_a)
        b = self.editor.device_items.get(id_b)
        if not a or not b:
            return
        if getattr(a, "device_type", None) != getattr(b, "device_type", None):
            self.chat_panel._append_message("AI", "Merge requires same device type.", "#fde8e8", "#a00")
            return
        self._sync_node_positions()
        self._push_undo()
        y = self.editor._snap_row((a.pos().y() + b.pos().y()) / 2.0)
        wa = a.rect().width()
        wb = b.rect().width()
        if mode == "SS":
            if hasattr(a, "set_flip_h"): a.set_flip_h(False)
            if hasattr(b, "set_flip_h"): b.set_flip_h(True)
            ax = self.editor._snap_value(a.pos().x())
            bx = self.editor._snap_value(ax - wb)
            a.setPos(ax, y); b.setPos(bx, y)
        else:
            if hasattr(a, "set_flip_h"): a.set_flip_h(False)
            if hasattr(b, "set_flip_h"): b.set_flip_h(True)
            ax = self.editor._snap_value(a.pos().x())
            bx = self.editor._snap_value(ax + wa)
            a.setPos(ax, y); b.setPos(bx, y)
        self.editor.resolve_overlaps(anchor_ids=[id_a, id_b])
        self._sync_node_positions()

    def do_flip_h(self):
        selected = self.editor.selected_device_ids()
        if not selected:
            return
        self._sync_node_positions(); self._push_undo()
        self.editor.flip_devices_h(selected)
        self._sync_node_positions()

    def do_flip_v(self):
        selected = self.editor.selected_device_ids()
        if not selected:
            return
        self._sync_node_positions(); self._push_undo()
        self.editor.flip_devices_v(selected)
        self._sync_node_positions()

    def do_delete(self):
        selected = self.editor.scene.selectedItems()
        if not selected:
            return
        self._sync_node_positions(); self._push_undo()
        for item in selected:
            if hasattr(item, "device_name"):
                dev_id = item.device_name
                self.nodes = [n for n in self.nodes if n.get("id") != dev_id]
                self._original_data["nodes"] = self.nodes
                if dev_id in self.editor.device_items:
                    del self.editor.device_items[dev_id]
                self.editor.scene.removeItem(item)
        self.device_tree.load_devices(self.nodes)
        self._update_undo_redo_state()
        self._schedule_live_klayout_update(delay_ms=100)

    # =================================================================
    #  Match Devices
    # =================================================================
    def do_match(self):
        selected = self.editor.selected_device_ids()
        if len(selected) < 2:
            self.chat_panel._append_message(
                "AI", "Select at least 2 devices to match.\nUse Ctrl+Click to select multiple devices.",
                "#fde8e8", "#a00",
            )
            return
        types = set()
        for sid in selected:
            item = self.editor.device_items.get(sid)
            if item:
                types.add(getattr(item, "device_type", "?"))
        if len(types) > 1:
            self.chat_panel._append_message(
                "AI", "All selected devices must be the same type.", "#fde8e8", "#a00",
            )
            return
        dlg = _MatchDialog(selected, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        technique = dlg.get_technique()
        custom_pattern = dlg.get_custom_pattern() if technique == "custom" else None
        self._apply_matching(selected, technique, custom_pattern)

    def _apply_matching(self, device_ids, technique, custom_str=None):
        self._sync_node_positions(); self._push_undo()
        engine = MatchingEngine(self.editor.device_items)
        try:
            placements = engine.generate_placement(device_ids, technique, custom_str)
            snap = self.editor._snap_value
            for p in placements:
                item = self.editor.device_items.get(p["id"])
                if item:
                    item.setPos(snap(p["x"]), snap(p["y"]))
            self._calculate_and_draw_centroids(device_ids, technique)
            self._matched_groups.append({"ids": list(device_ids), "technique": technique})
            if technique == "interdigitated":      color = QColor("#4FC3F7")
            elif technique == "common_centroid_2d": color = QColor("#CE93D8")
            elif technique == "custom":             color = QColor("#FFD54F")
            else:                                   color = QColor("#AED581")
            for did in device_ids:
                item = self.editor.device_items.get(did)
                if item and hasattr(item, "set_match_highlight"):
                    item.set_match_highlight(color)
            self.chat_panel._append_message(
                "AI",
                f"Successfully applied {technique.replace('_', ' ')} matching.\n"
                "✓ Analytical Audit: All centroids aligned at grid center.",
                "#e8f4fd", "#1a1a2e",
            )
        except Exception as e:
            for did in device_ids:
                item = self.editor.device_items.get(did)
                if item and hasattr(item, "set_match_highlight"):
                    item.set_match_highlight(QColor("#FF5252"))
            self.chat_panel._append_message(
                "AI", f"Matching Failed: {e}\nCentroids misaligned!", "#fde8e8", "#a00",
            )
        self._sync_node_positions()

    def _calculate_and_draw_centroids(self, device_ids, technique):
        import re as _re
        parent_map = {}
        for did in device_ids:
            m = _re.match(r"^([A-Za-z]+\d+)", did)
            p = m.group(1) if m else did
            parent_map.setdefault(p, []).append(did)
        markers = []
        colors = [QColor("#4FC3F7"), QColor("#CE93D8"), QColor("#AED581"), QColor("#FFD54F")]
        for i, (parent, ids) in enumerate(parent_map.items()):
            sx, sy = 0.0, 0.0
            for did in ids:
                item = self.editor.device_items.get(did)
                if item:
                    br = item.boundingRect()
                    pos = item.pos()
                    sx += pos.x() + br.width() / 2.0
                    sy += pos.y() + br.height() / 2.0
            markers.append({"x": sx / len(ids), "y": sy / len(ids),
                            "color": colors[i % len(colors)], "label": parent})
        self.editor.set_centroid_markers(markers)

    # =================================================================
    #  Matched Group Helpers
    # =================================================================
    def _is_device_locked(self, device_id):
        return any(device_id in g["ids"] for g in self._matched_groups)

    def _get_device_group(self, device_id):
        for g in self._matched_groups:
            if device_id in g["ids"]:
                return g
        return None

    def _move_matched_group_as_block(self, group, target_x, target_y):
        positions = []
        for gid in group["ids"]:
            item = self.editor.device_items.get(gid)
            if item:
                positions.append((gid, item, item.pos().x(), item.pos().y()))
        if not positions:
            return 0
        cur_min_x = min(p[2] for p in positions)
        cur_min_y = min(p[3] for p in positions)
        dx = target_x - cur_min_x
        dy = target_y - cur_min_y
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            return 0
        for gid, item, old_x, old_y in positions:
            item.setPos(old_x + dx, old_y + dy)
        return len(positions)

    def do_unlock_match(self):
        selected = self.editor.selected_device_ids()
        if not selected:
            self.chat_panel._append_message("AI", "Select devices from a matched group to unlock.", "#fde8e8", "#a00")
            return
        groups_to_remove = []
        for group in self._matched_groups:
            for sid in selected:
                if sid in group["ids"]:
                    groups_to_remove.append(group)
                    break
        if not groups_to_remove:
            self.chat_panel._append_message("AI", "None of the selected devices are in a matched group.", "#fde8e8", "#a00")
            return
        self._push_undo()
        dissolved = 0
        for group in groups_to_remove:
            for gid in group["ids"]:
                item = self.editor.device_items.get(gid)
                if item and hasattr(item, "clear_match_highlight"):
                    item.clear_match_highlight()
            if group in self._matched_groups:
                self._matched_groups.remove(group)
                dissolved += 1
        total_devs = sum(len(g["ids"]) for g in groups_to_remove)
        self.chat_panel._append_message(
            "AI",
            f"🔓 Unlocked {dissolved} matched group(s) ({total_devs} devices).\n"
            "These devices can now be moved individually.",
            "#fff3e0", "#e65100",
        )

    # =================================================================
    #  Dummy / Abutment toggles (called by MainWindow toolbar)
    # =================================================================
    def set_dummy_mode(self, enabled):
        self._dummy_mode = enabled
        self.editor.set_dummy_mode(enabled)
        msg = "Dummy mode ON: move over PMOS/NMOS row and click to place." if enabled else "Dummy mode OFF."
        self.chat_panel._append_message("AI", msg, "#e8f4fd", "#1a1a2e")

    def _on_toggle_route(self, checked: bool):
        routing_result = getattr(self, "_routing_result", {})
        channels = routing_result.get("channels", [])
        if not channels:
            self.chat_panel._append_message("AI", "No routing channels available. Run placement first.", "#fff3e0", "#e65100")
            return

        sorted_channels = sorted(channels, key=lambda c: c.get("y_boundary", 0.0))
        from config.design_rules import ROW_HEIGHT_UM
        
        shifts = []
        for ch in sorted_channels:
            y_boundary = ch.get("y_boundary", 0.0)
            current_w = ch.get("current_width_um", 0.0)
            target_w = ch.get("recommended_width_um", 0.0)
            if target_w > current_w:
                shift_amount = target_w - current_w
                shift_amount = round(shift_amount / ROW_HEIGHT_UM) * ROW_HEIGHT_UM
                if shift_amount > 0:
                    shifts.append({"y_boundary": y_boundary, "shift": shift_amount if checked else -shift_amount})

        if not shifts:
            self.chat_panel._append_message("AI", "No channels needed widening.", "#f1f5f9", "#475569")
            return

        self._push_undo()
        for node in self.nodes:
            geo = node.get("geometry", {})
            if "y" in geo:
                if checked:
                    # Expanding channels: calculate shift based on current Y
                    node_y = float(geo["y"])
                    total_shift = sum(sh["shift"] for sh in shifts if node_y > sh["y_boundary"])
                    if total_shift != 0.0:
                        geo["y"] = round(node_y + total_shift, 6)
                        node["_routing_shift"] = total_shift
                else:
                    # Removing channels: revert the exact shift we applied
                    stored_shift = node.pop("_routing_shift", None)
                    if stored_shift is not None:
                        geo["y"] = round(float(geo["y"]) - stored_shift, 6)
        
        self._refresh_panels(compact=False)
        action = "Inserted" if checked else "Removed"
        self.chat_panel._append_message("AI", f"{action} {len(shifts)} routing channel(s).", "#f0fdf4", "#166534")

    def set_colorize_mode(self, enabled):
        self._colorize_mode = enabled
        self.editor.set_colorize_mode(enabled)

    def set_abutment_mode(self, enabled):
        self._abutment_mode = enabled
        if enabled:
            candidates = self.editor.apply_abutment()
            self._abutment_candidates = candidates
            n = len(candidates)
            if n == 0:
                msg = "⚠️ No abutment candidates found.\nNo two same-type transistors share a Source or Drain net."
            else:
                lines = [f"✅ Found {n} abutment candidate(s) — highlighted in 🟢 green:\n"]
                for c in candidates:
                    flip_note = " (flip needed)" if c["needs_flip"] else ""
                    lines.append(
                        f"  • {c['dev_a']}.{c['term_a']} ↔ "
                        f"{c['dev_b']}.{c['term_b']}  "
                        f"[net: {c['shared_net']}]{flip_note}"
                    )
                lines.append("\nWhen you run AI Placement, these constraints will be injected.")
                msg = "\n".join(lines)
        else:
            self.editor.clear_abutment()
            self._abutment_candidates = []
            msg = "Abutment analysis cleared."
        self.chat_panel._append_message("AI", msg, "#e8f4fd", "#1a1a2e")

    def set_moving_groups_only(self, enabled):
        """Enable/disable moving groups only mode."""
        self.editor.set_moving_groups_only(enabled)
        if not enabled:
            self._active_group_drag = None
            self._group_drag_last_pos = {}

    # =================================================================
    #  Dummy helpers
    # =================================================================
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
            (n for n in self.nodes if str(n.get("type", "")).strip().lower() == dev_type),
            None,
        )
        electrical = {"l": 1.4e-08, "nf": 1, "nfin": 1}
        if template:
            electrical = copy.deepcopy(template.get("electrical", electrical))
        x = candidate["x"] / self.editor.scale_factor
        # Candidate coordinates come from QGraphicsScene, where y is inverted
        # relative to layout geometry.
        y = -candidate["y"] / self.editor.scale_factor
        width = candidate["width"] / self.editor.scale_factor
        height = candidate["height"] / self.editor.scale_factor
        dummy = {
            "id": self._next_dummy_id(dev_type),
            "type": dev_type,
            "is_dummy": True,
            "dummy_source": "user",
            "electrical": electrical,
            "geometry": {"x": x, "y": y, "width": width, "height": height, "orientation": "R0"},
        }
        if template:
            if template.get("layout_index") is not None:
                dummy["template_layout_index"] = template.get("layout_index")
            if template.get("layout_cell"):
                dummy["layout_cell"] = template.get("layout_cell")
        return dummy

    def _dummy_row_step(self, dev_type):
        """Return the next same-type dummy row step in scene coordinates."""
        return -self.editor._row_pitch if dev_type == "pmos" else self.editor._row_pitch

    def _dummy_col_capacity(self):
        """Only enforce column capacity after the user explicitly expands cols."""
        return int(self._cols_virtual_min) if self._cols_virtual_min > 0 else None

    def _row_type_count(self, row_y, dev_type):
        return sum(
            1 for it in self.editor.device_items.values()
            if self.editor._snap_row(it.pos().y()) == row_y
            and getattr(it, "device_type", None) == dev_type
        )

    def _row_edge_target_x(self, row_y, width, side, dev_type=None):
        row_items = [
            it for it in self.editor.device_items.values()
            if self.editor._snap_row(it.pos().y()) == row_y
            and (dev_type is None or getattr(it, "device_type", None) == dev_type)
        ]
        if not row_items:
            return 0.0
        if side == "right":
            return self.editor._snap_value(max(it.pos().x() + it.rect().width() for it in row_items))
        return self.editor._snap_value(min(it.pos().x() for it in row_items) - width)

    def _legalize_dummy_candidate(self, candidate, side=None):
        candidate = dict(candidate)
        dev_type = str(candidate.get("type", "")).strip().lower()
        candidate["type"] = dev_type
        candidate["y"] = self.editor._snap_row(candidate["y"])
        candidate["x"] = self.editor._snap_value(candidate["x"])
        preserve_position = bool(candidate.get("preserve_position")) and side is None
        if preserve_position:
            return candidate

        col_capacity = self._dummy_col_capacity()
        if col_capacity is not None:
            for _ in range(max(len(self.editor.device_items) + 1, 1)):
                if self._row_type_count(candidate["y"], dev_type) < col_capacity:
                    break
                candidate["y"] = self.editor._snap_row(candidate["y"] + self._dummy_row_step(dev_type))
                candidate["x"] = self._row_edge_target_x(
                    candidate["y"], candidate["width"], side or "right", dev_type
                )

        if side in {"left", "right"}:
            candidate["x"] = self._row_edge_target_x(
                candidate["y"], candidate["width"], side, dev_type
            )

        candidate["x"] = self.editor.find_nearest_free_x(
            row_y=candidate["y"],
            width=candidate["width"],
            target_x=candidate["x"],
            exclude_id=None,
        )
        return candidate

    def _add_dummy_device(self, candidate):
        self._sync_node_positions(); self._push_undo()
        candidate = self._legalize_dummy_candidate(candidate)
        dummy = self._build_dummy_node(candidate)
        self.nodes.append(dummy)
        self._original_data["nodes"] = self.nodes
        self._refresh_panels(compact=False)
        self._sync_node_positions()
        self.chat_panel._append_message(
            "AI", f"Added dummy {dummy['id']} ({dummy['type']}).", "#e8f4fd", "#1a1a2e",
        )

    # =================================================================
    #  Import from Netlist + Layout
    # =================================================================
    def load_example(self, sp_path, oas_path):
        self._pending_oas_path = oas_path or None
        self.overlay.show_message(f"Loading {os.path.basename(sp_path)}...")
        self._import_worker = GenericWorker(self._run_parser_pipeline, sp_path, oas_path, True)
        self._import_worker.finished.connect(lambda data: self._on_import_completed(data, sp_path))
        self._import_worker.error.connect(self._on_import_error)
        self._import_worker.start()

    def do_import(self):
        dlg = ImportDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        sp_path = dlg.sp_path
        oas_path = dlg.oas_path
        self._pending_oas_path = oas_path or None
        abutment_enabled = dlg.is_abutment_enabled()
        self.overlay.show_message("Parsing design files...")
        self._import_worker = GenericWorker(self._run_parser_pipeline, sp_path, oas_path, abutment_enabled)
        self._import_worker.finished.connect(lambda data: self._on_import_completed(data, sp_path))
        self._import_worker.error.connect(self._on_import_error)
        self._import_worker.start()

    def _on_import_completed(self, data, sp_path):
        base_name = os.path.splitext(os.path.basename(sp_path))[0]
        sp_dir = os.path.dirname(os.path.abspath(sp_path))
        out_path = os.path.join(sp_dir, f"{base_name}_graph.json")
        with open(out_path, "w") as f:
            json.dump(data, f, indent=4)
        original_size = os.path.getsize(out_path)
        compressed_path = os.path.join(sp_dir, f"{base_name}_graph_compressed.json")
        try:
            compressed_data = self._compress_graph_for_storage(data)
            with open(compressed_path, "w") as f:
                json.dump(compressed_data, f, indent=4)
            compressed_size = os.path.getsize(compressed_path)
            reduction = (1 - compressed_size / original_size) * 100 if original_size > 0 else 0
        except Exception:
            logging.warning("Failed to compress graph data, using full format", exc_info=True)
            compressed_path = out_path
            reduction = 0
        # Match the normal undo/open refresh path immediately after import:
        # snap to the editor grid, compact rows, and sync node coordinates once.
        self._load_from_data_dict(data, out_path, compact=True, force_schematic_ai=True)
        self._sync_klayout_source(explicit_oas=self._pending_oas_path, source_path=sp_path)
        self._pending_oas_path = None
        num_nodes = len(data.get("nodes", []))
        msg = (
            f"Imported {num_nodes} devices from {os.path.basename(sp_path)}\n"
            f"Saved graph to: {os.path.basename(out_path)}\n"
            f"Saved compressed graph to: {os.path.basename(compressed_path)}\n"
            f"Size reduction: {reduction:.1f}% (for AI prompts)\n\n"
            f"To run AI initial placement: Design > Run AI Initial Placement (Ctrl+P)"
        )
        self.chat_panel._append_message("AI", msg, "#e8f4fd", "#1a1a2e")
        if not self.schematic_panel.isVisible():
            self.overlay.hide_overlay()

    def _on_import_error(self, err_msg):
        self.overlay.hide_overlay()
        self._pending_oas_path = None
        QMessageBox.critical(self, "Import Failed", f"Failed to parse design files:\n\n{err_msg}")

    # =================================================================
    #  Run AI Initial Placement
    # =================================================================
    def _collect_available_nets(self) -> list:
        """Return sorted unique non-supply net names from self._terminal_nets.

        Iterates every device's pin→net mapping, drops supply rail names
        (case-insensitive), deduplicates, and returns a sorted list suitable
        for populating the Critical Nets widget.
        """
        try:
            from ai_agent.placement.critical_nets import SUPPLY_NETS
        except ImportError:
            _SUPPLY = frozenset({"vdd", "vss", "gnd", "vcc", "vee", "avdd", "avss"})
            SUPPLY_NETS = _SUPPLY  # type: ignore[assignment]

        seen: set[str] = set()
        nets: list[str] = []
        for pin_map in (self._terminal_nets or {}).values():
            if not isinstance(pin_map, dict):
                continue
            for net_name in pin_map.values():
                if not isinstance(net_name, str):
                    continue
                net_stripped = net_name.strip()
                if not net_stripped:
                    continue
                if net_stripped.lower() in SUPPLY_NETS:
                    continue
                key = net_stripped.lower()
                if key in seen:
                    continue
                seen.add(key)
                nets.append(net_stripped)
        return sorted(nets)

    def _critical_nets_from_routing_annotations(self) -> dict | None:
        """Convert chat net-priority annotations into critical_nets config.

        Expected source shape:
            self._routing_annotations = {
                "VOUTP": {"priority": "high"},
                "VOUTN": {"priority": "medium"},
                ...
            }

        Returns:
            {"priority": "Low|Medium|High", "nets": [..]} or None.
        """
        ann = getattr(self, "_routing_annotations", None) or {}
        if not isinstance(ann, dict):
            return None

        level_to_weight = {"low": 0, "medium": 5, "high": 10}
        weight_to_level = {0: "Low", 5: "Medium", 10: "High"}

        nets: list[str] = []
        max_weight = 0
        seen: set[str] = set()

        for net_name, cfg in ann.items():
            if not isinstance(net_name, str):
                continue
            net = net_name.strip()
            if not net:
                continue
            if not isinstance(cfg, dict):
                continue
            prio = str(cfg.get("priority", "low")).strip().lower()
            weight = level_to_weight.get(prio, 0)
            if weight <= 0:
                continue
            key = net.lower()
            if key not in seen:
                seen.add(key)
                nets.append(net)
            if weight > max_weight:
                max_weight = weight

        if not nets or max_weight <= 0:
            return None
        return {"priority": weight_to_level[max_weight], "nets": nets}

    def do_ai_placement(self):
        if not self.nodes:
            self.chat_panel._append_message("AI", "No layout loaded. Import a netlist first (Ctrl+I).", "#fde8e8", "#a00")
            return
        dialog = AIModelSelectionDialog(self, available_nets=self._collect_available_nets())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        model_choice = dialog.get_selected_model()
        self.chat_panel.selected_model = model_choice
        if hasattr(self.chat_panel, '_model_combo'):
            self.chat_panel._model_combo.setCurrentText(model_choice)
        abutment_enabled = dialog.is_abutment_enabled()

        dialog.apply_api_keys()

        self._sync_node_positions()
        data = copy.deepcopy(self._build_output_data())

        # ── Strip dummies from previous runs ────────────────────────────
        # On re-run, old filler/edge dummies would pollute the pipeline's
        # device count, row-width balancing, and footprint calculations.
        # Keep imported layout dummies: they are physical devices that must
        # round-trip through the parser/export/live-preview path.
        def _is_dummy(n):
            if not isinstance(n, dict):
                return False
            if n.get("dummy_source") == "layout":
                return False
            if n.get("is_dummy"):
                return True
            nid = str(n.get("id", ""))
            return nid.startswith("FILLER_DUMMY") or nid.startswith("EDGE_DUMMY")

        orig_count = len(data.get("nodes", []))
        data["nodes"] = [n for n in data.get("nodes", []) if not _is_dummy(n)]
        stripped = orig_count - len(data["nodes"])
        if stripped:
            print(f"[do_ai_placement] Stripped {stripped} dummy device(s) from previous run")

        if "terminal_nets" not in data:
            data["terminal_nets"] = self._terminal_nets
        if abutment_enabled:
            data["abutment_candidates"] = getattr(self, "_abutment_candidates", [])
        else:
            data["abutment_candidates"] = []
        data["no_abutment"] = not abutment_enabled
        # Pass placement goals + critical nets into pipeline JSON
        goals = dialog.get_goals() or {}
        crit_dialog = dialog.get_critical_nets()          # None when panel collapsed
        use_chat_priority = bool(getattr(self, "_apply_chat_net_priority_next_run", False))
        crit_from_chat = (
            self._critical_nets_from_routing_annotations()
            if use_chat_priority else None
        )
        # One-shot consumption: if chat-triggered once, reset immediately so
        # future general runs stay byte-identical unless user triggers again.
        self._apply_chat_net_priority_next_run = False

        # Merge dialog critical nets with chat net-priority annotations.
        # Dialog selection wins for ordering; priority uses the stronger level.
        merged_crit = None
        if crit_dialog and crit_dialog.get("nets"):
            merged_crit = {
                "priority": crit_dialog.get("priority", "Low"),
                "nets": list(crit_dialog.get("nets") or []),
            }
        if crit_from_chat and crit_from_chat.get("nets"):
            if merged_crit is None:
                merged_crit = crit_from_chat
            else:
                p_rank = {"Low": 0, "Medium": 5, "High": 10}
                merged_priority = (
                    merged_crit.get("priority", "Low")
                    if p_rank.get(merged_crit.get("priority", "Low"), 0)
                    >= p_rank.get(crit_from_chat.get("priority", "Low"), 0)
                    else crit_from_chat.get("priority", "Low")
                )
                seen = {str(n).strip().lower() for n in merged_crit.get("nets", [])}
                for n in crit_from_chat.get("nets", []):
                    nn = str(n).strip()
                    if nn and nn.lower() not in seen:
                        merged_crit["nets"].append(nn)
                        seen.add(nn.lower())
                merged_crit["priority"] = merged_priority

        if merged_crit and merged_crit.get("nets"):
            goals["critical_nets"] = merged_crit
        data["placement_goals"] = goals or None
        self._last_placement_goals = data["placement_goals"]
        abut_label = "with abutment" if abutment_enabled else "no abutment"
        pipeline_label = "LangGraph"
        self.overlay.show_message(
            f"Running AI initial placement ({pipeline_label}, {model_choice}, {abut_label})...",
            show_cancel=True
        )
        self._saved_locked_positions = {}
        for group in self._matched_groups:
            for gid in group["ids"]:
                node = next((n for n in self.nodes if n.get("id") == gid), None)
                if node:
                    geo = node.get("geometry", {})
                    self._saved_locked_positions[gid] = {"x": geo.get("x", 0), "y": geo.get("y", 0)}
        self._ai_worker = GenericWorker(
            self._run_ai_initial_placement,
            data, model_choice, abutment_enabled,
        )
        self._ai_worker.finished.connect(self._on_ai_placement_completed)
        self._ai_worker.error.connect(self._on_ai_placement_error)
        self._ai_worker.start()

    def _cancel_ai_placement(self):
        if hasattr(self, "_ai_worker") and self._ai_worker and self._ai_worker.isRunning():
            self._ai_worker.terminate()
            self._ai_worker.wait()
            self.overlay.hide_overlay()
            
            if hasattr(self, "chat_panel"):
                self.chat_panel._append_message("AI", "❌ Placement process cancelled by user.", "#3d1a1a", "#ff6b6b")
                
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Cancelled", "Initial placement was cancelled.")

    def _on_ai_placement_completed(self, data):
        self.overlay.hide_overlay()
        saved = getattr(self, "_saved_locked_positions", {})
        if saved and "nodes" in data:
            for node in data["nodes"]:
                nid = node.get("id")
                if nid in saved:
                    node["geometry"]["x"] = saved[nid]["x"]
                    node["geometry"]["y"] = saved[nid]["y"]
        if self._current_file:
            base = os.path.splitext(self._current_file)[0]
            if base.endswith("_graph"):
                out_path = base.replace("_graph", "_initial_placement") + ".json"
            else:
                out_path = base + "_placed.json"
        else:
            out_path = os.path.join(os.getcwd(), "placement.json")
        with open(out_path, "w") as f:
            json.dump(data, f, indent=4)
        self._load_from_data_dict(data, out_path)
        # Store a "before symmetry" snapshot for the compare toggle
        self._pre_symmetry_snapshot = None   # reset; symmetry enforcer will set it
        # Enable compare button if it exists
        if hasattr(self, '_tb_act_compare'):
            self._tb_act_compare.setEnabled(False)
            self._showing_before = False
        for group in self._matched_groups:
            technique = group.get("technique", "interdigitated")
            if technique == "interdigitated":      color = QColor("#4FC3F7")
            elif technique == "common_centroid_2d": color = QColor("#CE93D8")
            else:                                   color = QColor("#AED581")
            for gid in group["ids"]:
                item = self.editor.device_items.get(gid)
                if item and hasattr(item, "set_match_highlight"):
                    item.set_match_highlight(color)
        locked_msg = ""
        if saved:
            locked_msg = f"\n🔒 {len(saved)} matched devices preserved in place."

        # ── Area constraint check ─────────────────────────────────────
        goals = getattr(self, "_last_placement_goals", {}) or {}
        max_area = goals.get("max_area_um2")
        if max_area is not None:
            actual_area = self._compute_layout_area()
            if actual_area > max_area:
                while True:
                    from PySide6.QtWidgets import QInputDialog
                    new_max, ok = QInputDialog.getText(
                        self,
                        "Area Constraint Exceeded",
                        f"⚠️  Layout area is {actual_area:.2f} µm² but your limit is {max_area:.2f} µm².\n"
                        f"Enter a new max-area limit (µm²) or leave blank to accept:",
                    )
                    if not ok or not new_max.strip():
                        break
                    try:
                        max_area = float(new_max.strip())
                        self._last_placement_goals["max_area_um2"] = max_area
                        if actual_area <= max_area:
                            break
                    except ValueError:
                        pass

        # ── Enable Compare button ─────────────────────────────────────
        if hasattr(self, '_tb_act_compare'):
            self._after_placement_snapshot = copy.deepcopy(data.get("nodes", self.nodes))
            self._tb_act_compare.setEnabled(False)   # only enable after symmetry runs
            self._showing_before = False

        chat_response = data.get("chat_response", "")
        if chat_response:
            msg = chat_response
        else:
            msg = (
                f"AI initial placement complete!{locked_msg}\n"
                f"Saved to: {os.path.basename(out_path)}\n"
                f"You can now edit the layout, swap devices, or chat with the AI."
            )

        self.chat_panel._append_message("AI", msg, "#e8f4fd", "#1a1a2e")

    def _on_schematic_ai_started(self):
        """Update overlay text when the AI schematic generation kicks off."""
        self.overlay.show_message("Generating schematic layout with AI...")

    def _on_schematic_ai_finished(self):
        """Hide the overlay once the AI schematic layout has finished drawing."""
        self.overlay.hide_overlay()

    def _on_ai_placement_error(self, err_msg):
        self.overlay.hide_overlay()
        QMessageBox.warning(self, "AI Placement Failed", f"AI placement failed:\n\n{err_msg}")

    def _compute_layout_area(self) -> float:
        """Return bounding-box area of all current nodes in µm²."""
        nodes = [n for n in self.nodes if isinstance(n.get("geometry"), dict)]
        if not nodes:
            return 0.0
        min_x = min(n["geometry"].get("x", 0.0) for n in nodes)
        min_y = min(n["geometry"].get("y", 0.0) for n in nodes)
        max_x = max(n["geometry"].get("x", 0.0) + n["geometry"].get("width", 0.0) for n in nodes)
        max_y = max(n["geometry"].get("y", 0.0) + n["geometry"].get("height", 0.0) for n in nodes)
        return max(0.0, (max_x - min_x) * (max_y - min_y))

    def _set_pre_symmetry_snapshot(self):
        """Call this just before symmetry enforcement to capture the baseline."""
        self._pre_symmetry_snapshot = copy.deepcopy(self.nodes)
        if hasattr(self, '_tb_act_compare'):
            self._tb_act_compare.setEnabled(True)
            self._showing_before = False

    def _toggle_before_after(self):
        """Toggle the layout view between pre-symmetry and post-symmetry snapshots."""
        pre  = getattr(self, "_pre_symmetry_snapshot", None)
        post = getattr(self, "_after_placement_snapshot", None)
        if not pre or not post:
            return
        self._showing_before = not getattr(self, "_showing_before", False)
        import copy as _copy
        if self._showing_before:
            self.nodes = _copy.deepcopy(pre)
            if hasattr(self, '_tb_act_compare'):
                self._tb_act_compare.setText("▶ After Symmetry")
        else:
            self.nodes = _copy.deepcopy(post)
            if hasattr(self, '_tb_act_compare'):
                self._tb_act_compare.setText("◀ Before Symmetry")
        self._refresh_panels(compact=False)


    # =================================================================
    #  Static pipelines (unchanged)
    # =================================================================
    @staticmethod
    def _resolve_deterministic_node_overlaps(nodes, min_gap=0.0):
        """Legalize deterministic fallback rows without changing row assignment."""
        rows = {}
        for node in nodes or []:
            geo = node.get("geometry")
            if not isinstance(geo, dict):
                continue
            key = (
                round(float(geo.get("y", 0.0)), 6),
                str(node.get("type", "")).strip().lower(),
            )
            rows.setdefault(key, []).append(node)

        for row_nodes in rows.values():
            row_nodes.sort(
                key=lambda n: (
                    float(n.get("geometry", {}).get("x", 0.0)),
                    str(n.get("id", "")),
                )
            )
            cursor = None
            for node in row_nodes:
                geo = node["geometry"]
                x = float(geo.get("x", 0.0))
                width = max(float(geo.get("width", 0.0)), 0.0)
                if cursor is not None and x < cursor - 1e-6:
                    x = round(cursor, 6)
                    geo["x"] = x
                cursor = max(cursor if cursor is not None else x, x + width + min_gap)

    @staticmethod
    def _run_parser_pipeline(sp_path, oas_path="", abutment_enabled=True):
        from parser.netlist_reader import read_netlist_with_blocks
        from parser.circuit_graph import build_circuit_graph
        netlist, block_map = read_netlist_with_blocks(sp_path)
        layout_instances = []
        device_mapping = {}
        if oas_path and os.path.isfile(oas_path):
            try:
                from parser.layout_reader import extract_layout_instances
                layout_instances = extract_layout_instances(oas_path)
            except (ImportError, OSError) as exc:
                logging.warning("Failed to extract layout instances: %s", exc)
        if layout_instances:
            try:
                from parser.device_matcher import match_devices
                device_mapping = match_devices(netlist, layout_instances)
            except (ImportError, ValueError) as exc:
                logging.warning("Failed to match devices: %s", exc)
                device_mapping = {}
        from config.design_rules import PITCH_UM, ROW_HEIGHT_UM, BLOCK_GAP_UM, PASSIVE_ROW_GAP_UM as PASSIVE_ROW_GAP
        nodes = []
        terminal_nets = {}
        node_by_name = {}
        mapped_layout_indices = set(
            idx for idx in device_mapping.values() if idx is not None
        )

        def _layout_dummy_type(inst):
            dtype = str(inst.get("dummy_type") or "").strip().lower()
            if dtype in ("nmos", "pmos"):
                return dtype
            cell = str(inst.get("cell", "")).lower()
            if any(token in cell for token in ("pfet", "pmos", "pch")):
                return "pmos"
            return "nmos"

        def _unique_dummy_id(base):
            used = {n.get("id", "") for n in nodes}
            used.update(netlist.devices.keys())
            candidate = base
            i = 1
            while candidate in used:
                i += 1
                candidate = f"{base}_{i}"
            return candidate

        for dev_name, dev in netlist.devices.items():
            dev_type = dev.type
            is_passive = dev_type in ("res", "cap")
            electrical = {
                "l": dev.params.get("l", 1.4e-08), "nf": dev.params.get("nf", 1),
                "nfin": dev.params.get("nfin", 1), "w": dev.params.get("w", 0),
                "parent": dev.params.get("parent"), "m": dev.params.get("m", 1),
                "multiplier_index": dev.params.get("multiplier_index"),
                "finger_index": dev.params.get("finger_index"),
                "array_index": dev.params.get("array_index"),
            }
            if electrical["parent"] == dev_name:
                electrical["parent"] = None
            if dev_type == "cap":
                electrical["cval"] = dev.params.get("cval", 0.0)
            layout_idx = device_mapping.get(dev_name)
            abut_info = None
            if layout_idx is not None and layout_idx < len(layout_instances):
                inst = layout_instances[layout_idx]
                geom = {
                    "x": inst.get("x", 0.0), "y": inst.get("y", 0.0),
                    "width": inst.get("width", PITCH_UM), "height": inst.get("height", ROW_HEIGHT_UM),
                    "orientation": inst.get("orientation", "R0"),
                }
                abut_l = inst.get("abut_left", False) if abutment_enabled else False
                abut_r = inst.get("abut_right", False) if abutment_enabled else False
                if abut_l or abut_r:
                    abut_info = {"abut_left": abut_l, "abut_right": abut_r}
            elif is_passive:
                prm = dev.params
                raw_w = prm.get("w", PITCH_UM)
                raw_l = prm.get("l", ROW_HEIGHT_UM)
                nf_p = max(1, int(prm.get("nf", 1)))
                if dev_type == "res":
                    width_um = max(raw_l * nf_p, PITCH_UM)
                    height_um = max(raw_w, 0.1)
                else:
                    stm = max(1, int(prm.get("stm", 1)))
                    spm = max(1, int(prm.get("spm", 1)))
                    width_um = max(raw_w * max(nf_p, 1), PITCH_UM)
                    height_um = max(raw_l * max(stm * spm, 1), ROW_HEIGHT_UM) if raw_l > 0.1 else ROW_HEIGHT_UM
                geom = {"x": 0.0, "y": 0.0, "width": width_um, "height": height_um, "orientation": "R0"}
            else:
                geom = {"x": 0.0, "y": 0.0, "width": PITCH_UM, "height": ROW_HEIGHT_UM, "orientation": "R0"}
            node_dict = {"id": dev_name, "type": dev_type, "electrical": electrical, "geometry": geom}
            if layout_idx is not None:
                node_dict["layout_index"] = layout_idx
                if layout_idx < len(layout_instances):
                    node_dict["layout_cell"] = layout_instances[layout_idx].get("cell")
            if abut_info:
                node_dict["abutment"] = abut_info
            block_info = block_map.get(dev_name)
            if block_info is None:
                base = re.sub(r'_f\d+$', '', dev_name)
                if base != dev_name:
                    block_info = block_map.get(base)
            if block_info:
                node_dict["block"] = block_info
            nodes.append(node_dict)
            node_by_name[dev_name] = node_dict
            if hasattr(dev, "pins") and dev.pins:
                if is_passive:
                    terminal_nets[dev_name] = {"1": dev.pins.get("1", ""), "2": dev.pins.get("2", "")}
                else:
                    terminal_nets[dev_name] = {"D": dev.pins.get("D", ""), "G": dev.pins.get("G", ""), "S": dev.pins.get("S", "")}

        for layout_idx, inst in enumerate(layout_instances):
            if not inst.get("is_dummy") or layout_idx in mapped_layout_indices:
                continue
            dev_type = _layout_dummy_type(inst)
            prefix = "DUMMYP" if dev_type == "pmos" else "DUMMYN"
            dev_id = _unique_dummy_id(f"{prefix}{layout_idx + 1}")
            geom = {
                "x": inst.get("x", 0.0),
                "y": inst.get("y", 0.0),
                "width": inst.get("width", PITCH_UM),
                "height": inst.get("height", ROW_HEIGHT_UM),
                "orientation": inst.get("orientation", "R0"),
            }
            params = inst.get("params", {}) or {}
            electrical = {
                "l": params.get("l", 1.4e-08),
                "nf": params.get("nf", 1),
                "nfin": params.get("nfin", 1),
                "w": params.get("w", 0),
                "parent": None,
                "m": params.get("m", 1),
                "multiplier_index": None,
                "finger_index": None,
                "array_index": None,
            }
            node_dict = {
                "id": dev_id,
                "type": dev_type,
                "is_dummy": True,
                "dummy_source": "layout",
                "layout_index": layout_idx,
                "layout_cell": inst.get("cell"),
                "electrical": electrical,
                "geometry": geom,
            }
            if abutment_enabled and (inst.get("abut_left") or inst.get("abut_right")):
                node_dict["abutment"] = {
                    "abut_left": bool(inst.get("abut_left")),
                    "abut_right": bool(inst.get("abut_right")),
                }
            nodes.append(node_dict)
            node_by_name[dev_id] = node_dict

        # Fan-out shared layout instances
        layout_to_node_names = {}
        for dev_name, layout_idx in device_mapping.items():
            layout_to_node_names.setdefault(layout_idx, []).append(dev_name)
        def _shared_layout_sort_key(node_name):
            elec = node_by_name[node_name].get("electrical", {})
            return (elec.get("array_index") or 0, elec.get("multiplier_index") or 0, elec.get("finger_index") or 0, node_name)
        for layout_idx, grouped_names in layout_to_node_names.items():
            if layout_idx is None or len(grouped_names) <= 1 or layout_idx >= len(layout_instances):
                continue
            inst = layout_instances[layout_idx]
            ordered = sorted(grouped_names, key=_shared_layout_sort_key)
            slot_width = max(inst.get("width", PITCH_UM) / len(ordered), 0.001)
            for offset, dn in enumerate(ordered):
                node = node_by_name.get(dn)
                if node is None:
                    continue
                node["geometry"].update({
                    "x": inst.get("x", 0.0) + offset * slot_width,
                    "y": inst.get("y", 0.0), "width": slot_width,
                    "height": inst.get("height", ROW_HEIGHT_UM),
                    "orientation": inst.get("orientation", "R0"),
                })
                if "abutment" in node:
                    node["abutment"] = {
                        "abut_left": bool(inst.get("abut_left", False) and offset == 0),
                        "abut_right": bool(inst.get("abut_right", False) and offset == len(ordered) - 1),
                    }
        G = build_circuit_graph(netlist)
        edges = [{"source": u, "target": v, "net": d.get("net", "")} for u, v, d in G.edges(data=True)]
        blocks = {}
        for node in nodes:
            b = node.get("block")
            if b:
                inst_name = b.get("instance", "")
                if inst_name and inst_name not in blocks:
                    blocks[inst_name] = {"subckt": b.get("subckt", "?"), "devices": []}
                if inst_name:
                    blocks[inst_name]["devices"].append(node["id"])
        if not device_mapping:
            pmos_y = 0.0
            nmos_y = ROW_HEIGHT_UM
            passive_y = nmos_y + ROW_HEIGHT_UM + PASSIVE_ROW_GAP
            x_cursor = 0.0
            passive_x_cursor = 0.0
            block_order = list(blocks.keys())
            blocked_ids = set()
            for info in blocks.values():
                blocked_ids.update(info["devices"])
            unblocked = [n for n in nodes if n["id"] not in blocked_ids]
            for block_idx, inst_name in enumerate(block_order):
                info = blocks[inst_name]
                members = [node_by_name[d] for d in info["devices"] if d in node_by_name]
                pmos_members = [n for n in members if n["type"] == "pmos"]
                nmos_members = [n for n in members if n["type"] == "nmos"]
                passive_members = [n for n in members if n["type"] in ("res", "cap")]
                local_x = x_cursor
                for n in pmos_members:
                    w = n["geometry"]["width"]; n["geometry"]["x"] = local_x; n["geometry"]["y"] = pmos_y; local_x += w
                pmos_right = local_x
                local_x = x_cursor
                for n in nmos_members:
                    w = n["geometry"]["width"]; n["geometry"]["x"] = local_x; n["geometry"]["y"] = nmos_y; local_x += w
                nmos_right = local_x
                for n in passive_members:
                    w = n["geometry"]["width"]; n["geometry"]["x"] = passive_x_cursor; n["geometry"]["y"] = passive_y; passive_x_cursor += w + PITCH_UM
                x_cursor = max(pmos_right, nmos_right) + BLOCK_GAP_UM
            for n in unblocked:
                w = n["geometry"]["width"]
                if n["type"] == "pmos":
                    n["geometry"]["x"] = x_cursor; n["geometry"]["y"] = pmos_y; x_cursor += w
                elif n["type"] == "nmos":
                    n["geometry"]["x"] = x_cursor; n["geometry"]["y"] = nmos_y; x_cursor += w
                else:
                    n["geometry"]["x"] = passive_x_cursor; n["geometry"]["y"] = passive_y; passive_x_cursor += w + PITCH_UM
            LayoutEditorTab._resolve_deterministic_node_overlaps(nodes)
        if device_mapping:
            max_x = max(
                (n["geometry"]["x"] + n["geometry"]["width"]
                 for n in nodes if n["geometry"]["x"] != 0.0 or n["geometry"]["y"] != 0.0),
                default=0.0,
            )
            fanout_x = max_x + PITCH_UM
            for n in nodes:
                geo = n["geometry"]
                if geo["x"] == 0.0 and geo["y"] == 0.0 and device_mapping.get(n["id"]) is None:
                    geo["x"] = fanout_x; geo["y"] = 0.0; fanout_x += geo["width"] + PITCH_UM
        return {"nodes": nodes, "edges": edges, "terminal_nets": terminal_nets, "blocks": blocks}

    @staticmethod
    def _compress_graph_for_storage(data):
        from collections import defaultdict
        compressed = {
            "version": "2.0",
            "device_types": {
                "pmos": {"y_row": 0.668, "default_width": 0.294, "default_height": 0.818},
                "nmos": {"y_row": 0.0, "default_width": 0.294, "default_height": 0.668},
                "res": {"y_row": 1.630, "default_width": 0.294, "default_height": 0.1},
                "cap": {"y_row": 1.630, "default_width": 0.294, "default_height": 0.668},
            },
            "devices": {},
            "connectivity": {"nets": defaultdict(list)},
            "drc_rules": {"fin_pitch": 0.014, "row_pitch": 0.668, "device_pitch": 0.294, "abut_pitch": 0.070},
        }
        terminal_nets = data.get("terminal_nets", {})
        for node in data.get("nodes", []):
            node_id = node["id"]
            elec = node.get("electrical", {})
            parent_id = elec.get("parent") or re.sub(r'_[mf]\d+$', '', node_id)
            if parent_id in compressed["devices"]:
                continue
            compressed["devices"][parent_id] = {
                "type": node.get("type", "nmos"), "m": elec.get("m", 1),
                "nf": elec.get("nf", 1), "nfin": elec.get("nfin", 1),
                "l": elec.get("l", 0.0), "terminal_nets": terminal_nets.get(node_id, {}),
            }
            if node.get("block"):
                compressed["devices"][parent_id]["block"] = node["block"]
        for edge in data.get("edges", []):
            net = edge.get("net", "")
            if not net or net.upper() in {"VDD", "VSS", "GND", "VCC"}:
                continue
            source = re.sub(r'_[mf]\d+$', '', edge.get("source", ""))
            target = re.sub(r'_[mf]\d+$', '', edge.get("target", ""))
            if source and target:
                compressed["connectivity"]["nets"][net].append(source)
                compressed["connectivity"]["nets"][net].append(target)
        for net in compressed["connectivity"]["nets"]:
            compressed["connectivity"]["nets"][net] = sorted(set(compressed["connectivity"]["nets"][net]))
        compressed["connectivity"]["nets"] = dict(compressed["connectivity"]["nets"])
        compressed["blocks"] = data.get("blocks", {})
        return compressed

    @staticmethod
    def _run_ai_initial_placement(data, model_choice="Gemini", abutment_enabled=True):
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, dir=os.getcwd()) as tmp_in:
            json.dump(data, tmp_in, indent=2)
            tmp_in_path = tmp_in.name
        tmp_out_path = tmp_in_path.replace(".json", "_placed.json")
        try:


            # ── LangGraph Pipeline (CMD-block based) ──────────────────────
            from ai_agent.llm.placement_worker import PlacementWorker as PlacerGraphWorker

            final_payload = {}
            graph_error = {"message": ""}

            def _on_visual(payload):
                if isinstance(payload, dict) and payload.get("type") == "final_layout":
                    # Update existing payload without clearing (to preserve chat_response)
                    final_payload.update(payload)

            def _on_error(err_msg):
                graph_error["message"] = str(err_msg or "Graph execution failed.")

            def _on_response(msg):
                final_payload["chat_response"] = msg

            no_abutment = not abutment_enabled
            abutment_candidates = data.get("abutment_candidates", [])

            graph_worker = PlacerGraphWorker()
            graph_worker.visual_viewer_signal.connect(_on_visual)
            graph_worker.error_occurred.connect(_on_error)
            graph_worker.response_ready.connect(_on_response)

            graph_worker.process_initial_placement_request(
                json.dumps(data),
                "Optimize initial placement.",
                [],
                model_choice,
                no_abutment=no_abutment,
                abutment_candidates=abutment_candidates,
            )

            if graph_error["message"]:
                raise RuntimeError(graph_error["message"])

            placed_nodes_payload = final_payload.get("placement_nodes", []) if isinstance(final_payload, dict) else []

            placed_nodes = []
            if isinstance(placed_nodes_payload, list) and placed_nodes_payload:
                # Preferred path: use placement_nodes directly from graph final state.
                for src_node in data.get("nodes", []):
                    if not isinstance(src_node, dict):
                        continue
                    placed_nodes.append(copy.deepcopy(src_node))

                placed_map = {
                    n.get("id"): n
                    for n in placed_nodes_payload
                    if isinstance(n, dict) and n.get("id")
                }
                for node in placed_nodes:
                    node_id = node.get("id")
                    placed_node = placed_map.get(node_id)
                    if not placed_node:
                        continue
                    geometry = placed_node.get("geometry")
                    if geometry is None:
                        geometry = {
                            k: placed_node[k]
                            for k in ("x", "y", "orientation")
                            if k in placed_node
                        }
                    if isinstance(geometry, dict) and isinstance(node.get("geometry"), dict):
                        # Preserve original width/height; placement nodes may be pitch-sized.
                        if "x" in geometry:
                            node["geometry"]["x"] = geometry["x"]
                        if "y" in geometry:
                            node["geometry"]["y"] = geometry["y"]
                        if "orientation" in geometry:
                            node["geometry"]["orientation"] = geometry["orientation"]
                    placed_map.pop(node_id, None)
                    
                # Append any new nodes generated by the pipeline (e.g. dummies)
                for new_node in placed_map.values():
                    placed_nodes.append(copy.deepcopy(new_node))
            else:
                # Backward-compatible fallback: rebuild placement from move commands.
                placement_cmds = final_payload.get("placement", []) if isinstance(final_payload, dict) else []
                if not isinstance(placement_cmds, list):
                    placement_cmds = []

                move_map = {}
                for cmd in placement_cmds:
                    if not isinstance(cmd, dict):
                        continue
                    if str(cmd.get("action", "")).lower() != "move":
                        continue
                    dev_id = cmd.get("device")
                    if not dev_id:
                        continue
                    try:
                        move_map[dev_id] = {
                            "x": float(cmd["x"]),
                            "y": float(cmd["y"]),
                        }
                    except (KeyError, TypeError, ValueError):
                        continue

                for src_node in data.get("nodes", []):
                    if not isinstance(src_node, dict):
                        continue
                    node = copy.deepcopy(src_node)
                    node_id = node.get("id")
                    if node_id in move_map:
                        geom = node.get("geometry", {})
                        if isinstance(geom, dict):
                            geom["x"] = move_map[node_id]["x"]
                            geom["y"] = move_map[node_id]["y"]
                            node["geometry"] = geom
                    placed_nodes.append(node)

            placed = {"nodes": placed_nodes}
            with open(tmp_out_path, "w") as f:
                json.dump(placed, f, indent=4)

            placed_nodes_list = placed.get("nodes", [])
            if isinstance(placed_nodes_list, list):
                placed_map = {n["id"]: n for n in placed_nodes_list if isinstance(n, dict) and "id" in n}
                for node in data["nodes"]:
                    if isinstance(node, dict) and node.get("id") in placed_map:
                        placed_node = placed_map[node["id"]]
                        geometry = placed_node.get("geometry")
                        if geometry is None:
                            geometry = {k: placed_node[k] for k in ("x", "y", "orientation") if k in placed_node}
                        if geometry and isinstance(node.get("geometry"), dict):
                            if "x" in geometry:
                                node["geometry"]["x"] = geometry["x"]
                            if "y" in geometry:
                                node["geometry"]["y"] = geometry["y"]
                            if "orientation" in geometry:
                                node["geometry"]["orientation"] = geometry["orientation"]
                        placed_map.pop(node["id"], None)
                        
                for new_node in placed_map.values():
                    data["nodes"].append(copy.deepcopy(new_node))
                
                data["routing_result"] = final_payload.get("routing", {})
                data["chat_response"] = final_payload.get("chat_response", "")
                data["groups"] = final_payload.get("groups", {})
                print(f"[layout_tab] Captured chat response: {len(data['chat_response'])} chars")
        finally:
            for p in (tmp_in_path, tmp_out_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass
        return data

    # =================================================================
    #  Load / Save / Export
    # =================================================================
    def _load_from_data_dict(self, data, file_path, compact=False, force_schematic_ai=False):
        self._push_undo()
        self._original_data = data
        self.nodes = data["nodes"]
        self._custom_groups = copy.deepcopy(data.get("custom_groups", []))
        self._set_groups(data.get("groups", {}))
        self._terminal_nets = data.get("terminal_nets", {})
        self._routing_result = data.get("routing_result", {})
        self._current_file = file_path
        self._refresh_panels(compact=compact, force_schematic_ai=force_schematic_ai)
        self._sync_klayout_source(source_path=file_path)
        self.title_changed.emit(os.path.basename(file_path))
        QTimer.singleShot(100, self.editor.fit_to_view)

    def do_load(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Placement JSON", "", "JSON Files (*.json)")
        if not file_path:
            return
        self._push_undo()
        self._current_file = file_path
        self._load_data(file_path)
        self._refresh_panels(compact=True)
        self._sync_klayout_source(source_path=file_path)
        self.title_changed.emit(os.path.basename(file_path))

    def do_save(self):
        if not self._current_file:
            self.do_save_as()
            return
        self._write_json(self._current_file)

    def do_save_as(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Layout As", "", "JSON Files (*.json)")
        if not file_path:
            return
        self._current_file = file_path
        self._write_json(file_path)
        self.title_changed.emit(os.path.basename(file_path))

    def do_export_json(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Export Layout JSON", "", "JSON Files (*.json)")
        if not file_path:
            return
        self._write_json(file_path)

    def _write_json(self, file_path):
        output = self._build_output_data()
        with open(file_path, "w") as f:
            json.dump(output, f, indent=4)
        self.chat_panel._append_message("AI", f"Layout saved to {os.path.basename(file_path)}", "#e8f4fd", "#1a1a2e")

    def do_export_tcl(self):
        import os
        default_path = "ai_placement.txt"
        if self._current_file:
            json_dir = os.path.dirname(os.path.abspath(self._current_file))
            base = os.path.splitext(os.path.basename(self._current_file))[0]
            
            # Clean up the base name to get just the design name
            design_name = base.replace("_initial_placement", "").replace("_placement", "")
            file_name = f"{design_name}_ai_placement.txt"
            
            default_path = os.path.join(json_dir, file_name)
            
        file_path, _ = QFileDialog.getSaveFileName(self, "Export TCL Placement", default_path, "Text Files (*.txt);;All Files (*)")
        if not file_path:
            return
            
        import sys
        # Ensure we can import from the eda package
        proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if proj_root not in sys.path:
            sys.path.insert(0, proj_root)
            
        try:
            from eda.json_to_tcl import LayoutExporter
            exporter = LayoutExporter(file_path)
            
            output = self._build_output_data()
            for node in output.get("nodes", []):
                name = node["id"]
                x = node.get("geometry", {}).get("x", 0.0)
                y = node.get("geometry", {}).get("y", 0.0)
                orient = node.get("geometry", {}).get("orientation", "R0")
                # Parameters are stored under 'electrical' in the standard schema
                params = node.get("electrical", node.get("parameters", {}))
                
                exporter.add_instance(name, x, y, orient, params=params)
                
            success = exporter.export_for_tcl()
            if success:
                self.chat_panel._append_message("AI", f"TCL placement exported to {os.path.basename(file_path)}", "#e8f4fd", "#1a1a2e")
            else:
                self.chat_panel._append_message("AI", f"Failed to export TCL placement.", "#fde8e8", "#a00")
        except Exception as e:
            self.chat_panel._append_message("AI", f"Error exporting TCL placement: {str(e)}", "#fde8e8", "#a00")


    def do_export_oas(self):
        if not self._current_file:
            self.chat_panel._append_message("AI", "No layout loaded.", "#fde8e8", "#a00")
            return
        json_dir = os.path.dirname(os.path.abspath(self._current_file))
        oas_files = sorted(glob.glob(os.path.join(json_dir, "*.oas")))
        if not oas_files:
            self.chat_panel._append_message("AI", "No .oas file found.", "#fde8e8", "#a00")
            return
        base_oas = [f for f in oas_files if "_updated" not in os.path.basename(f).lower()]
        oas_path = base_oas[0] if base_oas else oas_files[0]
        sp_files = glob.glob(os.path.join(json_dir, "*.sp"))
        if not sp_files:
            self.chat_panel._append_message("AI", "No .sp netlist file found.", "#fde8e8", "#a00")
            return
        sp_path = sp_files[0]
        base_name = os.path.splitext(os.path.basename(oas_path))[0]
        default_path = os.path.join(json_dir, base_name + "_updated.oas")
        output_path, _ = QFileDialog.getSaveFileName(
            self, "Export to OAS", default_path,
            "OASIS Files (*.oas);;GDS Files (*.gds);;All Files (*)",
        )
        if not output_path:
            return
        self._sync_node_positions()
        saved_hierarchy_state = self._expand_all_for_export()
        try:
            from export.oas_writer import update_oas_placement
            abut_states = self.editor.get_device_abutment_states()
            for node in self.nodes:
                dev_id = node.get("id")
                if dev_id in abut_states:
                    node["abutment"] = abut_states[dev_id]
                else:
                    node.pop("abutment", None)
            update_oas_placement(oas_path=oas_path, sp_path=sp_path, nodes=self.nodes, output_path=output_path)
            self.chat_panel._append_message("AI", f"Layout exported to **{os.path.basename(output_path)}**", "#e8f4fd", "#1a1a2e")
            self.klayout_panel.refresh_preview(output_path)
        except Exception as e:
            self.chat_panel._append_message("AI", f"Export to OAS failed: {e}", "#fde8e8", "#a00")
            import traceback; traceback.print_exc()
        finally:
            self._restore_hierarchy_state(saved_hierarchy_state)

    def _expand_all_for_export(self):
        saved = {}
        try:
            iter_groups = getattr(self.editor, "_iter_live_hierarchy_groups", None)
            groups = iter_groups() if callable(iter_groups) else list(
                getattr(self.editor, "_hierarchy_groups", [])
            )
            iter_children = getattr(self.editor, "_iter_live_child_groups", None)
            for group in groups:
                children = (
                    iter_children(group)
                    if callable(iter_children)
                    else list(getattr(group, "_child_groups", []))
                )
                saved[group] = (
                    group._is_descended,
                    [(c, c._is_descended) for c in children],
                )
                if not group._is_descended:
                    group.descend()
                    for child in children:
                        if not child._is_descended and child._device_items:
                            child.descend()
        except Exception as e:
            print(f"[WARNING] Failed to expand hierarchy for export: {e}")
        return saved

    def _restore_hierarchy_state(self, saved):
        try:
            for group, state in saved.items():
                try:
                    group.scene()
                except RuntimeError:
                    continue
                if isinstance(state, tuple):
                    parent_desc, child_states = state
                else:
                    parent_desc, child_states = state, []
                if parent_desc and not group._is_descended:
                    group.descend()
                elif not parent_desc and group._is_descended:
                    group.ascend()
                if isinstance(state, tuple):
                    for child, child_desc in child_states:
                        try:
                            child.scene()
                        except RuntimeError:
                            continue
                        if child_desc and not child._is_descended:
                            child.descend()
                        elif not child_desc and child._is_descended:
                            child.ascend()
        except Exception as e:
            print(f"[WARNING] Failed to restore hierarchy state: {e}")

    # =================================================================
    #  AI command batch processing
    # =================================================================
    def _enqueue_ai_command(self, cmd):
        self._pending_cmds.append(cmd)
        if self._batch_flush_timer is None:
            self._batch_flush_timer = QTimer(self)
            self._batch_flush_timer.setSingleShot(True)
            self._batch_flush_timer.timeout.connect(self._flush_ai_command_batch)
        self._batch_flush_timer.start(0)

    def _flush_ai_command_batch(self):
        cmds = list(self._pending_cmds)
        self._pending_cmds.clear()
        self._batch_flush_timer = None
        if not cmds:
            return
        print(f"[AI BATCH] Executing {len(cmds)} command(s) as one undo group")
        self._sync_node_positions()
        self._push_undo()
        for cmd in cmds:
            self._handle_ai_command(cmd, _skip_undo=True)
        self._refresh_panels(compact=False)
        self._sync_node_positions()

    # =================================================================
    #  Pipeline stage highlights
    # =================================================================
    def _on_pipeline_stage_completed(self, stage_index, stage_name):
        device_items = getattr(self.editor, "device_items", {}) if self.editor else {}
        if not device_items:
            return
        if stage_index == 2:
            try:
                from ai_agent.agents.drc_critic import run_drc_check
                nodes = []
                for dev_id, item in device_items.items():
                    try:
                        pos = item.scenePos(); br = item.boundingRect()
                        nodes.append({"id": dev_id, "geometry": {"x": pos.x(), "y": pos.y(), "width": br.width(), "height": br.height()}})
                    except RuntimeError:
                        pass
                drc = run_drc_check(nodes)
                if not drc["pass"] and drc.get("structured"):
                    overlap_ids = set()
                    for v in drc["structured"]:
                        overlap_ids.add(v.dev_a); overlap_ids.add(v.dev_b)
                    highlight_ids = overlap_ids
                else:
                    highlight_ids = set(device_items.keys())
            except Exception:
                logging.debug("DRC highlight failed, highlighting all devices", exc_info=True)
                highlight_ids = set(device_items.keys())
        else:
            highlight_ids = set(device_items.keys())
        dimmed_ids = set()
        for dev_id in highlight_ids:
            item = device_items.get(dev_id)
            if item is None:
                continue
            try:
                item.setOpacity(0.55); dimmed_ids.add(dev_id)
            except RuntimeError:
                pass
        if self._stage_highlight_timer and self._stage_highlight_timer.isActive():
            try:
                self._stage_highlight_timer.stop()
            except RuntimeError:
                pass
        def _restore():
            live = getattr(self.editor, "device_items", {}) if self.editor else {}
            for did in dimmed_ids:
                itm = live.get(did)
                if itm:
                    try:
                        itm.setOpacity(1.0)
                    except RuntimeError:
                        pass
            self._stage_highlight_timer = None
        self._stage_highlight_timer = QTimer(self)
        self._stage_highlight_timer.setSingleShot(True)
        self._stage_highlight_timer.timeout.connect(_restore)
        self._stage_highlight_timer.start(3000)

    # =================================================================
    #  Resolve device ID + handle AI command
    # =================================================================
    def _resolve_device_id(self, raw_id):
        if raw_id is None:
            return None
        candidate = str(raw_id).strip()
        if not candidate:
            return None
        if candidate in self.editor.device_items:
            return candidate
        lookup = {str(k).lower(): k for k in self.editor.device_items.keys()}
        resolved = lookup.get(candidate.lower())
        if resolved:
            return resolved
        if candidate.isdigit():
            suffix_matches = [k for k in self.editor.device_items if str(k).lower().endswith(candidate.lower())]
            if len(suffix_matches) == 1:
                return suffix_matches[0]
        return None

    def _handle_ai_command(self, cmd, _skip_undo=False):
        print(f"[AI CMD] Received command: {cmd}")
        if not isinstance(cmd, dict):
            self.chat_panel._append_message("AI", "Invalid command format.", "#fde8e8", "#a00")
            return
        action = str(cmd.get("action", "")).strip().lower()
        try:
            if action in {"replace_layout", "apply_layout", "apply_layout_state"}:
                new_nodes = cmd.get("nodes")
                if not isinstance(new_nodes, list):
                    self.chat_panel._append_message("AI", "Layout update failed: missing nodes.", "#fde8e8", "#a00")
                    return
                if not _skip_undo:
                    self._push_undo()
                self.nodes = copy.deepcopy(new_nodes)
                if self._original_data is None:
                    self._original_data = {}
                self._original_data["nodes"] = self.nodes
                self._refresh_panels(compact=False)
                self._sync_node_positions()
                # Apply per-node visual metadata (color, lock state) to canvas items
                from PySide6.QtWidgets import QGraphicsItem
                from PySide6.QtGui import QColor as _QColor
                for _node in self.nodes:
                    _dev_id = _node.get("id")
                    _item   = self.editor.device_items.get(_dev_id) if _dev_id else None
                    if _item is None:
                        continue
                    _color = _node.get("color")
                    if _color and hasattr(_item, "set_custom_color"):
                        try:
                            _item.set_custom_color(_QColor(_color))
                        except Exception:
                            pass
                    elif not _color and hasattr(_item, "reset_custom_color"):
                        try:
                            _item.reset_custom_color()
                        except Exception:
                            pass
                    _locked = bool(_node.get("locked", False))
                    _item.setFlag(
                        QGraphicsItem.GraphicsItemFlag.ItemIsMovable, not _locked
                    )
                self.editor.viewport().update()
                message = cmd.get("message") or "Applied layout tool result."
                self.chat_panel._append_message("AI", f"✅ {message}", "#e8f4fd", "#1a1a2e")
                # Tell response_done_deferred that FC tools ran this turn,
                # and add the result to history for multi-turn context.
                self.chat_panel._fc_command_received = True
                self.chat_panel._chat_history.append(
                    {"role": "assistant", "content": message}
                )

            elif action == "create_group":
                _device_ids = cmd.get("device_ids") or []
                _name       = str(cmd.get("name") or "").strip()
                if not _name:
                    _existing = [g.get("name","") for g in self._custom_groups
                                 if str(g.get("name","")).startswith("GROUP_")]
                    _num = len(_existing) + 1
                    _name = f"GROUP_{_num}"
                if len(_device_ids) >= 2:
                    if not _skip_undo:
                        self._push_undo()
                    new_group = {"name": _name, "devices": list(_device_ids)}
                    self._custom_groups.append(new_group)
                    if self._original_data is not None:
                        self._original_data["custom_groups"] = copy.deepcopy(self._custom_groups)
                    self.editor.apply_custom_groups(self._custom_groups)
                    self._on_hierarchy_changed()
                    _gmsg = f"Created group '{_name}' ({len(_device_ids)} devices)"
                    self.chat_panel._append_message("AI", f"✅ {_gmsg}", "#e8f4fd", "#1a1a2e")
                    self.chat_panel._fc_command_received = True
                    self.chat_panel._chat_history.append(
                        {"role": "assistant", "content": _gmsg}
                    )
                else:
                    self.chat_panel._append_message(
                        "AI", f"⚠️ create_group needs at least 2 devices",
                        "#fde8e8", "#a00"
                    )

            elif action in {"swap", "swap_devices"}:
                raw_a = cmd.get("device_a", cmd.get("a"))
                raw_b = cmd.get("device_b", cmd.get("b"))
                id_a = self._resolve_device_id(raw_a)
                id_b = self._resolve_device_id(raw_b)
                if not id_a or not id_b:
                    self.chat_panel._append_message("AI", f"Swap failed: device not found ({raw_a}, {raw_b}).", "#fde8e8", "#a00")
                    return
                if self._is_device_locked(id_a) or self._is_device_locked(id_b):
                    self.chat_panel._append_message("AI", f"⚠️ Cannot swap locked devices ({id_a}, {id_b}).", "#fff3e0", "#e65100")
                    return
                self._sync_node_positions()
                if not _skip_undo:
                    self._push_undo()
                node_a = next((n for n in self.nodes if n.get("id") == id_a), None)
                node_b = next((n for n in self.nodes if n.get("id") == id_b), None)
                if node_a and node_b:
                    ga, gb = node_a["geometry"], node_b["geometry"]
                    ga["x"], gb["x"] = gb["x"], ga["x"]
                    ga["y"], gb["y"] = gb["y"], ga["y"]
                    oa, ob = ga.get("orientation", "R0"), gb.get("orientation", "R0")
                    ga["orientation"], gb["orientation"] = ob, oa
                    self._refresh_panels(compact=False)
                    #self.chat_panel._append_message("AI", f"✅ Swapped {id_a} ↔ {id_b}", "#e8f4fd", "#1a1a2e")

            elif action == "abut":
                raw_a = cmd.get("device_a", cmd.get("a"))
                raw_b = cmd.get("device_b", cmd.get("b"))
                id_a = self._resolve_device_id(raw_a)
                id_b = self._resolve_device_id(raw_b)
                if not id_a or not id_b:
                    self.chat_panel._append_message("AI", f"Abutment failed: device not found.", "#fde8e8", "#a00")
                    return
                if self._is_device_locked(id_a) or self._is_device_locked(id_b):
                    self.chat_panel._append_message("AI", "⚠️ Cannot abut locked devices.", "#fff3e0", "#e65100")
                    return
                self._sync_node_positions()
                if not _skip_undo:
                    self._push_undo()
                self._abut_devices(id_a, id_b)
                self._refresh_panels(compact=False)
                self.chat_panel._append_message("AI", f"✅ Abutted **{id_a}** and **{id_b}**", "#e8f4fd", "#1a1a2e")

            elif action in {"move", "move_device"}:
                raw_dev = cmd.get("device", cmd.get("device_id", cmd.get("id")))
                dev_id = self._resolve_device_id(raw_dev)
                x, y = cmd.get("x"), cmd.get("y")
                if dev_id is None:
                    self.chat_panel._append_message("AI", f"Move failed: device not found ({raw_dev}).", "#fde8e8", "#a00")
                    return
                if x is None or y is None:
                    self.chat_panel._append_message("AI", "Move failed: missing x or y.", "#fde8e8", "#a00")
                    return
                self._sync_node_positions()
                if not _skip_undo:
                    self._push_undo()
                group = self._get_device_group(dev_id)
                if group:
                    scale = self.editor.scale_factor
                    n_moved = self._move_matched_group_as_block(group, float(x) * scale, float(y) * scale)
                    self._sync_node_positions()
                    self._refresh_panels(compact=False)
                    self.chat_panel._append_message("AI", f"↕ Moved matched group ({n_moved} devices) as block.", "#e8f4fd", "#1a1a2e")
                    return
                node = next((n for n in self.nodes if n.get("id") == dev_id), None)
                if node:
                    node["geometry"]["x"] = float(x); node["geometry"]["y"] = float(y)
                    self._refresh_panels(compact=False)
                    #self.chat_panel._append_message("AI", f"✅ Moved {dev_id} to ({x}, {y})", "#e8f4fd", "#1a1a2e")

            elif action in {"move_row", "move_row_devices"}:
                dev_type = cmd.get("type", "")
                new_y = cmd.get("y")
                if not dev_type or new_y is None:
                    self.chat_panel._append_message("AI", "Move row failed: missing type or y.", "#fde8e8", "#a00")
                    return
                self._sync_node_positions()
                if not _skip_undo:
                    self._push_undo()
                count = 0
                for node in self.nodes:
                    if node.get("type") == dev_type:
                        node["geometry"]["y"] = float(new_y); count += 1
                self._refresh_panels(compact=False)
                self.chat_panel._append_message("AI", f"Moved all {count} {dev_type} devices to Y={new_y}", "#e8f4fd", "#1a1a2e")

            elif action in {"add_dummy", "add_dummies", "dummy"}:
                dev_type = str(cmd.get("type", "nmos")).strip().lower()
                if dev_type not in ("nmos", "pmos"):
                    self.chat_panel._append_message("AI", f"Invalid dummy type: {dev_type}.", "#fde8e8", "#a00")
                    return

                if "x" not in cmd or "y" not in cmd:
                    self.chat_panel._append_message(
                        "AI",
                        "add_dummy requires x and y coordinates.",
                        "#fde8e8",
                        "#a00",
                    )
                    return

                try:
                    target_x = float(cmd.get("x"))
                    target_y = float(cmd.get("y"))
                except (TypeError, ValueError):
                    self.chat_panel._append_message(
                        "AI",
                        "add_dummy requires numeric x and y coordinates.",
                        "#fde8e8",
                        "#a00",
                    )
                    return

                template = next((n for n in self.nodes if str(n.get("type", "")).strip().lower() == dev_type), None)
                if not template:
                    self.chat_panel._append_message("AI", f"No {dev_type} template.", "#fde8e8", "#a00")
                    return

                self._sync_node_positions()
                if not _skip_undo:
                    self._push_undo()

                tgeo = template["geometry"]
                electrical = copy.deepcopy(template.get("electrical", {"l": 1.4e-08, "nf": 1, "nfin": 1}))
                dummy = {
                    "id": self._next_dummy_id(dev_type),
                    "type": dev_type,
                    "is_dummy": True,
                    "dummy_source": "user",
                    "electrical": electrical,
                    "geometry": {
                        "x": target_x,
                        "y": target_y,
                        "width": float(tgeo.get("width", 1)),
                        "height": float(tgeo.get("height", 0.5)),
                        "orientation": "R0",
                    },
                }
                if template.get("layout_index") is not None:
                    dummy["template_layout_index"] = template.get("layout_index")
                if template.get("layout_cell"):
                    dummy["layout_cell"] = template.get("layout_cell")
                self.nodes.append(dummy)
                self._original_data["nodes"] = self.nodes
                self._refresh_panels(compact=False)
                self._sync_node_positions()
                # self.chat_panel._append_message(
                #     "AI",
                #     f"✅ Added {dev_type} dummy {dummy['id']} at x={target_x}, y={target_y}",
                #     "#e8f4fd",
                #     "#1a1a2e",
                # )

            elif action == "net_priority":
                net = cmd.get("net", "")
                priority = cmd.get("priority", "medium")
                if not hasattr(self, "_routing_annotations"):
                    self._routing_annotations = {}
                self._routing_annotations.setdefault(net, {})["priority"] = priority
                # Arm one-shot trigger so next AI initial-placement run uses
                # the chat priority annotation as critical-net input.
                self._apply_chat_net_priority_next_run = True
                self.chat_panel._append_message("AI", f"📡 Net **{net}** → **{priority}** priority.", "#e8f4fd", "#1a1a2e")
                if hasattr(self.editor, "highlight_net_by_name"):
                    self.editor.highlight_net_by_name(net, "#e74c3c" if priority == "high" else "#3498db")

            elif action == "wire_width":
                net = cmd.get("net", "")
                width_um = cmd.get("width_um", 0.3)
                if not hasattr(self, "_routing_annotations"):
                    self._routing_annotations = {}
                self._routing_annotations.setdefault(net, {})["wire_width_um"] = float(width_um)
                self.chat_panel._append_message("AI", f"🔌 Wire width for **{net}** → **{width_um} µm**.", "#e8f4fd", "#1a1a2e")

            elif action == "wire_spacing":
                net_a, net_b = cmd.get("net_a", ""), cmd.get("net_b", "")
                spacing = cmd.get("spacing_um", 0.2)
                if not hasattr(self, "_routing_annotations"):
                    self._routing_annotations = {}
                self._routing_annotations.setdefault(f"{net_a}|{net_b}", {})["spacing_um"] = float(spacing)
                self.chat_panel._append_message("AI", f"📏 Spacing {net_a}↔{net_b} → **{spacing} µm**.", "#e8f4fd", "#1a1a2e")

            elif action == "net_reroute":
                net = cmd.get("net", "")
                reason = cmd.get("reason", "")
                if not hasattr(self, "_routing_annotations"):
                    self._routing_annotations = {}
                self._routing_annotations.setdefault(net, {})["reroute"] = reason
                self.chat_panel._append_message("AI", f"🔀 Net **{net}** flagged for reroute: _{reason}_", "#e8f4fd", "#1a1a2e")
                if hasattr(self.editor, "highlight_net_by_name"):
                    self.editor.highlight_net_by_name(net, "#f39c12")

            else:
                self.chat_panel._append_message("AI", f"Unsupported AI action: {action or '(empty)'}", "#fde8e8", "#a00")

        except (KeyError, TypeError, ValueError) as e:
            self.chat_panel._append_message("AI", f"Could not execute command: {e}", "#fde8e8", "#a00")

    def _abut_devices(self, id_a, id_b):
        node_a = next((n for n in self.nodes if n.get("id") == id_a), None)
        node_b = next((n for n in self.nodes if n.get("id") == id_b), None)
        if not node_a or not node_b:
            return
        node_a.setdefault("abutment", {})["abut_right"] = True
        node_b.setdefault("abutment", {})["abut_left"] = True
        node_b["geometry"]["y"] = node_a["geometry"]["y"]
        node_b["geometry"]["x"] = node_a["geometry"]["x"] + 0.070

    # =================================================================
    #  Sync node positions
    # =================================================================
    def _sync_node_positions(self, schedule_live=True):
        if not self.nodes:
            edges = self._original_data.get("edges", []) if self._original_data else []
            self.chat_panel.set_layout_context([], edges, self._terminal_nets)
            self._update_grid_counts()
            return
        positions = self.editor.get_updated_positions()
        scale = getattr(self.editor, "scale_factor", 80) or 80
        for node in self.nodes:
            dev_id = node.get("id")
            if dev_id not in positions:
                continue
            canvas_x, canvas_y = positions[dev_id]
            item = self.editor.device_items.get(dev_id)
            snap_disabled = item is not None and getattr(item, "_snap_grid_x", None) is None
            if snap_disabled:
                if item and hasattr(item, "orientation_string"):
                    node["geometry"]["orientation"] = item.orientation_string()
            else:
                node["geometry"]["x"] = canvas_x
                node["geometry"]["y"] = canvas_y
                if item and hasattr(item, "orientation_string"):
                    node["geometry"]["orientation"] = item.orientation_string()
        self.chat_panel.set_layout_context(
            self.nodes,
            self._original_data.get("edges", []) if self._original_data else [],
            self._terminal_nets,
        )
        self._update_grid_counts()
        self._on_editor_selection_changed()
        if schedule_live:
            self._schedule_live_klayout_update()
