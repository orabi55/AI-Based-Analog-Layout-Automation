# -*- coding: utf-8 -*-
"""
AI Placement Progress Dialog — a rich, modern progress window that replaces
the generic loading overlay when the user runs AI Placement.

Features:
- Vertical stage stepper (left panel) with glowing active/completed states
- Live terminal-style log viewer (right panel) with syntax highlighting
- Elapsed time counter in the footer
- Cancel / Close buttons
- Auto-scroll toggle for the log viewer
- Completion / Error states with summary display
"""

from __future__ import annotations

import os
import re
import time

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QWidget,
    QFrame, QPushButton, QPlainTextEdit, QSizePolicy,
    QGraphicsDropShadowEffect, QScrollArea, QSpacerItem,
)
from PySide6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve,
    Signal, Property, QSize,
)
from PySide6.QtGui import (
    QFont, QColor, QPainter, QBrush, QPen,
    QLinearGradient, QRadialGradient, QTextCharFormat,
    QSyntaxHighlighter, QTextDocument,
)


# ═══════════════════════════════════════════════════════════════════
#  Syntax Highlighter for the log terminal
# ═══════════════════════════════════════════════════════════════════
class _LogHighlighter(QSyntaxHighlighter):
    """Colorises log tags, stage banners, scores, and status markers."""

    def __init__(self, document: QTextDocument):
        super().__init__(document)
        self._rules: list[tuple[re.Pattern, QTextCharFormat]] = []

        # ── Stage headers (═══ / ─── lines) ───────────────────────
        fmt_banner = QTextCharFormat()
        fmt_banner.setForeground(QColor("#5DADE2"))
        fmt_banner.setFontWeight(QFont.Weight.Bold)
        self._rules.append((re.compile(r"^[═─]{4,}.*$"), fmt_banner))
        self._rules.append((re.compile(r"^\s*STAGE\s+\d.*$", re.IGNORECASE), fmt_banner))
        self._rules.append((re.compile(r"^[+|].*[+|]$"), fmt_banner))

        # ── Tags like [TOPO], [STRATEGY], [PLACEMENT], etc ────────
        fmt_tag = QTextCharFormat()
        fmt_tag.setForeground(QColor("#58D68D"))
        fmt_tag.setFontWeight(QFont.Weight.Bold)
        self._rules.append((re.compile(r"\[(TOPO|STRATEGY|PLACEMENT|SYMM|CMD|GRAPH)\]"), fmt_tag))

        # ── Error / warning ───────────────────────────────────────
        fmt_err = QTextCharFormat()
        fmt_err.setForeground(QColor("#E74C3C"))
        fmt_err.setFontWeight(QFont.Weight.Bold)
        self._rules.append((re.compile(r"\[ERROR\]|\[!!\]|FAIL|Error|error", re.IGNORECASE), fmt_err))

        fmt_warn = QTextCharFormat()
        fmt_warn.setForeground(QColor("#F5B041"))
        self._rules.append((re.compile(r"\[WARN\]|WARNING|⚠", re.IGNORECASE), fmt_warn))

        # ── OK / PASS / ✓ ────────────────────────────────────────
        fmt_ok = QTextCharFormat()
        fmt_ok.setForeground(QColor("#2ECC71"))
        fmt_ok.setFontWeight(QFont.Weight.Bold)
        self._rules.append((re.compile(r"\[OK\]|PASS|✓|✔|pass"), fmt_ok))

        # ── Step markers (Step 3a, Step 3b, …) ────────────────────
        fmt_step = QTextCharFormat()
        fmt_step.setForeground(QColor("#BB8FCE"))
        fmt_step.setFontWeight(QFont.Weight.Bold)
        self._rules.append((re.compile(r"Step\s+\d+[a-z]?[.:]?", re.IGNORECASE), fmt_step))

        # ── Scores / grades (A+, 100.0%, etc) ─────────────────────
        fmt_score = QTextCharFormat()
        fmt_score.setForeground(QColor("#F7DC6F"))
        fmt_score.setFontWeight(QFont.Weight.Bold)
        self._rules.append((re.compile(r"\b\d{1,3}\.\d%|\b[A-F][+]?\b"), fmt_score))

        # ── Timestamps (HH:MM:SS) ─────────────────────────────────
        fmt_ts = QTextCharFormat()
        fmt_ts.setForeground(QColor("#85929E"))
        self._rules.append((re.compile(r"\b\d{2}:\d{2}:\d{2}\b"), fmt_ts))

        # ── LLM Response blocks ───────────────────────────────────
        fmt_resp = QTextCharFormat()
        fmt_resp.setForeground(QColor("#85C1E9"))
        self._rules.append((re.compile(r"Response:"), fmt_resp))

        # ── Metric labels ─────────────────────────────────────────
        fmt_metric = QTextCharFormat()
        fmt_metric.setForeground(QColor("#AED6F1"))
        self._rules.append((re.compile(
            r"(Layout Size|Total Area|Utilization|DRC Status|PMOS/NMOS|Devices|Time Total|HPWL|COMPOSITE|Model|Abutment)"
        ), fmt_metric))

    def highlightBlock(self, text: str) -> None:
        for pattern, fmt in self._rules:
            for match in pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), fmt)


