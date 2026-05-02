"""
passive_item.py — Visual items for passive components (resistors and capacitors).

ResistorItem: amber/gold tones with zig-zag body, parameter annotation.
CapacitorItem: teal/cyan tones with parallel-plate symbol, value annotation.

Both share the same drag/snap/select/orientation interface as DeviceItem.
"""

from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsItem
from PySide6.QtGui import (
    QBrush, QPen, QColor, QFont, QPainter,
    QLinearGradient, QPainterPath,
    QColor as tcolor,
)
from PySide6.QtCore import Qt, QRectF, QObject, Signal, QPointF


class DeviceSignals(QObject):
    """Helper QObject so passive items can emit signals."""
    drag_started = Signal()
    drag_finished = Signal()


class _PassiveBase(QGraphicsRectItem):
    """Shared base class for ResistorItem and CapacitorItem."""

    def __init__(self, name, dev_type, x, y, width, height):
        super().__init__(0, 0, width, height)
        self.setPos(x, y)
        self.device_name  = name
        self.device_type  = dev_type
        self.nf           = 1
        self.signals      = DeviceSignals()

        self._drag_active    = False
        self._drag_start_pos = QPointF()
        self._snap_grid_x    = None
        self._snap_grid_y    = None
        self._flip_h         = False
        self._flip_v         = False

        self._is_dimmed      = False
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setPen(QPen(Qt.PenStyle.NoPen))

    def get_logical_name(self):
        """Return the base name of the device (e.g., 'C0' for 'C0_f1')."""
        return self.device_name.split("_")[0]

    def set_dimmed(self, dimmed: bool):
        """Toggle dimmed state for visual focus."""
        if self._is_dimmed != dimmed:
            self._is_dimmed = bool(dimmed)
            self.update()

    def set_custom_color(self, base_color):
        """Stub for colorization support."""
        pass

    def reset_custom_color(self):
        """Stub for colorization support."""
        pass

    # ── Grid snapping ────────────────────────────────────────────────
    def set_snap_grid(self, grid_x, grid_y=None):
        self._snap_grid_x = float(grid_x) if grid_x else None
        self._snap_grid_y = float(grid_y) if grid_y else self._snap_grid_x

    def itemChange(self, change, value):
        if (change == QGraphicsItem.GraphicsItemChange.ItemPositionChange
                and self._snap_grid_x and self._snap_grid_y):
            x = round(value.x() / self._snap_grid_x) * self._snap_grid_x
            y = round(value.y() / self._snap_grid_y) * self._snap_grid_y
            return QPointF(x, y)
        return super().itemChange(change, value)

    # ── Drag tracking ────────────────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = self.pos()
            self._drag_active    = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if not self._drag_active and self.pos() != self._drag_start_pos:
            self._drag_active = True
            self.signals.drag_started.emit()

    def mouseReleaseEvent(self, event):
        if self._drag_active:
            self._drag_active = False
            self.signals.drag_finished.emit()
        super().mouseReleaseEvent(event)

    # ── Orientation stubs (required by editor) ────────────────────────
    def flip_horizontal(self):
        self._flip_h = not self._flip_h
        self.update()

    def flip_vertical(self):
        self._flip_v = not self._flip_v
        self.update()

    def is_flip_h(self): return self._flip_h
    def is_flip_v(self): return self._flip_v
    def set_flip_h(self, s): self._flip_h = bool(s); self.update()
    def set_flip_v(self, s): self._flip_v = bool(s); self.update()

    def orientation_string(self):
        base = "R0"
        if self._flip_h and self._flip_v: return f"{base}_FH_FV"
        if self._flip_h:                  return f"{base}_FH"
        if self._flip_v:                  return f"{base}_FV"
        return base

    # ── Terminal anchors (pin 1 = left, pin 2 = right) ────────────────
    def terminal_anchors(self):
        rect  = self.rect()
        mid_y = rect.y() + rect.height() / 2
        p1 = self.mapToScene(QPointF(rect.x(),                   mid_y))
        p2 = self.mapToScene(QPointF(rect.x() + rect.width(),    mid_y))
        # Alias so routing code that expects S/G/D still works
        return {"1": p1, "2": p2, "S": p1, "G": p1, "D": p2}

    # ── Shared helpers ────────────────────────────────────────────────
    def _draw_selection(self, painter, rect, color="#4a90d9"):
        if self.isSelected():
            c = QColor(color)
            painter.setPen(QPen(c, 2.0))
            c.setAlpha(35)
            painter.setBrush(QBrush(c))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 3, 3)

    def _format_si(self, value, unit):
        """Format a value with SI prefix, e.g. 3.68e-16 → '368.0 aF'."""
        if value <= 0:
            return ""
        # Precise scales for high-fidelity physics display
        for prefix, scale in [("f", 1e-15), ("p", 1e-12), ("n", 1e-9),
                               ("u", 1e-6),  ("m", 1e-3),  ("",  1e0),
                               ("k", 1e3),   ("M", 1e6),   ("G", 1e9)]:
            if value < scale * 1000:
                v = value / scale
                if v >= 100:
                    return f"{v:.1f}{prefix}{unit}"
                if v >= 10:
                    return f"{v:.2f}{prefix}{unit}"
                return f"{v:.3f}{prefix}{unit}"
        return f"{value:.2e}{unit}"


