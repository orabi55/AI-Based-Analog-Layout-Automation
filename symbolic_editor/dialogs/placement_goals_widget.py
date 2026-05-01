"""
Placement Goals Widget
======================
A QGroupBox embedded inside AIModelSelectionDialog.
The user picks Low / Medium / High for three priorities and optionally
enters a max-area constraint.

Each radio button has a tooltip explaining the exact technical effect.
A dynamic info-label below the radio row updates to show what the
currently selected level will do.

Public API
----------
get_goals() -> dict
    {
        "area_priority":     "Low" | "Medium" | "High",
        "matching_priority": "Low" | "Medium" | "High",
        "symmetry_priority": "Low" | "Medium" | "High",
        "max_area_um2":      float | None,
    }
"""

from PySide6.QtWidgets import (
    QGroupBox, QVBoxLayout, QHBoxLayout, QLabel,
    QRadioButton, QButtonGroup, QLineEdit, QWidget, QFrame,
)
from PySide6.QtCore import Qt


_PRIORITY_LEVELS = ["Low", "Medium", "High"]

# Map priority label -> numeric weight for quality_metrics
PRIORITY_WEIGHTS = {"Low": 1, "Medium": 5, "High": 10}

# ------------------------------------------------------------------
# Per-key, per-level: (tooltip on radio, dynamic info line)
# ------------------------------------------------------------------
_INFO = {
    "area": {
        "icon":  "📐 Area",
        "Low":   (
            "Spread out – area is not a priority.",
            "Each device type placed in fewer, wider rows. "
            "Leaves more headroom for placement quality. Lower utilization."
        ),
        "Medium": (
            "Balanced area vs. quality (default).",
            "Row width computed as geometric mean of PMOS/NMOS footprints. "
            "Good balance of compactness and quality."
        ),
        "High":  (
            "Maximise utilisation – smallest bounding box.",
            "Row width forced to the narrower type's footprint so the wider "
            "type splits into matching rows. Minimises dummy fill. "
            "Highest space efficiency."
        ),
    },
    "matching": {
        "icon":  "🔁 Matching",
        "Low":   (
            "ABBA for differential pairs and current mirrors only.",
            "Only VINP/VINN input diff pairs and diode-connected current mirrors "
            "receive interdigitation. CLK-symmetric, cross-coupled, and load pairs "
            "are placed individually. Use when area is critical."
        ),
        "Medium": (
            "ABBA for all standard matching tiers.",
            "Includes differential pairs, current mirrors, CLK-symmetric precharge pairs, "
            "cross-coupled latches, and load pairs. Good balance of area and matching."
        ),
        "High":  (
            "Strict ABBA / common-centroid for every matchable pair.",
            "All detected matched pairs use ABBA interdigitation or "
            "2D common-centroid matrix layout. "
            "Best electrical matching – may increase total layout area."
        ),
    },
    "symmetry": {
        "icon":  "↔ Symmetry",
        "Low":   (
            "Symmetry enforcer is disabled.",
            "The deterministic mirror-symmetry stage is SKIPPED entirely. "
            "Devices are placed by the AI without forced reflection about "
            "the layout centre-line. Saves area, faster placement."
        ),
        "Medium": (
            "Mirror symmetry applied if the topology calls for it.",
            "The symmetry enforcer runs when a [SYMMETRY] block is detected "
            "(e.g. differential pairs, comparators). Otherwise it is skipped."
        ),
        "High":  (
            "Mandatory mirror symmetry for all matched groups.",
            "The symmetry enforcer always runs and forces every matched group "
            "to be reflected about the layout centre-line. "
            "Best for comparators and diff-pair OTAs. Area may grow."
        ),
    },
}


def _make_radio_group(parent, key, default):
    """Return (QButtonGroup, {level: QRadioButton}, dynamic_info_label)."""
    bg = QButtonGroup(parent)
    radios = {}
    for lvl in _PRIORITY_LEVELS:
        rb = QRadioButton(lvl)
        rb.setStyleSheet("color: #c8d0dc; font-size: 9pt; spacing: 6px;")
        tooltip, _ = _INFO[key][lvl]
        rb.setToolTip(tooltip)
        if lvl == default:
            rb.setChecked(True)
        bg.addButton(rb)
        radios[lvl] = rb

    # Dynamic label that shows the current selection's explanation
    info_lbl = QLabel()
    info_lbl.setWordWrap(True)
    info_lbl.setStyleSheet(
        "color: #8ab4d4; font-size: 8pt; margin-left: 118px; "
        "background: transparent;"
    )

    def _update(lbl=info_lbl, k=key):
        for lv, r in radios.items():
            if r.isChecked():
                _, desc = _INFO[k][lv]
                lbl.setText(desc)
                return
    _update()

    for rb in radios.values():
        rb.toggled.connect(lambda _checked, fn=_update: fn())

    return bg, radios, info_lbl