# ═══════════════════════════════════════════════════════════════════
#  Animated Pulse Dot — small ring that pulses on the active stage
# ═══════════════════════════════════════════════════════════════════
class _PulseDot(QWidget):
    """Animated circular indicator: idle=grey, active=pulsing blue, done=green, error=red."""

    _COLORS = {
        "idle":   QColor("#3d5066"),
        "active": QColor("#4a90d9"),
        "done":   QColor("#2ECC71"),
        "error":  QColor("#E74C3C"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(22, 22)
        self._state = "idle"
        self._pulse_opacity = 1.0
        self._anim = QPropertyAnimation(self, b"pulseOpacity")
        self._anim.setDuration(1200)
        self._anim.setStartValue(0.35)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._anim.setLoopCount(-1)  # infinite

    # ── Qt property for animation ─────────────────────────────────
    def _get_pulse(self) -> float:
        return self._pulse_opacity

    def _set_pulse(self, v: float) -> None:
        self._pulse_opacity = v
        self.update()

    pulseOpacity = Property(float, _get_pulse, _set_pulse)

    def set_state(self, state: str) -> None:
        self._state = state
        if state == "active":
            self._anim.start()
        else:
            self._anim.stop()
            self._pulse_opacity = 1.0
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self._COLORS.get(self._state, self._COLORS["idle"])

        # Outer glow for active state
        if self._state == "active":
            glow = QColor(color)
            glow.setAlphaF(0.25 * self._pulse_opacity)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(glow))
            p.drawEllipse(1, 1, 20, 20)

        # Inner filled circle
        inner_color = QColor(color)
        if self._state == "active":
            inner_color.setAlphaF(self._pulse_opacity)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(inner_color))
        p.drawEllipse(5, 5, 12, 12)

        # Checkmark for done
        if self._state == "done":
            p.setPen(QPen(QColor("#ffffff"), 1.8))
            p.drawLine(7, 11, 10, 14)
            p.drawLine(10, 14, 15, 8)

        p.end()


# ═══════════════════════════════════════════════════════════════════
#  Stage Row — one row in the stepper (dot + label + status)
# ═══════════════════════════════════════════════════════════════════
class _StageRow(QWidget):
    """A single stage in the vertical stepper."""

    def __init__(self, stage_num: int, title: str, parent=None):
        super().__init__(parent)
        self.stage_num = stage_num
        self.title_text = title
        self._state = "idle"  # idle | active | done | error

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(10)

        self.dot = _PulseDot(self)
        layout.addWidget(self.dot)

        info = QVBoxLayout()
        info.setSpacing(0)

        self.lbl_title = QLabel(title)
        self.lbl_title.setStyleSheet("color: #5d6d7e; font-size: 10pt; font-weight: bold; background: transparent;")
        info.addWidget(self.lbl_title)

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: #85929E; font-size: 8pt; background: transparent;")
        self.lbl_status.setVisible(False)
        info.addWidget(self.lbl_status)

        layout.addLayout(info)
        layout.addStretch()

    def set_state(self, state: str, status_text: str = "") -> None:
        self._state = state
        self.dot.set_state(state)

        if state == "idle":
            self.lbl_title.setStyleSheet("color: #5d6d7e; font-size: 10pt; font-weight: bold; background: transparent;")
        elif state == "active":
            self.lbl_title.setStyleSheet("color: #4a90d9; font-size: 10pt; font-weight: bold; background: transparent;")
        elif state == "done":
            self.lbl_title.setStyleSheet("color: #2ECC71; font-size: 10pt; font-weight: bold; background: transparent;")
        elif state == "error":
            self.lbl_title.setStyleSheet("color: #E74C3C; font-size: 10pt; font-weight: bold; background: transparent;")

        if status_text:
            self.lbl_status.setText(status_text)
            self.lbl_status.setVisible(True)
        elif state == "active":
            self.lbl_status.setText("Running…")
            self.lbl_status.setVisible(True)
        elif state == "idle":
            self.lbl_status.setVisible(False)