# =============================================================================
class ResistorItem(_PassiveBase):
    """Visual symbol for a resistor — amber body with zig-zag pattern."""

    _BG_TOP    = QColor("#fff8e1")   # warm cream top
    _BG_BOT    = QColor("#ffe082")   # amber yellow bottom
    _ZZ_COLOR  = QColor("#e65100")   # deep burnt orange zig-zag
    _BORDER    = QColor("#bf360c")   # dark red-orange border
    _LEAD      = QColor("#795548")   # brown lead lines
    _LABEL_CLR = QColor("#4e2500")   # name text
    _PIN_CLR   = QColor("#e65100")   # pin label color

    def __init__(self, name, x, y, width, height):
        super().__init__(name, "res", x, y, width, height)
        # Instance colors for colorization support
        self._bg_top    = self._BG_TOP
        self._bg_bot    = self._BG_BOT
        self._zz_color  = self._ZZ_COLOR
        self._border    = self._BORDER
        self._lead      = self._LEAD
        self._label_clr = self._LABEL_CLR
        self._pin_clr   = self._PIN_CLR

    def set_custom_color(self, base_color: QColor):
        self._bg_top    = base_color.lighter(130)
        self._bg_bot    = base_color.lighter(110)
        self._zz_color  = base_color.darker(150)
        self._border    = base_color.darker(200)
        self._lead      = base_color.darker(180)
        self._label_clr = base_color.darker(300)
        self._pin_clr   = base_color.darker(150)
        self.update()

    def reset_custom_color(self):
        self._bg_top    = self._BG_TOP
        self._bg_bot    = self._BG_BOT
        self._zz_color  = self._ZZ_COLOR
        self._border    = self._BORDER
        self._lead      = self._LEAD
        self._label_clr = self._LABEL_CLR
        self._pin_clr   = self._PIN_CLR
        self.update()

    def paint(self, painter: QPainter, option, widget=None):
        if self._is_dimmed:
            painter.setOpacity(0.15)
        else:
            painter.setOpacity(1.0)
            
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect  = self.rect()
        w, h  = rect.width(), rect.height()
        x0, y0 = rect.x(), rect.y()
        mid_y = y0 + h * 0.5

        # ── 1. Main body background (Premium multi-stop gradient) ────
        grad = QLinearGradient(x0, y0, x0, y0 + h)
        grad.setColorAt(0.0, self._bg_top)
        grad.setColorAt(0.3, self._bg_top.lighter(105))
        grad.setColorAt(1.0, self._bg_bot)
        painter.setBrush(QBrush(grad))
        painter.setPen(QPen(self._border, 1.2))
        painter.drawRoundedRect(rect.adjusted(0.75, 0.75, -0.75, -0.75), 5, 5)

        # ── 2. Subtle Glossy Overlay ──────────────────────────────────
        gloss = QLinearGradient(x0, y0, x0, mid_y)
        gloss.setColorAt(0, QColor(255, 255, 255, 50))
        gloss.setColorAt(1, QColor(255, 255, 255, 0))
        painter.setBrush(QBrush(gloss))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(x0+1, y0+1, w-2, h*0.45), 4, 4)

        # ── 3. Metallic contact pads at ends ──────────────────────────
        pad_w = w * 0.08
        pad_pen = QPen(self._border.darker(150), 1.0)
        pad_brush = QLinearGradient(x0, y0, x0+pad_w, y0)
        pad_brush.setColorAt(0, self._border.darker(120))
        pad_brush.setColorAt(1, self._border.lighter(120))
        
        painter.setPen(pad_pen)
        painter.setBrush(QBrush(pad_brush))
        painter.drawRect(QRectF(x0+0.5, y0+0.5, pad_w, h-1))
        
        pad_brush_r = QLinearGradient(x0+w-pad_w, y0, x0+w, y0)
        pad_brush_r.setColorAt(0, self._border.lighter(120))
        pad_brush_r.setColorAt(1, self._border.darker(120))
        painter.setBrush(QBrush(pad_brush_r))
        painter.drawRect(QRectF(x0+w-pad_w-0.5, y0+0.5, pad_w, h-1))

        # ── 4. Zig-zag pattern with drop-shadow effect ────────────────
        lead_w = pad_w
        body_x0 = x0 + lead_w + 2
        body_x1 = x0 + w - lead_w - 2
        body_w  = body_x1 - body_x0

        n_teeth = max(4, int(body_w / (h * 0.3)))
        tooth_w  = body_w / n_teeth
        zz_amp   = h * 0.25

        # Draw shadow first
        shadow_pen = QPen(QColor(0, 0, 0, 60), max(1.8, h * 0.08))
        shadow_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        shadow_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(shadow_pen)
        
        path = QPainterPath()
        path.moveTo(body_x0, mid_y)
        for i in range(n_teeth):
            lx = body_x0 + i * tooth_w
            rx = lx + tooth_w
            peak_y = mid_y - zz_amp if i % 2 == 0 else mid_y + zz_amp
            path.lineTo(lx + tooth_w * 0.5, peak_y)
            path.lineTo(rx, mid_y)
        
        painter.save()
        painter.translate(0.8, 0.8)
        painter.drawPath(path)
        painter.restore()

        # Draw main zig-zag
        zz_pen = QPen(self._zz_color, max(1.5, h * 0.07))
        zz_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        zz_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(zz_pen)
        painter.drawPath(path)

        # ── 5. Terminal Labels (1 / 2) ────────────────────────────────
        pin_fs = max(5, min(9, int(h * 0.25)))
        painter.setFont(QFont("Segoe UI Variable Display", pin_fs, QFont.Weight.Black))
        
        # Draw "1" with subtle glow
        painter.setPen(QColor(0,0,0,100))
        painter.drawText(QRectF(x0+1, y0+1, pad_w*2, h*0.5), Qt.AlignmentFlag.AlignCenter, "1")
        painter.setPen(self._pin_clr.lighter(150))
        painter.drawText(QRectF(x0, y0, pad_w*2, h*0.5), Qt.AlignmentFlag.AlignCenter, "1")
        
        # Draw "2"
        painter.setPen(QColor(0,0,0,100))
        painter.drawText(QRectF(x0+w-pad_w*2+1, y0+1, pad_w*2, h*0.5), Qt.AlignmentFlag.AlignCenter, "2")
        painter.setPen(self._pin_clr.lighter(150))
        painter.drawText(QRectF(x0+w-pad_w*2, y0, pad_w*2, h*0.5), Qt.AlignmentFlag.AlignCenter, "2")

        # ── 6. Device Name (High Contrast) ────────────────────────────
        name_fs = max(6, min(11, int(w * 0.11)))
        painter.setFont(QFont("Segoe UI Variable Display", name_fs, QFont.Weight.ExtraBold))
        painter.setPen(QColor(0,0,0,80)) # shadow
        name_rect = QRectF(x0 + lead_w, y0 + 1, body_w, h * 0.45)
        painter.drawText(name_rect.translated(0.5, 0.5), Qt.AlignmentFlag.AlignCenter, self.device_name)
        painter.setPen(self._label_clr)
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignCenter, self.device_name)

        # ── 7. Selection Glow ─────────────────────────────────────────
        self._draw_selection(painter, rect, "#f39c12")


