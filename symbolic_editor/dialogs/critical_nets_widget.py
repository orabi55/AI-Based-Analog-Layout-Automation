"""
critical_nets_widget.py
=======================
A collapsible QGroupBox for selecting "Parasitic-Critical Nets" in the
AI Initial Placement dialog.

The widget exposes:

  set_available_nets(nets)  — populate the scrollable checkbox list
  get_critical_nets()       — return {"priority": str, "nets": [str, ...]}
  set_critical_nets(d)      — restore previously saved selection

It is embedded inside ai_model_dialog._CollapsibleCriticalNets
and is NOT a child of PlacementGoalsWidget.
"""

from __future__ import annotations

from typing import List

from PySide6.QtWidgets import (
    QGroupBox, QVBoxLayout, QHBoxLayout, QLabel,
    QRadioButton, QButtonGroup, QCheckBox, QLineEdit,
    QPushButton, QScrollArea, QWidget, QSizePolicy,
)
from PySide6.QtCore import Qt

_PRIORITY_LEVELS = ("Low", "Medium", "High")
_MAX_NETS = 10

# ---------------------------------------------------------------------------
# _PriorityRow  (small internal widget)
# ---------------------------------------------------------------------------

class _PriorityRow(QWidget):
    """Three radio buttons for Low / Medium / High."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        lbl = QLabel("Priority:")
        lbl.setStyleSheet("color: #c8d0dc; font-size: 9pt; font-weight: bold;")
        layout.addWidget(lbl)

        self._bg = QButtonGroup(self)
        self._radios: dict[str, QRadioButton] = {}
        for lvl in _PRIORITY_LEVELS:
            rb = QRadioButton(lvl)
            rb.setStyleSheet("color: #c8d0dc; font-size: 9pt; spacing: 6px;")
            self._bg.addButton(rb)
            self._radios[lvl] = rb
            layout.addWidget(rb)
        self._radios["Low"].setChecked(True)
        layout.addStretch()

    def get_priority(self) -> str:
        for lvl, rb in self._radios.items():
            if rb.isChecked():
                return lvl
        return "Low"

    def set_priority(self, priority: str) -> None:
        rb = self._radios.get(priority)
        if rb:
            rb.setChecked(True)


# ---------------------------------------------------------------------------
# CriticalNetsWidget  (public class)
# ---------------------------------------------------------------------------

class CriticalNetsWidget(QGroupBox):
    """
    QGroupBox titled '⚡ Critical Signal Nets'.

    Provides:
      • 3 priority radios (Low / Medium / High; default Low)
      • Scrollable QCheckBox column populated by set_available_nets()
      • '+ add custom net' QLineEdit
      • 'Selected: N/10' summary label
      • 10-net cap — further checkboxes disabled once 10 are ticked
    """

    def __init__(self, parent=None):
        super().__init__("⚡ Critical Signal Nets", parent)
        self.setStyleSheet("""
            QGroupBox {
                border: 1px solid #4a6fa5;
                border-radius: 6px;
                margin-top: 10px;
                padding-top: 14px;
                color: #88c0d0;
                font-weight: bold;
                font-size: 10pt;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 10px;
                padding: 0 4px;
            }
        """)

        outer = QVBoxLayout(self)
        outer.setSpacing(8)
        outer.setContentsMargins(12, 14, 12, 10)

        # ── Description ───────────────────────────────────────────────────
        desc = QLabel(
            "Nets selected here will be clustered tightly in the layout "
            "to minimise parasitic capacitance and resistance."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #6a7a90; font-size: 8pt;")
        outer.addWidget(desc)

        # ── Priority row ──────────────────────────────────────────────────
        self._priority_row = _PriorityRow()
        outer.addWidget(self._priority_row)

        # ── Scrollable net list ───────────────────────────────────────────
        scroll_container = QWidget()
        scroll_container.setStyleSheet("background: transparent;")
        self._checks_layout = QVBoxLayout(scroll_container)
        self._checks_layout.setSpacing(3)
        self._checks_layout.setContentsMargins(0, 0, 0, 0)
        self._checks_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(scroll_container)
        scroll.setFixedHeight(160)
        scroll.setStyleSheet("""
            QScrollArea {
                background: #1a1f2b;
                border: 1px solid #2d3548;
                border-radius: 4px;
            }
        """)
        outer.addWidget(scroll)

        self._checkboxes: list[QCheckBox] = []
        self._scroll_container = scroll_container

        # ── "Selected N/10" label ────────────────────────────────────────
        self._summary_lbl = QLabel("Selected: 0/10")
        self._summary_lbl.setStyleSheet("color: #8ab4d4; font-size: 8pt;")
        outer.addWidget(self._summary_lbl)

        # ── Custom net input ─────────────────────────────────────────────
        add_row = QHBoxLayout()
        self._custom_edit = QLineEdit()
        self._custom_edit.setPlaceholderText("Add custom net name…")
        self._custom_edit.setStyleSheet(
            "background: #232a38; color: #c8d0dc; "
            "border: 1px solid #2d3548; border-radius: 4px; "
            "padding: 4px 8px; font-size: 9pt;"
        )
        add_row.addWidget(self._custom_edit, 1)

        add_btn = QPushButton("Add")
        add_btn.setFixedWidth(48)
        add_btn.setStyleSheet(
            "background: #2a3345; color: #c8d0dc; "
            "border: 1px solid #3d5066; border-radius: 4px; "
            "padding: 4px 8px; font-size: 9pt;"
        )
        add_btn.clicked.connect(self._on_add_custom)
        add_row.addWidget(add_btn)
        outer.addLayout(add_row)

        self._custom_edit.returnPressed.connect(self._on_add_custom)

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _on_check_toggled(self, checked: bool) -> None:
        self._refresh_cap()

    def _refresh_cap(self) -> None:
        selected = sum(1 for cb in self._checkboxes if cb.isChecked())
        self._summary_lbl.setText(f"Selected: {selected}/{_MAX_NETS}")
        cap_reached = selected >= _MAX_NETS
        for cb in self._checkboxes:
            if not cb.isChecked():
                cb.setEnabled(not cap_reached)

    def _add_checkbox(self, net_name: str, checked: bool = False) -> QCheckBox:
        cb = QCheckBox(net_name)
        cb.setChecked(checked)
        cb.setStyleSheet("color: #c8d0dc; font-size: 9pt; spacing: 6px;")
        cb.toggled.connect(self._on_check_toggled)
        # Insert before the trailing stretch (last item)
        count = self._checks_layout.count()
        self._checks_layout.insertWidget(count - 1, cb)
        self._checkboxes.append(cb)
        self._refresh_cap()
        return cb

    def _on_add_custom(self) -> None:
        net = self._custom_edit.text().strip()
        if not net:
            return
        # Check for duplicates (case-insensitive)
        existing_names = {cb.text().lower() for cb in self._checkboxes}
        if net.lower() in existing_names:
            self._custom_edit.clear()
            return
        self._add_checkbox(net, checked=True)
        self._custom_edit.clear()

    # ── Public API ───────────────────────────────────────────────────────────

    def set_available_nets(self, nets: List[str]) -> None:
        """Populate the checkbox list from design net names.

        Preserves any previously checked custom nets that are not in the
        supplied list.  Completely replaces previously auto-populated nets.
        """
        # Remember which nets were user-checked (custom ones)
        custom_checked = {
            cb.text() for cb in self._checkboxes if cb.isChecked()
        }
        # Remove all existing checkboxes from layout and list
        for cb in self._checkboxes:
            self._checks_layout.removeWidget(cb)
            cb.deleteLater()
        self._checkboxes.clear()

        # Re-populate from design nets
        for net in nets:
            self._add_checkbox(net, checked=(net in custom_checked))

        # Re-add any custom nets not already in the list
        for custom_net in sorted(custom_checked):
            if custom_net not in {cb.text() for cb in self._checkboxes}:
                self._add_checkbox(custom_net, checked=True)

        self._refresh_cap()

    def get_critical_nets(self) -> dict:
        """Return the current widget state.

        Returns:
            ``{"priority": "Low"|"Medium"|"High", "nets": [str, ...]}``
        """
        priority = self._priority_row.get_priority()
        nets = [cb.text() for cb in self._checkboxes if cb.isChecked()]
        return {"priority": priority, "nets": nets}

    def set_critical_nets(self, d: dict) -> None:
        """Restore widget state from a previously obtained dict."""
        if not isinstance(d, dict):
            return
        self._priority_row.set_priority(d.get("priority", "Low"))
        nets_to_check = set(d.get("nets") or [])
        for cb in self._checkboxes:
            cb.setChecked(cb.text() in nets_to_check)
        # Add any saved nets not currently in the list
        existing = {cb.text() for cb in self._checkboxes}
        for net in sorted(nets_to_check):
            if net not in existing:
                self._add_checkbox(net, checked=True)
        self._refresh_cap()
