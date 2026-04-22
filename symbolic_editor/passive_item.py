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
        
        self._base_width = width
        self._base_height = height
        self.electrical_value = 0.0
        self.baseline_value = 1.0
        self.unit = ""

        self._drag_active    = False
        self._drag_start_pos = QPointF()
        self._snap_grid_x    = None
        self._snap_grid_y    = None
        self._flip_h         = False
        self._flip_v         = False

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setPen(QPen(Qt.PenStyle.NoPen))

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

    # ── Drag tracking ─────────────────────────────────────────────────
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
        """Format a value with SI prefix, e.g. 1e-6 → '1.00u'."""
        if value <= 0:
            return ""
            
        prefixes = [("M", 1e6), ("k", 1e3), ("", 1.0), ("m", 1e-3), 
                    ("u", 1e-6), ("n", 1e-9), ("p", 1e-12), ("f", 1e-15)]
                    
        for prefix, scale in prefixes:
            if value >= scale * 0.9995:
                v = value / scale
                if v >= 100:
                    return f"{v:.1f}{prefix}{unit}"
                return f"{v:.2f}{prefix}{unit}"
                
        return f"{value:.2e}{unit}"

    def update_size_by_value(self, base_val=None):
        """Visibly scale the device width based on its value."""
        if base_val is not None:
            self.baseline_value = base_val
            
        import math
        bv = self.baseline_value
        if self.electrical_value <= 0 or bv <= 0:
            scale_w = 1.0
            scale_h = 1.0
        else:
            ratio = self.electrical_value / bv
            # Logarithmic scaling: +0.5x width per 10x value increase
            scale_w = 1.0 + 0.5 * math.log10(ratio)
            # Restrict to visual bounds (max ~3x, min 0.4x)
            scale_w = max(0.4, min(scale_w, 3.0))
            scale_h = 1.0
        
        self.prepareGeometryChange()
        self.setRect(0, 0, self._base_width * scale_w, self._base_height * scale_h)
        self.update()


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

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect  = self.rect()
        w, h  = rect.width(), rect.height()
        x0, y0 = rect.x(), rect.y()
        mid_y = y0 + h * 0.5

        # ── Background gradient ──────────────────────────────────────
        grad = QLinearGradient(x0, y0, x0, y0 + h)
        grad.setColorAt(0.0, self._BG_TOP)
        grad.setColorAt(1.0, self._BG_BOT)
        painter.setBrush(QBrush(grad))
        painter.setPen(QPen(self._BORDER, 1.5))
        painter.drawRoundedRect(rect.adjusted(0.75, 0.75, -0.75, -0.75), 4, 4)

        # ── Lead wires from edges to body ────────────────────────────
        lead_w = w * 0.08
        body_x0 = x0 + lead_w
        body_x1 = x0 + w - lead_w
        body_w  = body_x1 - body_x0

        lead_pen = QPen(self._LEAD, max(1.2, h * 0.06),
                        Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap)
        painter.setPen(lead_pen)
        painter.drawLine(QPointF(x0, mid_y), QPointF(body_x0, mid_y))
        painter.drawLine(QPointF(body_x1, mid_y), QPointF(x0 + w, mid_y))

        # ── Zig-zag (resistor body) ───────────────────────────────────
        n_teeth = max(3, int(body_w / (h * 0.35)))
        tooth_w  = body_w / n_teeth
        zz_amp   = h * 0.18 # reduced amplitude to make space for text

        zz_pen = QPen(self._ZZ_COLOR, max(1.5, h * 0.07),
                      Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin)
        painter.setPen(zz_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        path = QPainterPath()
        path.moveTo(body_x0, mid_y)
        for i in range(n_teeth):
            lx = body_x0 + i * tooth_w
            rx = lx + tooth_w
            peak_y = mid_y - zz_amp if i % 2 == 0 else mid_y + zz_amp
            path.lineTo(lx + tooth_w * 0.5, peak_y)
            path.lineTo(rx, mid_y)
        painter.drawPath(path)

        # ── Terminal dots ─────────────────────────────────────────────
        dot_r = max(2.0, h * 0.09)
        painter.setBrush(QBrush(self._BORDER))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(x0 + dot_r, mid_y), dot_r, dot_r)
        painter.drawEllipse(QPointF(x0 + w - dot_r, mid_y), dot_r, dot_r)

        # ── Pin labels (1 left, 2 right) ─────────────────────────────
        pin_fs = max(4, min(8, int(h * 0.22)))
        painter.setFont(QFont("Segoe UI", pin_fs, QFont.Weight.Bold))
        painter.setPen(self._PIN_CLR)
        pin_w = lead_w * 3
        painter.drawText(QRectF(x0, y0, pin_w, h * 0.55),
                         Qt.AlignmentFlag.AlignCenter, "1")
        painter.drawText(QRectF(x0 + w - pin_w, y0, pin_w, h * 0.55),
                         Qt.AlignmentFlag.AlignCenter, "2")

        # ── Device name ───────────────────────────────────────────────
        name_fs = max(5, min(10, int(w * 0.10)))
        painter.setFont(QFont("Segoe UI", name_fs, QFont.Weight.Bold))
        painter.setPen(self._LABEL_CLR)
        name_rect = QRectF(x0 + lead_w, y0 + 1, body_w, h * 0.45)
        painter.drawText(name_rect,
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                         self.device_name)
                         
        if self.electrical_value > 0:
            val_str = self._format_si(self.electrical_value, self.unit)
            val_rect = QRectF(x0 + lead_w, y0 + h * 0.5, body_w, h * 0.45)
            painter.drawText(val_rect,
                             Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                             val_str)

        # ── Selection ─────────────────────────────────────────────────
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

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect  = self.rect()
        w, h  = rect.width(), rect.height()
        x0, y0 = rect.x(), rect.y()
        mid_y = y0 + h * 0.5

        # ── Background gradient ──────────────────────────────────────
        grad = QLinearGradient(x0, y0, x0, y0 + h)
        grad.setColorAt(0.0, self._BG_TOP)
        grad.setColorAt(1.0, self._BG_BOT)
        painter.setBrush(QBrush(grad))
        painter.setPen(QPen(self._BORDER, 1.5))
        painter.drawRoundedRect(rect.adjusted(0.75, 0.75, -0.75, -0.75), 4, 4)

        # ── Plate geometry ────────────────────────────────────────────
        cx     = x0 + w * 0.5
        gap    = max(4.0, w * 0.07)
        ph     = h * 0.45 # reduced height to make space for text
        py0    = y0 + (h - ph) * 0.5
        lead_w = w * 0.08

        # Lead wires
        lead_pen = QPen(self._LEAD, max(1.2, h * 0.05),
                        Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap)
        painter.setPen(lead_pen)
        painter.drawLine(QPointF(x0, mid_y), QPointF(cx - gap / 2, mid_y))
        painter.drawLine(QPointF(cx + gap / 2, mid_y), QPointF(x0 + w, mid_y))

        # Plates (thick vertical bars)
        plate_pen = QPen(self._PLATE, max(3.0, w * 0.07),
                         Qt.PenStyle.SolidLine,
                         Qt.PenCapStyle.FlatCap)
        painter.setPen(plate_pen)
        painter.drawLine(QPointF(cx - gap / 2, py0),
                         QPointF(cx - gap / 2, py0 + ph))
        painter.drawLine(QPointF(cx + gap / 2, py0),
                         QPointF(cx + gap / 2, py0 + ph))

        # ── Terminal dots ─────────────────────────────────────────────
        dot_r = max(2.0, h * 0.08)
        painter.setBrush(QBrush(self._BORDER))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(x0 + dot_r, mid_y), dot_r, dot_r)
        painter.drawEllipse(QPointF(x0 + w - dot_r, mid_y), dot_r, dot_r)

        # ── Pin labels (+ left, − right) ──────────────────────────────
        pin_fs = max(4, min(9, int(h * 0.26)))
        painter.setFont(QFont("Segoe UI", pin_fs, QFont.Weight.Bold))
        painter.setPen(self._PIN_CLR)
        pin_area_w = lead_w * 0.9
        # "+" on left plate side, "−" on right
        painter.drawText(QRectF(x0, y0, pin_area_w, h * 0.55),
                         Qt.AlignmentFlag.AlignCenter, "+")
        painter.drawText(QRectF(x0 + w - pin_area_w, y0, pin_area_w, h * 0.55),
                         Qt.AlignmentFlag.AlignCenter, "−")

        # ── Device name ───────────────────────────────────────────────
        name_fs = max(5, min(10, int(w * 0.10)))
        painter.setFont(QFont("Segoe UI", name_fs, QFont.Weight.Bold))
        painter.setPen(self._LABEL_CLR)
        name_rect = QRectF(x0 + lead_w, y0 + 1, w - 2 * lead_w, h * 0.45)
        painter.drawText(name_rect,
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                         self.device_name)
                         
        if self.electrical_value > 0:
            val_str = self._format_si(self.electrical_value, self.unit)
            val_rect = QRectF(x0 + lead_w, y0 + h * 0.5, w - 2 * lead_w, h * 0.45)
            painter.drawText(val_rect,
                             Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                             val_str)

        # ── Selection ─────────────────────────────────────────────────
        self._draw_selection(painter, rect, "#1abc9c")


