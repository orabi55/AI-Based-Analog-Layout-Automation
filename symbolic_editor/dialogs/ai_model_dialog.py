import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QWidget,
    QFrame, QCheckBox, QFormLayout, QLineEdit,
    QComboBox, QGroupBox, QHBoxLayout, QPushButton,
    QStackedWidget, QSizePolicy, QScrollArea,
)
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QFont
from symbolic_editor.dialogs.placement_goals_widget import PlacementGoalsWidget


# ---------------------------------------------------------------------------
# Collapsible Goals Section
# ---------------------------------------------------------------------------
class _CollapsibleGoals(QWidget):
    """
    A toggle-header + animated body that hides/shows the PlacementGoalsWidget.

    When collapsed (default) → get_goals() returns None → pipeline runs with
    original defaults (no priority filtering applied).
    When expanded → get_goals() returns the user-configured dict.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._expanded = False

        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        # ── Toggle header row ─────────────────────────────────────────────
        header = QWidget()
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        header.setStyleSheet("""
            QWidget {
                background: #1e2636;
                border: 1px solid #3d5066;
                border-radius: 6px;
            }
            QWidget:hover {
                background: #253044;
                border-color: #4a90d9;
            }
        """)
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(10, 7, 10, 7)
        h_lay.setSpacing(8)

        # Gear icon + label
        icon_lbl = QLabel("⚙")
        icon_lbl.setStyleSheet(
            "color: #4a90d9; font-size: 13pt; background: transparent; border: none;"
        )
        h_lay.addWidget(icon_lbl)

        text_lbl = QLabel("Placement Goals  <span style='color:#6a7a90;font-size:8pt;'>"
                          "(optional — click to configure priorities)</span>")
        text_lbl.setStyleSheet(
            "color: #c8d0dc; font-size: 9pt; font-weight: bold; "
            "background: transparent; border: none;"
        )
        text_lbl.setOpenExternalLinks(False)
        h_lay.addWidget(text_lbl, 1)

        self._arrow = QLabel("▶")
        self._arrow.setStyleSheet(
            "color: #6a7a90; font-size: 9pt; background: transparent; border: none;"
        )
        h_lay.addWidget(self._arrow)

        root.addWidget(header)

        # ── Body (collapsible) ────────────────────────────────────────────
        self._body = QWidget()
        self._body.setVisible(False)
        self._body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        body_lay = QVBoxLayout(self._body)
        body_lay.setContentsMargins(0, 6, 0, 0)
        body_lay.setSpacing(0)

        self.goals_widget = PlacementGoalsWidget()
        body_lay.addWidget(self.goals_widget)
        root.addWidget(self._body)

        # ── Click to toggle ───────────────────────────────────────────────
        header.mousePressEvent = lambda _e: self._toggle()

    # -- public API ─────────────────────────────────────────────────────────

    def get_goals(self):
        """Return goals dict when expanded, None when collapsed (= defaults)."""
        if not self._expanded:
            return None
        return self.goals_widget.get_goals()

    # -- private ────────────────────────────────────────────────────────────

    def _toggle(self):
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        self._arrow.setText("▼" if self._expanded else "▶")
        # Force the parent dialog to resize to fit
        if self.window():
            self.window().adjustSize()


# ---------------------------------------------------------------------------
# Main Dialog
# ---------------------------------------------------------------------------
class AIModelSelectionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select AI Model")
        self.setMinimumSize(480, 420)
        self.resize(480, 480)
        self.setSizeGripEnabled(True)
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
            QLineEdit, QComboBox {
                background-color: #232a38;
                color: #c8d0dc;
                border: 1px solid #2d3548;
                border-radius: 6px;
                padding: 5px 10px;
                font-size: 9pt;
            }
            QComboBox::drop-down {
                border: none;
                width: 30px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 5px solid #8899aa;
                margin-right: 10px;
            }
            QComboBox QAbstractItemView {
                background-color: #232a38;
                color: #c8d0dc;
                selection-background-color: #3d5066;
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
                border: 1px solid #3d5066;
                border-radius: 6px;
                margin-top: 10px;
                padding-top: 15px;
                color: #c8d0dc;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 10px;
                padding: 0 3px;
            }
        """)

        # ── Main layout ──────────────────────────────────────
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(14)
        main_layout.setContentsMargins(20, 20, 20, 20)

        # Title
        title = QLabel("AI Initial Placement")
        title.setStyleSheet("font-size: 14pt; font-weight: bold; color: #ffffff;")
        main_layout.addWidget(title)

        subtitle = QLabel("Choose a model and configure its settings below.")
        subtitle.setStyleSheet("font-size: 9pt; color: #8899aa; margin-bottom: 8px;")
        main_layout.addWidget(subtitle)

        # ── Model dropdown ───────────────────────────────────
        model_layout = QFormLayout()
        model_layout.setContentsMargins(0, 0, 0, 0)
        self.model_combo = QComboBox()
        self.model_combo.addItem("Gemini Pro (Cloud)", "Gemini")
        self.model_combo.addItem("Alibaba Qwen (DashScope)", "Alibaba")
        self.model_combo.addItem("Vertex AI — Gemini (Cloud ADC)", "VertexGemini")
        self.model_combo.addItem("Vertex AI — Claude (Cloud ADC)", "VertexClaude")
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        lbl = QLabel("AI Model:")
        lbl.setStyleSheet("font-weight: bold;")
        model_layout.addRow(lbl, self.model_combo)
        main_layout.addLayout(model_layout)

        # ── Stacked model settings ───────────────────────────
        self.settings_stack = QStackedWidget()

        self.gemini_api_key = QLineEdit(os.environ.get("GEMINI_API_KEY", "******"))
        self.settings_stack.addWidget(
            self._create_page("API Key:", self.gemini_api_key,
                              "Fast & efficient. Free tier: 15 req/min.")
        )

        self.alibaba_api_key = QLineEdit(
            os.environ.get("ALIBABA_API_KEY", "sk-567af8d3cf51494faa346579ba523add")
        )
        self.alibaba_model_combo = QComboBox()
        self.alibaba_model_combo.setEditable(True)
        self.alibaba_model_combo.addItems(
            ["qwen-plus", "qwen3.6-flash", "qwen3.6-max-preview", "qwen3.6-35b-a3b"]
        )
        self.settings_stack.addWidget(
            self._create_multi_page(
                [("API Key:", self.alibaba_api_key), ("Model:", self.alibaba_model_combo)],
                "Alibaba DashScope (Qwen). base_url: dashscope-intl.aliyuncs.com  ⚡ Fast & cheap."
            )
        )

        self.vertex_gemini_project = QLineEdit(
            os.environ.get("VERTEX_PROJECT_ID", "project-03484c74-0ab0-4f9e-b48")
        )
        self.vertex_gemini_project.setPlaceholderText("Google Cloud Project ID")
        self.vertex_gemini_location = QLineEdit(os.environ.get("VERTEX_LOCATION", "global"))
        self.vertex_gemini_location.setPlaceholderText("global")
        self.settings_stack.addWidget(
            self._create_multi_page(
                [("Project ID:", self.vertex_gemini_project),
                 ("Location:",   self.vertex_gemini_location)],
                "Uses Google Cloud ADC. No API key needed. "
                "Run 'gcloud auth application-default login' first."
            )
        )

        self.vertex_claude_project = QLineEdit(
            os.environ.get("VERTEX_PROJECT_ID", "project-03484c74-0ab0-4f9e-b48")
        )
        self.vertex_claude_project.setPlaceholderText("Google Cloud Project ID")
        self.vertex_claude_location = QLineEdit(os.environ.get("VERTEX_LOCATION", "global"))
        self.vertex_claude_location.setPlaceholderText("global")
        self.settings_stack.addWidget(
            self._create_multi_page(
                [("Project ID:", self.vertex_claude_project),
                 ("Location:",   self.vertex_claude_location)],
                "Anthropic Claude via Google Cloud Model Garden. Uses ADC auth."
            )
        )

        main_layout.addWidget(self.settings_stack)
        self.model_combo.setCurrentIndex(2)

        # ── Placement Options ────────────────────────────────
        options_group = QGroupBox("Placement Options")
        options_layout = QVBoxLayout(options_group)

        self.check_abutment = QCheckBox("Enable Abutment (Diffusion Sharing)")
        self.check_abutment.setChecked(False)
        self.check_abutment.setStyleSheet(
            "color: #c8d0dc; font-size: 10pt; font-weight: normal; spacing: 10px;"
        )
        options_layout.addWidget(self.check_abutment)

        abutment_desc = QLabel(
            "When enabled, adjacent fingers sharing a Source/Drain net will be "
            "abutted at 0.070 µm pitch to save area. "
            "When disabled, standard spacing is used everywhere."
        )
        abutment_desc.setStyleSheet("color: #8899aa; font-size: 9pt; margin-left: 28px;")
        abutment_desc.setWordWrap(True)
        options_layout.addWidget(abutment_desc)

        main_layout.addWidget(options_group)

        # ── Collapsible Placement Goals (optional) ───────────
        self._goals_section = _CollapsibleGoals()
        main_layout.addWidget(self._goals_section)

        main_layout.addStretch()

        # ── Buttons ──────────────────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 8, 0, 0)
        info_label = QLabel(
            "<a href='https://aistudio.google.com' style='color:#8899aa;'>Get Gemini Key</a>  |  "
            "<a href='https://console.groq.com' style='color:#8899aa;'>Get Groq Key</a>"
        )
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

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _create_page(self, label, widget, desc_text):
        return self._create_multi_page([(label, widget)], desc_text)

    def _create_multi_page(self, form_rows, desc_text):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        desc_box = QFrame()
        desc_box.setStyleSheet(
            "background-color: #1e2636; border: 1px solid #3d5066; border-radius: 6px;"
        )
        desc_layout = QVBoxLayout(desc_box)
        desc_layout.setContentsMargins(12, 10, 12, 10)
        desc = QLabel(desc_text)
        desc.setStyleSheet("color: #8899aa; font-size: 9pt; border: none;")
        desc.setWordWrap(True)
        desc_layout.addWidget(desc)
        layout.addWidget(desc_box)

        form = QFormLayout()
        form.setContentsMargins(0, 8, 0, 0)
        form.setSpacing(12)
        for row_label, widget in form_rows:
            form.addRow(row_label, widget)
        layout.addLayout(form)
        layout.addStretch()
        return page

    def _on_model_changed(self, index):
        self.settings_stack.setCurrentIndex(index)

    # ── Public API ────────────────────────────────────────────────────────────

    def get_selected_model(self):
        return self.model_combo.itemData(self.model_combo.currentIndex())

    def get_alibaba_model(self):
        return self.alibaba_model_combo.currentText()

    def is_abutment_enabled(self):
        return self.check_abutment.isChecked()

    def get_goals(self):
        """
        Return placement goals dict when the panel is open, or None when
        collapsed.  None means: run with original pipeline defaults.
        """
        return self._goals_section.get_goals()

    def apply_api_keys(self):
        gemini_key = self.gemini_api_key.text().strip().strip('\'"')
        if gemini_key and gemini_key != "******":
            os.environ["GEMINI_API_KEY"] = gemini_key

        alibaba_key = self.alibaba_api_key.text().strip().strip('\'"')
        if alibaba_key and alibaba_key != "******":
            os.environ["ALIBABA_API_KEY"] = alibaba_key

        vertex_project = (
            self.vertex_gemini_project.text().strip()
            or self.vertex_claude_project.text().strip()
        )
        if vertex_project:
            os.environ["VERTEX_PROJECT_ID"] = vertex_project

        selected = self.get_selected_model()
        vertex_location = ""
        if selected == "VertexGemini":
            vertex_location = self.vertex_gemini_location.text().strip() or "global"
        elif selected == "VertexClaude":
            vertex_location = self.vertex_claude_location.text().strip() or "global"

        if vertex_location:
            os.environ["VERTEX_LOCATION"] = vertex_location
