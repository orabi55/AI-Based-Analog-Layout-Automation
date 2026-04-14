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
        # Candidate highlight (auto-detected, green glow)
        self._hl_left  = None   # net name on the left  edge that can abut, or None
        self._hl_right = None   # net name on the right edge that can abut, or None
        # Manual abutment override (user-set, amber stripe)
        self._manual_abut_left  = False
        self._manual_abut_right = False
        # Match / lock highlight color (set when device is in a matched group)
        self._match_color = None   # QColor or None

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

    def set_candidate_highlight(self, left_net=None, right_net=None):
        """Highlight terminal edges that can participate in diffusion sharing.

        Args:
            left_net:  net name for the left  S/D edge, or None to clear.
            right_net: net name for the right S/D edge, or None to clear.
        """
        self._hl_left  = left_net  or None
        self._hl_right = right_net or None
        self.update()

    def clear_candidate_highlight(self):
        self._hl_left  = None
        self._hl_right = None
        self.update()

    # ── Manual abutment control ──────────────────────────────────────
    def toggle_abut_left(self):
        """Toggle the left-edge manual abutment flag and redraw."""
        self._manual_abut_left = not self._manual_abut_left
        self.update()

    def toggle_abut_right(self):
        """Toggle the right-edge manual abutment flag and redraw."""
        self._manual_abut_right = not self._manual_abut_right
        self.update()

    def set_abut_left(self, value: bool):
        self._manual_abut_left = bool(value)
        self.update()

    def set_abut_right(self, value: bool):
        self._manual_abut_right = bool(value)
        self.update()

    def get_abut_left(self) -> bool:
        return self._manual_abut_left

    def get_abut_right(self) -> bool:
        return self._manual_abut_right

    # Keep compat
    def set_abutment(self, left: bool, right: bool):
        self._manual_abut_left  = bool(left)
        self._manual_abut_right = bool(right)
        self.update()

    # ── Match / Lock highlight ───────────────────────────────────────
    def set_match_highlight(self, color: QColor):
        """Set a persistent colour border to indicate this device is in a matched group."""
        self._match_color = QColor(color) if color else None
        self.update()

    def clear_match_highlight(self):
        """Remove the matched-group highlight."""
        self._match_color = None
        self.update()

    def is_match_locked(self) -> bool:
        """Return True if this device is visually marked as part of a matched group."""
        return self._match_color is not None

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
        w    = rect.width()
        h    = rect.height()
        x0   = rect.x()
        y0   = rect.y()
        cx   = x0 + w / 2.0
        cy   = y0 + h / 2.0

        num_fingers = self.nf
        num_sd      = num_fingers + 1   # S/D diffusion regions

        # --- Visual proportions ---
        # Make gate fingers and S/D diffusion regions have the exact same visual width
        total_regions = num_fingers + num_sd
        part_w = w / total_regions
        gate_w = part_w
        sd_w   = part_w

        # S/D identity per column (before flip)
        # Column 0,2,4... = Source;  1,3,5... = Drain
        def _is_source_col(col):
            return (col % 2 == 0) ^ self._flip_h

        # ── Draw filled sections (with flip transform) ─────────────
        painter.save()
        painter.translate(cx, cy)
        painter.scale(-1.0 if self._flip_h else 1.0,
                       -1.0 if self._flip_v else 1.0)
        painter.translate(-cx, -cy)

        painter.setPen(Qt.PenStyle.NoPen)
        cursor_x = x0
        for i in range(num_sd):
            color = self._source_color if _is_source_col(i) else self._drain_color
            painter.setBrush(QBrush(color))
            painter.drawRect(QRectF(cursor_x, y0, sd_w, h))
            cursor_x += sd_w

            if i < num_fingers:
                # Gate strip gradient
                gate_rect = QRectF(cursor_x, y0, gate_w, h)
                grad = QLinearGradient(gate_rect.topLeft(), gate_rect.bottomLeft())
                grad.setColorAt(0.0, self._gate_color.lighter(130))
                grad.setColorAt(0.4, self._gate_color)
                grad.setColorAt(1.0, self._gate_color.darker(130))
                painter.setBrush(QBrush(grad))
                painter.drawRect(gate_rect)
                cursor_x += gate_w

        # Outer border
        painter.setPen(QPen(self._border, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(0.75, 0.75, -0.75, -0.75), 2, 2)

        # Separator lines
        sep_pen = QPen(self._border.darker(130), 0.8)
        painter.setPen(sep_pen)
        cursor_x = x0
        for i in range(num_fingers):
            cursor_x += sd_w
            painter.drawLine(QPointF(cursor_x, y0 + 2), QPointF(cursor_x, y0 + h - 2))
            cursor_x += gate_w
            painter.drawLine(QPointF(cursor_x, y0 + 2), QPointF(cursor_x, y0 + h - 2))

        painter.restore()   # back to un-flipped for text

        # Abutment candidate highlights (visual annotation removed).
        # _hl_left / _hl_right are kept for internal state but no longer painted.

        # ── Manual abutment state (amber solid stripe) ────────────────────
        # When the user manually sets leftAbut / rightAbut via right-click
        # menu, draw a distinct solid amber stripe on that edge.
        if self._manual_abut_left or self._manual_abut_right:
            ABUT_COLOR = QColor("#f39c12")    # amber
            ABUT_FILL  = QColor("#f39c12")
            ABUT_FILL.setAlpha(50)
            abut_w = max(3.5, sd_w * 0.20)

            if self._manual_abut_left:
                painter.setBrush(QBrush(ABUT_FILL))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRect(QRectF(x0, y0, abut_w, h))
                painter.setPen(QPen(ABUT_COLOR, 3.0, Qt.PenStyle.SolidLine,
                                    Qt.PenCapStyle.FlatCap))
                painter.drawLine(QPointF(x0 + 1.5, y0 + 3),
                                 QPointF(x0 + 1.5, y0 + h - 3))
                # Double-tick mark → indicates shared diffusion
                mid = y0 + h * 0.4
                painter.drawLine(QPointF(x0 + abut_w + 1, mid),
                                 QPointF(x0 + abut_w + 6, mid))
                mid2 = y0 + h * 0.6
                painter.drawLine(QPointF(x0 + abut_w + 1, mid2),
                                 QPointF(x0 + abut_w + 6, mid2))

            if self._manual_abut_right:
                painter.setBrush(QBrush(ABUT_FILL))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRect(QRectF(x0 + w - abut_w, y0, abut_w, h))
                painter.setPen(QPen(ABUT_COLOR, 3.0, Qt.PenStyle.SolidLine,
                                    Qt.PenCapStyle.FlatCap))
                painter.drawLine(QPointF(x0 + w - 1.5, y0 + 3),
                                 QPointF(x0 + w - 1.5, y0 + h - 3))
                mid = y0 + h * 0.4
                painter.drawLine(QPointF(x0 + w - abut_w - 1, mid),
                                 QPointF(x0 + w - abut_w - 6, mid))
                mid2 = y0 + h * 0.6
                painter.drawLine(QPointF(x0 + w - abut_w - 1, mid2),
                                 QPointF(x0 + w - abut_w - 6, mid2))

        # ── Text labels (always readable, no flip) ──────────────────
        # Font sizes scaled to available area
        sd_font_size   = max(4, min(9,  int(min(sd_w * 0.45, h * 0.28))))
        gate_font_size = max(4, min(9,  int(min(gate_w * 0.55, h * 0.28))))
        name_font_size = max(5, min(11, int(w * 0.085)))

        # ── S / D labels on each diffusion column ───────────────────
        sd_font = QFont("Segoe UI", sd_font_size, QFont.Weight.Bold)
        painter.setFont(sd_font)

        cursor_x = x0
        for i in range(num_sd):
            label = "S" if _is_source_col(i) else "D"
            col_rect = QRectF(cursor_x, y0, sd_w, h)
            painter.setPen(self._label_color)
            painter.drawText(col_rect,
                             Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                             label)
            cursor_x += sd_w
            if i < num_fingers:
                cursor_x += gate_w

        # ── G labels on each gate strip ─────────────────────────────
        g_font = QFont("Segoe UI", gate_font_size, QFont.Weight.Bold)
        painter.setFont(g_font)
        painter.setPen(self._terminal_label_color)

        cursor_x = x0 + sd_w        # first gate starts after first S/D
        for _ in range(num_fingers):
            gate_col_rect = QRectF(cursor_x, y0, gate_w, h)
            painter.drawText(gate_col_rect,
                             Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                             "G")
            cursor_x += gate_w + sd_w

        # ── Device name centred in upper half ───────────────────────
        name_font = QFont("Segoe UI", name_font_size, QFont.Weight.Bold)
        painter.setFont(name_font)
        painter.setPen(QColor("#ffffff"))
        name_rect = QRectF(x0 + 1, y0 + 1, w - 2, h * 0.52)
        painter.drawText(name_rect,
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                         self.device_name)

        # ── Selection highlight ──────────────────────────────────────
        if self.isSelected():
            sel_pen = QPen(QColor("#4a90d9"), 2.0, Qt.PenStyle.SolidLine)
            painter.setPen(sel_pen)
            painter.setBrush(QBrush(QColor(74, 144, 217, 35)))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 2, 2)

        # ── Matched-group (lock) highlight ───────────────────────────
        if self._match_color is not None:
            lock_pen = QPen(self._match_color, 2.5, Qt.PenStyle.SolidLine)
            painter.setPen(lock_pen)
            fill = QColor(self._match_color)
            fill.setAlpha(28)
            painter.setBrush(QBrush(fill))
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
            # Lock icon badge (top-right corner)
            badge_size = max(8, min(14, int(w * 0.10)))
            bx = x0 + w - badge_size - 2
            by = y0 + 2
            badge_font = QFont("Segoe UI", badge_size - 2, QFont.Weight.Bold)
            painter.setFont(badge_font)
            painter.setPen(self._match_color)
            painter.drawText(QRectF(bx, by, badge_size, badge_size),
                             Qt.AlignmentFlag.AlignCenter, "🔒")

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
