# -*- coding: utf-8 -*-
"""
Floorplan View — QGraphicsView-based widget showing circuit blocks
in either Blocks mode or circuit Symbol mode.

Blocks mode: grouped device blocks shown as rounded cards.
Symbol mode: one single circuit-level symbol block with labeled pins.

Blocks are derived from:
  1. SPICE .subckt definitions
  2. Device naming patterns (e.g., MM0_f1, MM0_f2 → block "MM0")
  3. Manual "blocks" key in JSON
"""

import re
import math
from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsRectItem,
    QGraphicsTextItem, QGraphicsEllipseItem, QGraphicsLineItem,
    QGraphicsItem, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QMenu, QGraphicsPathItem, QFrame,
)
from PySide6.QtCore import Qt, Signal, QRectF, QPointF, QSettings
from PySide6.QtGui import (
    QPainter, QPen, QColor, QBrush, QFont, QPainterPath,
    QLinearGradient, QRadialGradient,
)


# ═══════════════════════════════════════════════════════════════
# Gray-First Color Palette
# ═══════════════════════════════════════════════════════════════
_BG_DARK      = QColor("#14161a")
_BG_GRID_DOT  = QColor("#262a30")
_BLOCK_FILL   = QColor("#1f2329")
_BLOCK_BORDER = QColor("#4e5561")
_BLOCK_HOVER  = QColor("#2a2f37")
_BLOCK_SEL    = QColor("#a6adb8")
_ACCENT       = QColor("#8a919c")
_ACCENT_GLOW  = QColor(138, 145, 156, 48)
_TEXT_PRIMARY  = QColor("#e5e7eb")
_TEXT_SECONDARY = QColor("#98a2ad")
_BADGE_APPROVED = QColor("#34d399")
_BADGE_PREVIEW  = QColor("#fbbf24")
_PORT_POWER    = QColor("#ef4444")
_PORT_GROUND   = QColor("#f59e0b")
_PORT_SIGNAL   = QColor("#7aa2d4")
_PORT_CLK      = QColor("#96a2c8")

_BLOCK_THEME_COLORS = [
    QColor("#6b7280"),
    QColor("#7c848f"),
    QColor("#5f7a82"),
    QColor("#7f6f6b"),
    QColor("#66737d"),
    QColor("#7b7f87"),
    QColor("#62717b"),
    QColor("#7d7684"),
]

# Device-level colors (Unit Block mode)
_NMOS_FILL    = QColor("#253241")
_NMOS_BORDER  = QColor("#6f8aa7")
_PMOS_FILL    = QColor("#3a2e36")
_PMOS_BORDER  = QColor("#b08ca3")
_DUMMY_FILL   = QColor("#2e3138")
_DUMMY_BORDER = QColor("#888f9b")


