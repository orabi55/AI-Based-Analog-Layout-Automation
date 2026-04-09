# -*- coding: utf-8 -*-
import sys
import os
import json
import copy
import glob
import re

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path so that cross-package imports
# (e.g. ai_agent.ai_initial_placement.llm_worker from symbolic_editor/) work regardless of how
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
    QDialog,
    QFormLayout,
    QPushButton,
    QProgressDialog,
    QMessageBox,
    QGroupBox,
    QDialogButtonBox,
    QLineEdit,
    QFrame,
    QComboBox,
    QRadioButton,
    QButtonGroup,
)
from PySide6.QtCore import Qt, QTimer, QSize, QThread, Signal
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
# Async Background Worker
# -------------------------------------------------
class GenericWorker(QThread):
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, target, *args, **kwargs):
        super().__init__()
        self.target = target
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            result = self.target(*self.args, **self.kwargs)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))

# -------------------------------------------------
# Modern Loading Overlay
# -------------------------------------------------
class LoadingOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background-color: rgba(20, 24, 34, 180);")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.card = QFrame()
        self.card.setStyleSheet("""
            QFrame {
                background-color: #1e2636;
                border: 1px solid #3d5066;
                border-radius: 12px;
                padding: 30px;
            }
            QLabel#spinner {
                font-size: 32px;
                color: #4a90d9;
            }
            QLabel#message {
                font-size: 14px;
                font-family: 'Segoe UI';
                color: #e0e8f0;
                margin-top: 10px;
            }
        """)
        
        card_layout = QVBoxLayout(self.card)
        card_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.spinner = QLabel("⠋")
        self.spinner.setObjectName("spinner")
        self.spinner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.message_label = QLabel("Loading...")
        self.message_label.setObjectName("message")
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        card_layout.addWidget(self.spinner)
        card_layout.addWidget(self.message_label)
        
        layout.addWidget(self.card)

        self._dots = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self._dot_index = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)

    def _animate(self):
        self._dot_index = (self._dot_index + 1) % len(self._dots)
        self.spinner.setText(self._dots[self._dot_index])

    def show_message(self, text):
        self.message_label.setText(text)
        self._timer.start(100)
        self.show()
        self.raise_()

    def hide_overlay(self):
        self._timer.stop()
        self.hide()

