# -*- coding: utf-8 -*-
"""
Welcome Screen — the landing page shown when no layout tabs are open.
"""

import os
import glob

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QGridLayout, QScrollArea, QSizePolicy,
    QGraphicsDropShadowEffect,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import (
    QFont, QColor, QPainter, QLinearGradient, QRadialGradient,
    QPen, QBrush, QPainterPath,
)

class _CircuitIcon(QWidget):
    """Custom painted circuit symbol icons for the example tiles."""
    def __init__(self, type_name: str, parent=None):
        super().__init__(parent)
        self.type_name = type_name.lower().replace(" ", "_")
        self.setFixedSize(50, 36)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        
        pen = QPen(QColor("#4a90d9"), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        
        if "miller" in self.type_name or "ota" in self.type_name:
            # Unity gain amplifier
            p.drawLine(cx - 12, cy - 12, cx - 12, cy + 12)
            p.drawLine(cx - 12, cy - 12, cx + 12, cy)
            p.drawLine(cx - 12, cy + 12, cx + 12, cy)
            # Inputs
            p.drawLine(cx - 18, cy - 6, cx - 12, cy - 6) # (-)
            p.drawLine(cx - 18, cy + 6, cx - 12, cy + 6) # (+)
            p.setPen(QPen(QColor("#5a9bd4"), 1.5))
            p.drawLine(cx - 16, cy - 8, cx - 14, cy - 8) # minus sign
            p.drawLine(cx - 16, cy + 6, cx - 14, cy + 6) # plus sign (h)
            p.drawLine(cx - 15, cy + 5, cx - 15, cy + 7) # plus sign (v)
            p.setPen(pen)
            # Loopback
            p.drawLine(cx + 12, cy, cx + 16, cy)
            p.drawLine(cx + 16, cy, cx + 16, cy - 16)
            p.drawLine(cx + 16, cy - 16, cx - 18, cy - 16)
            p.drawLine(cx - 18, cy - 16, cx - 18, cy - 6)
            
        elif "comparator" in self.type_name:
            # Open loop amp with threshold step
            p.drawLine(cx - 14, cy - 13, cx - 14, cy + 13)
            p.drawLine(cx - 14, cy - 13, cx + 12, cy)
            p.drawLine(cx - 14, cy + 13, cx + 12, cy)
            # Inputs/Output
            p.drawLine(cx - 20, cy - 6, cx - 14, cy - 6)
            p.drawLine(cx - 20, cy + 6, cx - 14, cy + 6)
            p.drawLine(cx + 12, cy, cx + 18, cy)
            # Threshold step inside (shifted left to fit inside the narrowing triangle)
            p.setPen(QPen(QColor("#8cc6ff"), 1.5))
            p.drawLine(cx - 9, cy + 3, cx - 4, cy + 3)
            p.drawLine(cx - 4, cy + 3, cx - 4, cy - 3)
            p.drawLine(cx - 4, cy - 3, cx + 1, cy - 3)
            # Horizontal Threshold line
            p.setPen(QPen(QColor("#b0d0f0"), 1.0, Qt.PenStyle.DotLine))
            p.drawLine(cx - 11, cy, cx + 3, cy)
            
        elif "current_mirror" in self.type_name:
            # Two NMOS forming a mirror
            # Drain 1 & 2
            p.drawLine(cx - 8, cy - 10, cx - 8, cy + 2)
            p.drawLine(cx + 8, cy - 10, cx + 8, cy + 2)
            # Gates
            p.drawLine(cx - 12, cy - 2, cx - 4, cy - 2)
            p.drawLine(cx + 4, cy - 2, cx + 12, cy - 2)
            # Gate connection
            p.drawLine(cx - 8, cy - 2, cx + 8, cy - 2)
            # Diode connection
            p.drawLine(cx - 8, cy - 6, cx - 14, cy - 6)
            p.drawLine(cx - 14, cy - 6, cx - 14, cy - 2)
            # Sources to ground
            p.drawLine(cx - 8, cy + 2, cx - 8, cy + 8)
            p.drawLine(cx + 8, cy + 2, cx + 8, cy + 8)
            p.drawLine(cx - 12, cy + 8, cx - 4, cy + 8)
            p.drawLine(cx + 4, cy + 8, cx + 12, cy + 8)
            
        elif "nand" in self.type_name:
            # AND shape with circle
            path = QPainterPath()
            path.moveTo(cx - 8, cy - 10)
            path.lineTo(cx - 8, cy + 10)
            path.lineTo(cx, cy + 10)
            path.arcTo(cx - 10, cy - 10, 20, 20, -90, 180)
            path.closeSubpath()
            p.drawPath(path)
            p.drawEllipse(cx + 10, cy - 2, 4, 4)
            p.drawLine(cx - 14, cy - 5, cx - 8, cy - 5)
            p.drawLine(cx - 14, cy + 5, cx - 8, cy + 5)
            p.drawLine(cx + 14, cy, cx + 18, cy)
            
        elif "xor" in self.type_name:
            # XOR gate
            path = QPainterPath()
            path.moveTo(cx - 6, cy - 10)
            path.quadTo(cx, cy, cx - 6, cy + 10)
            path.quadTo(cx + 6, cy + 10, cx + 12, cy)
            path.quadTo(cx + 6, cy - 10, cx - 6, cy - 10)
            p.drawPath(path)
            path2 = QPainterPath()
            path2.moveTo(cx - 10, cy - 10)
            path2.quadTo(cx - 4, cy, cx - 10, cy + 10)
            p.drawPath(path2)
            p.drawLine(cx - 16, cy - 5, cx - 8, cy - 5)
            p.drawLine(cx - 16, cy + 5, cx - 8, cy + 5)
            p.drawLine(cx + 12, cy, cx + 18, cy)
            
        elif "rc" in self.type_name:
            # Resistor
            p.drawLine(cx - 18, cy, cx - 14, cy)
            p.drawLine(cx - 14, cy, cx - 12, cy - 4)
            p.drawLine(cx - 12, cy - 4, cx - 8, cy + 4)
            p.drawLine(cx - 8, cy + 4, cx - 4, cy - 4)
            p.drawLine(cx - 4, cy - 4, cx - 2, cy)
            p.drawLine(cx - 2, cy, cx + 4, cy)
            # Capacitor
            p.drawLine(cx + 4, cy - 6, cx + 4, cy + 6)
            p.drawLine(cx + 8, cy - 6, cx + 8, cy + 6)
            p.drawLine(cx + 8, cy, cx + 14, cy)
            
        elif "tx_driver" in self.type_name:
            # Transmitter Antenna (shifted down to prevent top-clipping)
            # Base
            p.drawLine(cx - 4, cy + 15, cx + 4, cy + 15)
            p.drawLine(cx, cy + 15, cx, cy + 4)
            # Antenna tip
            p.drawLine(cx - 6, cy + 4, cx + 6, cy + 4)
            p.drawLine(cx - 6, cy + 4, cx, cy - 2)
            p.drawLine(cx + 6, cy + 4, cx, cy - 2)
            # Radio Waves
            p.setPen(QPen(QColor("#8cc6ff"), 1.5))
            p.drawArc(cx - 6, cy - 8, 12, 12, 45 * 16, 90 * 16)
            p.drawArc(cx - 10, cy - 12, 20, 20, 45 * 16, 90 * 16)
            p.drawArc(cx - 14, cy - 16, 28, 28, 45 * 16, 90 * 16)
            
        else:
            # Generic block
            p.drawRect(cx - 12, cy - 8, 24, 16)
            for i in range(3):
                p.drawLine(cx - 8 + i * 8, cy - 8, cx - 8 + i * 8, cy - 12)
                p.drawLine(cx - 8 + i * 8, cy + 8, cx - 8 + i * 8, cy + 12)
        
        p.end()


class _ChipIcon(QWidget):
    """Custom-painted chip/IC icon for the hero section."""
    def __init__(self, size=80, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2

        # Outer glow
        glow = QRadialGradient(cx, cy, w // 2)
        glow.setColorAt(0.0, QColor(74, 144, 217, 30))
        glow.setColorAt(1.0, QColor(74, 144, 217, 0))
        p.setBrush(QBrush(glow))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(0, 0, w, h)

        # Chip body
        m = 18
        body = QPainterPath()
        body.addRoundedRect(m, m, w - 2*m, h - 2*m, 6, 6)
        grad = QLinearGradient(m, m, m, h - m)
        grad.setColorAt(0, QColor("#2a3a52"))
        grad.setColorAt(1, QColor("#1a2436"))
        p.fillPath(body, QBrush(grad))
        p.setPen(QPen(QColor("#4a90d9"), 1.5))
        p.drawPath(body)

        # Pins
        pin_color = QColor("#4a90d9")
        p.setPen(QPen(pin_color, 2))
        pin_len = 6
        for i in range(4):
            frac = 0.25 + 0.18 * i
            # Top
            px = int(m + frac * (w - 2*m))
            p.drawLine(px, m, px, m - pin_len)
            # Bottom
            p.drawLine(px, h - m, px, h - m + pin_len)
        for i in range(3):
            frac = 0.3 + 0.2 * i
            py = int(m + frac * (h - 2*m))
            # Left
            p.drawLine(m, py, m - pin_len, py)
            # Right
            p.drawLine(w - m, py, w - m + pin_len, py)

        # Internal routing lines
        p.setPen(QPen(QColor("#4a90d9"), 0.8, Qt.PenStyle.DotLine))
        inner = 8
        p.drawLine(m + inner, cy - 4, w - m - inner, cy - 4)
        p.drawLine(m + inner, cy + 4, w - m - inner, cy + 4)
        p.drawLine(cx, m + inner, cx, h - m - inner)

        p.end()


class _HeroBg(QWidget):
    """Animated gradient background."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(60)

    def _tick(self):
        self._phase = (self._phase + 0.006) % 2.0
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor("#0e1219"))

        cx1 = int(w * (0.3 + 0.04 * self._phase))
        r1 = QRadialGradient(cx1, int(h * 0.35), int(w * 0.45))
        r1.setColorAt(0.0, QColor(74, 144, 217, 16))
        r1.setColorAt(1.0, QColor(74, 144, 217, 0))
        p.setBrush(QBrush(r1))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(cx1 - int(w*0.45), 0, int(w*0.9), int(w*0.9))

        cx2 = int(w * (0.7 - 0.03 * self._phase))
        r2 = QRadialGradient(cx2, int(h * 0.5), int(w * 0.35))
        r2.setColorAt(0.0, QColor(120, 70, 200, 10))
        r2.setColorAt(1.0, QColor(120, 70, 200, 0))
        p.setBrush(QBrush(r2))
        p.drawEllipse(cx2 - int(w*0.35), int(h*0.15), int(w*0.7), int(w*0.7))

        fade = QLinearGradient(0, h - 60, 0, h)
        fade.setColorAt(0.0, QColor(14, 18, 25, 0))
        fade.setColorAt(1.0, QColor(14, 18, 25, 255))
        p.fillRect(0, h - 60, w, 60, QBrush(fade))
        p.end()


class _KbdBadge(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(20)
        self.setStyleSheet(
            "background-color: #1a2230; color: #6b7d92; "
            "border: 1px solid #2a3548; border-radius: 4px; padding: 1px 7px;"
        )


class _ActionCard(QPushButton):
    def __init__(self, icon_text, title, subtitle, accent, shortcut="", parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(120)
        self.setMinimumWidth(290)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._accent = accent

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(18)

        icon = QLabel(icon_text)
        icon.setFont(QFont("Segoe UI Emoji", 24))
        icon.setFixedSize(56, 56)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(
            f"background: qradialgradient(cx:0.5,cy:0.5,radius:0.7,"
            f"fx:0.5,fy:0.3,stop:0 {accent}28,stop:1 {accent}06);"
            f"border-radius: 16px; border: 1px solid {accent}35;"
        )
        layout.addWidget(icon)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        lbl_title = QLabel(title)
        lbl_title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        lbl_title.setStyleSheet("color: #eaf0f8; border: none; background: transparent;")
        text_col.addWidget(lbl_title)

        lbl_sub = QLabel(subtitle)
        lbl_sub.setFont(QFont("Segoe UI", 9.5))
        lbl_sub.setStyleSheet("color: #5e7080; border: none; background: transparent;")
        lbl_sub.setWordWrap(True)
        text_col.addWidget(lbl_sub)

        if shortcut:
            sr = QHBoxLayout()
            sr.setContentsMargins(0, 3, 0, 0)
            sr.addWidget(_KbdBadge(shortcut))
            sr.addStretch()
            text_col.addLayout(sr)
        layout.addLayout(text_col, 1)

        self.setStyleSheet(f"""
            QPushButton {{ background-color:#131a24; border:1px solid #222d3e;
                border-radius:14px; text-align:left; }}
            QPushButton:hover {{ background-color:#172030; border-color:{accent}80; }}
            QPushButton:pressed {{ background-color:#1c2840; border-color:{accent}; }}
        """)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(0); shadow.setOffset(0, 2)
        shadow.setColor(QColor(accent))
        self.setGraphicsEffect(shadow)
        self._shadow = shadow

    def enterEvent(self, e):
        self._shadow.setBlurRadius(28); super().enterEvent(e)
    def leaveEvent(self, e):
        self._shadow.setBlurRadius(0); super().leaveEvent(e)


class _ExampleTile(QPushButton):
    example_clicked = Signal(str, str)

    def __init__(self, display_name, sp_path, oas_path, parent=None):
        super().__init__(parent)
        self._sp = sp_path; self._oas = oas_path
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(195, 115)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(5)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_widget = QWidget()
        icon_layout = QHBoxLayout(icon_widget)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        icon = _CircuitIcon(display_name)
        icon_layout.addWidget(icon)
        layout.addWidget(icon_widget)

        name = QLabel(display_name)
        name.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name.setStyleSheet("color: #c8d4e2; border: none; background: transparent;")
        name.setWordWrap(True)
        layout.addWidget(name)

        tag = "✓ layout" if oas_path else "netlist only"
        tag_color = "#3d9970" if oas_path else "#445566"
        info = QLabel(tag)
        info.setFont(QFont("Segoe UI", 8))
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setStyleSheet(f"color: {tag_color}; border: none; background: transparent;")
        layout.addWidget(info)

        self.setStyleSheet("""
            QPushButton { background-color:#131a24; border:1px solid #1e2a3a;
                border-radius:12px; }
            QPushButton:hover { background-color:#182234; border-color:#4a90d9; }
            QPushButton:pressed { background-color:#1e2a40; }
        """)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(0); shadow.setOffset(0, 1)
        shadow.setColor(QColor("#4a90d9"))
        self.setGraphicsEffect(shadow); self._shadow = shadow
        self.clicked.connect(lambda: self.example_clicked.emit(self._sp, self._oas))

    def enterEvent(self, e):
        self._shadow.setBlurRadius(18); super().enterEvent(e)
    def leaveEvent(self, e):
        self._shadow.setBlurRadius(0); super().leaveEvent(e)


class WelcomeScreen(QWidget):
    open_file_requested = Signal()
    import_requested = Signal()
    example_requested = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet("background-color: #0e1219;")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea{border:none;background:transparent;}"
            "QScrollBar:vertical{width:6px;background:transparent;}"
            "QScrollBar::handle:vertical{background:#2d3548;border-radius:3px;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
        )

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        self._hero_bg = _HeroBg(content)

        root = QVBoxLayout(content)
        root.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        root.setContentsMargins(50, 24, 50, 20)
        root.setSpacing(0)

        # ── Chip + AI Icons ────────────────────────────────────
        icon_row = QHBoxLayout()
        icon_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_row.setSpacing(14)

        chip = _ChipIcon(72)
        icon_row.addWidget(chip)

        connector = QLabel("×")
        connector.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        connector.setStyleSheet("color: #ffffff;")
        connector.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_row.addWidget(connector)

        ai_icon = QLabel("🤖")
        ai_icon.setFont(QFont("Segoe UI Emoji", 36))
        ai_icon.setFixedSize(72, 72)
        ai_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ai_icon.setStyleSheet(
            "background: transparent; border: none;"
        )
        icon_row.addWidget(ai_icon)

        root.addLayout(icon_row)
        root.addSpacing(6)

        # ── Title ─────────────────────────────────────────────
        title = QLabel("Symbolic Layout Editor")
        title.setFont(QFont("Segoe UI", 32, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #f0f5fb;")
        root.addWidget(title)

        subtitle = QLabel("AI-Powered Analog IC Layout Automation")
        subtitle.setFont(QFont("Segoe UI", 13))
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #506478; margin-bottom: 2px;")
        root.addWidget(subtitle)
        root.addSpacing(6)

        # ── Feature pills ─────────────────────────────────────
        pills_row = QHBoxLayout()
        pills_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pills_row.setSpacing(15)
        for txt in ["Schematic Import", "Multi-Agent AI Placer", "AI Chat Bot", "Physical Export"]:
            pill = QLabel(txt)
            pill.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
            pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pill.setFixedHeight(24)
            pill.setStyleSheet(
                "background:#141c28; color:#5e7a94; border:1px solid #1e2a3a;"
                "border-radius:6px; padding:2px 12px;"
            )
            pills_row.addWidget(pill)
        root.addLayout(pills_row)
        root.addSpacing(25)

        # ── Action Cards ──────────────────────────────────────
        cards_row = QHBoxLayout()
        cards_row.setSpacing(18)
        cards_row.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card_import = _ActionCard(
            "🔬", "Import Netlist + Layout",
            "Parse a SPICE netlist (.sp) and optional OAS physical layout",
            "#4a90d9", "Ctrl+I",
        )
        card_import.clicked.connect(self.import_requested.emit)
        cards_row.addWidget(card_import)

        card_open = _ActionCard(
            "📋", "Open Saved Layout",
            "Resume work on a previously saved placement JSON",
            "#43b581", "Ctrl+O",
        )
        card_open.clicked.connect(self.open_file_requested.emit)
        cards_row.addWidget(card_open)

        root.addLayout(cards_row)

        # ── Separator ─────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color:#1a2230; max-height:1px; margin:60px 80px 16px 80px;")
        root.addWidget(sep)

        root.addSpacing(45)

        # ── Quick Start Examples ──────────────────────────────
        st = QLabel("Quick Start Examples")
        st.setFont(QFont("Segoe UI", 15, QFont.Weight.DemiBold))
        st.setStyleSheet("color: #8090a4; margin-bottom: 2px;")
        st.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(st)

        ss = QLabel("Select an example circuit to get started")
        ss.setFont(QFont("Segoe UI", 10))
        ss.setStyleSheet("color: #3e5060; margin-bottom: 10px;")
        ss.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(ss)

        self._examples_grid = QGridLayout()
        self._examples_grid.setSpacing(12)
        self._examples_grid.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._populate_examples()
        root.addLayout(self._examples_grid)

        root.addStretch(1)

        root.addSpacing(45)

        # ── Footer ────────────────────────────────────────────
        fl = QFrame()
        fl.setFrameShape(QFrame.Shape.HLine)
        fl.setStyleSheet("background-color:#161e2a; max-height:1px; margin:8px 100px 8px 100px;")
        root.addWidget(fl)

        fr = QHBoxLayout()
        fr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fr.setSpacing(20)
        for key, label in [("Ctrl+I","Import"),("Ctrl+O","Open"),("Ctrl+T","New Tab"),
                           ("Shift+F","Transistor View"),("Ctrl+F","Symbolic View")]:
            row = QHBoxLayout(); row.setSpacing(4)
            row.addWidget(_KbdBadge(key))
            l = QLabel(label); l.setFont(QFont("Segoe UI",8))
            l.setStyleSheet("color:#3a4d60;"); row.addWidget(l)
            fr.addLayout(row)
        root.addLayout(fr)
        root.addSpacing(8)

        scroll.setWidget(content)
        outer.addWidget(scroll)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_hero_bg"):
            self._hero_bg.setGeometry(0, 0, self.width(), min(480, self.height()))

    def _populate_examples(self):
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
                col = 0; row += 1
