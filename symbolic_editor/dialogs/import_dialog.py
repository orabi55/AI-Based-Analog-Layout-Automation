from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QGroupBox, QFormLayout, 
    QHBoxLayout, QLineEdit, QPushButton, QCheckBox, 
    QFileDialog, QMessageBox
)
from PySide6.QtCore import Qt

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
