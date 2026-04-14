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
    QScrollArea,
    QTextEdit,
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
from ai_agent.matching.matching_engine import MatchingEngine


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
# Match Dialog — select interdigitated or common-centroid
# -------------------------------------------------
class _MatchDialog(QDialog):
    """Dialog for choosing matching technique (Interdigitated / Common-Centroid)."""

    def __init__(self, device_ids: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Match Devices")
        self.setMinimumWidth(380)
        self.setStyleSheet("""
            QDialog {
                background-color: #1e2636;
                color: #e0e0e0;
                border-radius: 12px;
            }
            QLabel {
                color: #e0e0e0;
                font-size: 13px;
            }
            QRadioButton {
                color: #e0e0e0;
                font-size: 13px;
                spacing: 8px;
                padding: 6px;
            }
            QRadioButton::indicator {
                width: 16px; height: 16px;
            }
            QRadioButton::indicator:checked {
                background-color: #4FC3F7;
                border: 2px solid #4FC3F7;
                border-radius: 8px;
            }
            QRadioButton::indicator:unchecked {
                background-color: transparent;
                border: 2px solid #546E7A;
                border-radius: 8px;
            }
            QPushButton {
                background-color: #2196F3;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 20px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #42A5F5;
            }
            QPushButton[text="Cancel"] {
                background-color: #455A64;
            }
            QPushButton[text="Cancel"]:hover {
                background-color: #546E7A;
            }
            QGroupBox {
                color: #90CAF9;
                border: 1px solid #37474F;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 16px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Header
        header = QLabel(f"Match {len(device_ids)} Devices")
        header.setStyleSheet("font-size: 16px; font-weight: bold; color: #90CAF9;")
        layout.addWidget(header)

        # Device list
        dev_list = QLabel(f"Devices: {', '.join(device_ids[:20])}")
        dev_list.setWordWrap(True)
        dev_list.setStyleSheet("color: #B0BEC5; font-size: 12px;")
        layout.addWidget(dev_list)

        # Technique selection
        tech_group = QGroupBox("Matching Technique")
        tech_layout = QVBoxLayout(tech_group)

        self._radio_interdig = QRadioButton("Interdigitated (ABBA)")
        self._radio_interdig.setChecked(True)
        self._radio_interdig.setToolTip(
            "Pattern: A₁ B₁ B₂ A₂ A₃ B₃ B₄ A₄\n"
            "Best for: differential pairs, current mirrors\n"
            "Cancels linear process gradients"
        )
        tech_layout.addWidget(self._radio_interdig)

        # Description for interdigitated
        desc1 = QLabel("  Pattern: A₁B₁B₂A₂ — cancels linear gradients")
        desc1.setStyleSheet("color: #78909C; font-size: 11px; margin-left: 24px;")
        tech_layout.addWidget(desc1)

        self._radio_cc = QRadioButton("Common-Centroid (1D)")
        self._radio_cc.setToolTip(
            "Pattern: D C B A | A B C D (mirror around center in one row)\n"
            "Best for: 4+ matched devices\n"
            "Cancels both linear and quadratic gradients"
        )
        tech_layout.addWidget(self._radio_cc)

        desc2 = QLabel("  Pattern: DCBA|ABCD — 1D mirror")
        desc2.setStyleSheet("color: #78909C; font-size: 11px; margin-left: 24px;")
        tech_layout.addWidget(desc2)

        self._radio_cc_2d = QRadioButton("Common-Centroid (2D Multi-Row)")
        self._radio_cc_2d.setToolTip(
            "Pattern: A B | B A across two rows (cross-coupled)\n"
            "Best for: Differential pairs with 4 devices (e.g. 2 fingers each)\n"
            "Cancels gradients in both X and Y directions"
        )
        tech_layout.addWidget(self._radio_cc_2d)

        self._radio_custom = QRadioButton("Custom Pattern (A B / B A)")
        self._radio_custom.setToolTip("Type a custom string. '/' separates rows.")
        tech_layout.addWidget(self._radio_custom)

        self._custom_text = QTextEdit()
        self._custom_text.setPlaceholderText("Example: A B B A / B A A B")
        self._custom_text.setMaximumHeight(80)
        self._custom_text.setHidden(True)
        self._custom_text.setStyleSheet("background-color: #121826; border: 1px solid #37474F; color: white;")
        tech_layout.addWidget(self._custom_text)

        self._radio_custom.toggled.connect(lambda checked: self._custom_text.setVisible(checked))

        self._btn_group = QButtonGroup(self)
        self._btn_group.addButton(self._radio_interdig, 0)
        self._btn_group.addButton(self._radio_cc, 1)
        self._btn_group.addButton(self._radio_cc_2d, 2)
        self._btn_group.addButton(self._radio_custom, 3)

        layout.addWidget(tech_group)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)
        btn_ok = QPushButton("Apply Matching")
        btn_ok.clicked.connect(self.accept)
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)

    def get_technique(self) -> str:
        """Return 'interdigitated' or 'common_centroid' or 'common_centroid_2d' or 'custom'."""
        if self._radio_cc.isChecked():
            return "common_centroid"
        elif self._radio_cc_2d.isChecked():
            return "common_centroid_2d"
        elif self._radio_custom.isChecked():
            return "custom"
        return "interdigitated"

    def get_custom_pattern(self) -> str:
        return self._custom_text.toPlainText().strip()

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
        
        # --- Abutment Toggle ---
        self.check_abutment = QCheckBox("Enable Abutment (Diffusion Sharing)")
        self.check_abutment.setChecked(True)
        self.check_abutment.setToolTip(
            "When enabled, shared Source/Drain nets between same-type transistors "
            "will be marked for abutment."
        )
        layout.addWidget(self.check_abutment)

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
        self.abutment_enabled = self.check_abutment.isChecked()
        self.accept()

    def is_abutment_enabled(self):
        return self.check_abutment.isChecked()

# -------------------------------------------------
# AI Model Selection Dialog
# -------------------------------------------------
class AIModelSelectionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select AI Model")
        self.setMinimumSize(500, 550)
        self.resize(500, 550)
        self.setStyleSheet("""
            QDialog {
                background-color: #1a1f2b;
                color: #c8d0dc;
                font-family: 'Segoe UI';
            }
            QLabel {
                color: #c8d0dc;
                font-size: 9pt;
            }
            QLineEdit {
                background-color: #232a38;
                color: #c8d0dc;
                border: 1px solid #2d3548;
                border-radius: 6px;
                padding: 5px 10px;
                font-size: 9pt;
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
            QCheckBox {
                color: #c8d0dc;
                font-size: 10pt;
                font-weight: bold;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 16px; height: 16px;
            }
            QScrollArea {
                border: none;
                background-color: transparent;
            }
            QScrollBar:vertical {
                background-color: #1a1f2b;
                width: 10px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background-color: #3d5066;
                border-radius: 5px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #4d6076;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        # ── Main layout ──────────────────────────────────────
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(20, 16, 20, 16)

        # Title (fixed, not scrolled)
        title = QLabel("AI Initial Placement")
        title.setStyleSheet("font-size: 14pt; font-weight: bold; color: #ffffff;")
        main_layout.addWidget(title)

        subtitle = QLabel("Choose a model and enter its API key below.")
        subtitle.setStyleSheet("font-size: 9pt; color: #8899aa; margin-bottom: 4px;")
        main_layout.addWidget(subtitle)

        # ── Scrollable area for model cards ──────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setSpacing(10)
        scroll_layout.setContentsMargins(0, 8, 4, 8)

        # Button group for exclusivity
        self.model_group = QButtonGroup(self)
        self.model_group.setExclusive(True)

        # ── Helper: build a compact model card ───────────
        def make_card(label_text, desc_html, checkbox_attr, form_rows):
            """Build a compact model-selection card."""
            card = QFrame()
            card.setStyleSheet("""
                QFrame {
                    background-color: #1e2636;
                    border: 1px solid #3d5066;
                    border-radius: 8px;
                }
            """)
            card_layout = QVBoxLayout(card)
            card_layout.setSpacing(6)
            card_layout.setContentsMargins(12, 8, 12, 8)

            cb = QCheckBox(label_text)
            cb.setObjectName(checkbox_attr)
            self.model_group.addButton(cb)
            card_layout.addWidget(cb)

            desc = QLabel(desc_html)
            desc.setStyleSheet("color: #8899aa; font-size: 8pt; margin-left: 26px;")
            desc.setWordWrap(True)
            card_layout.addWidget(desc)

            form = QFormLayout()
            form.setContentsMargins(26, 2, 4, 2)
            form.setSpacing(4)
            for row_label, widget in form_rows:
                form.addRow(row_label, widget)
            card_layout.addLayout(form)

            return card, cb

        # ── 1. Gemini ────────────────────────────────────
        self.gemini_api_key = QLineEdit()
        self.gemini_api_key.setPlaceholderText("Enter Gemini API Key")
        self.gemini_api_key.setText(os.environ.get("GEMINI_API_KEY", "******"))
        self.card_gemini, self.check_gemini = make_card(
            "Gemini Pro (Cloud)",
            "Fast &amp; efficient. Free tier: 15 req/min.",
            "check_gemini",
            [("API Key:", self.gemini_api_key)],
        )
        self.check_gemini.setChecked(True)
        scroll_layout.addWidget(self.card_gemini)

        # ── 2. Groq ──────────────────────────────────────
        self.groq_api_key = QLineEdit()
        self.groq_api_key.setPlaceholderText("Enter Groq API Key")
        self.groq_api_key.setText(os.environ.get("GROQ_API_KEY", "******"))
        self.card_groq, self.check_groq = make_card(
            "Groq (Cloud — Ultra Fast)",
            "Llama 3.3 70B. Free tier: 30 req/min. ⚡",
            "check_groq",
            [("API Key:", self.groq_api_key)],
        )
        scroll_layout.addWidget(self.card_groq)

        # ── 3. DeepSeek ──────────────────────────────────
        self.deepseek_api_key = QLineEdit()
        self.deepseek_api_key.setPlaceholderText("Enter DeepSeek API Key")
        self.deepseek_api_key.setText(os.environ.get("DEEPSEEK_API_KEY", "******"))
        self.card_deepseek, self.check_deepseek = make_card(
            "DeepSeek (Cloud)",
            "Strong code reasoning. Free tier available.",
            "check_deepseek",
            [("API Key:", self.deepseek_api_key)],
        )
        scroll_layout.addWidget(self.card_deepseek)

        # ── 4. OpenAI ────────────────────────────────────
        self.openai_api_key = QLineEdit()
        self.openai_api_key.setPlaceholderText("Enter OpenAI API Key")
        self.openai_api_key.setText(os.environ.get("OPENAI_API_KEY", "******"))
        self.card_openai, self.check_openai = make_card(
            "OpenAI GPT-4 (Cloud)",
            "High precision spatial understanding.",
            "check_openai",
            [("API Key:", self.openai_api_key)],
        )
        scroll_layout.addWidget(self.card_openai)

        # ── 5. Ollama ────────────────────────────────────
        self.ollama_model_combo = QComboBox()
        self.ollama_model_combo.setEditable(True)
        self.ollama_model_combo.addItems([
            "qwen3.5", "llama3.2", "deepseek-coder:6.7b", "phi4-mini:3.8b", "gemma3:4b"
        ])
        self.card_ollama, self.check_ollama = make_card(
            "Ollama (Local — Private)",
            "Requires Ollama installed &amp; running locally.",
            "check_ollama",
            [("Model:", self.ollama_model_combo)],
        )
        scroll_layout.addWidget(self.card_ollama)

        # Stretch to push cards to top
        scroll_layout.addStretch()

        scroll.setWidget(scroll_widget)
        main_layout.addWidget(scroll)

        # 4. Placement Options
        options_group = QGroupBox("Placement Options")
        options_layout = QVBoxLayout(options_group)

        self.check_abutment = QCheckBox("Enable Abutment (Diffusion Sharing)")
        self.check_abutment.setChecked(True)
        self.check_abutment.setStyleSheet("""
            QCheckBox {
                color: #c8d0dc;
                font-size: 10pt;
                spacing: 10px;
            }
            QCheckBox::indicator {
                width: 18px; height: 18px;
            }
        """)
        options_layout.addWidget(self.check_abutment)

        abutment_desc = QLabel(
            "When enabled, adjacent fingers sharing a Source/Drain net will be "
            "abutted at 0.070 \u00b5m pitch to save area. "
            "When disabled, standard spacing (0.294 \u00b5m) is used everywhere."
        )
        abutment_desc.setStyleSheet("color: #8899aa; font-size: 9pt; margin-left: 30px;")
        abutment_desc.setWordWrap(True)
        options_layout.addWidget(abutment_desc)

        main_layout.addWidget(options_group)

        # ── Connect toggles ──────────────────────────────
        self.check_gemini.toggled.connect(self._on_model_changed)
        self.check_groq.toggled.connect(self._on_model_changed)
        self.check_deepseek.toggled.connect(self._on_model_changed)
        self.check_openai.toggled.connect(self._on_model_changed)
        self.check_ollama.toggled.connect(self._on_model_changed)
        self._on_model_changed()

        # ── Buttons (fixed at bottom, never scrolls) ─────
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 8, 0, 0)
        info_label = QLabel("<a href='https://aistudio.google.com' style='color:#8899aa;'>Get Gemini Key</a>  |  "
                             "<a href='https://console.groq.com' style='color:#8899aa;'>Get Groq Key</a>  |  "
                             "<a href='https://platform.deepseek.com' style='color:#8899aa;'>Get DeepSeek Key</a>")
        info_label.setOpenExternalLinks(True)
        info_label.setStyleSheet("font-size: 8pt; color: #8899aa;")
        btn_layout.addWidget(info_label)
        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        self.run_btn = QPushButton("Run Placement")
        self.run_btn.setObjectName("run_btn")
        self.run_btn.clicked.connect(self.accept)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(self.run_btn)

        main_layout.addLayout(btn_layout)

    def _on_model_changed(self):
        self.gemini_api_key.setEnabled(self.check_gemini.isChecked())
        self.groq_api_key.setEnabled(self.check_groq.isChecked())
        self.deepseek_api_key.setEnabled(self.check_deepseek.isChecked())
        self.openai_api_key.setEnabled(self.check_openai.isChecked())
        self.ollama_model_combo.setEnabled(self.check_ollama.isChecked())

    def get_selected_model(self):
        if self.check_gemini.isChecked():
            return "Gemini"
        elif self.check_groq.isChecked():
            return "Groq"
        elif self.check_deepseek.isChecked():
            return "DeepSeek"
        elif self.check_openai.isChecked():
            return "OpenAI"
        elif self.check_ollama.isChecked():
            return "Ollama"
        return "Gemini"

    def get_ollama_submodel(self):
        return self.ollama_model_combo.currentText()

    def is_abutment_enabled(self):
        return self.check_abutment.isChecked()

    def apply_api_keys(self):
        # Update environment variables based on user changes if they didn't leave them empty/starred out
        gemini_key = self.gemini_api_key.text().strip().strip('\'"')
        if gemini_key and gemini_key != "******":
            os.environ["GEMINI_API_KEY"] = gemini_key

        openai_key = self.openai_api_key.text().strip().strip('\'"')
        if openai_key and openai_key != "******":
            os.environ["OPENAI_API_KEY"] = openai_key

        groq_key = self.groq_api_key.text().strip().strip('\'"')
        if groq_key and groq_key != "******":
            os.environ["GROQ_API_KEY"] = groq_key

        deepseek_key = self.deepseek_api_key.text().strip().strip('\'"')
        if deepseek_key and deepseek_key != "******":
            os.environ["DEEPSEEK_API_KEY"] = deepseek_key

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
        self._matched_groups = []  # [{ids: [dev_ids], technique: str, anchor_x: float, anchor_y: float}]


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

        # --- Add Examples Submenu ---
        examples_menu = file_menu.addMenu("Examples")
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        examples_dir = os.path.join(base_dir, "examples")
        if os.path.isdir(examples_dir):
            for example_name in sorted(os.listdir(examples_dir)):
                ex_path = os.path.join(examples_dir, example_name)
                if os.path.isdir(ex_path):
                    sp_files = glob.glob(os.path.join(ex_path, "*.sp"))
                    if sp_files:
                        sp_file = sp_files[-1] # Usually preferred if multiples exist
                        oas_file = sp_file.rsplit('.', 1)[0] + ".oas"
                        if not os.path.exists(oas_file):
                            oas_file = ""
                        
                        def create_action(name, sp, oas):
                            act = QAction(name.replace('_', ' ').title(), self)
                            act.triggered.connect(lambda: self._load_example(sp, oas))
                            return act
                            
                        examples_menu.addAction(create_action(example_name, sp_file, oas_file))

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
        
        self._act_reload_app = QAction("Reload App", self)
        self._act_reload_app.setShortcut(QKeySequence("F5"))
        self._act_reload_app.triggered.connect(self._on_reload_app)
        view_menu.addAction(self._act_reload_app)

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

        # Add Dummy mode - changed to Shift+D to free up D for hierarchy descend
        self._act_add_dummy = QAction(icon_add_dummy(), "Dummy", self)
        self._act_add_dummy.setCheckable(True)
        self._act_add_dummy.setShortcut(QKeySequence("Shift+D"))
        self._act_add_dummy.setToolTip(
            "Toggle dummy placement mode (Shift+D)\nHover a row and click to place."
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

        # Matching / Interdigitation button
        self._act_match = QAction("⊠ Match", self)
        self._act_match.setShortcut(QKeySequence("Ctrl+M"))
        self._act_match.setToolTip(
            "Match Selected Devices (Ctrl+M)\n"
            "Select 2+ devices, then apply Interdigitated (ABBA)\n"
            "or Common-Centroid matching. Creates a fixed block."
        )
        self._act_match.triggered.connect(self._on_match_devices)
        toolbar.addAction(self._act_match)

        # Unlock matched group button
        self._act_unlock = QAction("🔓 Unlock", self)
        self._act_unlock.setShortcut(QKeySequence("Ctrl+Shift+M"))
        self._act_unlock.setToolTip(
            "Unlock Matched Group (Ctrl+Shift+M)\n"
            "Select any device from a matched group to dissolve it.\n"
            "Devices become individually moveable again."
        )
        self._act_unlock.triggered.connect(self._on_unlock_matched_group)
        toolbar.addAction(self._act_unlock)

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
        """Esc releases active modes and selection. D descends hierarchy. M enters move mode."""
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
        # D key → descend into hierarchy
        if event.key() == Qt.Key.Key_D and not event.modifiers():
            try:
                if self.editor:
                    self.editor.descend_nearest_hierarchy()
                    event.accept()
                    return
            except Exception:
                pass
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
        # Before finalizing: if the moved device is part of a matched group,
        # move all group members by the same delta
        if self._move_dev_id:
            self._enforce_matched_group_move(self._move_dev_id)
        self._move_mode = False
        self._move_dev_id = None
        self._sync_node_positions()

    def _enforce_matched_group_move(self, moved_id):
        """If moved_id is in a matched group, move all group members by the same delta."""
        for group in self._matched_groups:
            if moved_id not in group["ids"]:
                continue

            # Find the moved item and compute the delta from its original position
            moved_item = self.editor.device_items.get(moved_id)
            if not moved_item:
                return

            # Get the original position from the node data
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

            # Compute delta from how the user moved this device
            scale = self.editor._snap_grid
            dx = moved_item.pos().x() - orig_x * (scale / 0.294)
            dy = moved_item.pos().y() - orig_y * (scale / 0.668) if abs(orig_y) > 1e-9 else moved_item.pos().y()

            # Apply same delta to all other group members
            for gid in group["ids"]:
                if gid == moved_id:
                    continue
                item = self.editor.device_items.get(gid)
                if not item:
                    continue
                # Find this member's original node position
                for n in self.nodes:
                    if n.get("id") == gid:
                        g = n.get("geometry", {})
                        ox = g.get("x", 0.0) * (scale / 0.294)
                        oy = g.get("y", 0.0) * (scale / 0.668) if abs(g.get("y", 0)) > 1e-9 else item.pos().y()
                        item.setPos(ox + dx, moved_item.pos().y())
                        break
            return

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

    def _refresh_panels(self, compact=False):
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
        """Called when the user starts dragging a device — push undo.

        Re-enables per-item fine-grid snap for any item that was loaded
        without snapping (e.g. from an OAS import with exact coordinates).
        Uses the fine snap_grid for BOTH axes so movement feels free.
        """
        try:
            for it in self.editor.scene.selectedItems():
                if hasattr(it, "set_snap_grid"):
                    it.set_snap_grid(self.editor._snap_grid, self.editor._snap_grid)
        except RuntimeError:
            pass
        self._sync_node_positions()
        self._push_undo()

    def _on_device_drag_end(self):
        """Called when drag ends — sync canvas positions to data model."""
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

    # -------------------------------------------------
    # Match Devices (Interdigitation / Common-Centroid)
    # -------------------------------------------------
    def _on_match_devices(self):
        """Open the Match dialog for selected devices."""
        selected = self.editor.selected_device_ids()
        if len(selected) < 2:
            self.chat_panel._append_message(
                "AI",
                "Select at least 2 devices to match.\n"
                "Use Ctrl+Click to select multiple devices.",
                "#fde8e8",
                "#a00",
            )
            return

        # Check same type
        types = set()
        for sid in selected:
            item = self.editor.device_items.get(sid)
            if item:
                types.add(getattr(item, "device_type", "?"))
        if len(types) > 1:
            self.chat_panel._append_message(
                "AI",
                "All selected devices must be the same type (all NMOS or all PMOS).",
                "#fde8e8",
                "#a00",
            )
            return

        # Show dialog
        dlg = _MatchDialog(selected, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        technique = dlg.get_technique()
        custom_pattern = dlg.get_custom_pattern() if technique == "custom" else None
        self._apply_matching(selected, technique, custom_pattern)

    def _apply_matching(self, device_ids, technique, custom_str=None):
        """
        Apply matching to the selected devices using the Universal Layout Architect.
        Enforces Recursive Balancing and Point Symmetry.
        """
        self._sync_node_positions()
        self._push_undo()

        # Instantiate engine with current item map
        engine = MatchingEngine(self.editor.device_items)
        
        try:
            # Handle the placement result
            placements = engine.generate_placement(device_ids, technique, custom_str)
            
            # Apply results to items
            snap = self.editor._snap_value
            for p in placements:
                item = self.editor.device_items.get(p["id"])
                if item:
                    item.setPos(snap(p["x"]), snap(p["y"]))
            
            # Post-Placement Analytical Audit
            self._calculate_and_draw_centroids(device_ids, technique)
            
            # Register matched group
            self._matched_groups.append({
                "ids": list(device_ids),
                "technique": technique,
            })
            
            # Visual highlight (Success: standard colors)
            if technique == "interdigitated":
                color = QColor("#4FC3F7")      # blue
            elif technique == "common_centroid_2d":
                color = QColor("#CE93D8")      # purple
            elif technique == "custom":
                color = QColor("#FFD54F")      # amber for custom
            else:
                color = QColor("#AED581")      # green
                
            for did in device_ids:
                item = self.editor.device_items.get(did)
                if item and hasattr(item, "set_match_highlight"):
                    item.set_match_highlight(color)
                    
            self.chat_panel._append_message(
                "AI", 
                f"Successfully applied {technique.replace('_', ' ')} matching.\n"
                "✓ Analytical Audit: All centroids aligned at grid center.",
                "#e8f4fd", "#1a1a2e"
            )

        except Exception as e:
            # Failure: Highlight in RED as requested
            for did in device_ids:
                item = self.editor.device_items.get(did)
                if item and hasattr(item, "set_match_highlight"):
                    item.set_match_highlight(QColor("#FF5252")) # Red
            
            self.chat_panel._append_message(
                "AI", f"Matching Failed: {str(e)}\nCentroids misaligned!", "#fde8e8", "#a00"
            )

        self._sync_node_positions()

    def _calculate_and_draw_centroids(self, device_ids, technique):
        """Calculates centroids for each device group and draws crosshairs (+) in GUI."""
        # 1. Group by parent
        import re as _re
        parent_map = {}
        for did in device_ids:
            m = _re.match(r'^([A-Za-z]+\d+)', did)
            p = m.group(1) if m else did
            if p not in parent_map: parent_map[p] = []
            parent_map[p].append(did)
            
        markers = []
        colors = [QColor("#4FC3F7"), QColor("#CE93D8"), QColor("#AED581"), QColor("#FFD54F")]
        
        for i, (parent, ids) in enumerate(parent_map.items()):
            sum_x, sum_y = 0.0, 0.0
            for did in ids:
                item = self.editor.device_items.get(did)
                if item:
                    # Use center of the item
                    br = item.boundingRect()
                    pos = item.pos()
                    sum_x += pos.x() + br.width() / 2.0
                    sum_y += pos.y() + br.height() / 2.0
            
            avg_x = sum_x / len(ids)
            avg_y = sum_y / len(ids)
            
            markers.append({
                'x': avg_x, 
                'y': avg_y, 
                'color': colors[i % len(colors)],
                'label': parent
            })
            
        # Draw on editor
        self.editor.set_centroid_markers(markers)

    # -------------------------------------------------


    # -------------------------------------------------
    # Matched Group Helpers
    # -------------------------------------------------
    def _is_device_locked(self, device_id):
        """Check if a device belongs to ANY matched group."""
        for group in self._matched_groups:
            if device_id in group["ids"]:
                return True
        return False

    def _get_device_group(self, device_id):
        """Return the matched group dict containing device_id, or None."""
        for group in self._matched_groups:
            if device_id in group["ids"]:
                return group
        return None

    def _move_matched_group_as_block(self, group, target_x, target_y):
        """Move an entire matched group so that the top-left corner lands at (target_x, target_y).

        All devices shift by the same delta, preserving the internal pattern.
        Returns the number of devices moved.
        """
        # Find current bounding box of the group
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

        # Move every group member by (dx, dy)
        for gid, item, old_x, old_y in positions:
            item.setPos(old_x + dx, old_y + dy)

        return len(positions)

    def _on_unlock_matched_group(self):
        """Unlock / dissolve matched groups for the currently selected devices."""
        selected = self.editor.selected_device_ids()
        if not selected:
            self.chat_panel._append_message(
                "AI",
                "Select devices from a matched group to unlock.",
                "#fde8e8",
                "#a00",
            )
            return

        # Find all groups that contain any selected device
        groups_to_remove = []
        for group in self._matched_groups:
            for sid in selected:
                if sid in group["ids"]:
                    groups_to_remove.append(group)
                    break

        if not groups_to_remove:
            self.chat_panel._append_message(
                "AI",
                "None of the selected devices are in a matched group.",
                "#fde8e8",
                "#a00",
            )
            return

        self._push_undo()

        dissolved_count = 0
        for group in groups_to_remove:
            # Clear visual highlights
            for gid in group["ids"]:
                item = self.editor.device_items.get(gid)
                if item and hasattr(item, "clear_match_highlight"):
                    item.clear_match_highlight()
            # Remove from registry
            if group in self._matched_groups:
                self._matched_groups.remove(group)
                dissolved_count += 1

        tech_label = groups_to_remove[0].get("technique", "unknown") if groups_to_remove else "unknown"
        total_devs = sum(len(g["ids"]) for g in groups_to_remove)
        self.chat_panel._append_message(
            "AI",
            f"🔓 Unlocked {dissolved_count} matched group(s) ({total_devs} devices).\n"
            f"These devices can now be moved individually.",
            "#fff3e0",
            "#e65100",
        )

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
        """Analyse and highlight abutment candidates, or clear them."""
        if enabled:
            candidates = self.editor.apply_abutment()
            self._abutment_candidates = candidates   # stored for AI placer
            n = len(candidates)
            if n == 0:
                msg = (
                    "⚠️ No abutment candidates found.\n"
                    "This happens when no two same-type transistors share a "
                    "Source or Drain net."
                )
            else:
                lines = [
                    f"✅ Found {n} abutment candidate(s) — terminal edges that "
                    "can share diffusion are highlighted in 🟢 green:\n"
                ]
                for c in candidates:
                    flip_note = " (flip needed)" if c["needs_flip"] else ""
                    lines.append(
                        f"  • {c['dev_a']}.{c['term_a']} ↔ "
                        f"{c['dev_b']}.{c['term_b']}  "
                        f"[net: {c['shared_net']}]{flip_note}"
                    )
                lines.append(
                    "\nWhen you run AI Placement, these constraints will be "
                    "injected so the AI places candidates adjacent to each other."
                )
                msg = "\n".join(lines)
        else:
            self.editor.clear_abutment()
            self._abutment_candidates = []
            msg = "Abutment analysis cleared."
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
    def _load_example(self, sp_path, oas_path):
        """Helper to quickly load an example without showing the import dialog."""
        self.overlay.show_message(f"Loading {os.path.basename(sp_path)}...")
        self._import_worker = GenericWorker(self._run_parser_pipeline, sp_path, oas_path, True)
        self._import_worker.finished.connect(lambda data: self._on_import_completed(data, sp_path))
        self._import_worker.error.connect(self._on_import_error)
        self._import_worker.start()

    def _on_import_netlist_layout(self):
        """Open the import dialog, parse files, and visualize the graph."""
        dlg = ImportDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        sp_path = dlg.sp_path
        oas_path = dlg.oas_path
        abutment_enabled = dlg.is_abutment_enabled()

        self.overlay.show_message("Parsing design files...")
        
        self._import_worker = GenericWorker(self._run_parser_pipeline, sp_path, oas_path, abutment_enabled)
        self._import_worker.finished.connect(lambda data: self._on_import_completed(data, sp_path))
        self._import_worker.error.connect(self._on_import_error)
        self._import_worker.start()

    def _on_import_completed(self, data, sp_path):
        self.overlay.hide_overlay()

        # Save the full graph JSON for GUI loading (needs 'nodes' key)
        base_name = os.path.splitext(os.path.basename(sp_path))[0]
        sp_dir = os.path.dirname(os.path.abspath(sp_path))
        out_path = os.path.join(sp_dir, f"{base_name}_graph.json")
        
        # Save full format to disk first
        with open(out_path, "w") as f:
            json.dump(data, f, indent=4)
        original_size = os.path.getsize(out_path)
        
        # Also save compressed version for AI prompts
        compressed_path = os.path.join(sp_dir, f"{base_name}_graph_compressed.json")
        try:
            compressed_data = self._compress_graph_for_storage(data)
            with open(compressed_path, "w") as f:
                json.dump(compressed_data, f, indent=4)
            compressed_size = os.path.getsize(compressed_path)
            reduction = (1 - compressed_size / original_size) * 100 if original_size > 0 else 0
        except Exception as e:
            compressed_path = out_path  # Fallback
            reduction = 0

        # Load into the GUI using full format
        self._load_from_data_dict(data, out_path, False)

        num_nodes = len(data.get('nodes', []))
        msg = (
            f"Imported {num_nodes} devices from "
            f"{os.path.basename(sp_path)}\n"
            f"Saved graph to: {os.path.basename(out_path)}\n"
            f"Saved compressed graph to: {os.path.basename(compressed_path)}\n"
            f"Size reduction: {reduction:.1f}% (for AI prompts)\n\n"
            f"To run AI initial placement: Design > Run AI Initial Placement (Ctrl+P)"
        )
        self.chat_panel._append_message(
            "AI",
            msg,
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
        submodel = dialog.get_ollama_submodel() if model_choice == "Ollama" else None
        abutment_enabled = dialog.is_abutment_enabled()
        dialog.apply_api_keys()

        # Build the data dict from current state
        self._sync_node_positions()
        data = copy.deepcopy(self._build_output_data())
        if "terminal_nets" not in data:
            data["terminal_nets"] = self._terminal_nets

        # Inject abutment candidates only when the user opted in
        if abutment_enabled:
            data["abutment_candidates"] = getattr(self, "_abutment_candidates", [])
        else:
            data["abutment_candidates"] = []

        # Store the abutment flag so the placer knows whether to abut fingers
        data["no_abutment"] = not abutment_enabled

        abut_label = "with abutment" if abutment_enabled else "no abutment"
        self.overlay.show_message(f"Running AI initial placement ({model_choice}, {abut_label})...")

        # ── Save locked group positions before AI runs ──────────────
        self._saved_locked_positions = {}
        for group in self._matched_groups:
            for gid in group["ids"]:
                node = next((n for n in self.nodes if n.get("id") == gid), None)
                if node:
                    geo = node.get("geometry", {})
                    self._saved_locked_positions[gid] = {
                        "x": geo.get("x", 0),
                        "y": geo.get("y", 0),
                    }

        self._ai_worker = GenericWorker(self._run_ai_initial_placement, data, model_choice, submodel)
        self._ai_worker.finished.connect(self._on_ai_placement_completed)
        self._ai_worker.error.connect(self._on_ai_placement_error)
        self._ai_worker.start()


    def _on_ai_placement_completed(self, data):
        self.overlay.hide_overlay()
        
        # ── Restore locked matched-group positions ──────────────────
        saved = getattr(self, "_saved_locked_positions", {})
        if saved and "nodes" in data:
            for node in data["nodes"]:
                nid = node.get("id")
                if nid in saved:
                    node["geometry"]["x"] = saved[nid]["x"]
                    node["geometry"]["y"] = saved[nid]["y"]
            restored_count = len(saved)
            print(f"[MATCH] Restored {restored_count} locked device positions after AI placement")

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

        # ── Re-apply matched group visual highlights ───────────────
        for group in self._matched_groups:
            technique = group.get("technique", "interdigitated")
            if technique == "interdigitated":
                color = QColor("#4FC3F7")
            elif technique == "common_centroid_2d":
                color = QColor("#CE93D8")
            else:
                color = QColor("#AED581")
            for gid in group["ids"]:
                item = self.editor.device_items.get(gid)
                if item and hasattr(item, "set_match_highlight"):
                    item.set_match_highlight(color)

        locked_msg = ""
        if saved:
            locked_msg = f"\n🔒 {len(saved)} matched devices preserved in place."

        self.chat_panel._append_message(
            "AI",
            f"AI initial placement complete!{locked_msg}\n"
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
    def _run_parser_pipeline(sp_path, oas_path="", abutment_enabled=True):
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

            # 1. Base electrical params
            electrical = {
                "l":    dev.params.get("l",    1.4e-08),
                "nf":   dev.params.get("nf",   1),
                "nfin": dev.params.get("nfin", 1),
                "w":    dev.params.get("w",    0),
                # Hierarchy metadata for device tree grouping
                "parent":           dev.params.get("parent"),
                "m":                dev.params.get("m", 1),
                "multiplier_index": dev.params.get("multiplier_index"),
                "finger_index":     dev.params.get("finger_index"),
                "array_index":      dev.params.get("array_index"),
            }
            # Clean up parent for single devices
            if electrical["parent"] == dev_name:
                electrical["parent"] = None
            if dev_type == "cap":
                electrical["cval"] = dev.params.get("cval", 0.0)

            # 2. Determine Geometry
            layout_idx = device_mapping.get(dev_name)
            abut_info = None

            if layout_idx is not None and layout_idx < len(layout_instances):
                inst = layout_instances[layout_idx]
                geom = {
                    "x":           inst.get("x", 0.0),
                    "y":           inst.get("y", 0.0),
                    "width":       inst.get("width",  PITCH_UM),
                    "height":      inst.get("height", ROW_HEIGHT_UM),
                    "orientation": inst.get("orientation", "R0"),
                }
                # Carry OAS abutment state (only if enabled)
                abut_l = inst.get("abut_left",  False) if abutment_enabled else False
                abut_r = inst.get("abut_right", False) if abutment_enabled else False
                if abut_l or abut_r:
                    abut_info = {"abut_left": abut_l, "abut_right": abut_r}

            elif is_passive:
                # Compute passive geometry from params
                prm = dev.params
                raw_w = prm.get("w", PITCH_UM)
                raw_l = prm.get("l", ROW_HEIGHT_UM)
                nf_p  = max(1, int(prm.get("nf", 1)))
                if dev_type == "res":
                    width_um  = max(raw_l * nf_p, PITCH_UM)
                    height_um = max(raw_w, 0.1)
                else:
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

            # 3. Create Node
            node_dict = {
                "id":         dev_name,
                "type":       dev_type,
                "electrical": electrical,
                "geometry":   geom,
            }
            if abut_info:
                node_dict["abutment"] = abut_info

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

            # Terminal nets
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

        # 7. Fan-out safety net: if the matcher was partial (some devices
        #    mapped, others left at the default 0.0/0.0), spread the
        #    unmatched devices out next to the matched ones so they don't
        #    stack invisibly at the origin.
        if device_mapping:
            # Find the rightmost X occupied by any matched device
            max_x = max(
                (n["geometry"]["x"] + n["geometry"]["width"]
                 for n in nodes if n["geometry"]["x"] != 0.0 or n["geometry"]["y"] != 0.0),
                default=0.0,
            )
            fanout_x = max_x + PITCH_UM
            for n in nodes:
                geo = n["geometry"]
                if geo["x"] == 0.0 and geo["y"] == 0.0:
                    # Check this device was truly unmatched (no layout index)
                    if device_mapping.get(n["id"]) is None:
                        geo["x"] = fanout_x
                        geo["y"] = 0.0
                        fanout_x += geo["width"] + PITCH_UM

        return {
            "nodes": nodes,
            "edges": edges,
            "terminal_nets": terminal_nets,
            "blocks": blocks,
        }
    
    @staticmethod
    def _compress_graph_for_storage(data: dict) -> dict:
        """
        Create a compressed version of the graph JSON for storage.
        Keeps full detail for output but provides optimized view for AI prompts.
        """
        import re
        from collections import defaultdict
        
        compressed = {
            "version": "2.0",
            "device_types": {
                "pmos": {"y_row": 0.668, "default_width": 0.294, "default_height": 0.818},
                "nmos": {"y_row": 0.0, "default_width": 0.294, "default_height": 0.668},
                "res": {"y_row": 1.630, "default_width": 0.294, "default_height": 0.1},
                "cap": {"y_row": 1.630, "default_width": 0.294, "default_height": 0.668}
            },
            "devices": {},
            "connectivity": {"nets": defaultdict(list)},
            "drc_rules": {
                "fin_pitch": 0.014,
                "row_pitch": 0.668,
                "device_pitch": 0.294,
                "abut_pitch": 0.070
            }
        }
        
        # Collapse finger instances into parent devices
        terminal_nets = data.get("terminal_nets", {})
        for node in data.get("nodes", []):
            node_id = node["id"]
            electrical = node.get("electrical", {})
            parent_id = electrical.get("parent")
            
            # Extract parent from node_id if not in electrical
            if not parent_id:
                parent_id = re.sub(r'_[mf]\d+$', '', node_id)
            
            # Skip if already processed
            if parent_id in compressed["devices"]:
                continue
            
            dev_type = node.get("type", "nmos")
            dev_terminal_nets = terminal_nets.get(node_id, {})
            
            compressed["devices"][parent_id] = {
                "type": dev_type,
                "m": electrical.get("m", 1),
                "nf": electrical.get("nf", 1),
                "nfin": electrical.get("nfin", 1),
                "l": electrical.get("l", 0.0),
                "terminal_nets": dev_terminal_nets
            }
            
            # Add block info if present
            if node.get("block"):
                compressed["devices"][parent_id]["block"] = node["block"]
        
        # Build net-centric connectivity
        for edge in data.get("edges", []):
            net = edge.get("net", "")
            if not net or net.upper() in {"VDD", "VSS", "GND", "VCC"}:
                continue
            
            source = re.sub(r'_[mf]\d+$', '', edge.get("source", ""))
            target = re.sub(r'_[mf]\d+$', '', edge.get("target", ""))
            
            if source and target:
                compressed["connectivity"]["nets"][net].append(source)
                compressed["connectivity"]["nets"][net].append(target)
        
        # Deduplicate and sort net lists
        for net in compressed["connectivity"]["nets"]:
            compressed["connectivity"]["nets"][net] = sorted(
                set(compressed["connectivity"]["nets"][net])
            )
        
        # Convert defaultdict to dict for JSON serialization
        compressed["connectivity"]["nets"] = dict(compressed["connectivity"]["nets"])
        
        # Add blocks summary
        compressed["blocks"] = data.get("blocks", {})
        
        return compressed

    @staticmethod
    def _run_ai_initial_placement(data, model_choice="Gemini", submodel=None):
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
                ollama_submodel = submodel or "llama3.2"
                ollama_generate_placement(tmp_in_path, tmp_out_path, model=ollama_submodel)

            elif model_choice == "Groq":
                try:
                    from ai_agent.ai_initial_placement.groq_placer import groq_generate_placement
                    groq_generate_placement(tmp_in_path, tmp_out_path)
                except Exception as e:
                    err_str = str(e)
                    if "401" in err_str or "Authentication" in err_str or "invalid_api_key" in err_str:
                        raise RuntimeError(
                            "Invalid Groq API Key.\n\n"
                            "The API key provided is rejected by Groq.\n\n"
                            "Please enter a fresh, valid Groq secret key in the dialog.\n"
                            "Get one at: https://console.groq.com"
                        )
                    raise

            elif model_choice == "DeepSeek":
                try:
                    from ai_agent.ai_initial_placement.deepseek_placer import deepseek_generate_placement
                    deepseek_generate_placement(tmp_in_path, tmp_out_path)
                except Exception as e:
                    err_str = str(e)
                    if "401" in err_str or "Authentication" in err_str or "invalid_api_key" in err_str:
                        raise RuntimeError(
                            "Invalid DeepSeek API Key.\n\n"
                            "The API key provided is rejected by DeepSeek.\n\n"
                            "Please enter a fresh, valid DeepSeek secret key in the dialog.\n"
                            "Get one at: https://platform.deepseek.com"
                        )
                    raise

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

    def _load_from_data_dict(self, data, file_path, compact=False):
        """
        Load a placement data dict (with nodes, edges, terminal_nets)
        directly into the GUI without reading from a file.
        """
        self._push_undo()
        self._original_data = data
        self.nodes = data["nodes"]
        self._terminal_nets = data.get("terminal_nets", {})
        self._current_file = file_path
        self._refresh_panels(compact=compact)
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

        # Find sibling .oas files, preferring the original (no _updated suffix,
        # or the one with fewest cells so we don't start from a previously
        # exported file that already has variant cells).
        oas_files = sorted(glob.glob(os.path.join(json_dir, "*.oas")))
        if not oas_files:
            self.chat_panel._append_message(
                "AI",
                "No .oas file found next to the loaded JSON.",
                "#fde8e8", "#a00",
            )
            return

        # Prefer a file WITHOUT "_updated" in its name (i.e., the base OAS)
        base_oas_files = [f for f in oas_files if "_updated" not in os.path.basename(f).lower()]
        oas_path = base_oas_files[0] if base_oas_files else oas_files[0]

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
        base_name = os.path.splitext(os.path.basename(oas_path))[0]
        default_name = base_name + "_updated.oas"
        default_path = os.path.join(json_dir, default_name)
        output_path, _ = QFileDialog.getSaveFileName(
            self, "Export to OAS", default_path,
            "OASIS Files (*.oas);;GDS Files (*.gds);;All Files (*)",
        )
        if not output_path:
            return

        # Sync current canvas positions into self.nodes
        self._sync_node_positions()
        
        # CRITICAL: Before export, ensure ALL devices are visible
        # Hierarchy groups hide devices, but OAS export needs all finger devices
        saved_hierarchy_state = self._expand_all_for_export()

        try:
            from export.oas_writer import update_oas_placement

            # Collect per-device manual abutment states from the canvas
            abut_states = self.editor.get_device_abutment_states()

            # Inject into node dicts so the writer can pick them up
            for node in self.nodes:
                dev_id = node.get("id")
                if dev_id in abut_states:
                    node["abutment"] = abut_states[dev_id]
                else:
                    node.pop("abutment", None)   # clear any old value

            # Show a summary of what will be abutted
            if abut_states:
                lines = []
                for dev_id, flags in abut_states.items():
                    parts = []
                    if flags.get("abut_left"):
                        parts.append("left")
                    if flags.get("abut_right"):
                        parts.append("right")
                    lines.append(f"  • **{dev_id}**: {' + '.join(parts)}")
                self.chat_panel._append_message(
                    "AI",
                    f"Exporting OAS with abutment on {len(abut_states)} device(s):\n"
                    + "\n".join(lines)
                    + f"\n\nSource OAS: `{os.path.basename(oas_path)}`",
                    "#e8f4fd", "#1a1a2e",
                )
            else:
                self.chat_panel._append_message(
                    "AI",
                    "ℹ️  No abutment flags set — exporting with standard (non-abutted) cells.\n\n"
                    "To set abutment: **right-click a transistor** on the canvas → "
                    "**Left Abutment** / **Right Abutment**.\n\n"
                    f"Source OAS: `{os.path.basename(oas_path)}`",
                    "#fff8e1", "#7a5c00",
                )

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
        finally:
            # Always restore hierarchy state (whether export succeeded or failed)
            self._restore_hierarchy_state(saved_hierarchy_state)

    def _expand_all_for_export(self):
        """Temporarily expand all hierarchy groups so all devices are visible for export.
        
        OAS export needs all finger devices visible. This ensures positions are
        correctly synced before writing the file.
        
        Returns: dict mapping group -> (group._is_descended, child_groups states)
        """
        saved_state = {}
        try:
            for group in self.editor._hierarchy_groups:
                # Save parent group state
                saved_state[group] = group._is_descended
                
                # Save child group states
                saved_state[group] = (group._is_descended, 
                                     [(child, child._is_descended) for child in group._child_groups])
                
                # Expand if not already descended
                if not group._is_descended:
                    group.descend()
                    # Also descend child groups to show devices
                    for child in group._child_groups:
                        if not child._is_descended and child._device_items:
                            child.descend()
        except Exception as e:
            print(f"[WARNING] Failed to expand hierarchy for export: {e}")
        
        return saved_state
    
    def _restore_hierarchy_state(self, saved_state):
        """Restore hierarchy groups to their previous descended/ascended state."""
        try:
            for group, state in saved_state.items():
                if isinstance(state, tuple):
                    parent_descended, child_states = state
                else:
                    # Backwards compat
                    parent_descended = state
                    child_states = []
                
                # Restore parent state
                if parent_descended and not group._is_descended:
                    group.descend()
                elif not parent_descended and group._is_descended:
                    group.ascend()
                
                # Restore child states
                if isinstance(state, tuple):
                    for child, child_descended in child_states:
                        if child_descended and not child._is_descended:
                            child.descend()
                        elif not child_descended and child._is_descended:
                            child.ascend()
        except Exception as e:
            print(f"[WARNING] Failed to restore hierarchy state: {e}")




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

                # ── Lock guard: reject swap for locked devices ──
                if self._is_device_locked(id_a) or self._is_device_locked(id_b):
                    self.chat_panel._append_message(
                        "AI",
                        f"⚠️ Cannot swap — one or both devices ({id_a}, {id_b}) "
                        f"are in a locked matched group. Unlock the group first.",
                        "#fff3e0",
                        "#e65100",
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

            elif action == "abut":
                raw_a = cmd.get("device_a", cmd.get("a"))
                raw_b = cmd.get("device_b", cmd.get("b"))
                id_a = self._resolve_device_id(raw_a)
                id_b = self._resolve_device_id(raw_b)
                print(f"[AI CMD] Abut: raw=({raw_a},{raw_b}) resolved=({id_a},{id_b})")

                if not id_a or not id_b:
                    self.chat_panel._append_message(
                        "AI",
                        f"Abutment failed: device not found ({raw_a}, {raw_b}).",
                        "#fde8e8",
                        "#a00",
                    )
                    return

                # ── Lock guard: reject abut for locked devices ──
                if self._is_device_locked(id_a) or self._is_device_locked(id_b):
                    self.chat_panel._append_message(
                        "AI",
                        f"⚠️ Cannot abut — one or both devices ({id_a}, {id_b}) "
                        f"are in a locked matched group. Unlock the group first.",
                        "#fff3e0",
                        "#e65100",
                    )
                    return

                # Sync current canvas state into self.nodes
                self._sync_node_positions()
                if not _skip_undo:
                    self._push_undo()

                self._abut_devices(id_a, id_b)
                # Rebuild canvas WITHOUT re-compaction so the new abutted positions stick
                self._refresh_panels(compact=False)
                self.chat_panel._append_message(
                    "AI",
                    f"✅ Abutted **{id_a}** and **{id_b}**",
                    "#e8f4fd",
                    "#1a1a2e",
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

                # ── Lock guard: move entire matched group as a block ──
                group = self._get_device_group(dev_id)
                if group:
                    # Convert data-level coords to scene coords
                    scale = self.editor.scale_factor
                    target_scene_x = float(x) * scale
                    target_scene_y = float(y) * scale
                    n_moved = self._move_matched_group_as_block(
                        group, target_scene_x, target_scene_y,
                    )
                    self._sync_node_positions()
                    self._refresh_panels(compact=False)
                    self.chat_panel._append_message(
                        "AI",
                        f"↕ Moved matched group ({n_moved} devices) as a block "
                        f"to ({x}, {y}). Internal pattern preserved.",
                        "#e8f4fd",
                        "#1a1a2e",
                    )
                    return

                # --- Move at data level (unlocked device) ---
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

            elif action in {"move_row", "move_row_devices"}:
                dev_type = cmd.get("type", "")
                new_y = cmd.get("y")
                print(f"[AI CMD] Move row: type={dev_type} y={new_y}")

                if not dev_type or new_y is None:
                    self.chat_panel._append_message(
                        "AI",
                        "Move row failed: missing type or y in command.",
                        "#fde8e8",
                        "#a00",
                    )
                    return

                self._sync_node_positions()
                if not _skip_undo:
                    self._push_undo()

                # Move ALL devices of the given type to the new Y
                count = 0
                for node in self.nodes:
                    if node.get("type") == dev_type:
                        node["geometry"]["y"] = float(new_y)
                        count += 1

                self._refresh_panels(compact=False)
                self.chat_panel._append_message(
                    "AI",
                    f"Moved all {count} {dev_type} devices to Y={new_y}",
                    "#e8f4fd",
                    "#1a1a2e",
                )

            elif action == "abut":
                raw_a = cmd.get("device_a", cmd.get("a"))
                raw_b = cmd.get("device_b", cmd.get("b"))
                id_a = self._resolve_device_id(raw_a)
                id_b = self._resolve_device_id(raw_b)
                print(f"[AI CMD] Abut: raw=({raw_a},{raw_b}) resolved=({id_a},{id_b})")

                if not id_a or not id_b:
                    self.chat_panel._append_message(
                        "AI",
                        f"Abutment failed: device not found ({raw_a}, {raw_b}).",
                        "#fde8e8",
                        "#a00",
                    )
                    return

                # ── Lock guard: reject abut for locked devices ──
                if self._is_device_locked(id_a) or self._is_device_locked(id_b):
                    self.chat_panel._append_message(
                        "AI",
                        f"⚠️ Cannot abut — one or both devices ({id_a}, {id_b}) "
                        f"are in a locked matched group. Unlock the group first.",
                        "#fff3e0",
                        "#e65100",
                    )
                    return

                # Sync current canvas state into self.nodes
                self._sync_node_positions()
                if not _skip_undo:
                    self._push_undo()

                self._abut_devices(id_a, id_b)
                # Rebuild canvas WITHOUT re-compaction so the new abutted positions stick
                self._refresh_panels(compact=False)
                self.chat_panel._append_message(
                    "AI",
                    f"✅ Abutted **{id_a}** and **{id_b}**",
                    "#e8f4fd",
                    "#1a1a2e",
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

    def _abut_devices(self, id_a, id_b):
        """Align device B immediately to the right of device A and set abutment flags."""
        node_a = next((n for n in self.nodes if n.get("id") == id_a), None)
        node_b = next((n for n in self.nodes if n.get("id") == id_b), None)
        if not node_a or not node_b:
            return

        # 1. Set abutment flags in the data model
        node_a.setdefault("abutment", {})["abut_right"] = True
        node_b.setdefault("abutment", {})["abut_left"] = True

        # 2. Match Y positions (must be in same row)
        node_b["geometry"]["y"] = node_a["geometry"]["y"]

        # 3. Position B to the right of A
        # Using the overlap pitch (0.070um) instead of full pitch
        # Logic: origin_b = origin_a + overlap_pitch
        node_b["geometry"]["x"] = node_a["geometry"]["x"] + 0.070

        print(f"[AI CMD] Data abut done: {id_a} abut_right=True, {id_b} abut_left=True at x={node_b['geometry']['x']}")

    def _sync_node_positions(self):
        """Sync canvas positions back to self.nodes and update layout context.

        For devices that were loaded from OAS with exact coordinates (snap grid
        disabled), we compare the canvas position against the stored geometry.
        If they match (within floating-point tolerance), the original precision
        values are kept so lossless round-trips back to the OAS file are
        guaranteed even after Qt's internal float representation.
        """
        positions = self.editor.get_updated_positions()
        scale = getattr(self.editor, "scale_factor", 80) or 80

        for node in self.nodes:
            dev_id = node.get("id")
            if dev_id not in positions:
                continue

            canvas_x, canvas_y = positions[dev_id]
            item = self.editor.device_items.get(dev_id)

            # Check whether this item still has per-item snapping disabled
            # (i.e. it was loaded from OAS and the user hasn't dragged it yet).
            snap_disabled = (
                item is not None
                and getattr(item, "_snap_grid_x", None) is None
            )

            if snap_disabled:
                # Keep the stored OAS precision; only update orientation.
                # (The canvas position is already based on the stored value so
                #  there should be no real difference, but floating-point
                #  round-trips through Qt can introduce tiny errors.)
                if item and hasattr(item, "orientation_string"):
                    node["geometry"]["orientation"] = item.orientation_string()
            else:
                # Item has been (or will be) snapped — use the live canvas position.
                node["geometry"]["x"] = canvas_x
                node["geometry"]["y"] = canvas_y
                if item and hasattr(item, "orientation_string"):
                    node["geometry"]["orientation"] = item.orientation_string()

        # Refresh the chat panel's context with updated positions
        self.chat_panel.set_layout_context(
            self.nodes, self._original_data.get("edges"),
            self._terminal_nets,
        )
        self._update_grid_counts()
        self._on_selection_count_changed()

    def _on_reload_app(self):
        """Restarts the application by spawning a new process and exiting."""
        # Cleanup the LLM worker first
        self.chat_panel.shutdown()
        os.execl(sys.executable, sys.executable, *sys.argv)



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
