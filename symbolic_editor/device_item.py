from PySide6.QtWidgets import (
    QGraphicsRectItem,
    QGraphicsItem,
    QStyleOptionGraphicsItem,
    QStyle,
)
from PySide6.QtGui import QBrush, QPen, QColor, QFont, QPainter, QLinearGradient
from PySide6.QtCore import Qt, QRectF, QObject, Signal, QPointF


class DeviceSignals(QObject):
    """Helper QObject so DeviceItem (a QGraphicsRectItem) can emit signals."""
    drag_started = Signal()   # emitted when user begins dragging
    drag_finished = Signal()  # emitted when user releases after drag


class DeviceItem(QGraphicsRectItem):

    def __init__(self, name, dev_type, x, y, width, height):

        super().__init__(0, 0, width, height)

        self.setPos(x, y)
        self.device_name = name
        self.device_type = str(dev_type).strip().lower()
        self.signals = DeviceSignals()

        self._drag_active = False
        self._drag_start_pos = QPointF()
        self._snap_grid_x = None
        self._snap_grid_y = None
        self._flip_h = False
        self._flip_v = False

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)

        # --- Color palette per device type ---
        dtype = self.device_type
        if str(name).upper().startswith("DUMMY"):
            # Keep one consistent dummy color (pink) for both N/P.
            self._source_color = QColor("#ffd6ea")
            self._gate_color = QColor("#d14d94")
            self._drain_color = QColor("#ffd6ea")
            self._border = QColor("#b83b7c")
            self._label_color = QColor("#8f2d61")
            self._terminal_label_color = QColor("#fff1f8")
        elif dtype == "nmos":
            self._source_color = QColor("#d6eaf8")   # soft sky blue
            self._gate_color = QColor("#1b4f72")      # deep navy blue
            self._drain_color = QColor("#d6eaf8")     # soft sky blue
            self._border = QColor("#1a5276")
            self._label_color = QColor("#1a5276")
            self._terminal_label_color = QColor("#eaf2f8")
        else:
            self._source_color = QColor("#fadbd8")    # soft rose
            self._gate_color = QColor("#78281f")      # deep burgundy
            self._drain_color = QColor("#fadbd8")     # soft rose
            self._border = QColor("#7b241c")
            self._label_color = QColor("#7b241c")
            self._terminal_label_color = QColor("#f9ebea")

        # Transparent fill — we paint everything custom
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setPen(QPen(Qt.PenStyle.NoPen))

    def set_snap_grid(self, grid_x, grid_y=None):
        """Enable snapping item movement to scene grid (separate X/Y pitch)."""
        self._snap_grid_x = float(grid_x) if grid_x else None
        self._snap_grid_y = (
            float(grid_y) if grid_y else self._snap_grid_x
        )

    def flip_horizontal(self):
        """Mirror device left/right."""
        self._flip_h = not self._flip_h
        self.update()

    def flip_vertical(self):
        """Mirror device up/down."""
        self._flip_v = not self._flip_v
        self.update()

    def is_flip_h(self):
        return self._flip_h

    def set_flip_h(self, state):
        self._flip_h = bool(state)
        self.update()

    def is_flip_v(self):
        return self._flip_v

    def set_flip_v(self, state):
        self._flip_v = bool(state)
        self.update()

    def orientation_string(self):
        """Compact orientation token for save/export."""
        base = "R0"
        if self._flip_h and self._flip_v:
            return f"{base}_FH_FV"
        if self._flip_h:
            return f"{base}_FH"
        if self._flip_v:
            return f"{base}_FV"
        return base

    def itemChange(self, change, value):
        """Snap dragged positions to grid so devices never float between tracks."""
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemPositionChange
            and self._snap_grid_x
            and self._snap_grid_y
        ):
            x = round(value.x() / self._snap_grid_x) * self._snap_grid_x
            y = round(value.y() / self._snap_grid_y) * self._snap_grid_y
            return QPointF(x, y)
        return super().itemChange(change, value)

    # --------------------------------------------------
    # Drag tracking
    # --------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = self.pos()
            self._drag_active = False
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

    # --------------------------------------------------
    # Painting — 3-section MOS layout
    # --------------------------------------------------
    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        w = rect.width()
        h = rect.height()
        x0 = rect.x()
        y0 = rect.y()
        cx = x0 + w / 2.0
        cy = y0 + h / 2.0

        # --- Section geometry (always in local item coords) ---
        source_w = w * 0.30
        gate_w = w * 0.40
        drain_w = w * 0.30

        source_rect = QRectF(x0, y0, source_w, h)
        gate_rect = QRectF(x0 + source_w, y0, gate_w, h)
        drain_rect = QRectF(x0 + source_w + gate_w, y0, drain_w, h)

        # ── Draw coloured sections WITH flip transform ─────────
        painter.save()
        painter.translate(cx, cy)
        painter.scale(-1.0 if self._flip_h else 1.0,
                       -1.0 if self._flip_v else 1.0)
        painter.translate(-cx, -cy)

        # Source (left in unflipped orientation)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self._source_color))
        painter.drawRect(source_rect)

        # Gate (centre) — gradient fill
        gradient = QLinearGradient(gate_rect.topLeft(), gate_rect.bottomLeft())
        gradient.setColorAt(0.0, self._gate_color.lighter(115))
        gradient.setColorAt(0.5, self._gate_color)
        gradient.setColorAt(1.0, self._gate_color.darker(115))
        painter.setBrush(QBrush(gradient))
        painter.drawRect(gate_rect)

        # Drain (right in unflipped orientation)
        painter.setBrush(QBrush(self._drain_color))
        painter.drawRect(drain_rect)

        # Outer border
        painter.setPen(QPen(self._border, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))

        # Vertical separator lines
        sep_pen = QPen(self._border.darker(120), 1.0)
        painter.setPen(sep_pen)
        painter.drawLine(QPointF(x0 + source_w, y0),
                         QPointF(x0 + source_w, y0 + h))
        painter.drawLine(QPointF(x0 + source_w + gate_w, y0),
                         QPointF(x0 + source_w + gate_w, y0 + h))

        painter.restore()  # back to un-flipped coordinates

        # ── Draw text labels WITHOUT flip (always readable) ────
        # Visual position of terminals after horizontal flip:
        #   flip_h  → left=Drain, right=Source
        #   normal  → left=Source, right=Drain
        left_rect  = QRectF(x0, y0, source_w, h)
        center_rect = QRectF(x0 + source_w, y0, gate_w, h)
        right_rect = QRectF(x0 + source_w + gate_w, y0, drain_w, h)

        left_label  = "D" if self._flip_h else "S"
        right_label = "S" if self._flip_h else "D"

        term_font_size = max(3, min(9, int(min(source_w, h) / 3)))
        term_font = QFont("Segoe UI", term_font_size, QFont.Weight.Bold)
        painter.setFont(term_font)

        # Left terminal label
        painter.setPen(self._label_color)
        painter.drawText(left_rect, Qt.AlignmentFlag.AlignCenter, left_label)

        # G label (lower portion of gate)
        g_label_rect = QRectF(center_rect.x(),
                              center_rect.y() + center_rect.height() * 0.45,
                              center_rect.width(),
                              center_rect.height() * 0.55)
        painter.setPen(self._terminal_label_color)
        painter.drawText(g_label_rect,
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, "G")

        # Right terminal label
        painter.setPen(self._label_color)
        painter.drawText(right_rect, Qt.AlignmentFlag.AlignCenter, right_label)

        # Device name — centred over gate area, spanning full width
        name_font_size = max(3, min(10, int(w / 5)))
        name_font = QFont("Segoe UI", name_font_size, QFont.Weight.Bold)
        painter.setFont(name_font)
        painter.setPen(QColor("#ffffff"))
        name_rect = QRectF(x0, center_rect.y() + 2, w, center_rect.height() * 0.50)
        painter.drawText(name_rect,
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                         self.device_name)

        # ── Selection highlight (un-flipped) ───────────────────
        if self.isSelected():
            sel_pen = QPen(QColor("#4a90d9"), 2.0, Qt.PenStyle.SolidLine)
            painter.setPen(sel_pen)
            painter.setBrush(QBrush(QColor(74, 144, 217, 30)))
            painter.drawRect(rect.adjusted(1, 1, -1, -1))

    def terminal_anchors(self):
        """Return scene positions for S, G, D terminal centers.

        Accounts for horizontal flip so anchors match the visual layout.
        """
        rect = self.rect()
        w = rect.width()
        h = rect.height()
        x0 = rect.x()
        y0 = rect.y()

        source_w = w * 0.30
        gate_w = w * 0.40
        drain_w = w * 0.30

        if self._flip_h:
            # Flipped: Source visually on the right, Drain on the left
            s_local = QPointF(x0 + source_w + gate_w + drain_w / 2, y0 + h / 2)
            d_local = QPointF(x0 + source_w / 2, y0 + h / 2)
        else:
            s_local = QPointF(x0 + source_w / 2, y0 + h / 2)
            d_local = QPointF(x0 + source_w + gate_w + drain_w / 2, y0 + h / 2)

        g_local = QPointF(x0 + source_w + gate_w / 2, y0 + h / 2)

        return {
            "S": self.mapToScene(s_local),
            "G": self.mapToScene(g_local),
            "D": self.mapToScene(d_local),
        }