# ═══════════════════════════════════════════════════════════════
# Block Item — Symbol Mode (grouped devices as blocks)
# ═══════════════════════════════════════════════════════════════
class SymbolBlockItem(QGraphicsRectItem):
    """A block rendered as a clean rounded rectangle (Astrus-style)."""

    def __init__(self, block_id, block_name, device_ids, port_info=None,
                 x=0, y=0, width=200, height=130, status="previewing",
                 theme_color=None):
        super().__init__(0, 0, width, height)
        self.setPos(x, y)

        self.block_id = block_id
        self.block_name = block_name
        self.device_ids = device_ids or []
        self.port_info = port_info or {}
        self._status = status
        self._is_hovered = False
        self._theme_color = theme_color if isinstance(theme_color, QColor) else QColor("#6b7280")

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptHoverEvents(True)

    @property
    def status(self):
        return self._status

    def set_status(self, status):
        self._status = status
        self.update()

    def hoverEnterEvent(self, event):
        self._is_hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._is_hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        is_selected = self.isSelected()
        theme = QColor(self._theme_color)

        # ── Glow when selected ──
        if is_selected:
            glow = QPainterPath()
            glow.addRoundedRect(rect.adjusted(-3, -3, 3, 3), 10, 10)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(_ACCENT_GLOW))
            painter.drawPath(glow)

        # ── Background with subtle, muted per-block tint ──
        top = QColor(
            int((_BLOCK_FILL.red() * 3 + theme.red()) / 4),
            int((_BLOCK_FILL.green() * 3 + theme.green()) / 4),
            int((_BLOCK_FILL.blue() * 3 + theme.blue()) / 4),
        )
        bot = QColor(
            int((_BLOCK_FILL.red() * 4 + theme.red()) / 5),
            int((_BLOCK_FILL.green() * 4 + theme.green()) / 5),
            int((_BLOCK_FILL.blue() * 4 + theme.blue()) / 5),
        )
        if self._is_hovered:
            top = top.lighter(112)
            bot = bot.lighter(108)

        grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        grad.setColorAt(0, top)
        grad.setColorAt(1, bot)
        painter.setBrush(QBrush(grad))

        border = _BLOCK_SEL if is_selected else (theme.lighter(125) if self._is_hovered else theme)
        painter.setPen(QPen(border, 1.5 if not is_selected else 2.0))
        painter.drawRoundedRect(rect, 8, 8)

        # ── Block name ──
        painter.setPen(QPen(_TEXT_PRIMARY))
        name_font = QFont("Segoe UI", 11, QFont.Weight.DemiBold)
        painter.setFont(name_font)
        name_rect = QRectF(rect.x(), rect.y() + rect.height() * 0.25,
                           rect.width(), rect.height() * 0.3)
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignCenter, self.block_name)

        # ── Device count ──
        painter.setPen(QPen(_TEXT_SECONDARY))
        sub_font = QFont("Segoe UI", 8)
        painter.setFont(sub_font)
        sub_rect = QRectF(rect.x(), rect.y() + rect.height() * 0.52,
                          rect.width(), rect.height() * 0.2)
        painter.drawText(sub_rect, Qt.AlignmentFlag.AlignCenter,
                         f"{len(self.device_ids)} devices")

        # ── Status badge (top-left) ──
        badge_color = _BADGE_APPROVED if self._status == "approved" else _BADGE_PREVIEW
        badge_text = "Approved" if self._status == "approved" else "Previewing"
        bx, by = rect.x() + 10, rect.y() + 10
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(badge_color))
        painter.drawEllipse(QPointF(bx + 4, by + 4), 3.5, 3.5)
        painter.setPen(QPen(badge_color))
        painter.setFont(QFont("Segoe UI", 7, QFont.Weight.DemiBold))
        painter.drawText(QPointF(bx + 12, by + 8), badge_text)

        # ── Port indicators ──
        self._draw_ports(painter, rect)

    def _draw_ports(self, painter, rect):
        port_colors = {
            "power": _PORT_POWER, "ground": _PORT_GROUND,
            "signal": _PORT_SIGNAL, "clock": _PORT_CLK,
        }
        port_size = 5
        sides = {"left": [], "right": [], "top": [], "bottom": []}
        for port_name, info in self.port_info.items():
            sides.setdefault(info.get("side", "left"), []).append((port_name, info))

        for side, ports in sides.items():
            for i, (port_name, info) in enumerate(ports):
                color = port_colors.get(info.get("type", "signal"), _PORT_SIGNAL)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(color))
                spacing = 1.0 / (len(ports) + 1) * (i + 1)
                if side == "left":
                    px = rect.x() - port_size / 2
                    py = rect.y() + rect.height() * spacing - port_size / 2
                elif side == "right":
                    px = rect.x() + rect.width() - port_size / 2
                    py = rect.y() + rect.height() * spacing - port_size / 2
                elif side == "top":
                    px = rect.x() + rect.width() * spacing - port_size / 2
                    py = rect.y() - port_size / 2
                else:
                    px = rect.x() + rect.width() * spacing - port_size / 2
                    py = rect.y() + rect.height() - port_size / 2
                painter.drawRoundedRect(QRectF(px, py, port_size, port_size), 1.5, 1.5)

    def contextMenuEvent(self, event):
        menu = QMenu()
        menu.setStyleSheet("""
            QMenu {
                background-color: #141c28;
                border: 1px solid #2a3a52;
                border-radius: 8px;
                padding: 4px;
                font-family: 'Segoe UI';
                font-size: 9pt;
                color: #e8edf3;
            }
            QMenu::item {
                padding: 6px 20px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #3d8bfd;
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background: #2a3a52;
                margin: 4px 8px;
            }
        """)
        act_view = menu.addAction("View Layout")
        act_props = menu.addAction("Properties")
        menu.addSeparator()
        if self._status == "approved":
            act_status = menu.addAction("⬤  Mark as Previewing")
        else:
            act_status = menu.addAction("⬤  Approve")

        action = menu.exec(event.screenPos())
        if action == act_view:
            view = self.scene().views()[0] if self.scene().views() else None
            if view and hasattr(view, 'view_block_layout'):
                view.view_block_layout.emit(self.block_id)
        elif action == act_props:
            view = self.scene().views()[0] if self.scene().views() else None
            if view and hasattr(view, 'block_properties_requested'):
                view.block_properties_requested.emit(self.block_id)
        elif action == act_status:
            self.set_status("previewing" if self._status == "approved" else "approved")


