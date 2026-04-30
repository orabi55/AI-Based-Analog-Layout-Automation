# -*- coding: utf-8 -*-
"""
Properties panel for the symbolic editor.

Shows read-only details for the currently selected device or hierarchy block.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class PropertiesPanel(QWidget):
    """Inspector for devices and hierarchy blocks. Supports editing for certain fields."""

    property_changed = Signal(str, str, str)  # dev_id, field_key, new_value

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_dev_id = None
        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame()
        header.setFixedHeight(46)
        header.setStyleSheet(
            "background-color: #171d28; border-bottom: 1px solid #2d3548;"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 0, 14, 0)

        title = QLabel("Properties")
        title.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        title.setStyleSheet("color: #d8e2ee;")
        header_layout.addWidget(title)
        header_layout.addStretch()

        root.addWidget(header)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            """
            QScrollArea {
                background-color: #10151d;
                border: none;
            }
            QScrollBar:vertical {
                width: 8px;
                background: transparent;
            }
            QScrollBar::handle:vertical {
                background: #2d3548;
                border-radius: 4px;
                min-height: 28px;
            }
            QScrollBar::handle:vertical:hover {
                background: #3a4a60;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            """
        )
        root.addWidget(self._scroll, 1)

        self._content = QWidget()
        self._content.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(14, 14, 14, 14)
        self._content_layout.setSpacing(12)
        self._scroll.setWidget(self._content)

        self.clear_properties()

    def _group_style(self):
        return """
            QGroupBox {
                color: #94a6bb;
                border: 1px solid #232c3b;
                border-radius: 10px;
                margin-top: 12px;
                padding: 12px 10px 10px 10px;
                font-family: 'Segoe UI';
                font-size: 9pt;
                font-weight: 600;
                background-color: #111821;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """

    def _label_style(self):
        return "color: #8192a7; font-family: 'Segoe UI'; font-size: 9pt;"

    def _field_style(self):
        return """
            QLineEdit {
                background-color: #1a2330;
                color: #e4ebf3;
                border: 1px solid #2d3a4c;
                border-radius: 6px;
                padding: 5px 8px;
                font-family: 'Segoe UI';
                font-size: 9pt;
            }
        """

    def _clear_content(self):
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _make_label(self, text):
        label = QLabel(text)
        label.setStyleSheet(self._label_style())
        return label

    def _make_field(self, key, value, readonly=False):
        field = QLineEdit("" if value is None else str(value))
        field.setReadOnly(readonly)
        field.setStyleSheet(self._field_style())
        if not readonly:
            # Emit change on return pressed or loss of focus
            field.returnPressed.connect(lambda: self._on_field_edited(key, field.text()))
            field.editingFinished.connect(lambda: self._on_field_edited(key, field.text()))
        return field

    def _on_field_edited(self, key, new_text):
        if self._current_dev_id:
            self.property_changed.emit(self._current_dev_id, key, new_text)

    def _add_group(self, title, rows):
        if not rows:
            return
        group = QGroupBox(title)
        group.setStyleSheet(self._group_style())
        form = QFormLayout(group)
        form.setContentsMargins(8, 10, 8, 8)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        for label, val_info in rows:
            # val_info can be (value) or (key, value, is_readonly)
            if isinstance(val_info, tuple):
                key, val, readonly = val_info
            else:
                key, val, readonly = label, val_info, True

            if val in (None, "") and readonly:
                continue
            
            form.addRow(self._make_label(label), self._make_field(key, val, readonly))
        if form.rowCount() > 0:
            self._content_layout.addWidget(group)
        else:
            group.deleteLater()

    def clear_properties(self, message="Select a device or block to inspect its properties."):
        self._clear_content()
        placeholder = QLabel(message)
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setWordWrap(True)
        placeholder.setStyleSheet(
            "color: #5d6f84; font-family: 'Segoe UI'; font-size: 10pt; padding: 36px 18px;"
        )
        self._content_layout.addStretch()
        self._content_layout.addWidget(placeholder)
        self._content_layout.addStretch()

    def show_device_properties(self, dev_id, node_data, terminal_nets=None, block_data=None):
        self._clear_content()

        if not node_data:
            self.clear_properties(f"No properties found for {dev_id}.")
            return

        electrical = node_data.get("electrical", {})
        geometry = node_data.get("geometry", {})
        nets = (terminal_nets or {}).get(dev_id, {})
        block_info = node_data.get("block", {}) or {}

        self._current_dev_id = dev_id
        self._add_group(
            "Device",
            [
                ("Name", (None, dev_id, True)),
                ("Type", (None, str(node_data.get("type", "")).upper(), True)),
                ("Dummy", (None, "Yes" if node_data.get("is_dummy") else "No", True)),
            ],
        )
        
        # Build Electrical Group dynamically based on device type
        dtype = str(node_data.get("type", "nmos")).lower()
        elec_rows = []
        
        if dtype in ("res", "cap"):
            # It's a passive: Show 'Value'
            val = electrical.get("value")
            if val is None: val = electrical.get("rval")
            if val is None: val = electrical.get("cval")
            # If still None, it's at default but we should show the field for editing
            from config.design_rules import PASSIVE_RES_DEFAULT, PASSIVE_CAP_DEFAULT
            if val is None:
                val = PASSIVE_RES_DEFAULT if dtype == "res" else PASSIVE_CAP_DEFAULT
            
            elec_rows.append(("Value", ("value", val, False)))
            # Also show multiplier for passives if applicable
            elec_rows.append(("Multiplier (m)", ("m", electrical.get("m", 1.0), False)))
        else:
            # It's a transistor: Show standard L, W, nf, nfin, m
            elec_rows.extend([
                ("Length (L)", ("l", electrical.get("l"), False)),
                ("Width (W)", ("w", electrical.get("w"), False)),
                ("Fingers (nf)", ("nf", electrical.get("nf"), False)),
                ("Fins (nfin)", ("nfin", electrical.get("nfin"), False)),
                ("Multiplier (m)", ("m", electrical.get("m"), False)),
            ])

        self._add_group("Electrical", elec_rows)
        self._add_group(
            "Hierarchy",
            [
                ("Parent Device", electrical.get("parent")),
                ("Multiplier Index", electrical.get("multiplier_index")),
                ("Finger Index", electrical.get("finger_index")),
                ("Array Index", electrical.get("array_index")),
                ("Block Instance", block_info.get("instance")),
                ("Subckt", block_info.get("subckt") or (block_data or {}).get("subckt")),
            ],
        )
        self._add_group(
            "Connections",
            [
                ("Gate", nets.get("G")),
                ("Drain", nets.get("D")),
                ("Source", nets.get("S")),
            ],
        )
        self._add_group(
            "Geometry",
            [
                ("X", geometry.get("x")),
                ("Y", geometry.get("y")),
                ("Width", geometry.get("width")),
                ("Height", geometry.get("height")),
                ("Orientation", geometry.get("orientation")),
            ],
        )
        self._content_layout.addStretch()

    def show_block_properties(self, block_id, block_data):
        self._clear_content()

        if not block_data:
            self.clear_properties(f"No properties found for block {block_id}.")
            return

        devices = block_data.get("devices", []) or []
        self._add_group(
            "Hierarchy Block",
            [
                ("Instance", block_id),
                ("Subckt", block_data.get("subckt")),
                ("Device Count", len(devices)),
            ],
        )

        preview = ", ".join(devices[:8])
        if len(devices) > 8:
            preview += ", ..."
        self._add_group(
            "Members",
            [
                ("Devices", preview or "-"),
            ],
        )
        self._content_layout.addStretch()
