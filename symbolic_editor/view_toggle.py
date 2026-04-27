# -*- coding: utf-8 -*-
"""
Segmented workspace toggle for switching between symbolic, KLayout, and both.
"""

from PySide6.QtCore import Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget


class SegmentedToggle(QWidget):
    """Small segmented control for workspace view modes."""

    mode_changed = Signal(str)

    _MODES = (
        ("symbolic", "Symbolic"),
        ("klayout", "KLayout"),
        ("both", "Both"),
    )

    def __init__(self, parent=None, accent="#5aa9e6", variant="panel"):
        super().__init__(parent)
        self._accent = accent
        self._variant = variant
        self._current_mode = None
        self._buttons = {}
        self._init_ui()

    def _init_ui(self):
        if self._variant == "toolbar":
            self.setStyleSheet(
                f"""
                SegmentedToggle {{
                    background-color: #1a1d23;
                    border: 1px solid #303642;
                    border-radius: 6px;
                }}
                QPushButton {{
                    background-color: transparent;
                    border: none;
                    border-radius: 4px;
                    color: #9aa4b2;
                    padding: 3px 11px;
                    font-family: 'Segoe UI';
                    font-size: 8.5pt;
                    font-weight: 600;
                    min-height: 18px;
                }}
                QPushButton:hover {{
                    background-color: #252a33;
                    color: #e3e8f0;
                }}
                QPushButton:checked {{
                    background-color: #2b3b4d;
                    color: #ffffff;
                    border: 1px solid {self._accent};
                }}
                """
            )
            self.setFixedHeight(28)
        else:
            self.setStyleSheet(
                f"""
                SegmentedToggle {{
                    background-color: #1a1d23;
                    border: 1px solid #303642;
                    border-radius: 10px;
                }}
                QPushButton {{
                    background-color: transparent;
                    border: none;
                    border-radius: 7px;
                    color: #9aa4b2;
                    padding: 5px 14px;
                    font-family: 'Segoe UI';
                    font-size: 9pt;
                    font-weight: 600;
                    min-height: 24px;
                }}
                QPushButton:hover {{
                    background-color: #252a33;
                    color: #e3e8f0;
                }}
                QPushButton:checked {{
                    background-color: {self._accent};
                    color: #ffffff;
                }}
                """
            )
            self.setFixedHeight(34)

        layout = QHBoxLayout(self)
        if self._variant == "toolbar":
            layout.setContentsMargins(2, 2, 2, 2)
            layout.setSpacing(2)
        else:
            layout.setContentsMargins(3, 3, 3, 3)
            layout.setSpacing(3)

        for mode, label in self._MODES:
            button = QPushButton(label)
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, m=mode: self.set_mode(m))
            layout.addWidget(button)
            self._buttons[mode] = button

        self.set_mode("both", emit=False)

    def current_mode(self):
        return self._current_mode

    def set_mode(self, mode, emit=True):
        if mode not in self._buttons or mode == self._current_mode:
            return
        self._current_mode = mode
        for key, button in self._buttons.items():
            button.setChecked(key == mode)
        if emit:
            self.mode_changed.emit(mode)

    def setup_shortcuts(self, parent_widget):
        for key_sequence, mode in (
            ("Ctrl+1", "symbolic"),
            ("Ctrl+2", "klayout"),
            ("Ctrl+3", "both"),
        ):
            shortcut = QShortcut(QKeySequence(key_sequence), parent_widget)
            shortcut.activated.connect(lambda m=mode: self.set_mode(m))
