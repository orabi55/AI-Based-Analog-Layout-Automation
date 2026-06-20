# -*- coding: utf-8 -*-
"""
ActionsPanel — Design Panel Actions Tab

Lists all layout automation features as grouped, one-click buttons
in the left sidebar. Emits action_requested(str) so the parent tab
can route the command to the correct handler.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QLineEdit,
)


# ─────────────────────────────────────────────────────────────────────────────
#  Styling constants
# ─────────────────────────────────────────────────────────────────────────────
_PANEL_BG   = "#070c0f"
_SECTION_BG = "#071014"
_HEADER_FG  = "#9daab0"
_ACCENT     = "#00e5ff"
_BTN_BG     = "#0a1418"
_BTN_BORDER = "#1b3038"
_BTN_FG     = "#d7e4e8"
_BTN_HOVER  = "#0d1f26"
_BTN_HOVER_BORDER = "#00e5ff"

_SECTION_STYLE = f"""
    QFrame {{
        background-color: {_SECTION_BG};
        border: 1px solid #142127;
        border-radius: 8px;
    }}
"""

_HEADER_STYLE = f"""
    color: {_ACCENT};
    font-family: 'Segoe UI';
    font-size: 8pt;
    font-weight: 700;
    letter-spacing: 1px;
    padding: 0 2px;
"""

_BTN_STYLE = f"""
    QPushButton {{
        background-color: {_BTN_BG};
        color: {_BTN_FG};
        border: 1px solid {_BTN_BORDER};
        border-radius: 6px;
        padding: 7px 10px;
        text-align: left;
        font-family: 'Segoe UI';
        font-size: 9pt;
    }}
    QPushButton:hover {{
        background-color: {_BTN_HOVER};
        border-color: {_BTN_HOVER_BORDER};
        color: #ffffff;
    }}
    QPushButton:pressed {{
        background-color: #0f2a35;
        border-color: {_ACCENT};
    }}
"""

_SEARCH_STYLE = """
    QLineEdit {
        background-color: #05090b;
        color: #d7e4e8;
        border: 1px solid #1b3038;
        border-radius: 6px;
        padding: 6px 10px;
        font-family: 'Segoe UI';
        font-size: 9pt;
    }
    QLineEdit:focus {
        border-color: #00e5ff;
    }
    QLineEdit::placeholder {
        color: #4a6070;
    }
