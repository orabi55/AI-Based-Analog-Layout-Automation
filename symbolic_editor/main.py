# -*- coding: utf-8 -*-
"""
Symbolic Layout Editor — Multi-Tab Application Shell

MainWindow hosts a tabbed interface where each tab is a fully
independent LayoutEditorTab (editor + tree + chat + KLayout).
When no tabs are open the WelcomeScreen is displayed.
"""

import sys
import os
import glob

# Ensure project root is on the path so sub-packages resolve
_project_root = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
_editor_dir = os.path.dirname(os.path.abspath(__file__))
if _editor_dir not in sys.path:
    sys.path.insert(0, _editor_dir)

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTabWidget,
    QStackedWidget,
    QToolBar,
    QLabel,
    QSpinBox,
    QDoubleSpinBox,
    QCheckBox,
    QWidget,
    QWidgetAction,
    QFileDialog,
    QMessageBox,
    QHBoxLayout,
    QMenu,
    QStatusBar,
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import (
    QAction,
    QKeySequence,
    QPalette,
    QColor,
    QFont,
    QIcon,
)

from layout_tab import LayoutEditorTab
from view_toggle import SegmentedToggle
from icons import (
    icon_undo,
    icon_redo,
    icon_fit_view,
    icon_zoom_in,
    icon_zoom_out,
    icon_zoom_reset,
    icon_select_all,
    icon_delete,
    icon_swap,
    icon_flip_h,
    icon_flip_v,
    icon_add_dummy,
    icon_open_file,
    icon_import_file,
    icon_save_file,
    icon_export_file,
    icon_abutment,
    icon_ai_placement,
)
from widgets.welcome_screen import WelcomeScreen


