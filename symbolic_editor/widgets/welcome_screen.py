# -*- coding: utf-8 -*-
"""
Welcome Screen — the landing page shown when no layout tabs are open.

Provides:
  • Quick-access buttons: Import Netlist + Layout, Open Saved JSON
  • Grid of example circuit tiles scraped from the ``examples/`` directory
  • Modern dark-theme aesthetic with hover animations
"""

import os
import glob

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QGridLayout,
    QScrollArea,
    QSizePolicy,
    QGraphicsDropShadowEffect,
)
from PySide6.QtCore import Qt, Signal, QSize, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QFont, QColor, QIcon, QPainter, QLinearGradient


# ── Utility: circuit emoji based on name ──────────────────────────────
_CIRCUIT_ICONS = {
    "miller_ota": "🔊",
    "nand": "🔲",
    "comparator": "⚖️",
    "current_mirror": "🪞",
    "rc": "📐",
    "std_cell": "🧱",
    "tx_driver": "📡",
    "xor": "⊕",
}


def _icon_for(name: str) -> str:
    key = name.lower().replace(" ", "_")
    return _CIRCUIT_ICONS.get(key, "📦")


# ── Styled Action Card ───────────────────────────────────────────────
class _ActionCard(QPushButton):
    """A large, hoverable action card button."""

    def __init__(self, icon_text: str, title: str, subtitle: str, accent: str, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(110)
        self.setMinimumWidth(260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._accent = accent

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(16)

        icon = QLabel(icon_text)
        icon.setFont(QFont("Segoe UI Emoji", 28))
        icon.setFixedSize(56, 56)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(
            f"background: {accent}22; border-radius: 14px; border: none;"
        )
        layout.addWidget(icon)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        lbl_title = QLabel(title)
        lbl_title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        lbl_title.setStyleSheet("color: #e0e8f0; border: none; background: transparent;")
        text_col.addWidget(lbl_title)

        lbl_sub = QLabel(subtitle)
        lbl_sub.setFont(QFont("Segoe UI", 10))
        lbl_sub.setStyleSheet("color: #7b8a9c; border: none; background: transparent;")
        lbl_sub.setWordWrap(True)
        text_col.addWidget(lbl_sub)
        layout.addLayout(text_col, 1)

        self.setStyleSheet(f"""
            QPushButton {{
                background-color: #1a2230;
                border: 1px solid #2d3548;
                border-radius: 12px;
                text-align: left;
            }}
            QPushButton:hover {{
                background-color: #1e2a3a;
                border-color: {accent};
            }}
            QPushButton:pressed {{
                background-color: #243040;
                border-color: {accent};
            }}
        """)

        # Subtle glow shadow on hover
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(0)
        shadow.setOffset(0, 0)
        shadow.setColor(QColor(accent))
        self.setGraphicsEffect(shadow)
        self._shadow = shadow

    def enterEvent(self, event):
        self._shadow.setBlurRadius(24)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._shadow.setBlurRadius(0)
        super().leaveEvent(event)


# ── Example Tile ─────────────────────────────────────────────────────
class _ExampleTile(QPushButton):
    """A clickable tile representing one example circuit."""

    example_clicked = Signal(str, str)  # sp_path, oas_path

    def __init__(self, display_name: str, sp_path: str, oas_path: str, parent=None):
        super().__init__(parent)
        self._sp = sp_path
        self._oas = oas_path
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(180, 100)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = QLabel(_icon_for(display_name))
        icon.setFont(QFont("Segoe UI Emoji", 22))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("border: none; background: transparent;")
        layout.addWidget(icon)

        name = QLabel(display_name)
        name.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name.setStyleSheet("color: #c8d0dc; border: none; background: transparent;")
        name.setWordWrap(True)
        layout.addWidget(name)

        has_layout = "✓ layout" if oas_path else "netlist only"
        info = QLabel(has_layout)
        info.setFont(QFont("Segoe UI", 8))
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        color = "#4a90d9" if oas_path else "#556677"
        info.setStyleSheet(f"color: {color}; border: none; background: transparent;")
        layout.addWidget(info)

        self.setStyleSheet("""
            QPushButton {
                background-color: #161c28;
                border: 1px solid #2d3548;
                border-radius: 10px;
            }
            QPushButton:hover {
                background-color: #1e2a3a;
                border-color: #4a90d9;
            }
            QPushButton:pressed {
                background-color: #243040;
            }
        """)

        self.clicked.connect(lambda: self.example_clicked.emit(self._sp, self._oas))


# ── Welcome Screen ───────────────────────────────────────────────────
class WelcomeScreen(QWidget):
    """Full-screen welcome landing page.

    Signals:
        open_file_requested() — user clicked "Open Saved JSON"
        import_requested()    — user clicked "Import Netlist + Layout"
        example_requested(str, str) — user clicked an example tile (sp, oas)
    """

    open_file_requested = Signal()
    import_requested = Signal()
    example_requested = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    # ─────────────────────────────────────────────────────────────────
    def _init_ui(self):
        self.setStyleSheet("background-color: #0e1219;")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # Scroll area so the content stays usable on small screens
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollBar:vertical { width: 6px; background: transparent; }"
            "QScrollBar::handle:vertical { background: #2d3548; border-radius: 3px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }"
        )

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        root = QVBoxLayout(content)
        root.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        root.setContentsMargins(40, 60, 40, 40)
        root.setSpacing(0)

        # ── Logo / Title ──────────────────────────────────────────
        title = QLabel("⚡ Symbolic Layout Editor")
        title.setFont(QFont("Segoe UI", 26, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #e0e8f0; margin-bottom: 4px;")
        root.addWidget(title)

        subtitle = QLabel("AI-Powered Analog Layout Automation")
        subtitle.setFont(QFont("Segoe UI", 12))
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #556677; margin-bottom: 32px;")
        root.addWidget(subtitle)

        # ── Action Cards ──────────────────────────────────────────
        cards_row = QHBoxLayout()
        cards_row.setSpacing(20)
        cards_row.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card_import = _ActionCard(
            "📂", "Import Netlist + Layout",
            "Parse a SPICE netlist (.sp) and optional OAS/GDS layout file",
            "#4a90d9",
        )
        card_import.clicked.connect(self.import_requested.emit)
        cards_row.addWidget(card_import)

        card_open = _ActionCard(
            "📄", "Open Saved Layout",
            "Load a previously saved .json placement file",
            "#43b581",
        )
        card_open.clicked.connect(self.open_file_requested.emit)
        cards_row.addWidget(card_open)

        root.addLayout(cards_row)

        # ── Separator ─────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(
            "background-color: #2d3548; max-height: 1px; margin: 32px 60px;"
        )
        root.addWidget(sep)

        # ── Quick Start Examples ──────────────────────────────────
        section_title = QLabel("Quick Start Examples")
        section_title.setFont(QFont("Segoe UI", 14, QFont.Weight.DemiBold))
        section_title.setStyleSheet("color: #8899aa; margin-bottom: 16px;")
        section_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(section_title)

        self._examples_grid = QGridLayout()
        self._examples_grid.setSpacing(14)
        self._examples_grid.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._populate_examples()
        root.addLayout(self._examples_grid)

        root.addStretch(1)

        # ── Footer ────────────────────────────────────────────────
        footer = QLabel("Tip: Use Ctrl+I to import, Ctrl+O to open, Ctrl+T for a new tab")
        footer.setFont(QFont("Segoe UI", 9))
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.setStyleSheet("color: #3d4f60; margin-top: 24px;")
        root.addWidget(footer)

        scroll.setWidget(content)
        outer.addWidget(scroll)

    # ─────────────────────────────────────────────────────────────────
    def _populate_examples(self):
        """Scan the examples/ directory and create tiles."""
        base_dir = os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
        )
        examples_dir = os.path.join(base_dir, "examples")
        if not os.path.isdir(examples_dir):
            lbl = QLabel("No examples directory found.")
            lbl.setStyleSheet("color: #556677;")
            self._examples_grid.addWidget(lbl, 0, 0)
            return

        col, row = 0, 0
        max_cols = 4
        for example_name in sorted(os.listdir(examples_dir)):
            ex_path = os.path.join(examples_dir, example_name)
            if not os.path.isdir(ex_path):
                continue
            sp_files = glob.glob(os.path.join(ex_path, "*.sp"))
            if not sp_files:
                continue

            sp_file = sp_files[-1]
            oas_file = sp_file.rsplit(".", 1)[0] + ".oas"
            if not os.path.exists(oas_file):
                oas_file = ""

            display = example_name.replace("_", " ").title()
            tile = _ExampleTile(display, sp_file, oas_file)
            tile.example_clicked.connect(self.example_requested.emit)
            self._examples_grid.addWidget(tile, row, col)

            col += 1
            if col >= max_cols:
                col = 0
                row += 1
