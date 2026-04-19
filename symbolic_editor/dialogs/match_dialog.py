from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QGroupBox, QRadioButton, 
    QTextEdit, QButtonGroup, QHBoxLayout, QPushButton
)
from PySide6.QtCore import Qt

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
