# -*- coding: utf-8 -*-
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
    QLineEdit, QPushButton, QFrame
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor

class PassiveValueDialog(QDialog):
    """Dialog to edit the electrical value of a passive component."""

    def __init__(self, dev_id, dev_type, current_value, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Edit {dev_id}")
        self.setMinimumWidth(300)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        
        self.dev_id = dev_id
        self.dev_type = dev_type
        self.result_value = None
        
        self._init_ui(current_value)

    def _init_ui(self, current_value):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        self.setStyleSheet("""
            QDialog {
                background-color: #1a1f2b;
                color: #e0e8f0;
                font-family: 'Segoe UI';
            }
            QLabel {
                color: #9aa7b7;
                font-size: 10pt;
            }
            QLineEdit {
                background-color: #12161f;
                color: #ffffff;
                border: 1px solid #3a4452;
                border-radius: 6px;
                padding: 8px;
                font-size: 11pt;
            }
            QLineEdit:focus {
                border-color: #4a90d9;
            }
            QPushButton {
                background-color: #2d3a4c;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 10pt;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3b4a60;
            }
            QPushButton#applyBtn {
                background-color: #4a90d9;
            }
            QPushButton#applyBtn:hover {
                background-color: #5da3ec;
            }
        """)

        # Header
        header = QLabel(f"Edit {self.dev_type.upper()} Value for {self.dev_id}")
        header.setStyleSheet("color: #ffffff; font-size: 12pt; font-weight: bold;")
        layout.addWidget(header)

        # Input field
        unit = "Ω" if self.dev_type == "res" else "F"
        label = QLabel(f"Electrical Value ({unit}):")
        layout.addWidget(label)
        
        self.value_input = QLineEdit()
        val_str = f"{current_value:g}" if current_value is not None else ""
        self.value_input.setText(val_str)
        self.value_input.setPlaceholderText(f"e.g. 10k, 2.2u, 470p")
        layout.addWidget(self.value_input)
        
        hint = QLabel("Supports SI prefixes: f, p, n, u, m, k, M, g")
        hint.setStyleSheet("font-size: 8pt; color: #6a7a8a;")
        layout.addWidget(hint)

        layout.addSpacing(10)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        
        apply_btn = QPushButton("Apply")
        apply_btn.setObjectName("applyBtn")
        apply_btn.clicked.connect(self._on_apply)
        btn_layout.addWidget(apply_btn)
        
        layout.addLayout(btn_layout)

    def _on_apply(self):
        val_text = self.value_input.text().strip()
        if not val_text:
            self.result_value = None
            self.accept()
            return

        try:
            self.result_value = self._parse_spice_value(val_text)
            self.accept()
        except ValueError:
            self.value_input.setStyleSheet("border-color: #e57373;")
            
    def _parse_spice_value(self, value: str) -> float:
        value = value.strip()
        if not value: return 0.0

        # Scale factors
        # Note: We handle 'm' and 'M' differently per user request.
        scale = {
            'f': 1e-15, 'p': 1e-12, 'n': 1e-9, 'u': 1e-6,
            'm': 1e-3,  'k': 1e3,   'M': 1e6,  'G': 1e9,
            'g': 1e9,   'meg': 1e6, 'MEG': 1e6
        }
        
        # Check for multi-char suffixes first (like 'meg')
        for suffix in ['meg', 'MEG', 'Meg']:
            if value.lower().endswith('meg'):
                num_part = value[:-3].strip()
                return float(num_part) * 1e6

        # Check single-char suffixes
        last_char = value[-1]
        if last_char in scale:
            num_part = value[:-1].strip()
            if not num_part: return 0.0
            return float(num_part) * scale[last_char]
        
        # No suffix, just float
        return float(value)