# ═══════════════════════════════════════════════════════════════════
#  Animated Spinner Widget for the header
# ═══════════════════════════════════════════════════════════════════
class _AISpinner(QWidget):
    """Custom rotating gradient ring spinner."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 40)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._rotate)
        self._running = False

    def start(self) -> None:
        self._running = True
        self._timer.start(30)

    def stop(self) -> None:
        self._running = False
        self._timer.stop()
        self.update()

    def _rotate(self) -> None:
        self._angle = (self._angle + 6) % 360
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        if not self._running:
            # Draw a checkmark
            p.setPen(QPen(QColor("#2ECC71"), 3))
            p.drawLine(10, 20, 17, 27)
            p.drawLine(17, 27, 30, 12)
            p.end()
            return

        # Rotating gradient arc
        center = self.rect().center()
        grad = QRadialGradient(center.x(), center.y(), 18)
        grad.setColorAt(0.0, QColor(74, 144, 217, 0))
        grad.setColorAt(0.7, QColor(74, 144, 217, 200))
        grad.setColorAt(1.0, QColor(93, 173, 226, 255))

        pen = QPen(QBrush(grad), 3.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.translate(center)
        p.rotate(self._angle)
        p.translate(-center)
        p.drawArc(5, 5, 30, 30, 0, 270 * 16)
        p.end()


# ═══════════════════════════════════════════════════════════════════
#  MAIN DIALOG
# ═══════════════════════════════════════════════════════════════════
class AIPlacementProgressDialog(QDialog):
    """Rich progress dialog for AI Placement pipeline execution."""

    cancel_requested = Signal()

    # Stage definitions: (key_pattern_for_log, display_title)
    STAGES = [
        ("init",                  "Initialization"),
        ("topology_analyst",      "Topology Analysis"),
        ("strategy_selector",     "Strategy Selection"),
        ("placement_specialist",  "Placement Specialist"),
        ("finger_expansion",      "Finger Expansion"),
        ("symmetry_enforcer",     "Symmetry Enforcer"),
        ("drc_critic",            "DRC Critic"),
        ("completed",             "Completed"),
    ]

    # Map log-file patterns to stage keys (used for file-tail detection)
    _LOG_STAGE_PATTERNS = [
        (re.compile(r"AI INITIAL PLACEMENT", re.IGNORECASE),                    "init"),
        (re.compile(r"STAGE\s+1[:/]?\s*.*TOPOLOGY", re.IGNORECASE),            "topology_analyst"),
        (re.compile(r"STAGE\s+2[:/]?\s*.*STRATEGY", re.IGNORECASE),            "strategy_selector"),
        (re.compile(r"STAGE\s+3[:/]?\s*.*PLACEMENT", re.IGNORECASE),           "placement_specialist"),
        (re.compile(r"STAGE\s+4[:/]?\s*.*FINGER", re.IGNORECASE),              "finger_expansion"),
        (re.compile(r"STAGE\s+3\.5[:/]?\s*.*SYMMETRY", re.IGNORECASE),         "symmetry_enforcer"),
        (re.compile(r"STAGE\s+5[:/]?\s*.*DRC", re.IGNORECASE),                 "drc_critic"),
        (re.compile(r"PLACEMENT SUMMARY", re.IGNORECASE),                       "completed"),
    ]

    def __init__(self, parent=None, model_name: str = "", abutment: bool = True):
        super().__init__(parent)
        self.setWindowTitle("AI Placement — Progress")
        self.setMinimumSize(920, 580)
        self.resize(1050, 650)
        self.setModal(True)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowCloseButtonHint
        )

        self._model_name = model_name
        self._abutment = abutment
        self._start_time = time.time()
        self._auto_scroll = True
        self._is_finished = False
        self._log_file_path = os.path.join(os.getcwd(), "placement_live_output.log")
        self._last_log_pos = 0
        self._current_stage_idx = -1

        self._build_ui()
        self._apply_styles()

        # Start timers
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._update_elapsed)
        self._elapsed_timer.start(1000)

        self._log_poll_timer = QTimer(self)
        self._log_poll_timer.timeout.connect(self._poll_log_file)
        self._log_poll_timer.start(250)  # 4 times/sec

        # Activate first stage
        self._advance_to_stage(0)

    # ── Build UI ──────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header bar ────────────────────────────────────────────
        header = QFrame()
        header.setObjectName("header")
        header.setFixedHeight(60)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(20, 0, 20, 0)

        self._spinner = _AISpinner()
        self._spinner.start()
        h_layout.addWidget(self._spinner)
        h_layout.addSpacing(12)

        self._header_label = QLabel("AI Placement in Progress")
        self._header_label.setObjectName("headerLabel")
        h_layout.addWidget(self._header_label)
        h_layout.addStretch()

        self._elapsed_label = QLabel("00:00")
        self._elapsed_label.setObjectName("elapsedLabel")
        h_layout.addWidget(self._elapsed_label)

        root.addWidget(header)

        # ── Body: stepper (left) + terminal (right) ───────────────
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # Left panel — Stage stepper
        left_panel = QFrame()
        left_panel.setObjectName("leftPanel")
        left_panel.setFixedWidth(260)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(20, 20, 12, 20)
        left_layout.setSpacing(2)

        stepper_title = QLabel("Pipeline Stages")
        stepper_title.setObjectName("stepperTitle")
        left_layout.addWidget(stepper_title)
        left_layout.addSpacing(12)

        self._stage_rows: list[_StageRow] = []
        for i, (key, title) in enumerate(self.STAGES):
            row = _StageRow(i, title)
            self._stage_rows.append(row)
            left_layout.addWidget(row)

        left_layout.addStretch()

        # Model info at bottom of stepper
        model_label = QLabel(f"Model: {self._model_name}")
        model_label.setObjectName("modelLabel")
        left_layout.addWidget(model_label)
        abut_label = QLabel(f"Abutment: {'On' if self._abutment else 'Off'}")
        abut_label.setObjectName("modelLabel")
        left_layout.addWidget(abut_label)

        body.addWidget(left_panel)

        # Vertical separator
        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFixedWidth(1)
        body.addWidget(sep)

        # Right panel — Log terminal
        right_panel = QFrame()
        right_panel.setObjectName("rightPanel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(8)

        # Terminal header row
        term_header = QHBoxLayout()
        term_title = QLabel("🖥  Live Output")
        term_title.setObjectName("termTitle")
        term_header.addWidget(term_title)
        term_header.addStretch()

        self._auto_scroll_btn = QPushButton("Auto-scroll: ON")
        self._auto_scroll_btn.setObjectName("toggleBtn")
        self._auto_scroll_btn.setCheckable(True)
        self._auto_scroll_btn.setChecked(True)
        self._auto_scroll_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._auto_scroll_btn.clicked.connect(self._toggle_auto_scroll)
        term_header.addWidget(self._auto_scroll_btn)

        right_layout.addLayout(term_header)

        # Terminal text area
        self._terminal = QPlainTextEdit()
        self._terminal.setObjectName("terminal")
        self._terminal.setReadOnly(True)
        self._terminal.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont("Cascadia Mono, Consolas, Courier New, monospace", 9)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._terminal.setFont(font)
        self._terminal.setPlaceholderText("Waiting for pipeline output…")
        self._highlighter = _LogHighlighter(self._terminal.document())
        right_layout.addWidget(self._terminal)

        body.addWidget(right_panel, stretch=1)

        root.addLayout(body, stretch=1)

        # ── Footer bar ───────────────────────────────────────────
        footer = QFrame()
        footer.setObjectName("footer")
        footer.setFixedHeight(56)
        f_layout = QHBoxLayout(footer)
        f_layout.setContentsMargins(20, 0, 20, 0)

        self._status_label = QLabel("Running…")
        self._status_label.setObjectName("statusLabel")
        f_layout.addWidget(self._status_label)
        f_layout.addStretch()

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setObjectName("cancelBtn")
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        f_layout.addWidget(self._cancel_btn)

        self._close_btn = QPushButton("View Layout")
        self._close_btn.setObjectName("closeBtn")
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.clicked.connect(self.accept)
        self._close_btn.setVisible(False)
        f_layout.addWidget(self._close_btn)

        root.addWidget(footer)

    # ── Stylesheet ────────────────────────────────────────────────
    def _apply_styles(self) -> None:
        self.setStyleSheet("""
            AIPlacementProgressDialog {
                background-color: #141822;
                color: #c8d0dc;
                font-family: 'Segoe UI', 'Inter', sans-serif;
            }

            /* ── Header ─────────────────────────────────── */
            QFrame#header {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #1a2235, stop:1 #1e2a42);
                border-bottom: 1px solid #2d3a50;
            }
            QLabel#headerLabel {
                color: #e0e8f0;
                font-size: 15pt;
                font-weight: bold;
                background: transparent;
            }
            QLabel#elapsedLabel {
                color: #85929E;
                font-size: 13pt;
                font-family: 'Cascadia Mono', 'Consolas', monospace;
                background: transparent;
            }

            /* ── Left panel (stepper) ────────────────────── */
            QFrame#leftPanel {
                background-color: #161c28;
                border: none;
            }
            QLabel#stepperTitle {
                color: #AEB6BF;
                font-size: 9pt;
                font-weight: bold;
                text-transform: uppercase;
                letter-spacing: 1px;
                background: transparent;
            }
            QLabel#modelLabel {
                color: #566573;
                font-size: 8pt;
                background: transparent;
            }

            /* ── Separator ───────────────────────────────── */
            QFrame#separator {
                background-color: #2d3a50;
            }

            /* ── Right panel (terminal) ──────────────────── */
            QFrame#rightPanel {
                background-color: #0d1117;
                border: none;
            }
            QLabel#termTitle {
                color: #AEB6BF;
                font-size: 10pt;
                font-weight: bold;
                background: transparent;
            }
            QPushButton#toggleBtn {
                background: #1e2636;
                color: #AEB6BF;
                border: 1px solid #2d3a50;
                border-radius: 4px;
                padding: 3px 10px;
                font-size: 8pt;
            }
            QPushButton#toggleBtn:checked {
                background: #253048;
                color: #5DADE2;
                border-color: #4a90d9;
            }
            QPlainTextEdit#terminal {
                background-color: #0d1117;
                color: #c9d1d9;
                border: 1px solid #21262d;
                border-radius: 6px;
                padding: 8px;
                selection-background-color: #264f78;
            }

            /* ── Footer ─────────────────────────────────── */
            QFrame#footer {
                background: #161c28;
                border-top: 1px solid #2d3a50;
            }
            QLabel#statusLabel {
                color: #85929E;
                font-size: 9pt;
                background: transparent;
            }
            QPushButton#cancelBtn {
                background-color: #3d2020;
                color: #ff6b6b;
                border: 1px solid #5c3030;
                border-radius: 6px;
                padding: 7px 22px;
                font-size: 10pt;
                font-weight: bold;
            }
            QPushButton#cancelBtn:hover {
                background-color: #5c3030;
                color: #ff9999;
            }
            QPushButton#closeBtn {
                background-color: #1a5276;
                color: #ffffff;
                border: 1px solid #2e86c1;
                border-radius: 6px;
                padding: 7px 22px;
                font-size: 10pt;
                font-weight: bold;
            }
            QPushButton#closeBtn:hover {
                background-color: #2e86c1;
            }
        """)

    # ── Timer: update elapsed ─────────────────────────────────────
    def _update_elapsed(self) -> None:
        elapsed = int(time.time() - self._start_time)
        mins, secs = divmod(elapsed, 60)
        self._elapsed_label.setText(f"{mins:02d}:{secs:02d}")

    # ── Timer: tail the log file ──────────────────────────────────
    def _poll_log_file(self) -> None:
        try:
            if not os.path.isfile(self._log_file_path):
                return
            with open(self._log_file_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._last_log_pos)
                new_text = f.read()
                self._last_log_pos = f.tell()

            if not new_text:
                return

            self._terminal.appendPlainText(new_text.rstrip("\n"))

            # Detect stage transitions from newly read text
            for line in new_text.splitlines():
                for pattern, stage_key in self._LOG_STAGE_PATTERNS:
                    if pattern.search(line):
                        self._advance_to_stage_by_key(stage_key)
                        break

            if self._auto_scroll:
                self._terminal.verticalScrollBar().setValue(
                    self._terminal.verticalScrollBar().maximum()
                )
        except Exception:
            pass

    # ── Stage management ──────────────────────────────────────────
    def _advance_to_stage(self, idx: int) -> None:
        """Move stepper to stage index."""
        if idx <= self._current_stage_idx:
            return
        # Mark all prior stages as done
        for i in range(self._current_stage_idx + 1, idx):
            if i < len(self._stage_rows):
                self._stage_rows[i].set_state("done")
        # Mark the previous active stage as done
        if 0 <= self._current_stage_idx < len(self._stage_rows):
            self._stage_rows[self._current_stage_idx].set_state("done")
        # Set new active
        self._current_stage_idx = idx
        if idx < len(self._stage_rows):
            self._stage_rows[idx].set_state("active")

    def _advance_to_stage_by_key(self, key: str) -> None:
        for i, (stage_key, _) in enumerate(self.STAGES):
            if stage_key == key:
                self._advance_to_stage(i)
                return

    # ── Public methods for external signalling ────────────────────
    def on_stage_started(self, stage_key: str, title: str) -> None:
        """Called by the worker/layout_tab when a LangGraph node starts."""
        self._advance_to_stage_by_key(stage_key)

    def on_stage_done(self, stage_key: str, summary: str) -> None:
        """Called when a LangGraph node completes."""
        for i, (sk, _) in enumerate(self.STAGES):
            if sk == stage_key and i <= self._current_stage_idx:
                self._stage_rows[i].set_state("done", summary[:60] if summary else "")
                break

    def on_completed(self) -> None:
        """Mark the entire pipeline as completed."""
        if self._is_finished:
            return
        self._is_finished = True

        # Poll one last time to catch remaining log output
        self._poll_log_file()

        # Mark all stages done
        for row in self._stage_rows:
            if row._state not in ("done", "error"):
                row.set_state("done")

        # Update UI
        self._spinner.stop()
        self._header_label.setText("AI Placement Complete ✓")
        self._header_label.setStyleSheet(
            "color: #2ECC71; font-size: 15pt; font-weight: bold; background: transparent;"
        )
        self._status_label.setText("Placement finished successfully")
        self._status_label.setStyleSheet("color: #2ECC71; font-size: 9pt; background: transparent;")
        self._cancel_btn.setVisible(False)
        self._close_btn.setVisible(True)

        # Stop timers
        self._elapsed_timer.stop()
        self._log_poll_timer.stop()

        # Final elapsed
        self._update_elapsed()

    def on_error(self, error_msg: str) -> None:
        """Mark the pipeline as failed."""
        if self._is_finished:
            return
        self._is_finished = True

        # Poll one last time
        self._poll_log_file()

        # Mark current stage as error
        if 0 <= self._current_stage_idx < len(self._stage_rows):
            self._stage_rows[self._current_stage_idx].set_state("error", "Failed")

        # Update UI
        self._spinner.stop()
        self._header_label.setText("AI Placement Failed ✗")
        self._header_label.setStyleSheet(
            "color: #E74C3C; font-size: 15pt; font-weight: bold; background: transparent;"
        )
        self._status_label.setText(f"Error: {error_msg[:100]}")
        self._status_label.setStyleSheet("color: #E74C3C; font-size: 9pt; background: transparent;")
        self._cancel_btn.setVisible(False)
        self._close_btn.setText("Close")
        self._close_btn.setVisible(True)
        self._close_btn.setStyleSheet("""
            QPushButton {
                background-color: #3d2020;
                color: #ff6b6b;
                border: 1px solid #5c3030;
                border-radius: 6px;
                padding: 7px 22px;
                font-size: 10pt;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #5c3030; color: #ff9999; }
        """)
        self._close_btn.clicked.disconnect()
        self._close_btn.clicked.connect(self.reject)

        # Stop timers
        self._elapsed_timer.stop()
        self._log_poll_timer.stop()
        self._update_elapsed()

        # Append error to terminal
        self._terminal.appendPlainText(f"\n[ERROR] {error_msg}")

    # ── Auto-scroll toggle ────────────────────────────────────────
    def _toggle_auto_scroll(self) -> None:
        self._auto_scroll = self._auto_scroll_btn.isChecked()
        self._auto_scroll_btn.setText(
            f"Auto-scroll: {'ON' if self._auto_scroll else 'OFF'}"
        )

    # ── Cancel ────────────────────────────────────────────────────
    def _on_cancel_clicked(self) -> None:
        self.cancel_requested.emit()

    # ── Override close event ──────────────────────────────────────
    def closeEvent(self, event) -> None:
        if not self._is_finished:
            # Ask for confirmation if still running
            from PySide6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self, "Cancel?",
                "The AI placement is still running.\nDo you want to cancel it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.cancel_requested.emit()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

    def cleanup(self) -> None:
        """Stop all timers (call when dialog is no longer needed)."""
        self._elapsed_timer.stop()
        self._log_poll_timer.stop()
        self._spinner.stop()
        for row in self._stage_rows:
            row.dot._anim.stop()