# =====================================================================
#  MainWindow — Tab Manager
# =====================================================================
class MainWindow(QMainWindow):
    """Application shell: menu bar, toolbar, QTabWidget + WelcomeScreen."""

    def __init__(self, initial_file=None):
        super().__init__()
        self.setWindowTitle("Symbolic Layout Editor")
        self.resize(1500, 950)

        # ── Central stack: Welcome / Tabs ────────────────────────
        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self._welcome = WelcomeScreen()
        self._welcome.open_file_requested.connect(self._on_open_file)
        self._welcome.import_requested.connect(self._on_import)
        self._welcome.example_requested.connect(self._on_load_example)
        self._stack.addWidget(self._welcome)  # index 0

        self._tab_widget = QTabWidget()
        self._tab_widget.setTabsClosable(True)
        self._tab_widget.setMovable(True)
        self._tab_widget.setDocumentMode(True)
        self._tab_widget.tabCloseRequested.connect(self._close_tab)
        self._tab_widget.currentChanged.connect(self._on_tab_changed)
        self._tab_widget.setStyleSheet(self._tab_bar_style())
        self._stack.addWidget(self._tab_widget)  # index 1

        # ── Menu bar & Toolbar ───────────────────────────────────
        self._create_menu_bar()
        self._create_file_toolbar()
        self._create_toolbar()
        self._create_status_bar()

        # ── Open initial file if given ───────────────────────────
        if initial_file and os.path.isfile(initial_file):
            self._new_tab(initial_file)
        else:
            self._stack.setCurrentIndex(0)  # show welcome
            self._set_chrome_visible(False)

    # =================================================================
    #  Tab helpers
    # =================================================================
    def current_tab(self) -> LayoutEditorTab | None:
        w = self._tab_widget.currentWidget()
        return w if isinstance(w, LayoutEditorTab) else None

    def _new_tab(self, placement_file=None) -> LayoutEditorTab:
        tab = LayoutEditorTab(placement_file, parent=self._tab_widget)
        title = tab.document_title
        idx = self._tab_widget.addTab(tab, title)
        self._tab_widget.setCurrentIndex(idx)
        self._stack.setCurrentIndex(1)  # show tabs
        self._set_chrome_visible(True)
        self.setWindowTitle(f"Symbolic Layout Editor — {title}")
        tab.editor.setFocus(Qt.FocusReason.OtherFocusReason)

        # Connect tab signals → toolbar updates
        tab.undo_state_changed.connect(self._sync_undo_redo)
        tab.selection_changed.connect(self._sync_selection)
        tab.grid_changed.connect(self._sync_grid)
        tab.title_changed.connect(lambda t, t_=tab: self._on_tab_title_changed(t_, t))
        tab.workspace_mode_changed.connect(lambda _m, t_=tab: self._on_tab_workspace_mode_changed(t_))

        # Initial sync
        self._sync_undo_redo(tab.can_undo(), tab.can_redo())
        self._sync_selection(tab.selection_count())
        self._sync_mode_toggles()
        return tab

    def _close_tab(self, index):
        tab = self._tab_widget.widget(index)
        if isinstance(tab, LayoutEditorTab):
            tab.shutdown()
        self._tab_widget.removeTab(index)
        if self._tab_widget.count() == 0:
            self._stack.setCurrentIndex(0)  # show welcome
            self._set_chrome_visible(False)
            self.setWindowTitle("Symbolic Layout Editor")

    def _on_tab_changed(self, index):
        tab = self.current_tab()
        if tab is None:
            return
        self._sync_undo_redo(tab.can_undo(), tab.can_redo())
        self._sync_selection(tab.selection_count())
        self._sync_mode_toggles()
        tab.editor.setFocus(Qt.FocusReason.OtherFocusReason)

    def _on_tab_title_changed(self, tab, title):
        idx = self._tab_widget.indexOf(tab)
        if idx >= 0:
            self._tab_widget.setTabText(idx, title)
        if tab is self.current_tab():
            self.setWindowTitle(f"Symbolic Layout Editor — {title}")

    def _on_tab_workspace_mode_changed(self, tab):
        if tab is self.current_tab():
            self._sync_mode_toggles()

    # =================================================================
    #  Welcome-screen handlers
    # =================================================================
    def _on_open_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open Placement JSON", "", "JSON Files (*.json)"
        )
        if file_path:
            self._new_tab(file_path)

    def _on_import(self):
        tab = self._new_tab()
        tab.do_import()

    def _on_load_example(self, sp_path, oas_path):
        tab = self._new_tab()
        tab.load_example(sp_path, oas_path)

    # =================================================================
    #  Toolbar / status sync
    # =================================================================
    def _sync_undo_redo(self, can_undo, can_redo):
        self._act_undo.setEnabled(can_undo)
        self._act_redo.setEnabled(can_redo)
        if hasattr(self, "_tb_act_undo"):
            self._tb_act_undo.setEnabled(can_undo)
        if hasattr(self, "_tb_act_redo"):
            self._tb_act_redo.setEnabled(can_redo)

    def _sync_selection(self, count):
        self._sel_label.setText(f"  Sel: {count}  ")

    def _sync_grid(self, rows, cols, min_rows, min_cols):
        self._ignore_grid_spin = True
        self._row_spin.setMinimum(min_rows)
        self._col_spin.setMinimum(min_cols)
        self._row_spin.setValue(rows)
        self._col_spin.setValue(cols)
        self._ignore_grid_spin = False

    def _sync_mode_toggles(self):
        tab = self.current_tab()
        dummy_checked = bool(getattr(tab, "_dummy_mode", False)) if tab else False
        abut_checked = bool(getattr(tab, "_abutment_mode", False)) if tab else False
        for action, checked in (
            (self._act_add_dummy, dummy_checked),
            (self._act_abutment, abut_checked),
        ):
            blocked = action.blockSignals(True)
            action.setChecked(checked)
            action.blockSignals(blocked)
        blocked = self._workspace_quick_toggle.blockSignals(True)
        self._workspace_quick_toggle.setEnabled(tab is not None)
        self._workspace_quick_toggle.set_mode(tab.workspace_mode() if tab else "both", emit=False)
        self._workspace_quick_toggle.blockSignals(blocked)

    # =================================================================
    #  Menu bar
    # =================================================================
    def _create_menu_bar(self):
        mb = self.menuBar()
        mb.setStyleSheet(
            "QMenuBar { background-color: #12161f; color: #c8d0dc; "
            "border-bottom: 1px solid #2d3548; font-family: 'Segoe UI'; font-size: 10pt; }"
            "QMenuBar::item:selected { background-color: #2d3f54; }"
            "QMenu { background-color: #1a1f2b; color: #c8d0dc; border: 1px solid #2d3548; "
            "font-family: 'Segoe UI'; font-size: 10pt; }"
            "QMenu::item:selected { background-color: #2d3f54; }"
            "QMenu::separator { background-color: #2d3548; height: 1px; margin: 4px 8px; }"
        )

        # ── File ─────────────────────────────────────────────────
        file_menu = mb.addMenu("&File")
        file_menu.addAction("New Tab", self._on_new_tab, QKeySequence("Ctrl+T"))
        file_menu.addSeparator()
        file_menu.addAction("Import Netlist + Layout…", self._on_import, QKeySequence("Ctrl+I"))
        file_menu.addAction("&Open JSON…", self._on_open_file, QKeySequence.StandardKey.Open)
        file_menu.addSeparator()
        file_menu.addAction("&Save", lambda: self._fwd("do_save"), QKeySequence.StandardKey.Save)
        file_menu.addAction("Save &As…", lambda: self._fwd("do_save_as"), QKeySequence("Ctrl+Shift+S"))
        file_menu.addAction("Export JSON…", lambda: self._fwd("do_export_json"))
        file_menu.addAction("Export to OAS…", lambda: self._fwd("do_export_oas"))
        file_menu.addSeparator()
        file_menu.addAction("Close Tab", self._close_current_tab, QKeySequence("Ctrl+W"))

        # Quick-start examples sub-menu
        examples_menu = file_menu.addMenu("Quick Start Examples")
        examples_dir = os.path.normpath(os.path.join(_project_root, "examples"))
        if os.path.isdir(examples_dir):
            for name in sorted(os.listdir(examples_dir)):
                edir = os.path.join(examples_dir, name)
                if not os.path.isdir(edir):
                    continue
                sp_files = glob.glob(os.path.join(edir, "*.sp"))
                if not sp_files:
                    continue
                sp = sp_files[-1]
                oas = sp.rsplit(".", 1)[0] + ".oas"
                if not os.path.exists(oas):
                    oas = ""
                display = name.replace("_", " ").title()
                # Capture by default-arg
                examples_menu.addAction(
                    display, lambda s=sp, o=oas: self._on_load_example(s, o)
                )

        file_menu.addSeparator()
        file_menu.addAction("Reload App", self._on_reload_app, QKeySequence("Ctrl+Shift+R"))

        # ── Edit ─────────────────────────────────────────────────
        edit_menu = mb.addMenu("&Edit")
        self._act_undo = edit_menu.addAction("&Undo", lambda: self._fwd("do_undo"), QKeySequence.StandardKey.Undo)
        self._act_redo = edit_menu.addAction("&Redo", lambda: self._fwd("do_redo"), QKeySequence.StandardKey.Redo)
        self._act_undo.setEnabled(False)
        self._act_redo.setEnabled(False)
        edit_menu.addSeparator()
        self._act_select_all = edit_menu.addAction("Select &All", lambda: self._fwd("do_select_all"), QKeySequence.StandardKey.SelectAll)
        self._act_select_all.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        edit_menu.addAction("&Delete Selected", lambda: self._fwd("do_delete"), QKeySequence("Del"))
        edit_menu.addSeparator()

        # Close Row Gap — embedded checkbox + spin
        self._act_close_row_gap = QCheckBox(" Close Row Gap")
        self._act_close_row_gap.setStyleSheet(
            "QCheckBox { color: #c8d0dc; font-family: 'Segoe UI'; padding: 4px 8px; }"
        )
        self._row_gap_spin = QDoubleSpinBox()
        self._row_gap_spin.setRange(0.0, 10.0)
        self._row_gap_spin.setSingleStep(0.1)
        self._row_gap_spin.setValue(0.0)
        self._row_gap_spin.setSuffix(" µm")
        self._row_gap_spin.setEnabled(False)
        self._row_gap_spin.setFixedWidth(100)
        self._row_gap_spin.setStyleSheet(
            "QDoubleSpinBox { background: #1a1f2b; color: #c8d0dc; border: 1px solid #2d3548; "
            "border-radius: 4px; padding: 2px 6px; }"
        )
        w_gap = QWidget()
        h_gap = QHBoxLayout(w_gap)
        h_gap.setContentsMargins(8, 2, 8, 2)
        h_gap.addWidget(self._act_close_row_gap)
        h_gap.addWidget(self._row_gap_spin)
        wa_gap = QWidgetAction(self)
        wa_gap.setDefaultWidget(w_gap)
        edit_menu.addAction(wa_gap)
        self._act_close_row_gap.toggled.connect(self._on_close_row_gap_toggled)
        self._row_gap_spin.valueChanged.connect(self._on_row_gap_spin_changed)

        # ── View ─────────────────────────────────────────────────
        view_menu = mb.addMenu("&View")
        view_menu.addAction("Fit to View", lambda: self._fwd_editor("fit_to_view"))
        view_menu.addAction("Zoom In", lambda: self._fwd_editor("zoom_in"), QKeySequence("Ctrl+="))
        view_menu.addAction("Zoom Out", lambda: self._fwd_editor("zoom_out"), QKeySequence("Ctrl+-"))
        view_menu.addAction("Reset Zoom", lambda: self._fwd_editor("zoom_reset"))
        view_menu.addSeparator()
        view_menu.addAction("Toggle Device Tree", lambda: self._fwd("_toggle_device_tree"))
        view_menu.addAction("Toggle Chat Panel", lambda: self._fwd("_toggle_chat_panel"))
        view_menu.addAction("Toggle KLayout Preview", lambda: self._fwd("_toggle_klayout_panel"))
        view_menu.addSeparator()
        view_menu.addAction("Detailed Device View", lambda: self._fwd_editor("show_detailed_devices"))
        view_menu.addAction("Outline Device View", lambda: self._fwd_editor("show_outline_devices"))
        view_menu.addAction("Block Symbols", lambda: self._fwd_editor("set_view_level", "symbol"))
        view_menu.addSeparator()
        view_menu.addAction("Symbolic Workspace", lambda: self._fwd("set_workspace_mode", "symbolic"), QKeySequence("Ctrl+1"))
        view_menu.addAction("KLayout Workspace", lambda: self._fwd("set_workspace_mode", "klayout"), QKeySequence("Ctrl+2"))
        view_menu.addAction("Both Views", lambda: self._fwd("set_workspace_mode", "both"), QKeySequence("Ctrl+3"))

        # ── Design ───────────────────────────────────────────────
        design_menu = mb.addMenu("&Design")
        design_menu.addAction("Swap Selected (2)", lambda: self._fwd("do_swap"), QKeySequence("Ctrl+Shift+X"))
        design_menu.addAction("Merge Shared Source", lambda: self._fwd("do_merge_ss"))
        design_menu.addAction("Merge Shared Drain", lambda: self._fwd("do_merge_dd"))
        design_menu.addAction("Flip Horizontal", lambda: self._fwd("do_flip_h"), QKeySequence("Ctrl+H"))
        design_menu.addAction("Flip Vertical", lambda: self._fwd("do_flip_v"), QKeySequence("Ctrl+J"))
        design_menu.addAction("Toggle Dummy Placement", self._toggle_dummy_action)
        design_menu.addSeparator()
        design_menu.addAction("Match Devices…", lambda: self._fwd("do_match"), QKeySequence("Ctrl+M"))
        design_menu.addAction("Unlock Matched", lambda: self._fwd("do_unlock_match"), QKeySequence("Ctrl+U"))
        design_menu.addSeparator()
        design_menu.addAction("Run AI Placement…", lambda: self._fwd("do_ai_placement"), QKeySequence("Ctrl+P"))
        design_menu.addSeparator()
        design_menu.addAction("View in KLayout", lambda: self._fwd("on_view_in_klayout"))

    # =================================================================
    #  Toolbar
    # =================================================================
    def _create_file_toolbar(self):
        tb = QToolBar("File Quick Actions")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setAllowedAreas(Qt.ToolBarArea.TopToolBarArea)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        tb.setIconSize(QSize(16, 16))
        tb.setStyleSheet(
            "QToolBar { background-color: #12161f; border: none; border-bottom: 1px solid #2d3548; spacing: 2px; padding: 2px 8px 4px 8px; }"
            "QToolBar::separator { background-color: #2d3548; width: 1px; margin: 4px 6px; }"
            "QToolButton { background: transparent; border: 1px solid transparent; border-radius: 6px; "
            "padding: 4px; min-width: 24px; min-height: 24px; }"
            "QToolButton:hover { background-color: #1e2a3a; border-color: #31445c; }"
            "QToolButton:pressed { background-color: #24354a; }"
            "QLabel { color: #8a9caf; font-family: 'Segoe UI'; font-size: 8.5pt; font-weight: 600; padding: 0 2px 0 6px; }"
        )
        self._file_toolbar = tb
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)

        self._quick_act_import = QAction(icon_import_file(), "Import Netlist + Layout", self)
        self._quick_act_import.setToolTip("Import netlist + layout (Ctrl+I)")
        self._quick_act_import.triggered.connect(self._on_import)
        tb.addAction(self._quick_act_import)

        self._quick_act_open = QAction(icon_open_file(), "Open JSON", self)
        self._quick_act_open.setToolTip("Open placement JSON (Ctrl+O)")
        self._quick_act_open.triggered.connect(self._on_open_file)
        tb.addAction(self._quick_act_open)

        self._quick_act_save = QAction(icon_save_file(), "Save", self)
        self._quick_act_save.setToolTip("Save current layout (Ctrl+S)")
        self._quick_act_save.triggered.connect(lambda: self._fwd("do_save"))
        tb.addAction(self._quick_act_save)

        self._quick_act_export = QAction(icon_export_file(), "Export JSON", self)
        self._quick_act_export.setToolTip("Export placement JSON")
        self._quick_act_export.triggered.connect(lambda: self._fwd("do_export_json"))
        tb.addAction(self._quick_act_export)

        tb.addSeparator()
        self._workspace_toggle_label = QLabel("View")
        tb.addWidget(self._workspace_toggle_label)
        self._workspace_quick_toggle = SegmentedToggle(variant="toolbar")
        self._workspace_quick_toggle.setToolTip("Switch workspace view")
        self._workspace_quick_toggle.mode_changed.connect(self._on_workspace_mode_changed)
        self._workspace_quick_toggle.setEnabled(False)
        tb.addWidget(self._workspace_quick_toggle)

    def _create_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setAllowedAreas(Qt.ToolBarArea.LeftToolBarArea)
        tb.setOrientation(Qt.Orientation.Vertical)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        tb.setIconSize(QSize(18, 18))
        tb.setStyleSheet(
            "QToolBar { background-color: #10151d; border-right: 1px solid #2d3548; spacing: 4px; padding: 6px 5px; }"
            "QToolButton { background: transparent; border: 1px solid transparent; border-radius: 8px; "
            "padding: 5px; color: #c8d0dc; min-width: 28px; min-height: 28px; }"
            "QToolButton:hover { background-color: #1e2a3a; border-color: #3d5066; }"
            "QToolButton:pressed { background-color: #2d3f54; }"
            "QToolButton:checked { background-color: #243a53; border-color: #4a90d9; }"
        )
        self._toolbar = tb
        self.addToolBar(Qt.ToolBarArea.LeftToolBarArea, tb)

        self._tb_act_undo = QAction(icon_undo(), "Undo", self)
        self._tb_act_undo.setToolTip("Undo (Ctrl+Z)")
        self._tb_act_undo.setEnabled(False)
        self._tb_act_undo.triggered.connect(lambda: self._fwd("do_undo"))
        tb.addAction(self._tb_act_undo)

        self._tb_act_redo = QAction(icon_redo(), "Redo", self)
        self._tb_act_redo.setToolTip("Redo (Ctrl+Y)")
        self._tb_act_redo.setEnabled(False)
        self._tb_act_redo.triggered.connect(lambda: self._fwd("do_redo"))
        tb.addAction(self._tb_act_redo)
        tb.addSeparator()

        self._tb_act_fit = QAction(icon_fit_view(), "Fit View", self)
        self._tb_act_fit.setToolTip("Fit to view (F)")
        self._tb_act_fit.triggered.connect(lambda: self._fwd_editor("fit_to_view"))
        tb.addAction(self._tb_act_fit)

        self._tb_act_zoom_in = QAction(icon_zoom_in(), "Zoom In", self)
        self._tb_act_zoom_in.setToolTip("Zoom in")
        self._tb_act_zoom_in.triggered.connect(lambda: self._fwd_editor("zoom_in"))
        tb.addAction(self._tb_act_zoom_in)

        self._tb_act_zoom_out = QAction(icon_zoom_out(), "Zoom Out", self)
        self._tb_act_zoom_out.setToolTip("Zoom out")
        self._tb_act_zoom_out.triggered.connect(lambda: self._fwd_editor("zoom_out"))
        tb.addAction(self._tb_act_zoom_out)

        self._tb_act_zoom_reset = QAction(icon_zoom_reset(), "Reset Zoom", self)
        self._tb_act_zoom_reset.setToolTip("Reset zoom")
        self._tb_act_zoom_reset.triggered.connect(lambda: self._fwd_editor("zoom_reset"))
        tb.addAction(self._tb_act_zoom_reset)
        tb.addSeparator()

        self._tb_act_swap = QAction(icon_swap(), "Swap", self)
        self._tb_act_swap.setToolTip("Swap selected devices (Ctrl+Shift+X)")
        self._tb_act_swap.triggered.connect(lambda: self._fwd("do_swap"))
        tb.addAction(self._tb_act_swap)

        self._tb_act_flip_h = QAction(icon_flip_h(), "Flip Horizontal", self)
        self._tb_act_flip_h.setToolTip("Flip horizontal (Ctrl+H)")
        self._tb_act_flip_h.triggered.connect(lambda: self._fwd("do_flip_h"))
        tb.addAction(self._tb_act_flip_h)

        self._tb_act_flip_v = QAction(icon_flip_v(), "Flip Vertical", self)
        self._tb_act_flip_v.setToolTip("Flip vertical (Ctrl+J)")
        self._tb_act_flip_v.triggered.connect(lambda: self._fwd("do_flip_v"))
        tb.addAction(self._tb_act_flip_v)
        tb.addSeparator()

        self._act_add_dummy = QAction(icon_add_dummy(), "Toggle Dummy Placement", self)
        self._act_add_dummy.setCheckable(True)
        self._act_add_dummy.setToolTip("Toggle dummy placement mode (D)")
        self._act_add_dummy.toggled.connect(self._on_toggle_dummy)
        tb.addAction(self._act_add_dummy)

        self._act_abutment = QAction(icon_abutment(), "Abutment Analysis", self)
        self._act_abutment.setCheckable(True)
        self._act_abutment.setToolTip("Analyze & apply abutment candidates")
        self._act_abutment.toggled.connect(self._on_toggle_abutment)
        tb.addAction(self._act_abutment)

        self._tb_act_ai = QAction(icon_ai_placement(), "Run AI Placement", self)
        self._tb_act_ai.setToolTip("Run AI placement (Ctrl+P)")
        self._tb_act_ai.triggered.connect(lambda: self._fwd("do_ai_placement"))
        tb.addAction(self._tb_act_ai)
        tb.addSeparator()

        self._ignore_grid_spin = False
        self._tb_act_select_all = QAction(icon_select_all(), "Select All", self)
        self._tb_act_select_all.setToolTip("Select all devices (Ctrl+A)")
        self._tb_act_select_all.triggered.connect(lambda: self._fwd("do_select_all"))
        tb.addAction(self._tb_act_select_all)

        self._tb_act_delete = QAction(icon_delete(), "Delete Selected", self)
        self._tb_act_delete.setToolTip("Delete selected devices (Delete)")
        self._tb_act_delete.triggered.connect(lambda: self._fwd("do_delete"))
        tb.addAction(self._tb_act_delete)
        return

        tb.addAction("⬅", lambda: self._fwd("do_undo")).setToolTip("Undo (Ctrl+Z)")
        tb.addAction("➡", lambda: self._fwd("do_redo")).setToolTip("Redo (Ctrl+Y)")
        tb.addSeparator()
        tb.addAction("🔀", lambda: self._fwd("do_swap")).setToolTip("Swap (Ctrl+Shift+X)")
        tb.addAction("↔", lambda: self._fwd("do_flip_h")).setToolTip("Flip H (Ctrl+H)")
        tb.addAction("↕", lambda: self._fwd("do_flip_v")).setToolTip("Flip V (Ctrl+J)")
        tb.addSeparator()

        # ── Dummy mode toggle ────────────────────────────────────
        self._act_add_dummy = QAction("＋ Dummy", self)
        self._act_add_dummy.setCheckable(True)
        self._act_add_dummy.setToolTip("Toggle dummy placement mode")
        self._act_add_dummy.toggled.connect(self._on_toggle_dummy)
        tb.addAction(self._act_add_dummy)

        # ── Abutment toggle ──────────────────────────────────────
        self._act_abutment = QAction("⊞ Abut", self)
        self._act_abutment.setCheckable(True)
        self._act_abutment.setToolTip("Analyze & apply abutment candidates")
        self._act_abutment.toggled.connect(self._on_toggle_abutment)
        tb.addAction(self._act_abutment)

        tb.addSeparator()

        # ── Row / Col spinboxes ──────────────────────────────────
        self._ignore_grid_spin = False
        spin_style = (
            "QSpinBox { background: #1a1f2b; color: #e0e8f0; border: 1px solid #2d3548; "
            "border-radius: 4px; padding: 2px 6px; min-width: 50px; }"
            "QSpinBox:focus { border-color: #4a90d9; }"
        )
        lbl_r = QLabel(" Rows:")
        lbl_r.setStyleSheet("color: #8899aa; font-family: 'Segoe UI'; font-size: 9pt;")
        tb.addWidget(lbl_r)
        self._row_spin = QSpinBox()
        self._row_spin.setRange(1, 20)
        self._row_spin.setValue(2)
        self._row_spin.setStyleSheet(spin_style)
        self._row_spin.valueChanged.connect(self._on_row_spin_changed)
        tb.addWidget(self._row_spin)

        lbl_c = QLabel(" Cols:")
        lbl_c.setStyleSheet("color: #8899aa; font-family: 'Segoe UI'; font-size: 9pt;")
        tb.addWidget(lbl_c)
        self._col_spin = QSpinBox()
        self._col_spin.setRange(1, 50)
        self._col_spin.setValue(4)
        self._col_spin.setStyleSheet(spin_style)
        self._col_spin.valueChanged.connect(self._on_col_spin_changed)
        tb.addWidget(self._col_spin)

        tb.addSeparator()

        # ── Selection count ──────────────────────────────────────
        self._sel_label = QLabel("  Sel: 0  ")
        self._sel_label.setStyleSheet(
            "color: #8899aa; font-family: 'Segoe UI'; font-size: 9pt; "
            "background: #161c28; border: 1px solid #2d3548; border-radius: 4px; padding: 2px 8px;"
        )
        tb.addWidget(self._sel_label)

    def _create_status_bar(self):
        sb = QStatusBar(self)
        sb.setSizeGripEnabled(False)
        sb.setStyleSheet(
            "QStatusBar { background-color: #10151d; border-top: 1px solid #2d3548; color: #9aa7b7; }"
            "QStatusBar::item { border: none; }"
        )
        self.setStatusBar(sb)

        spin_style = (
            "QSpinBox { background: #1a1f2b; color: #e0e8f0; border: 1px solid #2d3548; "
            "border-radius: 5px; padding: 2px 6px; min-width: 52px; }"
            "QSpinBox:focus { border-color: #4a90d9; }"
        )

        sb.addPermanentWidget(QLabel("Rows"))
        self._row_spin = QSpinBox()
        self._row_spin.setRange(1, 20)
        self._row_spin.setValue(2)
        self._row_spin.setStyleSheet(spin_style)
        self._row_spin.valueChanged.connect(self._on_row_spin_changed)
        sb.addPermanentWidget(self._row_spin)

        sb.addPermanentWidget(QLabel("Cols"))
        self._col_spin = QSpinBox()
        self._col_spin.setRange(1, 50)
        self._col_spin.setValue(4)
        self._col_spin.setStyleSheet(spin_style)
        self._col_spin.valueChanged.connect(self._on_col_spin_changed)
        sb.addPermanentWidget(self._col_spin)

        self._sel_label = QLabel("  Sel: 0  ")
        self._sel_label.setStyleSheet(
            "color: #8899aa; font-family: 'Segoe UI'; font-size: 9pt; "
            "background: #161c28; border: 1px solid #2d3548; border-radius: 5px; padding: 3px 10px;"
        )
        sb.addPermanentWidget(self._sel_label)

    def _set_chrome_visible(self, visible):
        self.menuBar().setVisible(visible)
        self._file_toolbar.setVisible(visible)
        self._toolbar.setVisible(visible)
        self.statusBar().setVisible(visible)

    # =================================================================
    #  Forward helpers (delegate to active tab)
    # =================================================================
    def _fwd(self, method_name, *args):
        tab = self.current_tab()
        if tab and hasattr(tab, method_name):
            getattr(tab, method_name)(*args)

    def _fwd_editor(self, method_name, *args):
        tab = self.current_tab()
        if tab and hasattr(tab.editor, method_name):
            getattr(tab.editor, method_name)(*args)

    # =================================================================
    #  Toolbar → tab callbacks
    # =================================================================
    def _on_new_tab(self):
        self._new_tab()

    def _close_current_tab(self):
        idx = self._tab_widget.currentIndex()
        if idx >= 0:
            self._close_tab(idx)

    def _on_toggle_dummy(self, checked):
        tab = self.current_tab()
        if tab:
            tab.set_dummy_mode(checked)

    def _on_workspace_mode_changed(self, mode):
        tab = self.current_tab()
        if tab:
            tab.set_workspace_mode(mode)

    def _toggle_dummy_action(self):
        self._act_add_dummy.toggle()

    def _on_toggle_abutment(self, checked):
        tab = self.current_tab()
        if tab:
            tab.set_abutment_mode(checked)

    def _on_row_spin_changed(self, value):
        if self._ignore_grid_spin:
            return
        tab = self.current_tab()
        if tab:
            tab.set_row_target(value)

    def _on_col_spin_changed(self, value):
        if self._ignore_grid_spin:
            return
        tab = self.current_tab()
        if tab:
            tab.set_col_target(value)

    def _on_close_row_gap_toggled(self, checked):
        self._row_gap_spin.setEnabled(checked)
        tab = self.current_tab()
        if tab:
            tab.set_close_row_gap(checked, self._row_gap_spin.value())

    def _on_row_gap_spin_changed(self, value):
        tab = self.current_tab()
        if tab:
            tab.set_row_gap_value(value)

    def _on_reload_app(self):
        for i in range(self._tab_widget.count()):
            tab = self._tab_widget.widget(i)
            if isinstance(tab, LayoutEditorTab):
                tab.shutdown()
        os.execl(sys.executable, sys.executable, *sys.argv)

    # =================================================================
    #  Close event – graceful shutdown
    # =================================================================
    def closeEvent(self, event):
        for i in range(self._tab_widget.count()):
            tab = self._tab_widget.widget(i)
            if isinstance(tab, LayoutEditorTab):
                tab.shutdown()
        super().closeEvent(event)

    # =================================================================
    #  Tab-bar stylesheet
    # =================================================================
    @staticmethod
    def _tab_bar_style():
        return """
            QTabWidget::pane {
                border: none;
                background-color: #0e1219;
            }
            QTabBar {
                background-color: #12161f;
                border-bottom: 1px solid #2d3548;
            }
            QTabBar::tab {
                background-color: #171c24;
                color: #8d9aac;
                border: 1px solid transparent;
                border-bottom: none;
                border-top-left-radius: 7px;
                border-top-right-radius: 7px;
                padding: 5px 14px;
                margin-top: 4px;
                margin-right: 2px;
                font-family: 'Segoe UI';
                font-size: 9.5pt;
                min-width: 96px;
            }
            QTabBar::tab:selected {
                background-color: #1d2430;
                color: #eef4fb;
                border-color: #2d3548;
                font-weight: 600;
            }
            QTabBar::tab:hover:!selected {
                background-color: #202937;
                color: #c8d0dc;
            }
            QTabBar::close-button {
                image: none;
                subcontrol-position: right;
                padding: 2px;
            }
            QTabBar::close-button:hover {
                background-color: rgba(255, 80, 80, 0.3);
                border-radius: 3px;
            }
        """


# =====================================================================
#  Main Entry Point
# =====================================================================
if __name__ == "__main__":
    app = QApplication(sys.argv)

    # ── Fusion dark palette ──────────────────────────────────────
    app.setStyle("Fusion")
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

    # ── Global tooltip styling ───────────────────────────────────
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

    # ── Resolve initial file from CLI args ───────────────────────
    placement_path = None
    if len(sys.argv) > 1:
        placement_path = sys.argv[1]
        if not os.path.isabs(placement_path):
            placement_path = os.path.abspath(placement_path)

    window = MainWindow(placement_path)
    window.show()
    sys.exit(app.exec())