# =============================================================================
class InductorItem(_PassiveBase):
    """Visual symbol for an inductor — purple/magenta body with coil loops."""

    _BG_TOP   = QColor("#f3e5f5")   # light purple top
    _BG_BOT   = QColor("#ce93d8")   # purple bottom
    _COIL     = QColor("#6a1b9a")   # deep purple coil
    _BORDER   = QColor("#4a148c")   # dark purple border
    _LEAD     = QColor("#6a1b9a")   # matching lead
    _LABEL_CLR= QColor("#38006b")   # name text
    _PIN_CLR  = QColor("#6a1b9a")   # pin label color

    def __init__(self, name, x, y, width, height):
        super().__init__(name, "ind", x, y, width, height)

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect  = self.rect()
        w, h  = rect.width(), rect.height()
        x0, y0 = rect.x(), rect.y()
        mid_y = y0 + h * 0.5

        # ── Background gradient ──────────────────────────────────────
        grad = QLinearGradient(x0, y0, x0, y0 + h)
        grad.setColorAt(0.0, self._BG_TOP)
        grad.setColorAt(1.0, self._BG_BOT)
        painter.setBrush(QBrush(grad))
        painter.setPen(QPen(self._BORDER, 1.5))
        painter.drawRoundedRect(rect.adjusted(0.75, 0.75, -0.75, -0.75), 4, 4)

        # ── Lead wires from edges to body ────────────────────────────
        lead_w = w * 0.08
        body_x0 = x0 + lead_w
        body_x1 = x0 + w - lead_w
        body_w  = body_x1 - body_x0

        lead_pen = QPen(self._LEAD, max(1.2, h * 0.06),
                        Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap)
        painter.setPen(lead_pen)
        painter.drawLine(QPointF(x0, mid_y), QPointF(body_x0, mid_y))
        painter.drawLine(QPointF(body_x1, mid_y), QPointF(x0 + w, mid_y))

        # ── Coil Loops (inductor body) ────────────────────────────────
        n_loops  = max(3, int(body_w / (h * 0.35)))
        loop_w   = body_w / n_loops
        loop_amp = h * 0.18 # reduced amplitude to make space for text

        coil_pen = QPen(self._COIL, max(1.5, h * 0.07),
                        Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin)
        painter.setPen(coil_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        path = QPainterPath()
        path.moveTo(body_x0, mid_y)
        for i in range(n_loops):
            cx = body_x0 + (i + 0.5) * loop_w
            # Draw an arc for each coil loop
            path.arcTo(QRectF(cx - loop_w * 0.6, mid_y - loop_amp, loop_w * 1.2, loop_amp * 2), 180, -180)
        painter.drawPath(path)

        # ── Terminal dots ─────────────────────────────────────────────
        dot_r = max(2.0, h * 0.09)
        painter.setBrush(QBrush(self._BORDER))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(x0 + dot_r, mid_y), dot_r, dot_r)
        painter.drawEllipse(QPointF(x0 + w - dot_r, mid_y), dot_r, dot_r)

        # ── Pin labels (1 left, 2 right) ─────────────────────────────
        pin_fs = max(4, min(8, int(h * 0.22)))
        painter.setFont(QFont("Segoe UI", pin_fs, QFont.Weight.Bold))
        painter.setPen(self._PIN_CLR)
        pin_w = lead_w * 3
        painter.drawText(QRectF(x0, y0, pin_w, h * 0.55),
                         Qt.AlignmentFlag.AlignCenter, "1")
        painter.drawText(QRectF(x0 + w - pin_w, y0, pin_w, h * 0.55),
                         Qt.AlignmentFlag.AlignCenter, "2")

        # ── Device name ───────────────────────────────────────────────
        name_fs = max(5, min(10, int(w * 0.10)))
        painter.setFont(QFont("Segoe UI", name_fs, QFont.Weight.Bold))
        painter.setPen(self._LABEL_CLR)
        name_rect = QRectF(x0 + lead_w, y0 + 1, body_w, h * 0.45)
        painter.drawText(name_rect,
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                         self.device_name)
                         
        if self.electrical_value > 0:
            val_str = self._format_si(self.electrical_value, self.unit)
            val_rect = QRectF(x0 + lead_w, y0 + h * 0.5, body_w, h * 0.45)
            painter.drawText(val_rect,
                             Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                             val_str)

        # ── Selection ─────────────────────────────────────────────────
        self._draw_selection(painter, rect, "#ab47bc")