# =============================================================================
class CapacitorItem(_PassiveBase):
    """Visual symbol for a capacitor — teal body with parallel-plate symbol."""

    _BG_TOP   = QColor("#e0f7fa")   # light cyan top
    _BG_BOT   = QColor("#80deea")   # teal bottom
    _PLATE    = QColor("#00695c")   # dark teal plate bars
    _BORDER   = QColor("#004d40")   # deep teal border
    _LEAD     = QColor("#00796b")   # teal lead lines
    _LABEL_CLR= QColor("#00251a")   # name text (very dark teal)
    _PIN_CLR  = QColor("#00695c")   # pin label color

    def __init__(self, name, x, y, width, height):
        super().__init__(name, "cap", x, y, width, height)
        # Instance colors for colorization support
        self._bg_top    = self._BG_TOP
        self._bg_bot    = self._BG_BOT
        self._plate     = self._PLATE
        self._border    = self._BORDER
        self._lead      = self._LEAD
        self._label_clr = self._LABEL_CLR
        self._pin_clr   = self._PIN_CLR

    def set_custom_color(self, base_color: QColor):
        self._bg_top    = base_color.lighter(130)
        self._bg_bot    = base_color.lighter(110)
        self._plate     = base_color.darker(150)
        self._border    = base_color.darker(200)
        self._lead      = base_color.darker(180)
        self._label_clr = base_color.darker(300)
        self._pin_clr   = base_color.darker(150)
        self.update()

    def reset_custom_color(self):
        self._bg_top    = self._BG_TOP
        self._bg_bot    = self._BG_BOT
        self._plate     = self._PLATE
        self._border    = self._BORDER
        self._lead      = self._LEAD
        self._label_clr = self._LABEL_CLR
        self._pin_clr   = self._PIN_CLR
        self.update()

    def paint(self, painter: QPainter, option, widget=None):
        if self._is_dimmed:
            painter.setOpacity(0.15)
        else:
            painter.setOpacity(1.0)
            
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect  = self.rect()
        w, h  = rect.width(), rect.height()
        x0, y0 = rect.x(), rect.y()
        mid_y = y0 + h * 0.5

        # ── 1. Main body background (Glassy Teal Gradient) ────────────
        grad = QLinearGradient(x0, y0, x0, y0 + h)
        grad.setColorAt(0.0, self._bg_top)
        grad.setColorAt(0.4, self._bg_top.lighter(110))
        grad.setColorAt(1.0, self._bg_bot)
        painter.setBrush(QBrush(grad))
        painter.setPen(QPen(self._border, 1.2))
        painter.drawRoundedRect(rect.adjusted(0.75, 0.75, -0.75, -0.75), 6, 6)

        # ── 2. Glassy Gloss ───────────────────────────────────────────
        gloss = QLinearGradient(x0, y0, x0 + w, y0 + h)
        gloss.setColorAt(0, QColor(255, 255, 255, 60))
        gloss.setColorAt(0.5, QColor(255, 255, 255, 0))
        painter.setBrush(QBrush(gloss))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect.adjusted(1.5, 1.5, -1.5, -1.5), 5, 5)

        # ── 3. Internal Dielectric Plate Geometry ─────────────────────
        cx     = x0 + w * 0.5
        gap    = max(5.0, w * 0.08)
        ph     = h * 0.7
        py0    = y0 + (h - ph) * 0.5
        lead_w = w * 0.25

        # Vertical Plate Bars (3D Effect)
        plate_w = max(4.0, w * 0.08)
        
        # Shadow for plates
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(0,0,0,40)))
        painter.drawRect(QRectF(cx - gap/2 - plate_w + 1, py0 + 1, plate_w, ph))
        painter.drawRect(QRectF(cx + gap/2 + 1, py0 + 1, plate_w, ph))

        # Main plates
        plate_grad = QLinearGradient(0, py0, 0, py0 + ph)
        plate_grad.setColorAt(0, self._plate.lighter(130))
        plate_grad.setColorAt(1, self._plate.darker(110))
        painter.setBrush(QBrush(plate_grad))
        painter.drawRect(QRectF(cx - gap/2 - plate_w, py0, plate_w, ph))
        painter.drawRect(QRectF(cx + gap/2, py0, plate_w, ph))

        # ── 4. Internal Lead Connections ──────────────────────────────
        lead_pen = QPen(self._lead, max(1.5, h * 0.06))
        lead_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(lead_pen)
        painter.drawLine(QPointF(x0, mid_y), QPointF(cx - gap/2 - plate_w, mid_y))
        painter.drawLine(QPointF(cx + gap/2 + plate_w, mid_y), QPointF(x0 + w, mid_y))

        # ── 5. Polarity / Terminal Labels ─────────────────────────────
        pin_fs = max(5, min(10, int(h * 0.28)))
        painter.setFont(QFont("Segoe UI Variable Display", pin_fs, QFont.Weight.Black))
        painter.setPen(self._pin_clr)
        painter.drawText(QRectF(x0+2, y0+2, lead_w, h-4), Qt.AlignmentFlag.AlignCenter, "+")
        painter.drawText(QRectF(x0+w-lead_w-2, y0+2, lead_w, h-4), Qt.AlignmentFlag.AlignCenter, "\u2212")

        # ── 6. Device Name ────────────────────────────────────────────
        name_fs = max(6, min(12, int(w * 0.11)))
        painter.setFont(QFont("Segoe UI Variable Display", name_fs, QFont.Weight.ExtraBold))
        name_rect = QRectF(x0 + lead_w, y0 + 1, w - 2 * lead_w, h * 0.45)
        
        # Text shadow
        painter.setPen(QColor(0,0,0,80))
        painter.drawText(name_rect.translated(0.5, 0.5), Qt.AlignmentFlag.AlignCenter, self.device_name)
        # Text main
        painter.setPen(self._label_clr)
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignCenter, self.device_name)

        # ── 7. Selection Glow ─────────────────────────────────────────
        self._draw_selection(painter, rect, "#1abc9c")
