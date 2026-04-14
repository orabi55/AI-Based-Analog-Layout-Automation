# -*- coding: utf-8 -*-
"""
Properties Panel — right-side panel showing selected device/block properties.
Shares space with AI chat panel via tabs.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QLineEdit, QComboBox, QPushButton, QScrollArea,
    QSizePolicy, QGroupBox, QFormLayout, QTextEdit,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor


class PropertiesPanel(QWidget):
    """Shows properties for the selected device or floorplan block."""

    status_changed = Signal(str, str)  # (block_id, new_status)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_device = None
        self._current_block = None
        self._mode = "device"  # "device" or "block"
        self._init_ui()

    def _init_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Header
        header = QFrame()
        header.setFixedHeight(40)
        header.setStyleSheet(
            "background-color: #1a1f2b; border-bottom: 1px solid #2d3548;"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 0, 14, 0)

        title = QLabel("PROPERTIES")
        title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        title.setStyleSheet("color: #8899aa; letter-spacing: 1px;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        outer.addWidget(header)

        # Scrollable content area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("""
            QScrollArea {
                background-color: #0f1318;
                border: none;
            }
            QScrollBar:vertical {
                width: 6px; background: transparent;
            }
            QScrollBar::handle:vertical {
                background: #2d3548; border-radius: 3px; min-height: 24px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(14, 14, 14, 14)
        self._content_layout.setSpacing(12)
        scroll.setWidget(self._content)
        outer.addWidget(scroll, 1)

        # Placeholder
        self._placeholder = QLabel("Select a device or block\nto view its properties")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet(
            "color: #556677; font-family: 'Segoe UI'; font-size: 10pt; padding: 40px;"
        )
        self._content_layout.addWidget(self._placeholder)
        self._content_layout.addStretch()

    def _group_style(self):
        return """
            QGroupBox {
                color: #8899aa;
                border: 1px solid #232a38;
                border-radius: 8px;
                margin-top: 14px;
                padding: 14px 10px 10px 10px;
                font-family: 'Segoe UI';
                font-size: 9pt;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
        """

    def _label_style(self):
        return "color: #8899aa; font-family: 'Segoe UI'; font-size: 9pt;"

    def _field_style(self):
        return """
            QLineEdit, QTextEdit {
                background-color: #1a1f2b;
                color: #e0e8f0;
                border: 1px solid #2d3548;
                border-radius: 5px;
                padding: 5px 8px;
                font-family: 'Segoe UI';
                font-size: 9pt;
            }
            QLineEdit:focus, QTextEdit:focus {
                border-color: #4a9eff;
            }
        """

    def _combo_style(self):
        return """
            QComboBox {
                background-color: #1a1f2b;
                color: #e0e8f0;
                border: 1px solid #2d3548;
                border-radius: 5px;
                padding: 5px 8px;
                font-family: 'Segoe UI';
                font-size: 9pt;
            }
            QComboBox:focus { border-color: #4a9eff; }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox QAbstractItemView {
                background-color: #1e2636;
                color: #e0e8f0;
                border: 1px solid #3d5066;
                selection-background-color: #4a90d9;
            }
        """

    def _btn_style(self):
        return """
            QPushButton {
                background-color: #232a38;
                color: #c8d0dc;
                border: 1px solid #2d3548;
                border-radius: 5px;
                padding: 5px 14px;
                font-family: 'Segoe UI';
                font-size: 9pt;
            }
            QPushButton:hover {
                background-color: #2d3f54;
                border-color: #4a9eff;
            }
            QPushButton:checked, QPushButton[selected="true"] {
                background-color: #4a9eff;
                color: #ffffff;
                border-color: #4a9eff;
            }
        """

    def _clear_content(self):
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    # ── Device Properties ──────────────────────────────────────
    def show_device_properties(self, dev_id, node_data, terminal_nets=None):
        """Show properties for a device."""
        self._clear_content()
        self._mode = "device"
        self._current_device = dev_id

        if not node_data:
            self._placeholder = QLabel(f"No data for {dev_id}")
            self._placeholder.setStyleSheet(
                "color: #556677; font-family: 'Segoe UI'; font-size: 10pt; padding: 20px;"
            )
            self._content_layout.addWidget(self._placeholder)
            return

        # ── Name & Type ──
        info_group = QGroupBox("Device Info")
        info_group.setStyleSheet(self._group_style())
        info_form = QFormLayout(info_group)
        info_form.setSpacing(8)

        name_edit = QLineEdit(dev_id)
        name_edit.setReadOnly(True)
        name_edit.setStyleSheet(self._field_style())
        info_form.addRow(self._make_label("Name"), name_edit)

        dev_type = str(node_data.get("type", "")).upper()
        type_edit = QLineEdit(dev_type)
        type_edit.setReadOnly(True)
        type_edit.setStyleSheet(self._field_style())
        info_form.addRow(self._make_label("Type"), type_edit)

        self._content_layout.addWidget(info_group)

        # ── Electrical Parameters ──
        elec = node_data.get("electrical", {})
        if elec:
            param_group = QGroupBox("Parameters")
            param_group.setStyleSheet(self._group_style())
            param_form = QFormLayout(param_group)
            param_form.setSpacing(8)

            for key in ["l", "w", "nf", "nfin"]:
                val = elec.get(key)
                if val is not None:
                    label_text = {"l": "Length (L)", "w": "Width (W)",
                                  "nf": "Fingers (nf)", "nfin": "Fins (nfin)"}.get(key, key)
                    val_edit = QLineEdit(str(val))
                    val_edit.setReadOnly(True)
                    val_edit.setStyleSheet(self._field_style())
                    param_form.addRow(self._make_label(label_text), val_edit)

            self._content_layout.addWidget(param_group)

        # ── Net Connections ──
        nets = terminal_nets or {}
        dev_nets = nets.get(dev_id, {})
        if dev_nets:
            net_group = QGroupBox("Net Connections")
            net_group.setStyleSheet(self._group_style())
            net_form = QFormLayout(net_group)
            net_form.setSpacing(8)

            for term, net_name in sorted(dev_nets.items()):
                label_map = {"S": "Source (S)", "G": "Gate (G)", "D": "Drain (D)",
                             "1": "Pin 1", "2": "Pin 2"}
                net_edit = QLineEdit(net_name or "—")
                net_edit.setReadOnly(True)
                net_edit.setStyleSheet(self._field_style())
                net_form.addRow(self._make_label(label_map.get(term, term)), net_edit)

            self._content_layout.addWidget(net_group)

        # ── Geometry ──
        geom = node_data.get("geometry", {})
        if geom:
            geom_group = QGroupBox("Geometry")
            geom_group.setStyleSheet(self._group_style())
            geom_form = QFormLayout(geom_group)
            geom_form.setSpacing(8)

            for key in ["x", "y", "width", "height", "orientation"]:
                val = geom.get(key)
                if val is not None:
                    val_edit = QLineEdit(str(val))
                    val_edit.setReadOnly(True)
                    val_edit.setStyleSheet(self._field_style())
                    geom_form.addRow(self._make_label(key.capitalize()), val_edit)

            self._content_layout.addWidget(geom_group)

        self._content_layout.addStretch()

    # ── Block Properties (Floorplan Mode) ──────────────────────
    def show_block_properties(self, block_id, block_data):
        """Show properties for a floorplan block (Astrus-style)."""
        self._clear_content()
        self._mode = "block"
        self._current_block = block_id

        if not block_data:
            return

        # ── Name ──
        name_group = QGroupBox("Block Info")
        name_group.setStyleSheet(self._group_style())
        name_form = QFormLayout(name_group)
        name_form.setSpacing(8)

        name_edit = QLineEdit(block_data.get("name", block_id))
        name_edit.setStyleSheet(self._field_style())
        name_form.addRow(self._make_label("Name"), name_edit)

        owner_edit = QLineEdit(block_data.get("owner", "—"))
        owner_edit.setStyleSheet(self._field_style())
        name_form.addRow(self._make_label("Circuit Design Owner"), owner_edit)

        desc_edit = QTextEdit()
        desc_edit.setPlaceholderText("Enter a brief description of this block")
        desc_edit.setText(block_data.get("description", ""))
        desc_edit.setMaximumHeight(60)
        desc_edit.setStyleSheet(self._field_style())
        name_form.addRow(self._make_label("Description"), desc_edit)

        self._content_layout.addWidget(name_group)

        # ── Preview (Layout selection) ──
        preview_group = QGroupBox("Preview")
        preview_group.setStyleSheet(self._group_style())
        pv_layout = QVBoxLayout(preview_group)

        layouts = block_data.get("layouts", [{"name": "Layout 1"}, {"name": "Layout 2"}])
        for i, lay in enumerate(layouts):
            row = QHBoxLayout()
            lbl = QLabel(lay.get("name", f"Layout {i+1}"))
            lbl.setStyleSheet(self._label_style())

            btn = QPushButton("✓ Selected" if i == 0 else "Select")
            btn.setStyleSheet(self._btn_style())
            btn.setFixedWidth(90)
            if i == 0:
                btn.setProperty("selected", True)
                btn.setStyleSheet(btn.styleSheet())  # refresh

            row.addWidget(lbl)
            row.addStretch()
            row.addWidget(btn)
            pv_layout.addLayout(row)

        self._content_layout.addWidget(preview_group)

        # ── Dimensions ──
        dim_group = QGroupBox("Dimensions")
        dim_group.setStyleSheet(self._group_style())
        dim_form = QFormLayout(dim_group)
        dim_form.setSpacing(8)

        w_edit = QLineEdit(str(block_data.get("width", "---")))
        w_edit.setStyleSheet(self._field_style())
        dim_form.addRow(self._make_label("Width"), w_edit)

        h_edit = QLineEdit(str(block_data.get("height", "---")))
        h_edit.setStyleSheet(self._field_style())
        dim_form.addRow(self._make_label("Height"), h_edit)

        self._content_layout.addWidget(dim_group)

        # ── Max Metal ──
        metal_group = QGroupBox("Max Metal")
        metal_group.setStyleSheet(self._group_style())
        metal_form = QFormLayout(metal_group)

        metal_combo = QComboBox()
        metal_combo.addItems(["---", "M1", "M2", "M3", "M4", "M5"])
        metal_combo.setStyleSheet(self._combo_style())
        metal_form.addRow(self._make_label("Layer"), metal_combo)

        self._content_layout.addWidget(metal_group)
        self._content_layout.addStretch()

    def clear_properties(self):
        """Reset panel to placeholder."""
        self._clear_content()
        self._placeholder = QLabel("Select a device or block\nto view its properties")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet(
            "color: #556677; font-family: 'Segoe UI'; font-size: 10pt; padding: 40px;"
        )
        self._content_layout.addWidget(self._placeholder)
        self._content_layout.addStretch()

    def _make_label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(self._label_style())
        return lbl