class CircuitSymbolItem(SymbolBlockItem):
    """Single black-box circuit symbol used in Floorplan Symbol sub-mode."""

    def __init__(self, block_id, block_name, device_ids, port_info=None,
                 x=0, y=0, width=420, height=260, status="previewing"):
        super().__init__(
            block_id=block_id,
            block_name=block_name,
            device_ids=device_ids,
            port_info=port_info,
            x=x,
            y=y,
            width=width,
            height=height,
            status=status,
            theme_color=QColor("#2f8d72"),
        )

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        is_selected = self.isSelected()

        if is_selected:
            glow = QPainterPath()
            glow.addRoundedRect(rect.adjusted(-4, -4, 4, 4), 14, 14)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(47, 141, 114, 60)))
            painter.drawPath(glow)

        fill_top = QColor("#1f6a56") if self._is_hovered else QColor("#1a5c4c")
        fill_bot = QColor("#154d40") if self._is_hovered else QColor("#14463b")
        grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        grad.setColorAt(0, fill_top)
        grad.setColorAt(1, fill_bot)

        painter.setBrush(QBrush(grad))
        border = QColor("#7dd3b6") if is_selected else QColor("#3f8f77")
        painter.setPen(QPen(border, 2.0 if is_selected else 1.6))
        painter.drawRoundedRect(rect, 12, 12)

        # Circuit name
        painter.setPen(QPen(QColor("#e7f9f1")))
        painter.setFont(QFont("Segoe UI", 20, QFont.Weight.DemiBold))
        painter.drawText(
            QRectF(rect.x(), rect.y() + rect.height() * 0.28, rect.width(), 46),
            Qt.AlignmentFlag.AlignCenter,
            self.block_name,
        )

        # Device count
        painter.setPen(QPen(QColor("#b7ddcf")))
        painter.setFont(QFont("Segoe UI", 10))
        painter.drawText(
            QRectF(rect.x(), rect.y() + rect.height() * 0.50, rect.width(), 28),
            Qt.AlignmentFlag.AlignCenter,
            f"{len(self.device_ids)} devices",
        )

        # Status badge
        badge_color = _BADGE_APPROVED if self._status == "approved" else _BADGE_PREVIEW
        badge_text = "Approved" if self._status == "approved" else "Previewing"
        bx, by = rect.x() + 14, rect.y() + 14
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(badge_color))
        painter.drawEllipse(QPointF(bx + 4, by + 4), 4, 4)
        painter.setPen(QPen(badge_color))
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        painter.drawText(QPointF(bx + 14, by + 9), badge_text)

        self._draw_ports_with_labels(painter, rect)

    def _draw_ports_with_labels(self, painter, rect):
        port_colors = {
            "power": QColor("#ef4444"),
            "ground": QColor("#22c55e"),
            "signal": QColor("#facc15"),
            "clock": QColor("#facc15"),
        }
        sides = {"left": [], "right": [], "top": [], "bottom": []}
        for port_name, info in (self.port_info or {}).items():
            side = info.get("side", "left")
            sides.setdefault(side, []).append((str(port_name), info))

        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Medium))
        dot_r = 4.5
        for side, ports in sides.items():
            for i, (port_name, info) in enumerate(ports):
                color = port_colors.get(info.get("type", "signal"), QColor("#facc15"))
                spacing = (i + 1) / (len(ports) + 1)

                if side == "left":
                    px = rect.left() - 2
                    py = rect.top() + rect.height() * spacing
                    tx = px + 10
                    align = Qt.AlignmentFlag.AlignLeft
                elif side == "right":
                    px = rect.right() + 2
                    py = rect.top() + rect.height() * spacing
                    tx = px - 110
                    align = Qt.AlignmentFlag.AlignRight
                elif side == "top":
                    px = rect.left() + rect.width() * spacing
                    py = rect.top() - 2
                    tx = px - 45
                    align = Qt.AlignmentFlag.AlignCenter
                else:
                    px = rect.left() + rect.width() * spacing
                    py = rect.bottom() + 2
                    tx = px - 45
                    align = Qt.AlignmentFlag.AlignCenter

                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(color))
                painter.drawEllipse(QPointF(px, py), dot_r, dot_r)

                painter.setPen(QPen(color.lighter(125)))
                if side in ("left", "right"):
                    painter.drawText(QRectF(tx, py - 8, 100, 16), align | Qt.AlignmentFlag.AlignVCenter, port_name)
                elif side == "top":
                    painter.drawText(QRectF(tx, py + 8, 90, 14), align | Qt.AlignmentFlag.AlignTop, port_name)
                else:
                    painter.drawText(QRectF(tx, py - 22, 90, 14), align | Qt.AlignmentFlag.AlignBottom, port_name)


