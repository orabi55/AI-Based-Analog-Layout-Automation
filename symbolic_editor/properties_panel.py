# -*- coding: utf-8 -*-
"""
Properties panel for the symbolic editor.

Shows read-only details for the currently selected device or hierarchy block.
"""

from PySide6.QtCore import Qt
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
    """Read-only inspector for devices and hierarchy blocks."""

    def __init__(self, parent=None):
        super().__init__(parent)
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

    def _make_field(self, value):
        field = QLineEdit("" if value is None else str(value))
        field.setReadOnly(True)
        field.setStyleSheet(self._field_style())
        return field

    def _add_group(self, title, rows):
        if not rows:
            return
        group = QGroupBox(title)
        group.setStyleSheet(self._group_style())
        form = QFormLayout(group)
        form.setContentsMargins(8, 10, 8, 8)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        for label, value in rows:
            if value in (None, ""):
                continue
            form.addRow(self._make_label(label), self._make_field(value))
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

        self._add_group(
            "Device",
            [
                ("Name", dev_id),
                ("Type", str(node_data.get("type", "")).upper()),
                ("Dummy", "Yes" if node_data.get("is_dummy") else "No"),
            ],
        )
        self._add_group(
            "Electrical",
            [
                ("Length (L)", electrical.get("l")),
                ("Width (W)", electrical.get("w")),
                ("Fingers (nf)", electrical.get("nf")),
                ("Fins (nfin)", electrical.get("nfin")),
                ("Multiplier (m)", electrical.get("m")),
            ],
        )
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
