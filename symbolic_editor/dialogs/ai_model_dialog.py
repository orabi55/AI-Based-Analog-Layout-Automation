import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QWidget, 
    QFrame, QCheckBox, QFormLayout, QLineEdit,
    QComboBox, QGroupBox, QHBoxLayout, QPushButton,
    QStackedWidget
)
from PySide6.QtCore import Qt

class AIModelSelectionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select AI Model")
        self.setMinimumSize(450, 380)
        self.resize(450, 380)
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
                image: none; /* Can replace with a custom arrow if desired */
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
        main_layout.setSpacing(16)
        main_layout.setContentsMargins(20, 20, 20, 20)

        # Title
        title = QLabel("AI Initial Placement")
        title.setStyleSheet("font-size: 14pt; font-weight: bold; color: #ffffff;")
        main_layout.addWidget(title)

        subtitle = QLabel("Choose a model and configure its settings below.")
        subtitle.setStyleSheet("font-size: 9pt; color: #8899aa; margin-bottom: 8px;")
        main_layout.addWidget(subtitle)

        # ── Dropdown Menu ────────────────────────────────────
        model_layout = QFormLayout()
        model_layout.setContentsMargins(0, 0, 0, 0)
        self.model_combo = QComboBox()
        self.model_combo.addItem("Gemini Pro (Cloud)", "Gemini")
        self.model_combo.addItem("Alibaba Qwen (DashScope)", "Alibaba")
        self.model_combo.addItem("Vertex AI — Gemini (Cloud ADC)", "VertexGemini")
        self.model_combo.addItem("Vertex AI — Claude (Cloud ADC)", "VertexClaude")
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        
        # Style the combobox label
        lbl = QLabel("AI Model:")
        lbl.setStyleSheet("font-weight: bold;")
        model_layout.addRow(lbl, self.model_combo)
        main_layout.addLayout(model_layout)

        # ── Stacked Widget for Model Settings ────────────────
        self.settings_stack = QStackedWidget()
        
        # 1. Gemini
        self.gemini_api_key = QLineEdit(os.environ.get("GEMINI_API_KEY", "******"))
        self.gemini_page = self._create_page("API Key:", self.gemini_api_key, "Fast & efficient. Free tier: 15 req/min.")
        self.settings_stack.addWidget(self.gemini_page)

        # 6. Alibaba DashScope (Qwen)
        self.alibaba_api_key = QLineEdit(os.environ.get("ALIBABA_API_KEY", "******"))
        self.alibaba_model_combo = QComboBox()
        self.alibaba_model_combo.setEditable(True)
        self.alibaba_model_combo.addItems(["qwen-plus", "qwen3.6-flash", "qwen3.6-max-preview", "qwen3.6-35b-a3b"])
        alibaba_page_widget = QWidget()
        alibaba_layout = QVBoxLayout(alibaba_page_widget)
        alibaba_layout.setContentsMargins(0, 0, 0, 0)
        alibaba_desc_box = self._create_multi_page(
            [("API Key:", self.alibaba_api_key), ("Model:", self.alibaba_model_combo)],
            "Alibaba DashScope (Qwen). base_url: dashscope-intl.aliyuncs.com  \u26a1 Fast & cheap."
        )
        self.settings_stack.addWidget(alibaba_desc_box)

        # 7. Vertex AI Gemini
        self.vertex_gemini_project = QLineEdit(os.environ.get("VERTEX_PROJECT_ID", "project-03484c74-0ab0-4f9e-b48"))
        self.vertex_gemini_project.setPlaceholderText("Google Cloud Project ID")
        self.vertex_gemini_location = QLineEdit(os.environ.get("VERTEX_LOCATION", "global"))
        self.vertex_gemini_location.setPlaceholderText("global")
        self.vertex_gemini_page = self._create_multi_page(
            [("Project ID:", self.vertex_gemini_project), ("Location:", self.vertex_gemini_location)],
            "Uses Google Cloud ADC. No API key needed. Run 'gcloud auth application-default login' first."
        )
        self.settings_stack.addWidget(self.vertex_gemini_page)

        # 7. Vertex AI Claude
        self.vertex_claude_project = QLineEdit(os.environ.get("VERTEX_PROJECT_ID", "project-03484c74-0ab0-4f9e-b48"))
        self.vertex_claude_project.setPlaceholderText("Google Cloud Project ID")
        self.vertex_claude_location = QLineEdit(os.environ.get("VERTEX_LOCATION", "global"))
        self.vertex_claude_location.setPlaceholderText("global")
        self.vertex_claude_page = self._create_multi_page(
            [("Project ID:", self.vertex_claude_project), ("Location:", self.vertex_claude_location)],
            "Anthropic Claude via Google Cloud Model Garden. Uses ADC auth."
        )
        self.settings_stack.addWidget(self.vertex_claude_page)

        main_layout.addWidget(self.settings_stack)

        # ── Placement Options ────────────────────────────────
        options_group = QGroupBox("Placement Options")
        options_layout = QVBoxLayout(options_group)

        self.check_multi_agent = QCheckBox("Use Autonomous Multi-Agent Pipeline")
        self.check_multi_agent.setChecked(True)
        self.check_multi_agent.setStyleSheet("color: #c8d0dc; font-size: 10pt; font-weight: bold; spacing: 10px;")
        options_layout.addWidget(self.check_multi_agent)

        multi_desc = QLabel(
            "Uses the intelligent 'Layout Copilot' pipeline: Topology Analyst \u2192 "
            "Strategy Selector \u2192 Placement Specialist \u2192 DRC Critic \u2192 Routing Previewer."
        )
        multi_desc.setStyleSheet("color: #8899aa; font-size: 9pt; margin-left: 28px; margin-bottom: 8px;")
        multi_desc.setWordWrap(True)
        options_layout.addWidget(multi_desc)

        self.check_abutment = QCheckBox("Enable Abutment (Diffusion Sharing)")
        self.check_abutment.setChecked(True)
        self.check_abutment.setStyleSheet("color: #c8d0dc; font-size: 10pt; font-weight: normal; spacing: 10px;")
        options_layout.addWidget(self.check_abutment)

        abutment_desc = QLabel(
            "When enabled, adjacent fingers sharing a Source/Drain net will be "
            "abutted at 0.070 \u00b5m pitch to save area. "
            "When disabled, standard spacing is used everywhere."
        )
        abutment_desc.setStyleSheet("color: #8899aa; font-size: 9pt; margin-left: 28px;")
        abutment_desc.setWordWrap(True)
        options_layout.addWidget(abutment_desc)

        self.check_sa = QCheckBox("Run Post-Optimization (Simulated Annealing)")
        self.check_sa.setChecked(True)
        self.check_sa.setStyleSheet("color: #c8d0dc; font-size: 10pt; font-weight: normal; spacing: 10px;")
        options_layout.addWidget(self.check_sa)

        sa_desc = QLabel(
            "After AI placement, run a local SA optimizer to swap devices "
            "within each row and minimise total wire length. Adds ~1-3 seconds."
        )
        sa_desc.setStyleSheet("color: #8899aa; font-size: 9pt; margin-left: 28px;")
        sa_desc.setWordWrap(True)
        options_layout.addWidget(sa_desc)

        main_layout.addWidget(options_group)
        main_layout.addStretch()

        # ── Buttons ──────────────────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 8, 0, 0)
        info_label = QLabel("<a href='https://aistudio.google.com' style='color:#8899aa;'>Get Gemini Key</a>  |  "
                             "<a href='https://console.groq.com' style='color:#8899aa;'>Get Groq Key</a>")
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

    def _create_page(self, label, widget, desc_text):
        """Helper to create a single-field QStackedWidget page."""
        return self._create_multi_page([(label, widget)], desc_text)

    def _create_multi_page(self, form_rows, desc_text):
        """Helper to create a multi-field QStackedWidget page."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Info box
        desc_box = QFrame()
        desc_box.setStyleSheet("background-color: #1e2636; border: 1px solid #3d5066; border-radius: 6px;")
        desc_layout = QVBoxLayout(desc_box)
        desc_layout.setContentsMargins(12, 10, 12, 10)
        desc = QLabel(desc_text)
        desc.setStyleSheet("color: #8899aa; font-size: 9pt; border: none;")
        desc.setWordWrap(True)
        desc_layout.addWidget(desc)
        layout.addWidget(desc_box)

        # Form fields
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

    def get_selected_model(self):
        # The user data contains our internal ID ("Gemini", "Groq", etc)
        return self.model_combo.itemData(self.model_combo.currentIndex())

    def get_alibaba_model(self):
        return self.alibaba_model_combo.currentText()

    def is_abutment_enabled(self):
        return self.check_abutment.isChecked()

    def is_sa_enabled(self):
        return self.check_sa.isChecked()
        
    def get_multi_agent_enabled(self):
        return self.check_multi_agent.isChecked()

    def apply_api_keys(self):
        # Update environment variables based on user changes
        gemini_key = self.gemini_api_key.text().strip().strip('\'"')
        if gemini_key and gemini_key != "******":
            os.environ["GEMINI_API_KEY"] = gemini_key

        alibaba_key = self.alibaba_api_key.text().strip().strip('\'"')
        if alibaba_key and alibaba_key != "******":
            os.environ["ALIBABA_API_KEY"] = alibaba_key

        # Vertex AI settings (shared by Gemini and Claude)
        vertex_project = (
            self.vertex_gemini_project.text().strip()
            or self.vertex_claude_project.text().strip()
        )
        if vertex_project:
            os.environ["VERTEX_PROJECT_ID"] = vertex_project

        # Since stacked widget is aligned with combo box index, we can check model directly
        selected = self.get_selected_model()
        vertex_location = ""
        if selected == "VertexGemini":
            vertex_location = self.vertex_gemini_location.text().strip() or "global"
        elif selected == "VertexClaude":
            vertex_location = self.vertex_claude_location.text().strip() or "global"
        
        if vertex_location:
            os.environ["VERTEX_LOCATION"] = vertex_location