# ═══════════════════════════════════════════════════════════════
# Device-Level Item — Unit Block Mode (individual MOS view)
# ═══════════════════════════════════════════════════════════════
class UnitBlockDeviceItem(QGraphicsRectItem):
    """Individual MOS device shown in Unit Block floorplan mode."""

    def __init__(self, dev_id, dev_type, x=0, y=0, width=60, height=80,
                 orientation="R0", is_dummy=False):
        super().__init__(0, 0, width, height)
        self.setPos(x, y)
        self.dev_id = dev_id
        self.dev_type = dev_type.lower() if dev_type else "nmos"
        self.orientation = orientation
        self.is_dummy = is_dummy
        self._is_hovered = False

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptHoverEvents(True)

    def hoverEnterEvent(self, event):
        self._is_hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._is_hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        is_selected = self.isSelected()

        # Choose fill/border by type
        if self.is_dummy:
            fill, border_c = _DUMMY_FILL, _DUMMY_BORDER
        elif "pmos" in self.dev_type or "pfet" in self.dev_type:
            fill, border_c = _PMOS_FILL, _PMOS_BORDER
        else:
            fill, border_c = _NMOS_FILL, _NMOS_BORDER

        if self._is_hovered:
            fill = fill.lighter(125)

        # Glow on selection
        if is_selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(_ACCENT.red(), _ACCENT.green(), _ACCENT.blue(), 35)))
            painter.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), 5, 5)

        # Body
        painter.setBrush(QBrush(fill))
        border = _BLOCK_SEL if is_selected else border_c
        painter.setPen(QPen(border, 1.5 if is_selected else 1.0))
        painter.drawRoundedRect(rect, 5, 5)

        # MOS symbol inside
        cx, cy = rect.center().x(), rect.center().y()
        w, h = rect.width(), rect.height()

        # Gate line
        gate_pen = QPen(QColor("#8899aa"), 1.2)
        painter.setPen(gate_pen)
        painter.drawLine(QPointF(cx - w * 0.25, cy), QPointF(cx - w * 0.08, cy))
        # Channel
        ch_pen = QPen(border_c.lighter(140), 1.5)
        painter.setPen(ch_pen)
        painter.drawLine(QPointF(cx - w * 0.05, cy - h * 0.15),
                         QPointF(cx - w * 0.05, cy + h * 0.15))
        # Source/Drain
        painter.setPen(gate_pen)
        painter.drawLine(QPointF(cx, cy - h * 0.12), QPointF(cx + w * 0.18, cy - h * 0.12))
        painter.drawLine(QPointF(cx, cy + h * 0.12), QPointF(cx + w * 0.18, cy + h * 0.12))

        # Device ID label at bottom
        painter.setPen(QPen(_TEXT_PRIMARY))
        painter.setFont(QFont("Segoe UI", 6, QFont.Weight.DemiBold))
        label_rect = QRectF(rect.x(), rect.bottom() - 14, rect.width(), 12)
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, self.dev_id)

        # Type badge at top
        type_color = _PMOS_BORDER if "pmos" in self.dev_type else _NMOS_BORDER
        if self.is_dummy:
            type_color = _DUMMY_BORDER
        painter.setPen(QPen(type_color))
        painter.setFont(QFont("Segoe UI", 5))
        type_text = "PMOS" if "pmos" in self.dev_type else "NMOS"
        if self.is_dummy:
            type_text = "DUM"
        painter.drawText(QRectF(rect.x(), rect.y() + 3, rect.width(), 10),
                         Qt.AlignmentFlag.AlignCenter, type_text)