class PlacementGoalsWidget(QGroupBox):
    """Embeddable panel for placement goal selection."""

    def __init__(self, parent=None):
        super().__init__("Placement Goals", parent)
        self.setStyleSheet("""
            QGroupBox {
                border: 1px solid #3d5066;
                border-radius: 6px;
                margin-top: 10px;
                padding-top: 14px;
                color: #c8d0dc;
                font-weight: bold;
                font-size: 10pt;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 10px;
                padding: 0 4px;
            }
            QToolTip {
                background-color: #1e2636;
                color: #c8d0dc;
                border: 1px solid #3d5066;
                font-size: 9pt;
                padding: 4px 6px;
            }
        """)

        outer = QVBoxLayout(self)
        outer.setSpacing(8)
        outer.setContentsMargins(12, 14, 12, 10)

        # ── Header hint ───────────────────────────────────────────────────
        header_hint = QLabel(
            "Choose what matters most for this design. "
            "Hover over Low / Medium / High for details."
        )
        header_hint.setStyleSheet("color: #6a7a90; font-size: 8pt; margin-bottom: 4px;")
        header_hint.setWordWrap(True)
        outer.addWidget(header_hint)

        # ── Separator ─────────────────────────────────────────────────────
        sep0 = QFrame(); sep0.setFrameShape(QFrame.Shape.HLine)
        sep0.setStyleSheet("color: #2d3548;"); outer.addWidget(sep0)

        # ── Priority rows ─────────────────────────────────────────────────
        self._groups = {}   # key -> (QButtonGroup, {label: QRadioButton})
        defaults = {"area": "Medium", "matching": "High", "symmetry": "High"}

        for key in ("matching", "symmetry", "area"):
            info = _INFO[key]
            row_widget = QWidget()
            row_layout = QVBoxLayout(row_widget)
            row_layout.setSpacing(3)
            row_layout.setContentsMargins(0, 0, 0, 4)

            # Icon label + radio buttons
            top_row = QHBoxLayout()
            top_row.setSpacing(12)
            lbl = QLabel(info["icon"])
            lbl.setFixedWidth(110)
            lbl.setStyleSheet("color: #e0e8f0; font-size: 9pt; font-weight: bold;")
            top_row.addWidget(lbl)

            bg, radios, info_lbl = _make_radio_group(row_widget, key, defaults[key])
            self._groups[key] = (bg, radios)

            for rb in radios.values():
                top_row.addWidget(rb)
            top_row.addStretch()
            row_layout.addLayout(top_row)
            row_layout.addWidget(info_lbl)

            outer.addWidget(row_widget)

        # ── Separator ─────────────────────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #3d5066;"); outer.addWidget(sep)

        # ── Max area constraint ───────────────────────────────────────────
        area_row = QHBoxLayout()
        area_lbl = QLabel("Max Area (µm²):")
        area_lbl.setStyleSheet("color: #c8d0dc; font-size: 9pt;")
        self._area_edit = QLineEdit()
        self._area_edit.setPlaceholderText("e.g. 15.0  (leave blank = no limit)")
        self._area_edit.setToolTip(
            "Optional hard area limit.\n"
            "If the placed layout exceeds this value you will be warned\n"
            "and given the chance to enter a new limit."
        )
        self._area_edit.setStyleSheet(
            "background-color: #232a38; color: #c8d0dc; "
            "border: 1px solid #2d3548; border-radius: 4px; "
            "padding: 4px 8px; font-size: 9pt;"
        )
        self._area_edit.setFixedWidth(180)
        area_row.addWidget(area_lbl)
        area_row.addWidget(self._area_edit)
        area_row.addStretch()
        outer.addLayout(area_row)

        area_note = QLabel(
            "If the layout exceeds this limit, a warning is shown and "
            "you can adjust the value without re-running placement."
        )
        area_note.setStyleSheet("color: #6a7a90; font-size: 8pt;")
        area_note.setWordWrap(True)
        outer.addWidget(area_note)

    # ── Public API ────────────────────────────────────────────────────────

    def get_goals(self) -> dict:
        result = {}
        for key, (bg, radios) in self._groups.items():
            for lvl, rb in radios.items():
                if rb.isChecked():
                    result[f"{key}_priority"] = lvl
                    break
            else:
                result[f"{key}_priority"] = "Medium"

        raw = self._area_edit.text().strip()
        try:
            result["max_area_um2"] = float(raw) if raw else None
        except ValueError:
            result["max_area_um2"] = None

        return result

    def set_goals(self, goals: dict):
        """Restore widget state from a previously obtained goals dict."""
        for key in ("area", "matching", "symmetry"):
            lvl = goals.get(f"{key}_priority", "Medium")
            _, radios = self._groups[key]
            if lvl in radios:
                radios[lvl].setChecked(True)
        max_a = goals.get("max_area_um2")
        self._area_edit.setText(str(max_a) if max_a is not None else "")