"""


class ActionsPanel(QWidget):
    """Sidebar panel listing all layout tool features as clickable buttons.

    Emits ``action_requested(action_key: str)`` when a button is clicked.
    """

    action_requested = Signal(str)

    # ──────────────────────────────────────────────────────────────────────
    # Feature registry — (label, action_key, tooltip)
    # ──────────────────────────────────────────────────────────────────────
    FEATURES = [
        # Physical Cell Insertion
        ("Physical Cells", [
            ("⚡  Auto Insert Taps",
             "auto_taps",
             "Place VDD ntap above topmost PMOS row\nand GND ptap below bottommost NMOS row"),
            ("🪨  Insert Edge Dummies",
             "edge_dummies",
             "Add identical dummy transistors at both left\nand right edges of every active device row"),
            ("🛡️  Insert Guard Ring",
             "guard_ring",
             "Place an isolation guard ring around the NMOS group"),
        ]),
        # Symmetry & Visualization
        ("Symmetry & Visualization", [
            ("📏  Draw Symmetry Axis",
             "symmetry_axis",
             "Auto-compute layout center and draw a\nbold neon cyan dashed symmetry axis"),
            ("💡  Highlight VDD Net",
             "highlight_vdd",
             "Highlight all terminal labels connected to net VDD"),
            ("🧹  Clear All Overlays",
             "clear_overlays",
             "Remove all highlights, colors, and symmetry axes\nfrom the canvas"),
        ]),
        # Device Operations
        ("Device Operations", [
            ("🔀  Match Selected Devices",
             "match_devices",
             "Apply common-centroid or interdigitation matching\nto the selected devices (select ≥2 first)"),
            ("🔓  Unlock Match Group",
             "unlock_match",
             "Release selected devices from their matched group"),
            ("↔   Flip Horizontal",
             "flip_h",
             "Mirror selected devices horizontally (Ctrl+H)"),
            ("↕   Flip Vertical",
             "flip_v",
             "Mirror selected devices vertically (Ctrl+J)"),
            ("🔄  Swap Devices",
             "swap",
             "Swap positions of two selected devices (Ctrl+Shift+X)"),
        ]),
        # Layout Optimization
        ("Layout Optimization", [
            ("🚀  Run AI Placement",
             "ai_placement",
             "Run the AI engine to optimally place all devices"),
            ("🔍  DRC Spacing Check",
             "drc_check",
             "Run legalizer to detect and fix overlapping devices"),
            ("🧬  RAG Style Migration",
             "rag_migration",
             "Apply common-centroid ABBA interdigitation\nusing the RAG matching engine"),
        ]),
        # Export & Reports
        ("Export & Reports", [
            ("📤  Export JSON",
             "export_json",
             "Save current placement to a JSON file"),
            ("📤  Export to OAS",
             "export_oas",
             "Export layout to KLayout OAS format"),
            ("📤  Deploy to Custom Compiler",
             "deploy_cc",
             "Export TCL and upload placement to\nSynopsys Custom Compiler"),
        ]),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_buttons: list[tuple[str, str, QPushButton]] = []
        self._init_ui()

    def _init_ui(self):
        self.setMinimumWidth(220)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header bar ──────────────────────────────────────────────────
        header = QFrame()
        header.setFixedHeight(46)
        header.setStyleSheet(
            f"background-color: #070c0f; border-bottom: 1px solid #142127;"
        )
        hdr_layout = QHBoxLayout(header)
        hdr_layout.setContentsMargins(14, 0, 14, 0)
        title = QLabel("Actions")
        title.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        title.setStyleSheet("color: #e4ecef;")
        hdr_layout.addWidget(title)
        hdr_layout.addStretch()
        root.addWidget(header)

        # ── Search bar ──────────────────────────────────────────────────
        search_frame = QFrame()
        search_frame.setStyleSheet(f"background-color: {_PANEL_BG}; border: none;")
        search_layout = QHBoxLayout(search_frame)
        search_layout.setContentsMargins(10, 8, 10, 6)
        self._search = QLineEdit()
        self._search.setPlaceholderText("🔍  Search actions…")
        self._search.setStyleSheet(_SEARCH_STYLE)
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._filter_buttons)
        search_layout.addWidget(self._search)
        root.addWidget(search_frame)

        # ── Scrollable content ──────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"""
            QScrollArea {{
                background-color: {_PANEL_BG};
                border: none;
            }}
            QScrollBar:vertical {{
                width: 6px;
                background: transparent;
            }}
            QScrollBar::handle:vertical {{
                background: #1b3038;
                border-radius: 3px;
                min-height: 24px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: #00a9bc;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)
        root.addWidget(scroll, 1)

        content = QWidget()
        content.setStyleSheet(f"background-color: {_PANEL_BG};")
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(10, 10, 10, 16)
        self._content_layout.setSpacing(8)
        scroll.setWidget(content)

        # Build sections
        for group_name, features in self.FEATURES:
            self._add_section(group_name, features)

        self._content_layout.addStretch()

    def _add_section(self, title: str, features: list):
        """Add a titled group of action buttons."""
        # Section header label
        hdr = QLabel(title.upper())
        hdr.setStyleSheet(_HEADER_STYLE)
        hdr.setContentsMargins(2, 6, 2, 2)
        self._content_layout.addWidget(hdr)

        # Section container
        section = QFrame()
        section.setStyleSheet(_SECTION_STYLE)
        sec_layout = QVBoxLayout(section)
        sec_layout.setContentsMargins(6, 6, 6, 6)
        sec_layout.setSpacing(4)

        for label, action_key, tooltip in features:
            btn = QPushButton(label)
            btn.setStyleSheet(_BTN_STYLE)
            btn.setToolTip(tooltip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setFixedHeight(34)
            # Capture action_key properly
            btn.clicked.connect(lambda checked=False, k=action_key: self.action_requested.emit(k))
            sec_layout.addWidget(btn)
            self._all_buttons.append((label.lower(), action_key, btn))

        self._content_layout.addWidget(section)

    def _filter_buttons(self, text: str):
        """Show/hide buttons based on search text."""
        q = text.strip().lower()
        for label_lower, action_key, btn in self._all_buttons:
            visible = not q or q in label_lower or q in action_key.lower()
            btn.setVisible(visible)