# ═══════════════════════════════════════════════════════════════
# Block Connection Line
# ═══════════════════════════════════════════════════════════════
class BlockConnectionLine(QGraphicsPathItem):
    """Labeled connection line between two blocks."""

    def __init__(self, start_block, end_block, net_name="", net_type="signal"):
        super().__init__()
        self._start = start_block
        self._end = end_block
        self.net_name = net_name
        self.net_type = net_type

        colors = {
            "power": _PORT_POWER, "ground": _PORT_GROUND,
            "signal": _PORT_SIGNAL, "clock": _PORT_CLK,
        }
        self._color = colors.get(net_type, _PORT_SIGNAL)
        self.setPen(QPen(self._color, 1.2, Qt.PenStyle.SolidLine,
                         Qt.PenCapStyle.RoundCap))
        self.setZValue(-1)
        self.update_path()

    def update_path(self):
        try:
            sr = self._start.sceneBoundingRect()
            er = self._end.sceneBoundingRect()
        except RuntimeError:
            return
        sp = self._edge_point(sr, er.center())
        ep = self._edge_point(er, sr.center())
        path = QPainterPath()
        path.moveTo(sp)
        mid_x = (sp.x() + ep.x()) / 2
        path.cubicTo(QPointF(mid_x, sp.y()), QPointF(mid_x, ep.y()), ep)
        self.setPath(path)

    @staticmethod
    def _edge_point(rect, target):
        cx, cy = rect.center().x(), rect.center().y()
        dx = target.x() - cx
        dy = target.y() - cy
        if abs(dx) < 0.01 and abs(dy) < 0.01:
            return QPointF(cx, rect.top())
        if abs(dx) * rect.height() > abs(dy) * rect.width():
            if dx > 0:
                return QPointF(rect.right(), cy + dy * (rect.width() / 2) / abs(dx))
            else:
                return QPointF(rect.left(), cy - dy * (rect.width() / 2) / abs(dx))
        else:
            if dy > 0:
                return QPointF(cx + dx * (rect.height() / 2) / abs(dy), rect.bottom())
            else:
                return QPointF(cx - dx * (rect.height() / 2) / abs(dy), rect.top())

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        if self.net_name:
            path = self.path()
            mid = path.pointAtPercent(0.5)
            painter.setPen(QPen(self._color.lighter(140)))
            painter.setFont(QFont("Segoe UI", 7))
            painter.drawText(mid + QPointF(4, -4), self.net_name)


# ═══════════════════════════════════════════════════════════════
# Sub-Mode Toggle (Blocks | Symbol)
# ═══════════════════════════════════════════════════════════════
class FloorplanSubModeToggle(QWidget):
    sub_mode_changed = Signal(str)  # "blocks" or "symbol"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(30)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)

        self.setStyleSheet("""
            FloorplanSubModeToggle {
                background-color: rgba(26, 29, 34, 0.94);
                border: 1px solid #434a55;
                border-radius: 12px;
            }
        """)

        btn_style = """
            QPushButton {{
                background-color: transparent;
                color: #9ba3af;
                border: none;
                border-radius: 9px;
                padding: 3px 14px;
                font-family: 'Segoe UI';
                font-size: 8pt;
                font-weight: 600;
                min-height: 22px;
            }}
            QPushButton:hover {{
                color: #f3f4f6;
                background-color: rgba(255,255,255,0.06);
            }}
            QPushButton:checked {{
                background-color: #6b7280;
                color: #ffffff;
            }}
        """

        self._btn_blocks = QPushButton("Blocks")
        self._btn_blocks.setCheckable(True)
        self._btn_blocks.setStyleSheet(btn_style)
        self._btn_blocks.clicked.connect(lambda: self._select("blocks"))

        self._btn_symbol = QPushButton("Symbol")
        self._btn_symbol.setCheckable(True)
        self._btn_symbol.setStyleSheet(btn_style)
        self._btn_symbol.clicked.connect(lambda: self._select("symbol"))

        layout.addWidget(self._btn_blocks)
        layout.addWidget(self._btn_symbol)
        self._current = "blocks"
        self._restore_mode()

    def _select(self, mode):
        if mode not in {"blocks", "symbol"}:
            mode = "blocks"
        self._current = mode
        self._btn_blocks.setChecked(mode == "blocks")
        self._btn_symbol.setChecked(mode == "symbol")
        settings = QSettings("SymbolicEditor", "FloorplanSubMode")
        settings.setValue("last_mode", mode)
        self.sub_mode_changed.emit(mode)

    def _restore_mode(self):
        settings = QSettings("SymbolicEditor", "FloorplanSubMode")
        mode = settings.value("last_mode", "blocks")
        if mode not in {"blocks", "symbol"}:
            mode = "blocks"
        self._current = mode
        self._btn_blocks.setChecked(mode == "blocks")
        self._btn_symbol.setChecked(mode == "symbol")

    def current(self):
        return self._current


