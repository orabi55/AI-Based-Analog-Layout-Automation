# -*- coding: utf-8 -*-
"""
View Mode Toggle — Segmented pill-button for switching between
Symbolic Editor, KLayout, and Both views.

Emits ``mode_changed(str)`` with one of: "layout", "klayout", "both".
Persists last-selected mode via QSettings.
"""

from PySide6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QGraphicsOpacityEffect
from PySide6.QtCore import Qt, Signal, QSettings, QPropertyAnimation, QEasingCurve, QSize
from PySide6.QtGui import QFont, QKeySequence, QShortcut


class SegmentedToggle(QWidget):
    """A pill-shaped segmented toggle with three modes."""

    mode_changed = Signal(str)  # "layout", "klayout", "both"

    _MODES = [
        ("layout", "Symbolic Editor"),
        ("klayout", "KLayout"),
        ("both", "Both"),
    ]

    # ── Styling ────────────────────────────────────────────────────
    _CONTAINER_STYLE = """
        SegmentedToggle {
            background-color: #23272f;
            border: 1px solid #4b5563;
            border-radius: 14px;
            padding: 2px;
        }
    """

    _BTN_STYLE = """
        QPushButton {{
            background-color: transparent;
            color: #a0a7b3;
            border: none;
            border-radius: 12px;
            padding: 4px 16px;
            font-family: 'Segoe UI';
            font-size: 10pt;
            font-weight: 600;
            min-width: 70px;
            min-height: 24px;
        }}
        QPushButton:hover {{
            color: #e5e7eb;
            background-color: rgba(255, 255, 255, 0.06);
        }}
        QPushButton:checked {{
            background-color: {accent};
            color: #ffffff;
        }}
    """

    def __init__(self, parent=None, accent_color="#6b7280"):
        super().__init__(parent)
        self._accent = accent_color
        self._current_mode = "layout"

        self.setStyleSheet(self._CONTAINER_STYLE)
        self.setFixedHeight(32)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(2)

        self._buttons: dict[str, QPushButton] = {}
        for mode_key, mode_label in self._MODES:
            btn = QPushButton(mode_label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(self._BTN_STYLE.format(accent=self._accent))
            btn.clicked.connect(lambda checked, m=mode_key: self._on_btn_clicked(m))
            layout.addWidget(btn)
            self._buttons[mode_key] = btn

        # Default to Symbolic Editor
        self._buttons["layout"].setChecked(True)

        # Restore from QSettings
        self._restore_mode()

    # ── Public API ─────────────────────────────────────────────────
    def current_mode(self) -> str:
        return self._current_mode

    def set_mode(self, mode: str, emit=True):
        """Programmatically set the active mode."""
        if mode not in self._buttons:
            return
        if mode == self._current_mode:
            return

        self._current_mode = mode
        for key, btn in self._buttons.items():
            btn.setChecked(key == mode)

        # Persist
        settings = QSettings("SymbolicEditor", "ViewMode")
        settings.setValue("last_mode", mode)

        if emit:
            self.mode_changed.emit(mode)

    def setup_shortcuts(self, parent_widget):
        """Register Ctrl+1/2/3 keyboard shortcuts on the given parent."""
        shortcuts = [
            ("Ctrl+1", "layout"),
            ("Ctrl+2", "klayout"),
            ("Ctrl+3", "both"),
        ]
        for key_seq, mode in shortcuts:
            sc = QShortcut(QKeySequence(key_seq), parent_widget)
            sc.activated.connect(lambda m=mode: self.set_mode(m))

    # ── Private ────────────────────────────────────────────────────
    def _on_btn_clicked(self, mode: str):
        self.set_mode(mode)

    def _restore_mode(self):
        settings = QSettings("SymbolicEditor", "ViewMode")
        saved = settings.value("last_mode", "layout")
        if saved == "floorplan":
            saved = "layout"
        if saved in self._buttons:
            self.set_mode(saved, emit=False)


class ViewTransitionHelper:
    """Utility to manage fade transitions when switching views.

    Usage:
        helper = ViewTransitionHelper()
        helper.fade_switch(old_widget, new_widget, duration_ms=150)
    """

    @staticmethod
    def fade_switch(outgoing, incoming, duration_ms=150, on_finished=None):
        """Fade out ``outgoing`` and fade in ``incoming``."""
        if outgoing is incoming:
            if on_finished:
                on_finished()
            return

        # Ensure both widgets have opacity effects
        out_effect = outgoing.graphicsEffect()
        if not isinstance(out_effect, QGraphicsOpacityEffect):
            out_effect = QGraphicsOpacityEffect(outgoing)
            outgoing.setGraphicsEffect(out_effect)

        in_effect = incoming.graphicsEffect()
        if not isinstance(in_effect, QGraphicsOpacityEffect):
            in_effect = QGraphicsOpacityEffect(incoming)
            incoming.setGraphicsEffect(in_effect)

        # Fade out
        out_anim = QPropertyAnimation(out_effect, b"opacity")
        out_anim.setDuration(duration_ms // 2)
        out_anim.setStartValue(1.0)
        out_anim.setEndValue(0.0)
        out_anim.setEasingCurve(QEasingCurve.Type.InQuad)

        # Fade in
        in_anim = QPropertyAnimation(in_effect, b"opacity")
        in_anim.setDuration(duration_ms // 2)
        in_anim.setStartValue(0.0)
        in_anim.setEndValue(1.0)
        in_anim.setEasingCurve(QEasingCurve.Type.OutQuad)

        def _after_fade_out():
            outgoing.setVisible(False)
            incoming.setVisible(True)
            in_anim.start()

        out_anim.finished.connect(_after_fade_out)

        if on_finished:
            in_anim.finished.connect(on_finished)

        # Keep references alive during animation
        outgoing._view_anim = out_anim
        incoming._view_anim = in_anim

        out_anim.start()