# -------------------------------------------------
# Import Dialog — select .sp + .oas and parse
# -------------------------------------------------
class ImportDialog(QDialog):
    """Dialog for importing a SPICE netlist and layout file."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import from Netlist + Layout")
        self.setMinimumWidth(520)
        self.setStyleSheet("""
            QDialog {
                background-color: #1a1f2b;
                color: #c8d0dc;
                font-family: 'Segoe UI';
            }
            QLabel {
                color: #c8d0dc;
                font-size: 10pt;
            }
            QLineEdit {
                background-color: #232a38;
                color: #c8d0dc;
                border: 1px solid #2d3548;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 10pt;
            }
            QPushButton {
                background-color: #2a3345;
                color: #c8d0dc;
                border: 1px solid #3d5066;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 10pt;
            }
            QPushButton:hover {
                background-color: #3d5066;
                color: #ffffff;
            }
            QPushButton#ok_btn {
                background-color: #4a90d9;
                border-color: #4a90d9;
                color: #ffffff;
                font-weight: bold;
            }
            QPushButton#ok_btn:hover {
                background-color: #5da0e9;
            }
            QCheckBox {
                color: #c8d0dc;
                font-size: 10pt;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 16px; height: 16px;
            }
            QGroupBox {
                color: #8899aa;
                border: 1px solid #2d3548;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 16px;
                font-size: 9pt;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        # Title
        title = QLabel("Import Circuit from Design Files")
        title.setStyleSheet("font-size: 13pt; font-weight: bold; color: #e0e8f0;")
        layout.addWidget(title)

        subtitle = QLabel("Select a SPICE netlist and (optionally) a layout file to generate the placement.")
        subtitle.setStyleSheet("font-size: 9pt; color: #8899aa; margin-bottom: 8px;")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # --- File pickers ---
        files_group = QGroupBox("Design Files")
        files_layout = QFormLayout(files_group)
        files_layout.setSpacing(10)

        # Netlist (.sp)
        sp_row = QHBoxLayout()
        self._sp_edit = QLineEdit()
        self._sp_edit.setPlaceholderText("Select a .sp netlist file (required)")
        self._sp_edit.setReadOnly(True)
        sp_btn = QPushButton("Browse…")
        sp_btn.setFixedWidth(90)
        sp_btn.clicked.connect(self._browse_sp)
        sp_row.addWidget(self._sp_edit, 1)
        sp_row.addWidget(sp_btn)
        files_layout.addRow("SPICE Netlist:", sp_row)

        # Layout (.oas / .gds)
        oas_row = QHBoxLayout()
        self._oas_edit = QLineEdit()
        self._oas_edit.setPlaceholderText("Select a .oas/.gds layout file (optional)")
        self._oas_edit.setReadOnly(True)
        oas_btn = QPushButton("Browse…")
        oas_btn.setFixedWidth(90)
        oas_btn.clicked.connect(self._browse_oas)
        oas_row.addWidget(self._oas_edit, 1)
        oas_row.addWidget(oas_btn)
        files_layout.addRow("Layout File:", oas_row)

        layout.addWidget(files_group)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        ok_btn = QPushButton("Import")
        ok_btn.setObjectName("ok_btn")
        ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        # Results
        self.sp_path = ""
        self.oas_path = ""

    def _browse_sp(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select SPICE Netlist", "",
            "SPICE Files (*.sp *.spice *.cdl *.cir);;All Files (*)"
        )
        if path:
            self._sp_edit.setText(path)

    def _browse_oas(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Layout File", "",
            "Layout Files (*.oas *.gds);;All Files (*)"
        )
        if path:
            self._oas_edit.setText(path)

    def _on_ok(self):
        if not self._sp_edit.text().strip():
            QMessageBox.warning(self, "Missing File",
                                "Please select a SPICE netlist (.sp) file.")
            return
        self.sp_path = self._sp_edit.text().strip()
        self.oas_path = self._oas_edit.text().strip()
        self.accept()

# -------------------------------------------------
# AI Model Selection Dialog
# -------------------------------------------------
class AIModelSelectionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select AI Model")
        self.setMinimumWidth(450)
        self.setStyleSheet("""
            QDialog {
                background-color: #1a1f2b;
                color: #c8d0dc;
                font-family: 'Segoe UI';
            }
            QLabel {
                color: #c8d0dc;
                font-size: 10pt;
            }
            QLineEdit {
                background-color: #232a38;
                color: #c8d0dc;
                border: 1px solid #2d3548;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 10pt;
            }
            QPushButton {
                background-color: #2a3345;
                color: #c8d0dc;
                border: 1px solid #3d5066;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 10pt;
            }
            QPushButton:hover {
                background-color: #3d5066;
                color: #ffffff;
            }
            QPushButton#run_btn {
                background-color: #4a90d9;
                border-color: #4a90d9;
                color: #ffffff;
                font-weight: bold;
            }
            QPushButton#run_btn:hover {
                background-color: #5da0e9;
            }
            QGroupBox {
                background-color: #1e2636;
                border: 1px solid #3d5066;
                border-radius: 8px;
                margin-top: 14px;
                padding-top: 16px;
                font-size: 11pt;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
            QCheckBox {
                color: #c8d0dc;
                font-size: 11pt;
                font-weight: bold;
                spacing: 10px;
            }
            QCheckBox::indicator {
                width: 18px; height: 18px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # Title
        title = QLabel("AI Initial Placement")
        title.setStyleSheet("font-size: 15pt; font-weight: bold; color: #ffffff;")
        layout.addWidget(title)

        subtitle = QLabel("Select an AI model and configure its constraints to proceed.")
        subtitle.setStyleSheet("font-size: 10pt; color: #8899aa; margin-bottom: 8px;")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # Button group for exclusivity natively with checkpoints
        self.model_group = QButtonGroup(self)
        self.model_group.setExclusive(True)

        # 1. Gemini
        gemini_group = QGroupBox()
        gemini_layout = QVBoxLayout(gemini_group)
        self.check_gemini = QCheckBox("Gemini Pro (Cloud)")
        self.check_gemini.setChecked(True)
        self.model_group.addButton(self.check_gemini)
        gemini_layout.addWidget(self.check_gemini)

        gemini_desc = QLabel("Fast and efficient layout reasoning.")
        gemini_desc.setStyleSheet("color: #8899aa; font-size: 9pt; margin-left: 30px;")
        gemini_layout.addWidget(gemini_desc)

        gemini_form = QFormLayout()
        gemini_form.setContentsMargins(30, 4, 0, 4)
        self.gemini_api_key = QLineEdit()
        self.gemini_api_key.setPlaceholderText("Enter Gemini API Key")
        self.gemini_api_key.setText(os.environ.get("GEMINI_API_KEY", "******"))
        gemini_form.addRow("Default API Key:", self.gemini_api_key)
        gemini_layout.addLayout(gemini_form)

        layout.addWidget(gemini_group)

        # 2. OpenAI
        openai_group = QGroupBox()
        openai_layout = QVBoxLayout(openai_group)
        self.check_openai = QCheckBox("OpenAI GPT-4 (Cloud)")
        self.model_group.addButton(self.check_openai)
        openai_layout.addWidget(self.check_openai)

        openai_desc = QLabel("High precision spatial understanding.")
        openai_desc.setStyleSheet("color: #8899aa; font-size: 9pt; margin-left: 30px;")
        openai_layout.addWidget(openai_desc)

        openai_form = QFormLayout()
        openai_form.setContentsMargins(30, 4, 0, 4)
        self.openai_api_key = QLineEdit()
        self.openai_api_key.setPlaceholderText("Enter OpenAI API Key")
        self.openai_api_key.setText(os.environ.get("OPENAI_API_KEY", "******"))
        openai_form.addRow("Default API Key:", self.openai_api_key)
        openai_layout.addLayout(openai_form)

        layout.addWidget(openai_group)

        # 3. Ollama
        ollama_group = QGroupBox()
        ollama_layout = QVBoxLayout(ollama_group)
        self.check_ollama = QCheckBox("Ollama (Local)")
        self.model_group.addButton(self.check_ollama)
        ollama_layout.addWidget(self.check_ollama)

        ollama_desc = QLabel("Private execution. <b>Note: Requires Ollama installed & running.</b>")
        ollama_desc.setStyleSheet("color: #8899aa; font-size: 9pt; margin-left: 30px;")
        ollama_desc.setWordWrap(True)
        ollama_layout.addWidget(ollama_desc)

        layout.addWidget(ollama_group)

        # Connect toggles to enable/disable inputs
        self.check_gemini.toggled.connect(self._on_model_changed)
        self.check_openai.toggled.connect(self._on_model_changed)
        self.check_ollama.toggled.connect(self._on_model_changed)
        self._on_model_changed()

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        
        self.run_btn = QPushButton("Run Placement")
        self.run_btn.setObjectName("run_btn")
        self.run_btn.clicked.connect(self.accept)
        
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(self.run_btn)
        
        layout.addLayout(btn_layout)

    def _on_model_changed(self):
        self.gemini_api_key.setEnabled(self.check_gemini.isChecked())
        self.openai_api_key.setEnabled(self.check_openai.isChecked())

    def get_selected_model(self):
        if self.check_gemini.isChecked():
            return "Gemini"
        elif self.check_openai.isChecked():
            return "OpenAI"
        elif self.check_ollama.isChecked():
            return "Ollama"

    def apply_api_keys(self):
        # Update environment variables based on user changes if they didn't leave them empty/starred out
        gemini_key = self.gemini_api_key.text().strip().strip('\'"')
        if gemini_key and gemini_key != "******":
            os.environ["GEMINI_API_KEY"] = gemini_key
            
        openai_key = self.openai_api_key.text().strip().strip('\'"')
        if openai_key and openai_key != "******":
            os.environ["OPENAI_API_KEY"] = openai_key

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
        self._original_data = None  # raw loaded JSON (for edges + terminals)
        self.nodes = None


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
        # command_requested carries ONE cmd dict at a time; we batch-collect
        # them so orchestrator multi-CMD responses become ONE undo operation.
        self._pending_cmds = []           # collects commands in the same Qt event-loop turn
        self._batch_flush_timer = None    # fires after all cmds arrive this turn
        self.chat_panel.command_requested.connect(self._enqueue_ai_command)
        self.editor.set_dummy_place_callback(self._add_dummy_device)

        # Connect panel toggle buttons (in each panel header)
        self.device_tree.toggle_requested.connect(self._toggle_device_tree)
        self.chat_panel.toggle_requested.connect(self._toggle_chat_panel)

        # Loading Overlay
        self.overlay = LoadingOverlay(self)
        self.overlay.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'overlay'):
            self.overlay.resize(self.size())

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

        self._act_import = QAction("Import from Netlist + Layout…", self)
        self._act_import.setShortcut(QKeySequence("Ctrl+I"))
        self._act_import.triggered.connect(self._on_import_netlist_layout)
        file_menu.addAction(self._act_import)

        file_menu.addSeparator()

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
        self._act_ai_placement = QAction("Run AI Initial Placement", self)
        self._act_ai_placement.setShortcut(QKeySequence("Ctrl+P"))
        self._act_ai_placement.triggered.connect(self._on_run_ai_placement)
        design_menu.addAction(self._act_ai_placement)

        view_menu = mb.addMenu("View")
        
        self._act_view_symbol = QAction("Symbol View (Macro Level)", self)
        self._act_view_symbol.setShortcut(QKeySequence("Ctrl+F"))
        self._act_view_symbol.triggered.connect(
            lambda: self.editor.set_view_level("symbol")
        )
        view_menu.addAction(self._act_view_symbol)

        self._act_view_transistor = QAction("Transistor View (Micro Level)", self)
        self._act_view_transistor.setShortcut(QKeySequence("Shift+F"))
        self._act_view_transistor.triggered.connect(
            lambda: self.editor.set_view_level("transistor")
        )
        view_menu.addAction(self._act_view_transistor)
        
        view_menu.addSeparator()

        self._act_toggle_blocks = QAction("Toggle Block Overlays", self)
        self._act_toggle_blocks.setCheckable(True)
        self._act_toggle_blocks.setChecked(True)
        self._act_toggle_blocks.triggered.connect(
            lambda checked: self.editor.toggle_block_overlays(checked)
        )
        view_menu.addAction(self._act_toggle_blocks)

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

        toolbar.addSeparator()

        # Transistor Abutment toggle
        self._act_abutment = QAction("⊞ Abut", self)
        self._act_abutment.setCheckable(True)
        self._act_abutment.setShortcut(QKeySequence("A"))
        self._act_abutment.setToolTip(
            "Toggle Transistor Abutment (A)\n"
            "Detects shared S/D nets between adjacent transistors\n"
            "and marks them as abutted (diffusion sharing)."
        )
        self._act_abutment.toggled.connect(self._on_toggle_abutment)
        toolbar.addAction(self._act_abutment)


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
        if filepath == None or not os.path.isfile(filepath): 
            return
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
        if not self._original_data:
            return
        edges = self._original_data.get("edges")
        blocks = self._original_data.get("blocks", {})
        # Rebuild blocks from per-node block tags if top-level key is missing
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
        self.device_tree.set_edges(edges)
        self.device_tree.set_terminal_nets(self._terminal_nets)
        self.device_tree.load_devices(self.nodes, blocks=blocks)
        self.editor.load_placement(self.nodes, compact=compact)
        self.editor.set_edges(edges)
        self.editor.set_terminal_nets(self._terminal_nets)
        self.editor.set_blocks(blocks)
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
        """Build the output dict with updated positions and routing annotations."""
        self._sync_node_positions()
        output = {"nodes": copy.deepcopy(self.nodes)}
        if "edges" in self._original_data:
            output["edges"] = self._original_data["edges"]
        if hasattr(self, "_routing_annotations") and self._routing_annotations:
            output["routing_annotations"] = copy.deepcopy(self._routing_annotations)
        return output

    # -------------------------------------------------
    # Undo / Redo
    # -------------------------------------------------
    def _push_undo(self):
        """Snapshot current positions onto the undo stack."""
        if not self.nodes:
            return
        snapshot = copy.deepcopy(self.nodes)
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
        self._redo_stack.append(copy.deepcopy(self.nodes))
        # Restore previous state
        self.nodes = self._undo_stack.pop()
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

    def _on_toggle_abutment(self, enabled):
        """Apply or clear transistor abutment based on toggle state."""
        if enabled:
            self.editor.apply_abutment()
            msg = (
                "✅ Abutment ON — Adjacent transistors sharing Source/Drain nets "
                "are now marked as abutted (diffusion sharing active).\n"
                "Orange stripes = NMOS abutted edge | Red stripes = PMOS abutted edge."
            )
        else:
            self.editor.clear_abutment()
            msg = "Abutment OFF — all abutment markers cleared."
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
    # Import from Netlist + Layout
    # -------------------------------------------------
    def _on_import_netlist_layout(self):
        """Open the import dialog, parse files, and visualize the graph."""
        dlg = ImportDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        sp_path = dlg.sp_path
        oas_path = dlg.oas_path

        self.overlay.show_message("Parsing design files...")
        
        self._import_worker = GenericWorker(self._run_parser_pipeline, sp_path, oas_path)
        self._import_worker.finished.connect(lambda data: self._on_import_completed(data, sp_path))
        self._import_worker.error.connect(self._on_import_error)
        self._import_worker.start()

    def _on_import_completed(self, data, sp_path):
        self.overlay.hide_overlay()
        
        # Save the generated graph JSON next to the .sp file
        base_name = os.path.splitext(os.path.basename(sp_path))[0]
        sp_dir = os.path.dirname(os.path.abspath(sp_path))
        out_path = os.path.join(sp_dir, f"{base_name}_graph.json")
        with open(out_path, "w") as f:
            json.dump(data, f, indent=4)

        # Load into the GUI
        self._load_from_data_dict(data, out_path)

        num_nodes = len(data.get('nodes', []))
        self.chat_panel._append_message(
            "AI",
            f"Imported {num_nodes} devices from "
            f"{os.path.basename(sp_path)}\n"
            f"Saved graph to: {os.path.basename(out_path)}\n\n"
            f"To run AI initial placement: Design > Run AI Initial Placement (Ctrl+P)",
            "#e8f4fd", "#1a1a2e",
        )

    def _on_import_error(self, err_msg):
        self.overlay.hide_overlay()
        QMessageBox.critical(
            self, "Import Failed",
            f"Failed to parse design files:\n\n{err_msg}",
        )

    # -------------------------------------------------
    # Run AI Initial Placement (Design menu)
    # -------------------------------------------------
    def _on_run_ai_placement(self):
        """Run AI initial placement on the currently loaded data."""
        if not self.nodes:
            self.chat_panel._append_message(
                "AI", "No layout loaded. Import a netlist first (Ctrl+I).",
                "#fde8e8", "#a00",
            )
            return

        # Show the AI model selection dialog first
        dialog = AIModelSelectionDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
            
        model_choice = dialog.get_selected_model()
        dialog.apply_api_keys()

        # Build the data dict from current state
        self._sync_node_positions()
        data = copy.deepcopy(self._build_output_data())
        if "terminal_nets" not in data:
            data["terminal_nets"] = self._terminal_nets

        self.overlay.show_message(f"Running AI initial placement ({model_choice})...")

        self._ai_worker = GenericWorker(self._run_ai_initial_placement, data, model_choice)
        self._ai_worker.finished.connect(self._on_ai_placement_completed)
        self._ai_worker.error.connect(self._on_ai_placement_error)
        self._ai_worker.start()

    def _on_ai_placement_completed(self, data):
        self.overlay.hide_overlay()
        
        # Save the placement JSON
        if self._current_file:
            base = os.path.splitext(self._current_file)[0]
            # Replace _graph with _placement, or append _placement
            if base.endswith("_graph"):
                out_path = base.replace("_graph", "_initial_placement") + ".json"
            else:
                out_path = base + "_placed.json"
        else:
            out_path = os.path.join(os.getcwd(), "placement.json")

        with open(out_path, "w") as f:
            json.dump(data, f, indent=4)

        # Load the updated placement into the GUI
        self._load_from_data_dict(data, out_path)

        self.chat_panel._append_message(
            "AI",
            f"AI initial placement complete!\n"
            f"Saved to: {os.path.basename(out_path)}\n"
            f"You can now edit the layout, swap devices, or chat with the AI.",
            "#e8f4fd", "#1a1a2e",
        )

    def _on_ai_placement_error(self, err_msg):
        self.overlay.hide_overlay()
        QMessageBox.warning(
            self, "AI Placement Failed",
            f"AI placement failed:\n\n{err_msg}",
        )

    @staticmethod
    def _run_parser_pipeline(sp_path, oas_path=""):
        """
        Run the full parser pipeline:
          1. Parse SPICE netlist (with block detection)
          2. Parse layout (.oas/.gds) and match devices
          3. Build circuit graph (edges)
          4. Assemble nodes with geometry + edges + block info

        Returns: {"nodes": [...], "edges": [...], "terminal_nets": {...}, "blocks": {...}}
        """
        from parser.netlist_reader import read_netlist_with_blocks
        from parser.circuit_graph import build_circuit_graph

        # 1. Parse netlist with block tracking
        netlist, block_map = read_netlist_with_blocks(sp_path)

        # 2. Parse layout (optional) and match devices
        layout_instances = []
        device_mapping = {}  # {device_name: layout_index}
        if oas_path and os.path.isfile(oas_path):
            try:
                from parser.layout_reader import extract_layout_instances
                layout_instances = extract_layout_instances(oas_path)
            except Exception as e:
                pass

        if layout_instances:
            try:
                from parser.device_matcher import match_devices
                device_mapping = match_devices(netlist, layout_instances)
            except Exception as e:
                device_mapping = {}

        # 3. Build nodes (first pass — collect all devices with temp geometry)
        PITCH_UM      = 0.294
        ROW_HEIGHT_UM = 0.668
        BLOCK_GAP_UM  = PITCH_UM * 2
        PASSIVE_ROW_GAP = PITCH_UM  # gap between NMOS row and passive row
        nodes = []
        terminal_nets = {}
        node_by_name = {}

        for dev_name, dev in netlist.devices.items():
            # Exact type — do NOT collapse to nmos/pmos
            dev_type = dev.type  # "nmos" | "pmos" | "res" | "cap"
            is_passive = dev_type in ("res", "cap")

            # Geometry: from layout matcher or from params
            layout_idx = device_mapping.get(dev_name)
            if layout_idx is not None and layout_idx < len(layout_instances):
                inst = layout_instances[layout_idx]
                geom = {
                    "x":           inst.get("x", 0.0),
                    "y":           inst.get("y", 0.0),
                    "width":       inst.get("width",  PITCH_UM),
                    "height":      inst.get("height", ROW_HEIGHT_UM),
                    "orientation": inst.get("orientation", "R0"),
                }
            elif is_passive:
                # Compute passive geometry from params
                prm = dev.params
                raw_w = prm.get("w", PITCH_UM)
                raw_l = prm.get("l", ROW_HEIGHT_UM)
                nf_p  = max(1, int(prm.get("nf", 1)))
                m_p   = max(1, int(prm.get("m",  1)))
                if dev_type == "res":
                    # Resistor: length is the long axis, width is the narrow axis
                    width_um  = max(raw_l * nf_p, PITCH_UM)
                    height_um = max(raw_w, 0.1)
                else:
                    # Capacitor: width scaled by fingers/stacks
                    stm = max(1, int(prm.get("stm", 1)))
                    spm = max(1, int(prm.get("spm", 1)))
                    width_um  = max(raw_w * max(nf_p, 1), PITCH_UM)
                    height_um = max(raw_l * max(stm * spm, 1), ROW_HEIGHT_UM) \
                                if raw_l > 0.1 else ROW_HEIGHT_UM
                geom = {
                    "x": 0.0, "y": 0.0,
                    "width": width_um, "height": height_um,
                    "orientation": "R0",
                }
            else:
                # Placeholder — will be repositioned by block-aware layout below
                geom = {
                    "x": 0.0,
                    "y": 0.0,
                    "width": PITCH_UM,
                    "height": ROW_HEIGHT_UM,
                    "orientation": "R0",
                }

            electrical = {
                "l":    dev.params.get("l",    1.4e-08),
                "nf":   dev.params.get("nf",   1),
                "nfin": dev.params.get("nfin", 1),
                "w":    dev.params.get("w",    0),
            }
            if dev_type == "cap":
                electrical["cval"] = dev.params.get("cval", 0.0)

            node_dict = {
                "id":         dev_name,
                "type":       dev_type,
                "electrical": electrical,
                "geometry":   geom,
            }

            # Block membership
            block_info = block_map.get(dev_name)
            if block_info is None:
                base = re.sub(r'_f\d+$', '', dev_name)
                if base != dev_name:
                    block_info = block_map.get(base)
            if block_info:
                node_dict["block"] = block_info

            nodes.append(node_dict)
            node_by_name[dev_name] = node_dict

            # Terminal nets — passives use pin1/pin2; transistors use D/G/S
            if hasattr(dev, 'pins') and dev.pins:
                if is_passive:
                    terminal_nets[dev_name] = {
                        "1": dev.pins.get("1", ""),
                        "2": dev.pins.get("2", ""),
                    }
                else:
                    terminal_nets[dev_name] = {
                        "D": dev.pins.get("D", ""),
                        "G": dev.pins.get("G", ""),
                        "S": dev.pins.get("S", ""),
                    }

        # 4. Build edges from circuit graph
        G = build_circuit_graph(netlist)
        edges = [
            {"source": u, "target": v, "net": d.get("net", "")}
            for u, v, d in G.edges(data=True)
        ]

        # 5. Build blocks summary (include finger-expanded device names)
        blocks = {}
        for node in nodes:
            b = node.get("block")
            if b:
                inst = b.get("instance", "")
                if inst and inst not in blocks:
                    blocks[inst] = {"subckt": b.get("subckt", "?"), "devices": []}
                if inst:
                    blocks[inst]["devices"].append(node["id"])

        if blocks:
            block_labels = [f"{k} ({v['subckt']})" for k, v in blocks.items()]

        # 6. Block-aware placement (only when no layout geometry is available)
        if not device_mapping:
            pmos_y    = 0.0                                       # PMOS row y (Top)
            nmos_y    = ROW_HEIGHT_UM                             # NMOS row y (Middle)
            passive_y = nmos_y + ROW_HEIGHT_UM + PASSIVE_ROW_GAP  # Passive row y (Bottom)
            x_cursor  = 0.0
            passive_x_cursor = 0.0

            block_order = list(blocks.keys())

            blocked_ids = set()
            for info in blocks.values():
                blocked_ids.update(info["devices"])
            unblocked = [n for n in nodes if n["id"] not in blocked_ids]

            for block_idx, inst in enumerate(block_order):
                info = blocks[inst]
                members = [node_by_name[d] for d in info["devices"]
                           if d in node_by_name]
                pmos_members    = [n for n in members if n["type"] == "pmos"]
                nmos_members    = [n for n in members if n["type"] == "nmos"]
                passive_members = [n for n in members if n["type"] in ("res", "cap")]

                # Place PMOS in top row
                local_x = x_cursor
                for n in pmos_members:
                    w = n["geometry"]["width"]
                    n["geometry"]["x"] = local_x
                    n["geometry"]["y"] = pmos_y
                    local_x += w
                pmos_right = local_x

                # Place NMOS in middle row
                local_x = x_cursor
                for n in nmos_members:
                    w = n["geometry"]["width"]
                    n["geometry"]["x"] = local_x
                    n["geometry"]["y"] = nmos_y
                    local_x += w
                nmos_right = local_x

                # Place passives right in the passive row (shared x cursor)
                for n in passive_members:
                    w = n["geometry"]["width"]
                    n["geometry"]["x"] = passive_x_cursor
                    n["geometry"]["y"] = passive_y
                    passive_x_cursor += w + PITCH_UM

                block_right = max(pmos_right, nmos_right)
                x_cursor = block_right + BLOCK_GAP_UM

            # Place unblocked devices
            for n in unblocked:
                w = n["geometry"]["width"]
                if n["type"] == "pmos":
                    n["geometry"]["x"] = x_cursor
                    n["geometry"]["y"] = pmos_y
                    x_cursor += w
                elif n["type"] == "nmos":
                    n["geometry"]["x"] = x_cursor
                    n["geometry"]["y"] = nmos_y
                    x_cursor += w
                else:
                    # Passive device — goes in passive row
                    n["geometry"]["x"] = passive_x_cursor
                    n["geometry"]["y"] = passive_y
                    passive_x_cursor += w + PITCH_UM


        return {
            "nodes": nodes,
            "edges": edges,
            "terminal_nets": terminal_nets,
            "blocks": blocks,
        }

    @staticmethod
    def _run_ai_initial_placement(data, model_choice="Gemini"):
        """
        Send the parsed graph to the selected AI model for initial placement.
        Updates x/y coordinates in the nodes and returns the updated data.
        """
        import tempfile

        # Write to temp file for the placer
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp_in:
            json.dump(data, tmp_in, indent=2)
            tmp_in_path = tmp_in.name

        tmp_out_path = tmp_in_path.replace(".json", "_placed.json")

        try:
            if model_choice == "OpenAI":
                try:
                    from ai_agent.ai_initial_placement.openai_placer import llm_generate_placement
                    llm_generate_placement(tmp_in_path, tmp_out_path)
                except Exception as e:
                    err_str = str(e)
                    if "401" in err_str or "Incorrect API key" in err_str:
                        raise RuntimeError(
                            "Invalid OpenAI API Key.\n\n"
                            "The API key provided is completely incorrect, expired, or rejected by OpenAI.\n\n"
                            "Please enter a fresh, valid OpenAI secret key in the dialog."
                        )
                    raise

            elif model_choice == "Ollama":
                import shutil
                import subprocess
                import urllib.request
                import time

                # 1. Check if Ollama is installed
                if not shutil.which("ollama"):
                    raise RuntimeError(
                        "Ollama executable not found.\n"
                        "Please download and install it from https://ollama.com/\n\n"
                        "(Restart your terminal/PC if already installed)."
                    )

                # 2. Check if it's already running, if not start it
                ollama_running = False
                try:
                    urllib.request.urlopen("http://localhost:11434", timeout=1)
                    ollama_running = True
                except Exception:
                    pass
                
                if not ollama_running:
                    try:
                        kwargs = {}
                        # Hide the console window on Windows
                        if os.name == 'nt':
                            kwargs['creationflags'] = 0x08000000 # CREATE_NO_WINDOW
                        
                        # Start in background
                        subprocess.Popen(["ollama", "serve"], **kwargs)
                        
                        # Wait up to 8 seconds for it to start
                        for _ in range(8):
                            time.sleep(1)
                            try:
                                urllib.request.urlopen("http://localhost:11434", timeout=1)
                                ollama_running = True
                                break
                            except Exception:
                                pass
                                
                        if not ollama_running:
                            raise RuntimeError("Tried to start Ollama automatically, but it didn't respond within 8 seconds. Please run 'ollama serve' manually.")
                    except Exception as e:
                        if isinstance(e, RuntimeError):
                            raise
                        raise RuntimeError(f"Failed to start Ollama serve automatically: {e}")

                from ai_agent.ai_initial_placement.ollama_placer import ollama_generate_placement
                ollama_generate_placement(tmp_in_path, tmp_out_path)
            else:
                try:
                    from ai_agent.ai_initial_placement.gemini_placer import gemini_generate_placement
                    gemini_generate_placement(tmp_in_path, tmp_out_path)
                except Exception as e:
                    err_str = str(e)
                    if "API key not valid" in err_str or "400" in err_str or "API_KEY_INVALID" in err_str or "403" in err_str:
                        raise RuntimeError(
                            "Invalid Gemini API Key.\n\n"
                            "The API key provided is rejected by Google.\n\n"
                            "Please enter a fresh, valid Gemini secret key in the dialog."
                        )
                    raise

            with open(tmp_out_path) as f:
                raw_placed = json.load(f)

            # Normalise: LLM might save a bare JSON array — wrap it
            from ai_agent.ai_initial_placement.gemini_placer import _ensure_placement_dict
            placed = _ensure_placement_dict(raw_placed)

            # Merge placed coordinates back into original data
            placed_nodes_list = placed.get("nodes", [])
            if isinstance(placed_nodes_list, list):
                placed_map = {
                    n["id"]: n
                    for n in placed_nodes_list
                    if isinstance(n, dict) and "id" in n      # guard against nested lists
                }
                for node in data["nodes"]:
                    if isinstance(node, dict) and node.get("id") in placed_map:
                        placed_node = placed_map[node["id"]]
                        if "geometry" in placed_node:
                            node["geometry"].update(placed_node["geometry"])
        finally:
            # Clean up temp files
            for p in (tmp_in_path, tmp_out_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass

        return data

    def _load_from_data_dict(self, data, file_path):
        """
        Load a placement data dict (with nodes, edges, terminal_nets)
        directly into the GUI without reading from a file.
        """
        self._push_undo()
        self._original_data = data
        self.nodes = data["nodes"]
        self._terminal_nets = data.get("terminal_nets", {})
        self._current_file = file_path
        self._refresh_panels()
        self.setWindowTitle(
            f"Symbolic Layout Editor \u2014 {os.path.basename(file_path)}"
        )
        QTimer.singleShot(100, self.editor.fit_to_view)

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
        self.setWindowTitle(f"Symbolic Layout Editor \u2014 {os.path.basename(file_path)}")

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
    # Pipeline stage canvas highlights
    # -------------------------------------------------
    def _on_pipeline_stage_completed(self, stage_index, stage_name):
        """Briefly highlight devices relevant to the completed pipeline stage.

        Stage 0 – Topology Analyst:  amber
        Stage 1 – Placement Specialist: blue
        Stage 2 – DRC Critic:         red (overlapping devices only)
        Stage 3 – Routing Pre-Viewer: purple

        NOTE: we capture only device *IDs*, not Qt item pointers, so the
        restore callback is safe even after swap commands rebuild the items.
        """
        from PySide6.QtCore import QTimer as _QTimer

        device_items = (
            getattr(self, 'editor', None)
            and getattr(self.editor, 'device_items', {})
        ) or {}
        if not device_items:
            return

        # ---- Choose which device IDs to highlight ----
        if stage_index == 2:
            # DRC stage: highlight overlapping pairs only
            from ai_agent.ai_initial_placement.drc_critic import run_drc_check
            nodes = []
            for dev_id, item in device_items.items():
                try:
                    pos = item.scenePos()
                    br  = item.boundingRect()
                    nodes.append({
                        "id": dev_id,
                        "geometry": {
                            "x": pos.x(), "y": pos.y(),
                            "width": br.width(), "height": br.height(),
                        },
                    })
                except RuntimeError:
                    pass  # item already deleted; skip
            drc = run_drc_check(nodes)
            if not drc["pass"] and drc.get("structured"):
                overlap_ids = set()
                for v in drc["structured"]:
                    overlap_ids.add(v.dev_a)
                    overlap_ids.add(v.dev_b)
                highlight_ids = overlap_ids
            else:
                highlight_ids = set(device_items.keys())
        else:
            highlight_ids = set(device_items.keys())

        # ---- Dim selected items (capture IDs, not object refs) ----
        dimmed_ids = set()
        for dev_id in highlight_ids:
            item = device_items.get(dev_id)
            if item is None:
                continue
            try:
                item.setOpacity(0.55)
                dimmed_ids.add(dev_id)
            except RuntimeError:
                pass

        print(f"[STAGE HL] Stage {stage_index} ({stage_name}): "
              f"{len(dimmed_ids)} device(s) highlighted")

        # ---- Auto-restore after 3 s --- safe: re-lookup items by ID ----
        if self._stage_highlight_timer and self._stage_highlight_timer.isActive():
            try:
                self._stage_highlight_timer.stop()
            except RuntimeError:
                pass

        def _restore():
            """Restore opacity by re-looking up live items from editor."""
            live_items = (
                getattr(self, 'editor', None)
                and getattr(self.editor, 'device_items', {})
            ) or {}
            for did in dimmed_ids:
                itm = live_items.get(did)
                if itm is None:
                    continue
                try:
                    itm.setOpacity(1.0)
                except RuntimeError:
                    pass  # item deleted between highlight and restore
            self._stage_highlight_timer = None

        self._stage_highlight_timer = _QTimer(self)
        self._stage_highlight_timer.setSingleShot(True)
        self._stage_highlight_timer.timeout.connect(_restore)
        self._stage_highlight_timer.start(3000)

    def _clear_stage_highlights(self):
        """Restore all devices to full opacity immediately."""
        device_items = getattr(self, 'editor', None) and self.editor.device_items or {}
        for item in device_items.values():
            item.setOpacity(1.0)
        if self._stage_highlight_timer:
            self._stage_highlight_timer.stop()
            self._stage_highlight_timer = None


    def _enqueue_ai_command(self, cmd):
        """Collect commands emitted this Qt event-loop turn, then flush atomically.

        The Orchestrator may emit many command_requested signals in rapid
        succession (one per [CMD] block). By queuing them and flushing with a
        zero-delay timer we ensure a single undo snapshot covers all of them.
        """
        self._pending_cmds.append(cmd)
        if self._batch_flush_timer is None:
            from PySide6.QtCore import QTimer as _QTimer
            self._batch_flush_timer = _QTimer(self)
            self._batch_flush_timer.setSingleShot(True)
            self._batch_flush_timer.timeout.connect(self._flush_ai_command_batch)
        # Re-start with 0 ms so it fires after current event processing finishes
        self._batch_flush_timer.start(0)

    def _flush_ai_command_batch(self):
        """Execute all pending AI commands as one atomic undo group."""
        cmds = list(self._pending_cmds)
        self._pending_cmds.clear()
        self._batch_flush_timer = None
        if not cmds:
            return
        print(f"[AI BATCH] Executing {len(cmds)} command(s) as one undo group")
        # Push a SINGLE undo snapshot covering all commands
        self._sync_node_positions()
        self._push_undo()
        # Execute each command without individual undo pushes
        for cmd in cmds:
            self._handle_ai_command(cmd, _skip_undo=True)
        # One refresh after all commands
        self._refresh_panels(compact=False)
        self._sync_node_positions()


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

    def _handle_ai_command(self, cmd, _skip_undo=False):
        """Execute a command dict from the AI on the canvas.

        Args:
            cmd: dict with 'action' and action-specific keys.
            _skip_undo: if True, do NOT push an undo snapshot (caller
                already pushed one for the whole batch).
        """
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
                if not _skip_undo:
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
                if not _skip_undo:
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
                if not _skip_undo:
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

            elif action == "net_priority":
                net = cmd.get("net", "")
                priority = cmd.get("priority", "medium")
                if not hasattr(self, "_routing_annotations"):
                    self._routing_annotations = {}
                self._routing_annotations.setdefault(net, {})["priority"] = priority
                print(f"[AI CMD] net_priority: net={net} priority={priority}")
                self.chat_panel._append_message(
                    "AI",
                    f"📡 Net **{net}** marked as **{priority}** priority for routing.",
                    "#e8f4fd", "#1a1a2e",
                )
                # Highlight net on canvas
                if hasattr(self, "editor") and hasattr(self.editor, "highlight_net_by_name"):
                    color = "#e74c3c" if priority == "high" else "#3498db"
                    self.editor.highlight_net_by_name(net, color)

            elif action == "wire_width":
                net = cmd.get("net", "")
                width_um = cmd.get("width_um", 0.3)
                if not hasattr(self, "_routing_annotations"):
                    self._routing_annotations = {}
                self._routing_annotations.setdefault(net, {})["wire_width_um"] = float(width_um)
                print(f"[AI CMD] wire_width: net={net} width={width_um}µm")
                self.chat_panel._append_message(
                    "AI",
                    f"🔌 Wire width for **{net}** set to **{width_um} µm**.",
                    "#e8f4fd", "#1a1a2e",
                )

            elif action == "wire_spacing":
                net_a = cmd.get("net_a", "")
                net_b = cmd.get("net_b", "")
                spacing_um = cmd.get("spacing_um", 0.2)
                if not hasattr(self, "_routing_annotations"):
                    self._routing_annotations = {}
                key = f"{net_a}|{net_b}"
                self._routing_annotations.setdefault(key, {})["spacing_um"] = float(spacing_um)
                print(f"[AI CMD] wire_spacing: {net_a}<>{net_b} spacing={spacing_um}µm")
                self.chat_panel._append_message(
                    "AI",
                    f"📏 Minimum spacing between **{net_a}** and **{net_b}** set to **{spacing_um} µm**.",
                    "#e8f4fd", "#1a1a2e",
                )

            elif action == "net_reroute":
                net = cmd.get("net", "")
                reason = cmd.get("reason", "")
                if not hasattr(self, "_routing_annotations"):
                    self._routing_annotations = {}
                self._routing_annotations.setdefault(net, {})["reroute"] = reason
                print(f"[AI CMD] net_reroute: net={net} reason={reason!r}")
                self.chat_panel._append_message(
                    "AI",
                    f"🔀 Net **{net}** flagged for reroute: _{reason}_",
                    "#e8f4fd", "#1a1a2e",
                )
                # Highlight as needing attention
                if hasattr(self, "editor") and hasattr(self.editor, "highlight_net_by_name"):
                    self.editor.highlight_net_by_name(net, "#f39c12")

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
    if len(sys.argv) > 1:
        placement_path = sys.argv[1]
        # Resolve relative to current working directory if not absolute
        if not os.path.isabs(placement_path):
            placement_path = os.path.abspath(placement_path)
    else:
        placement_path = None

    window = MainWindow(placement_path)
    window.show()

    sys.exit(app.exec())