# ═══════════════════════════════════════════════════════════════
# Floorplan View (Main Widget)
# ═══════════════════════════════════════════════════════════════
class FloorplanView(QGraphicsView):
    """High-level floorplan view with Blocks and circuit Symbol sub-modes."""

    block_selected = Signal(str)
    view_block_layout = Signal(str)
    block_properties_requested = Signal(str)
    sub_mode_changed = Signal(str)  # "blocks" or "symbol"

    def __init__(self, parent=None):
        super().__init__(parent)

        self.fp_scene = QGraphicsScene()
        self.setScene(self.fp_scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.setStyleSheet("""
            QGraphicsView {
                border: none;
                background-color: #14161a;
            }
        """)

        self._blocks: dict[str, SymbolBlockItem] = {}
        self._connections: list[BlockConnectionLine] = []
        self._device_items: list[UnitBlockDeviceItem] = []
        self._sub_mode = "blocks"
        self._cached_nodes = None
        self._cached_terminal_nets = None
        self._cached_design_name = None

        # Sub-mode toggle overlay
        self._sub_toggle = FloorplanSubModeToggle(self)
        self._sub_toggle.sub_mode_changed.connect(self._on_sub_mode_changed)
        self._sub_toggle.move(10, 10)
        self._sub_mode = self._sub_toggle.current()

        # Zoom
        self.zoom_factor = 1.15
        self._zoom_level = 1.0

        self.fp_scene.selectionChanged.connect(self._on_selection_changed)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sub_toggle.move(
            self.viewport().width() - self._sub_toggle.width() - 12, 12
        )

    # ── Data Loading (Symbol Mode) ─────────────────────────────
    def load_blocks(self, blocks_data, connections_data=None):
        self.fp_scene.clear()
        self._blocks.clear()
        self._connections.clear()
        self._device_items.clear()

        cols = max(1, int(math.ceil(math.sqrt(len(blocks_data)))))
        block_w, block_h = 220, 140
        gap_x, gap_y = 80, 60

        for i, bdata in enumerate(blocks_data):
            col = i % cols
            row = i // cols
            x = bdata.get("x")
            y = bdata.get("y")
            if x is None or y is None:
                x = col * (block_w + gap_x)
                y = row * (block_h + gap_y)

            raw_color = bdata.get("color")
            if isinstance(raw_color, QColor):
                block_color = raw_color
            elif isinstance(raw_color, str):
                block_color = QColor(raw_color)
            else:
                block_color = _BLOCK_THEME_COLORS[i % len(_BLOCK_THEME_COLORS)]

            block = SymbolBlockItem(
                block_id=bdata.get("id", f"block_{i}"),
                block_name=bdata.get("name", f"Block {i}"),
                device_ids=bdata.get("devices", []),
                port_info=bdata.get("ports", {}),
                x=x, y=y, width=block_w, height=block_h,
                status=bdata.get("status", "previewing"),
                theme_color=block_color,
            )
            self.fp_scene.addItem(block)
            self._blocks[block.block_id] = block

        if connections_data:
            for conn in connections_data:
                src = self._blocks.get(conn.get("from"))
                dst = self._blocks.get(conn.get("to"))
                if src and dst:
                    line = BlockConnectionLine(src, dst,
                        net_name=conn.get("net", ""),
                        net_type=conn.get("type", "signal"))
                    self.fp_scene.addItem(line)
                    self._connections.append(line)

        self.fp_scene.setSceneRect(
            self.fp_scene.itemsBoundingRect().adjusted(-60, -60, 60, 60))

    # ── Data Loading (Unit Block Mode — Individual Devices) ────
    def load_devices_unit_block(self, nodes):
        """Load individual devices for Unit Block mode at original coordinates."""
        self.fp_scene.clear()
        self._blocks.clear()
        self._connections.clear()
        self._device_items.clear()

        scale_xy = 80.0
        scale_w = 72.0
        scale_h = 96.0

        for i, node in enumerate(nodes):
            geom = node.get("geometry", {}) or {}
            x = float(geom.get("x", i)) * scale_xy
            y = -float(geom.get("y", 0.0)) * scale_xy
            width = max(42.0, float(geom.get("width", 0.7)) * scale_w)
            height = max(54.0, float(geom.get("height", 0.55)) * scale_h)

            item = UnitBlockDeviceItem(
                dev_id=node.get("id", f"DEV{i}"),
                dev_type=str(node.get("type", "nmos")),
                x=x,
                y=y,
                width=width,
                height=height,
                orientation=geom.get("orientation", "R0"),
                is_dummy=node.get("is_dummy", False),
            )
            self.fp_scene.addItem(item)
            self._device_items.append(item)

        self.fp_scene.setSceneRect(
            self.fp_scene.itemsBoundingRect().adjusted(-40, -40, 40, 40))

    def load_from_nodes(self, nodes, terminal_nets=None, design_name=None):
        """Auto-detect blocks from node data."""
        self._cached_nodes = nodes
        self._cached_terminal_nets = terminal_nets
        if design_name:
            self._cached_design_name = str(design_name)

        if self._sub_mode == "symbol":
            self._load_circuit_symbol(nodes, terminal_nets)
        else:
            self._load_symbol_from_nodes(nodes, terminal_nets)

    def _build_circuit_ports(self, terminal_nets):
        nets = set()
        for dev_nets in (terminal_nets or {}).values():
            for net in (dev_nets or {}).values():
                if net:
                    nets.add(str(net))

        if not nets:
            return {}

        supply_power = {"VDD", "VCC", "AVDD", "DVDD"}
        supply_ground = {"VSS", "GND", "AVSS", "DVSS"}

        def _is_internal(name):
            n = name.upper()
            return bool(re.match(r"^(NET|N)\d+$", n))

        named = sorted(nets, key=lambda n: n.upper())
        external = [n for n in named if not _is_internal(n)]
        if not external:
            external = named

        power = [n for n in external if n.upper() in supply_power]
        ground = [n for n in external if n.upper() in supply_ground]
        others = [n for n in external if n not in power and n not in ground]

        left = []
        right = []
        for net in others:
            up = net.upper()
            if "OUT" in up:
                right.append(net)
            elif "IN" in up or "CLK" in up:
                left.append(net)
            elif len(left) <= len(right):
                left.append(net)
            else:
                right.append(net)

        ports = {}
        for n in power[:4]:
            ports[n] = {"side": "top", "type": "power"}
        for n in ground[:4]:
            ports[n] = {"side": "bottom", "type": "ground"}
        for n in left[:5]:
            ptype = "clock" if "CLK" in n.upper() else "signal"
            ports[n] = {"side": "left", "type": ptype}
        for n in right[:5]:
            ptype = "clock" if "CLK" in n.upper() else "signal"
            ports[n] = {"side": "right", "type": ptype}
        return ports

    def _load_circuit_symbol(self, nodes, terminal_nets=None):
        self.fp_scene.clear()
        self._blocks.clear()
        self._connections.clear()
        self._device_items.clear()

        design_name = (self._cached_design_name or "Circuit").strip()
        status = "previewing"
        ports = self._build_circuit_ports(terminal_nets)
        dev_ids = [n.get("id", "") for n in nodes if n.get("id")]

        symbol = CircuitSymbolItem(
            block_id="__CIRCUIT__",
            block_name=design_name,
            device_ids=dev_ids,
            port_info=ports,
            x=-210,
            y=-130,
            width=420,
            height=260,
            status=status,
        )
        self.fp_scene.addItem(symbol)
        self._blocks[symbol.block_id] = symbol
        self.fp_scene.setSceneRect(
            self.fp_scene.itemsBoundingRect().adjusted(-80, -80, 80, 80)
        )

    def _load_symbol_from_nodes(self, nodes, terminal_nets=None):
        """Group nodes into blocks by naming convention."""
        groups = {}
        ungrouped_nmos = []
        ungrouped_pmos = []
        node_map = {n.get("id", ""): n for n in nodes}

        def _block_center(device_ids):
            xs = []
            ys = []
            for dev_id in device_ids:
                geom = (node_map.get(dev_id, {}) or {}).get("geometry", {}) or {}
                try:
                    xs.append(float(geom.get("x", 0.0)))
                    ys.append(float(geom.get("y", 0.0)))
                except (TypeError, ValueError):
                    continue
            if not xs or not ys:
                return None, None
            # Preserve rough spatial relation from original layout in floorplan mode.
            return (sum(xs) / len(xs)) * 240.0, -(sum(ys) / len(ys)) * 240.0

        for node in nodes:
            dev_id = node.get("id", "")
            dev_type = str(node.get("type", "")).lower()
            match = re.match(r'^([A-Za-z]+\d+)_', dev_id)
            if match:
                prefix = match.group(1)
                groups.setdefault(prefix, []).append(dev_id)
            elif "nmos" in dev_type or "nfet" in dev_type:
                ungrouped_nmos.append(dev_id)
            elif "pmos" in dev_type or "pfet" in dev_type:
                ungrouped_pmos.append(dev_id)
            else:
                groups.setdefault("OTHER", []).append(dev_id)

        blocks = []
        for group_name, devs in sorted(groups.items()):
            if len(devs) < 2:
                dev_type = "nmos"
                for n in nodes:
                    if n.get("id") == devs[0]:
                        dev_type = str(n.get("type", "nmos")).lower()
                        break
                if "pmos" in dev_type:
                    ungrouped_pmos.extend(devs)
                else:
                    ungrouped_nmos.extend(devs)
                continue

            ports = {}
            if terminal_nets:
                all_nets = set()
                for dev_id in devs:
                    tnets = terminal_nets.get(dev_id, {})
                    for net in tnets.values():
                        if net:
                            all_nets.add(net)
                supply = {"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS"}
                for net in sorted(all_nets):
                    nu = net.upper()
                    if nu in supply:
                        ntype = "power" if "VDD" in nu or "VCC" in nu else "ground"
                        side = "top" if ntype == "power" else "bottom"
                    else:
                        ntype = "signal"
                        side = "left" if len(ports) % 2 == 0 else "right"
                    ports[net] = {"side": side, "type": ntype}

            cx, cy = _block_center(devs)
            blocks.append({
                "id": group_name, "name": group_name,
                "devices": devs, "ports": ports, "status": "previewing",
                "x": cx, "y": cy,
            })

        if ungrouped_nmos:
            cx, cy = _block_center(ungrouped_nmos)
            blocks.append({
                "id": "NMOS", "name": "NMOS", "devices": ungrouped_nmos,
                "ports": {"VSS": {"side": "bottom", "type": "ground"}},
                "status": "previewing",
                "x": cx, "y": cy,
            })
        if ungrouped_pmos:
            cx, cy = _block_center(ungrouped_pmos)
            blocks.append({
                "id": "PMOS", "name": "PMOS", "devices": ungrouped_pmos,
                "ports": {"VDD": {"side": "top", "type": "power"}},
                "status": "previewing",
                "x": cx, "y": cy,
            })

        # Auto-detect connections
        connections = []
        if terminal_nets:
            block_nets = {}
            for b in blocks:
                nets = set()
                for dev_id in b["devices"]:
                    for net in terminal_nets.get(dev_id, {}).values():
                        if net:
                            nets.add(net)
                block_nets[b["id"]] = nets
            block_ids = [b["id"] for b in blocks]
            supply = {"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS"}
            seen = set()
            for i, bid_a in enumerate(block_ids):
                for bid_b in block_ids[i+1:]:
                    shared = block_nets.get(bid_a, set()) & block_nets.get(bid_b, set())
                    for net in sorted(shared - supply):
                        key = tuple(sorted([bid_a, bid_b])) + (net,)
                        if key not in seen:
                            seen.add(key)
                            connections.append({
                                "from": bid_a, "to": bid_b,
                                "net": net, "type": "signal",
                            })

        self.load_blocks(blocks, connections)

    # ── Fit to View ────────────────────────────────────────────
    def fit_to_view(self):
        rect = self.fp_scene.itemsBoundingRect()
        if rect.isNull():
            return
        self.fitInView(rect.adjusted(-40, -40, 40, 40),
                       Qt.AspectRatioMode.KeepAspectRatio)

    # ── Sub-mode ───────────────────────────────────────────────
    def _on_sub_mode_changed(self, mode):
        self._sub_mode = mode if mode in {"blocks", "symbol"} else "blocks"
        self.sub_mode_changed.emit(self._sub_mode)
        if self._cached_nodes:
            self.load_from_nodes(
                self._cached_nodes,
                self._cached_terminal_nets,
                self._cached_design_name,
            )
            from PySide6.QtCore import QTimer
            QTimer.singleShot(50, self.fit_to_view)

    def set_sub_mode(self, mode):
        self._sub_toggle._select(mode)

    def get_sub_mode(self):
        return self._sub_mode

    # ── Selection ──────────────────────────────────────────────
    def _on_selection_changed(self):
        selected = self.fp_scene.selectedItems()
        if selected:
            item = selected[0]
            if isinstance(item, CircuitSymbolItem) and self._sub_mode == "symbol":
                # Click-through drill-down from circuit symbol to block mode.
                self.set_sub_mode("blocks")
                return
            if isinstance(item, SymbolBlockItem):
                self.block_selected.emit(item.block_id)

    # ── Zoom ───────────────────────────────────────────────────
    def wheelEvent(self, event):
        factor = self.zoom_factor if event.angleDelta().y() > 0 else 1.0 / self.zoom_factor
        new_zoom = self._zoom_level * factor
        if 0.1 < new_zoom < 10.0:
            self._zoom_level = new_zoom
            self.scale(factor, factor)

    # ── Background ─────────────────────────────────────────────
    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        painter.fillRect(rect, _BG_DARK)

        grid_size = 30
        pen = QPen(_BG_GRID_DOT, 1.0)
        painter.setPen(pen)
        left = int(rect.left()) - (int(rect.left()) % grid_size)
        top = int(rect.top()) - (int(rect.top()) % grid_size)
        x = left
        while x < rect.right():
            y = top
            while y < rect.bottom():
                painter.drawPoint(QPointF(x, y))
                y += grid_size
            x += grid_size
