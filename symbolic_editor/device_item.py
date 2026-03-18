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

    def __init__(self, name, dev_type, x, y, width, height, nf=1):

        super().__init__(0, 0, width, height)

        self.setPos(x, y)
        self.device_name = name
        self.device_type = str(dev_type).strip().lower()
        self.nf = max(1, int(nf))
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
    # Painting — Multi-finger MOS layout
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

        num_fingers = self.nf
        num_sd = num_fingers + 1  # Number of S/D diffusion regions
        # We assign 40% of the width to the gates collectively, 60% to the diffusions
        total_gate_w = w * 0.40
        total_sd_w = w * 0.60
        gate_w = total_gate_w / num_fingers
        sd_w = total_sd_w / num_sd

        # ── Draw coloured sections WITH flip transform ─────────
        painter.save()
        painter.translate(cx, cy)
        painter.scale(-1.0 if self._flip_h else 1.0,
                       -1.0 if self._flip_v else 1.0)
        painter.translate(-cx, -cy)

        # Alternating S/D logic:
        # S D S D ... or D S D S ... (if flipped)
        # Assuming left-most is Source normally.
        cursor_x = x0

        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(num_sd):
            is_source = (i % 2 == 0)
            color = self._source_color if is_source else self._drain_color
            sd_rect = QRectF(cursor_x, y0, sd_w, h)
            painter.setBrush(QBrush(color))
            painter.drawRect(sd_rect)
            cursor_x += sd_w

            # Draw gate to the right of this diffusion, except for the last diffusion
            if i < num_fingers:
                gate_rect = QRectF(cursor_x, y0, gate_w, h)
                gradient = QLinearGradient(gate_rect.topLeft(), gate_rect.bottomLeft())
                gradient.setColorAt(0.0, self._gate_color.lighter(115))
                gradient.setColorAt(0.5, self._gate_color)
                gradient.setColorAt(1.0, self._gate_color.darker(115))
                painter.setBrush(QBrush(gradient))
                painter.drawRect(gate_rect)
                cursor_x += gate_w

        # Outer border
        painter.setPen(QPen(self._border, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))

        # Vertical separator lines
        sep_pen = QPen(self._border.darker(120), 1.0)
        painter.setPen(sep_pen)
        # Draw separators
        cursor_x = x0
        for i in range(num_fingers):
            cursor_x += sd_w
            painter.drawLine(QPointF(cursor_x, y0), QPointF(cursor_x, y0 + h))
            cursor_x += gate_w
            painter.drawLine(QPointF(cursor_x, y0), QPointF(cursor_x, y0 + h))

        painter.restore()  # back to un-flipped coordinates

        # ── Draw text labels WITHOUT flip (always readable) ────
        term_font_size = max(3, min(9, int(min(sd_w, h) / 3)))
        term_font = QFont("Segoe UI", term_font_size, QFont.Weight.Bold)
        painter.setFont(term_font)

        # In un-flipped coordinates:
        left_label = "D" if self._flip_h else "S"
        right_label = "S" if (num_sd % 2 == 0) ^ self._flip_h else "D"
        # The visual rightmost diffusion type depends on nf:
        # nf=1 (2 diffusions): left=S, right=D. If flipped: left=D, right=S.
        # nf=2 (3 diffusions): left=S, right=S. If flipped: left=D, right=D.

        # Draw left and right terminal labels
        left_rect = QRectF(x0, y0, sd_w, h)
        right_rect = QRectF(x0 + w - sd_w, y0, sd_w, h)
        
        painter.setPen(self._label_color)
        painter.drawText(left_rect, Qt.AlignmentFlag.AlignCenter, left_label)
        painter.drawText(right_rect, Qt.AlignmentFlag.AlignCenter, right_label)

        # Device name — centred
        name_font_size = max(3, min(10, int(w / max(5, min(w/10, 8)))))
        name_font = QFont("Segoe UI", name_font_size, QFont.Weight.Bold)
        painter.setFont(name_font)
        painter.setPen(QColor("#ffffff"))
        name_rect = QRectF(x0, y0 + 2, w, min(h, 20))
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
        """Return scene positions for S, G, D terminal centers."""
        rect = self.rect()
        w = rect.width()
        h = rect.height()
        x0 = rect.x()
        y0 = rect.y()

        num_fingers = self.nf
        num_sd = num_fingers + 1
        total_gate_w = w * 0.40
        total_sd_w = w * 0.60
        gate_w = total_gate_w / num_fingers
        sd_w = total_sd_w / num_sd

        # We return the geometric centers. If there are multiple S/D/G,
        # we return the center of the middle-most one for simplicity of routing lines.
        # Visually:
        mid_y = y0 + h / 2

        if self._flip_h:
            # Flipped: Leftmost is D, rightmost is S (if nf=1)
            left_is_s = False
        else:
            left_is_s = True

        # Find all S centers and D centers
        s_centers = []
        d_centers = []
        g_centers = []

        cursor_x = x0
        for i in range(num_sd):
            cx = cursor_x + sd_w / 2
            is_source = (i % 2 == 0)
            if left_is_s == is_source:
                s_centers.append(QPointF(cx, mid_y))
            else:
                d_centers.append(QPointF(cx, mid_y))
            cursor_x += sd_w
            
            if i < num_fingers:
                g_centers.append(QPointF(cursor_x + gate_w / 2, mid_y))
                cursor_x += gate_w

        # Pick the most "central" one for the anchor
        s_anchor = s_centers[len(s_centers)//2] if s_centers else QPointF(x0, mid_y)
        d_anchor = d_centers[len(d_centers)//2] if d_centers else QPointF(x0+w, mid_y)
        g_anchor = g_centers[len(g_centers)//2] if g_centers else QPointF(x0+w/2, mid_y)

        return {
            "S": self.mapToScene(s_anchor),
            "G": self.mapToScene(g_anchor),
            "D": self.mapToScene(d_anchor),
        }
