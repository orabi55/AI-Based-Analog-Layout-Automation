import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QScrollArea, QWidget, 
    QButtonGroup, QFrame, QCheckBox, QFormLayout, QLineEdit,
    QComboBox, QGroupBox, QHBoxLayout, QPushButton
)
from PySide6.QtCore import Qt

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
            "Fast & efficient. Free tier: 15 req/min.",
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
            "Llama 3.3 70B. Free tier: 30 req/min. \u26a1",
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
            "Requires Ollama installed & running locally.",
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
