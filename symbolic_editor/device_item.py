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

    def __init__(self, dev_id, name, dev_type, x, y, width, height, is_dummy=False):

        super().__init__(0, 0, width, height)

        self.setPos(x, y)
        self.dev_id = dev_id
        self.device_type = str(dev_type).strip().lower()
        
        # Abbreviate Dummy Names for cleaner Canvas
        name_str = str(name)
        if name_str.upper().startswith("DUMMYP"):
            self.device_name = name_str.upper().replace("DUMMYP", "DP")
        elif name_str.upper().startswith("DUMMYN"):
            self.device_name = name_str.upper().replace("DUMMYN", "DN")
        else:
            self.device_name = name_str
        self.is_dummy = is_dummy  # Track if this is a dummy device
        self.signals = DeviceSignals()

        self._drag_active = False
        self._drag_start_pos = QPointF()
        self._snap_grid_x = None
        self._snap_grid_y = None
        self._flip_h = False
        self._flip_v = False
        self._hide_left_terminal_label = False
        self._hide_right_terminal_label = False
        self._net_labels = {}  # {'S': 'net11', 'G': 'net5', 'D': 'net3'}

        # Abut state: True means this edge is merged with neighbor (shares diffusion)
        self._abut_left = False
        self._abut_right = False
        # Shared net name for abutted edges (for green highlighting)
        self._shared_net_left = ""
        self._shared_net_right = ""

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)

        # --- Color palette per device type ---
        dtype = self.device_type
        # Check for dummy: either is_dummy flag OR name starts with DUMMY
        if is_dummy or str(name).upper().startswith("DUMMY"):
            self.is_dummy = True  # Ensure flag is set
            self._source_color = QColor("#f8bbd0")  # light pink
            self._gate_color = QColor("#e91e63")     # pink (material design)
            self._drain_color = QColor("#f8bbd0")
            self._border = QColor("#880e4f")         # dark pink
            self._label_color = QColor("#880e4f")
            self._terminal_label_color = QColor("#ffffff")
        elif dtype == "nmos":
            self._source_color = QColor("#cce5ff")   # light blue
            self._gate_color = QColor("#4a90d9")      # blue
            self._drain_color = QColor("#cce5ff")
            self._border = QColor("#2d5986")
            self._label_color = QColor("#1a365d")
            self._terminal_label_color = QColor("#ffffff")
        else:  # pmos
            self._source_color = QColor("#ffcccc")    # light red/pink
            self._gate_color = QColor("#d94a4a")      # red
            self._drain_color = QColor("#ffcccc")
            self._border = QColor("#8b2d2d")
            self._label_color = QColor("#5d1a1a")
            self._terminal_label_color = QColor("#ffffff")

        # Shared diffusion color (green like in reference)
        self._shared_diff_color = QColor("#4CAF50")  # Material green

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

    def set_boundary_label_visibility(self, show_left=True, show_right=True):
        """Control whether left/right terminal labels are drawn."""
        self._hide_left_terminal_label = not bool(show_left)
        self._hide_right_terminal_label = not bool(show_right)
        self.update()

    def set_net_labels(self, net_map):
        """Set net annotations for terminals: {'S': 'net11', 'G': 'Vg', 'D': 'net3'}."""
        self._net_labels = dict(net_map) if net_map else {}
        self.update()

    def set_abut_state(self, left=None, right=None, shared_net_left="", shared_net_right=""):
        """Set which edges are abutted (merged with neighbor).

        Args:
            left: True = left edge is merged with neighbor (share diffusion)
            right: True = right edge is merged with neighbor (share diffusion)
            shared_net_left: Net name shared on left edge (for green display)
            shared_net_right: Net name shared on right edge (for green display)
        """
        if left is not None:
            self._abut_left = bool(left)
        if right is not None:
            self._abut_right = bool(right)
        if shared_net_left:
            self._shared_net_left = shared_net_left
        if shared_net_right:
            self._shared_net_right = shared_net_right
        self.update()

    def is_abut_left(self):
        """Return True if left edge is abutted (merged with neighbor)."""
        return self._abut_left

    def is_abut_right(self):
        """Return True if right edge is abutted (merged with neighbor)."""
        return self._abut_right

    def get_abut_state(self):
        """Return current abut state as dict."""
        return {'left': self._abut_left, 'right': self._abut_right}

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
    # Painting — Clean professional MOS layout
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

        # Section geometry: S(30%) - G(40%) - D(30%)
        diff_w = w * 0.30   # diffusion (S or D) width
        gate_w = w * 0.40   # gate width

        # Determine visual abut state (flip reverses left/right)
        visual_abut_left = self._abut_right if self._flip_h else self._abut_left
        visual_abut_right = self._abut_left if self._flip_h else self._abut_right

        # ── Apply flip transform for drawing ─────────────────────
        painter.save()
        painter.translate(cx, cy)
        painter.scale(-1.0 if self._flip_h else 1.0,
                      -1.0 if self._flip_v else 1.0)
        painter.translate(-cx, -cy)

        # ── Draw Source (left) section ───────────────────────────
        source_rect = QRectF(x0, y0, diff_w, h)
        if visual_abut_left:
            # Shared diffusion - draw in green
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(self._shared_diff_color))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(self._source_color))
        painter.drawRect(source_rect)

        # ── Draw Gate (center) section with gradient ─────────────
        gate_rect = QRectF(x0 + diff_w, y0, gate_w, h)
        gradient = QLinearGradient(gate_rect.topLeft(), gate_rect.bottomLeft())
        gradient.setColorAt(0.0, self._gate_color.lighter(110))
        gradient.setColorAt(0.5, self._gate_color)
        gradient.setColorAt(1.0, self._gate_color.darker(110))
        painter.setBrush(QBrush(gradient))
        painter.drawRect(gate_rect)

        # ── Draw Drain (right) section ───────────────────────────
        drain_rect = QRectF(x0 + diff_w + gate_w, y0, diff_w, h)
        if visual_abut_right:
            # Shared diffusion - draw in green
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(self._shared_diff_color))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(self._drain_color))
        painter.drawRect(drain_rect)

        # ── Draw borders ─────────────────────────────────────────
        border_pen = QPen(self._border, 1.5)
        painter.setPen(border_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Top border
        painter.drawLine(QPointF(x0, y0 + 0.75), QPointF(x0 + w, y0 + 0.75))
        # Bottom border
        painter.drawLine(QPointF(x0, y0 + h - 0.75), QPointF(x0 + w, y0 + h - 0.75))

        # Left border: solid black if NOT abutted (thin)
        if not visual_abut_left:
            painter.setPen(QPen(QColor("#1a1a1a"), 0.75))
            painter.drawLine(QPointF(x0 + 1, y0), QPointF(x0 + 1, y0 + h))

        # Right border: solid black if NOT abutted (thin)
        if not visual_abut_right:
            painter.setPen(QPen(QColor("#1a1a1a"), 0.75))
            painter.drawLine(QPointF(x0 + w - 1, y0), QPointF(x0 + w - 1, y0 + h))

        # Internal separator lines (always drawn)
        sep_pen = QPen(self._border, 1.0)
        painter.setPen(sep_pen)
        painter.drawLine(QPointF(x0 + diff_w, y0), QPointF(x0 + diff_w, y0 + h))
        painter.drawLine(QPointF(x0 + diff_w + gate_w, y0), QPointF(x0 + diff_w + gate_w, y0 + h))

        painter.restore()  # back to un-flipped coordinates

        # ── Draw text labels (always readable, no flip) ──────────
        left_rect = QRectF(x0, y0, diff_w, h)
        center_rect = QRectF(x0 + diff_w, y0, gate_w, h)
        right_rect = QRectF(x0 + diff_w + gate_w, y0, diff_w, h)

        # Terminal labels depend on flip state
        left_term = "D" if self._flip_h else "S"
        right_term = "S" if self._flip_h else "D"
        left_net = self._net_labels.get(left_term, "")
        right_net = self._net_labels.get(right_term, "")
        gate_net = self._net_labels.get("G", "")

        # Font sizes - very small as requested
        term_font_size = 3
        term_font = QFont("Segoe UI", term_font_size, QFont.Weight.Bold)

        net_font_size = 5
        net_font = QFont("Segoe UI", net_font_size)

        name_font_size = 3
        name_font = QFont("Segoe UI", name_font_size, QFont.Weight.Bold)

        # ── Device name at bottom (centered across entire device) ────
        painter.setFont(name_font)
        painter.setPen(self._terminal_label_color)
        name_rect = QRectF(x0, y0 + h - 8, w, 8)
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                         self.device_name)

        # ── Left section (S or D) ────────────────────────────────
        # Terminal letter at bottom - only show if NOT abutted
        if not self._hide_left_terminal_label and not visual_abut_left:
            painter.setFont(term_font)
            painter.setPen(self._label_color)
            term_rect = QRectF(left_rect.x(), left_rect.y() + h - 10,
                               left_rect.width(), 8)
            painter.drawText(term_rect, Qt.AlignmentFlag.AlignCenter, left_term)

        # Net name VERTICAL — skip if left edge is abutted (the left neighbor draws it via its right edge)
        if left_net and not visual_abut_left:
            painter.save()
            # Position at center of left diffusion section
            text_x = left_rect.x() + left_rect.width() / 2
            text_y = left_rect.y() + h * 0.50
            painter.translate(text_x, text_y)
            painter.rotate(90)  # Rotate 90 degrees clockwise
            painter.setFont(net_font)
            # Use black for better visibility on any background
            painter.setPen(QColor("#000000"))
            # Draw text centered at origin (after rotation)
            v_rect = QRectF(-h * 0.30, -left_rect.width() / 2, h * 0.60, left_rect.width())
            painter.drawText(v_rect, Qt.AlignmentFlag.AlignCenter, left_net)
            painter.restore()

        # ── Gate section (center) ────────────────────────────────
        # "G" terminal label at bottom
        painter.setFont(term_font)
        painter.setPen(self._terminal_label_color)
        g_term_rect = QRectF(center_rect.x(), center_rect.y() + h - 10,
                             center_rect.width(), 8)
        painter.drawText(g_term_rect, Qt.AlignmentFlag.AlignCenter, "G")

        # Gate net name VERTICAL (rotated 90 degrees clockwise)
        if gate_net:
            painter.save()
            text_x = center_rect.x() + center_rect.width() / 2
            text_y = center_rect.y() + h * 0.50
            painter.translate(text_x, text_y)
            painter.rotate(90)
            painter.setFont(net_font)
            # Use black for better visibility
            painter.setPen(QColor("#000000"))
            v_rect = QRectF(-h * 0.30, -center_rect.width() / 2, h * 0.60, center_rect.width())
            painter.drawText(v_rect, Qt.AlignmentFlag.AlignCenter, gate_net)
            painter.restore()

        # ── Right section (D or S) ───────────────────────────────
        # Terminal letter at bottom - only show if NOT abutted
        if not self._hide_right_terminal_label and not visual_abut_right:
            painter.setFont(term_font)
            painter.setPen(self._label_color)
            term_rect = QRectF(right_rect.x(), right_rect.y() + h - 10,
                               right_rect.width(), 8)
            painter.drawText(term_rect, Qt.AlignmentFlag.AlignCenter, right_term)

        # Net name VERTICAL - skip if right edge is abutted (we draw it via the left device of the pair)
        if right_net and not visual_abut_right:
            painter.save()
            text_x = right_rect.x() + right_rect.width() / 2
            text_y = right_rect.y() + h * 0.50
            painter.translate(text_x, text_y)
            painter.rotate(90)
            painter.setFont(net_font)
            painter.setPen(QColor("#000000"))
            v_rect = QRectF(-h * 0.30, -right_rect.width() / 2, h * 0.60, right_rect.width())
            painter.drawText(v_rect, Qt.AlignmentFlag.AlignCenter, right_net)
            painter.restore()
            
        # Draw SHARED net text exactly once, assigned to the left device of an abutment pair
        if right_net and visual_abut_right:
            painter.save()
            # The left device owns the drawing of the shared net.
            # We want to center it exactly in the middle of the FULL shared overlapping region.
            # The full shared region starts at the left device's right_rect.x(), and spans
            # 2 * right_rect.width() minus the overlap amount (which is overlap_val).
            # But the simplest visual centering is just the very right edge of the left device's right_rect.
            text_x = right_rect.x() + right_rect.width()
            text_y = right_rect.y() + h * 0.50
            painter.translate(text_x, text_y)
            painter.rotate(90)
            painter.setFont(net_font)
            painter.setPen(QColor("#000000"))  # User requested all black shared text
            v_rect = QRectF(-h * 0.30, -right_rect.width() / 2, h * 0.60, right_rect.width())
            painter.drawText(v_rect, Qt.AlignmentFlag.AlignCenter, right_net)
            painter.restore()

        # ── Selection highlight ──────────────────────────────────
        if self.isSelected():
            sel_pen = QPen(QColor("#ffcc00"), 2.5, Qt.PenStyle.SolidLine)
            painter.setPen(sel_pen)
            painter.setBrush(QBrush(QColor(255, 204, 0, 40)))
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

        diff_w = w * 0.30
        gate_w = w * 0.40

        if self._flip_h:
            # Flipped: Source visually on the right, Drain on the left
            s_local = QPointF(x0 + diff_w + gate_w + diff_w / 2, y0 + h / 2)
            d_local = QPointF(x0 + diff_w / 2, y0 + h / 2)
        else:
            s_local = QPointF(x0 + diff_w / 2, y0 + h / 2)
            d_local = QPointF(x0 + diff_w + gate_w + diff_w / 2, y0 + h / 2)

        g_local = QPointF(x0 + diff_w + gate_w / 2, y0 + h / 2)

        return {
            "S": self.mapToScene(s_local),
            "G": self.mapToScene(g_local),
            "D": self.mapToScene(d_local),
        }

    def terminal_rects(self):
        """Return scene rectangles for S, G, D terminal areas for partial highlighting.

        Accounts for horizontal flip so rects match the visual layout.
        """
        rect = self.rect()
        w = rect.width()
        h = rect.height()
        x0 = rect.x()
        y0 = rect.y()

        diff_w = w * 0.30
        gate_w = w * 0.40

        if self._flip_h:
            # Flipped: Source visually on the right, Drain on the left
            s_local = QRectF(x0 + diff_w + gate_w, y0, diff_w, h)
            d_local = QRectF(x0, y0, diff_w, h)
        else:
            s_local = QRectF(x0, y0, diff_w, h)
            d_local = QRectF(x0 + diff_w + gate_w, y0, diff_w, h)

        g_local = QRectF(x0 + diff_w, y0, gate_w, h)

        return {
            "S": self.mapRectToScene(s_local),
            "G": self.mapRectToScene(g_local),
            "D": self.mapRectToScene(d_local),
        }


# ---------------------------------------------------------------------------
# Abutment Group Overlay (for merged multi-device display)
# ---------------------------------------------------------------------------

class AbutGroupItem(QGraphicsRectItem):
    """Merged visual for N abutted devices sharing diffusion nets.

    Draws as one connected shape with shared diffusion in green:
        [S | gate0 | shared(green) | gate1 | shared(green) | ... | D]
    """

    def __init__(self, chain_data, unit_w, unit_h):
        n = len(chain_data)
        dw = unit_w * 0.30          # diffusion section width
        gw = unit_w * 0.40          # gate section width
        total_w = (n + 1) * dw + n * gw

        super().__init__(0, 0, total_w, unit_h)

        self._chain = chain_data
        self._unit_w = unit_w
        self._dw = dw
        self._gw = gw

        # Purely cosmetic — no interaction
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setAcceptHoverEvents(False)
        self.setZValue(50)

        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setPen(QPen(Qt.PenStyle.NoPen))

    @staticmethod
    def _colors_for(dev_type, name=""):
        """Return (diff_c, gate_c, border_c, lbl_c, tlbl_c) for device type."""
        if str(name).upper().startswith("DUMMY"):
            return (QColor("#e8e8e8"), QColor("#888888"),
                    QColor("#666666"), QColor("#444444"), QColor("#333333"))
        if str(dev_type).strip().lower() == "nmos":
            return (QColor("#cce5ff"), QColor("#4a90d9"),
                    QColor("#2d5986"), QColor("#1a365d"), QColor("#ffffff"))
        return (QColor("#ffcccc"), QColor("#d94a4a"),
                QColor("#8b2d2d"), QColor("#5d1a1a"), QColor("#ffffff"))

    @staticmethod
    def _side_net(dev_data, side):
        """Net at visual 'left' or 'right' side, respecting flip_h."""
        nets = dev_data.get('nets', {})
        flip_h = dev_data.get('flip_h', False)
        term = ('D' if flip_h else 'S') if side == 'left' else ('S' if flip_h else 'D')
        return nets.get(term, '')

    @staticmethod
    def _side_term(dev_data, side):
        """Terminal letter at visual 'left' or 'right' side."""
        flip_h = dev_data.get('flip_h', False)
        if side == 'left':
            return 'D' if flip_h else 'S'
        return 'S' if flip_h else 'D'

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        r = self.rect()
        h = r.height()
        x0, y0 = r.x(), r.y()
        n = len(self._chain)
        dw, gw = self._dw, self._gw
        total_w = (n + 1) * dw + n * gw

        shared_color = QColor("#4CAF50")  # Green for shared diffusion

        # ── Draw sections ────────────────────────────────────────
        x = x0
        for i, dev in enumerate(self._chain):
            diff_c, gate_c, *_ = self._colors_for(dev.get('dev_type', ''), dev.get('name', ''))

            # Diffusion section
            painter.setPen(Qt.PenStyle.NoPen)
            if i > 0:
                # Shared diffusion (between devices) - green
                painter.setBrush(QBrush(shared_color))
            else:
                # Outer left diffusion - normal color
                painter.setBrush(QBrush(diff_c))
            painter.drawRect(QRectF(x, y0, dw, h))
            x += dw

            # Gate section with gradient
            g_rect = QRectF(x, y0, gw, h)
            grad = QLinearGradient(g_rect.topLeft(), g_rect.bottomLeft())
            grad.setColorAt(0.0, gate_c.lighter(110))
            grad.setColorAt(0.5, gate_c)
            grad.setColorAt(1.0, gate_c.darker(110))
            painter.setBrush(QBrush(grad))
            painter.drawRect(g_rect)
            x += gw

        # Right-most outer diffusion (normal color)
        last = self._chain[-1]
        diff_c, *_ = self._colors_for(last.get('dev_type', ''), last.get('name', ''))
        painter.setBrush(QBrush(diff_c))
        painter.drawRect(QRectF(x, y0, dw, h))

        # ── Outer border (black) ─────────────────────────────────
        painter.setPen(QPen(QColor("#1a1a1a"), 2.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(x0, y0, total_w, h).adjusted(1, 1, -1, -1))

        # ── Separator lines ──────────────────────────────────────
        _, _, b0, *_ = self._colors_for(
            self._chain[0].get('dev_type', ''), self._chain[0].get('name', ''))
        painter.setPen(QPen(b0, 1.0))
        sx = x0
        for _ in range(n):
            sx += dw
            painter.drawLine(QPointF(sx, y0), QPointF(sx, y0 + h))
            sx += gw
            painter.drawLine(QPointF(sx, y0), QPointF(sx, y0 + h))

        # ── Text labels ──────────────────────────────────────────
        term_fs = max(6, min(8, int(h * 0.18)))
        term_font = QFont("Segoe UI", term_fs, QFont.Weight.Bold)
        net_fs = max(5, min(7, int(h * 0.14)))
        net_font = QFont("Segoe UI", net_fs)
        name_fs = max(6, min(9, int(h * 0.20)))
        name_font = QFont("Segoe UI", name_fs, QFont.Weight.Bold)

        x = x0
        for i, dev in enumerate(self._chain):
            diff_c, gate_c, border_c, lbl_c, tlbl_c = self._colors_for(
                dev.get('dev_type', ''), dev.get('name', ''))
            nets = dev.get('nets', {})

            # Diffusion section label
            dr = QRectF(x, y0, dw, h)
            l_term = self._side_term(dev, 'left')
            l_net = self._side_net(dev, 'left')

            # Terminal letter
            painter.setFont(term_font)
            if i > 0:
                painter.setPen(QColor("#ffffff"))  # White on green
            else:
                painter.setPen(lbl_c)
            term_rect = QRectF(dr.x(), dr.y() + 2, dr.width(), dr.height() * 0.35)
            painter.drawText(term_rect, Qt.AlignmentFlag.AlignCenter, l_term)

            # Net name
            if l_net:
                painter.setFont(net_font)
                net_rect = QRectF(dr.x(), dr.y() + dr.height() * 0.4,
                                  dr.width(), dr.height() * 0.55)
                painter.drawText(net_rect, Qt.AlignmentFlag.AlignCenter, l_net)
            x += dw

            # Gate section
            painter.setFont(name_font)
            painter.setPen(tlbl_c)
            name_rect = QRectF(x, y0 + 2, gw, h * 0.40)
            painter.drawText(name_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                             dev.get('name', ''))

            painter.setFont(term_font)
            g_rect = QRectF(x, y0 + h * 0.35, gw, h * 0.30)
            painter.drawText(g_rect, Qt.AlignmentFlag.AlignCenter, 'G')

            g_net = nets.get('G', '')
            if g_net:
                painter.setFont(net_font)
                g_net_rect = QRectF(x, y0 + h * 0.60, gw, h * 0.35)
                painter.drawText(g_net_rect, Qt.AlignmentFlag.AlignCenter, g_net)
            x += gw

        # Right-most diffusion label
        last = self._chain[-1]
        diff_c, _, _, lbl_c, _ = self._colors_for(last.get('dev_type', ''), last.get('name', ''))
        r_term = self._side_term(last, 'right')
        r_net = self._side_net(last, 'right')
        dr = QRectF(x, y0, dw, h)

        painter.setFont(term_font)
        painter.setPen(lbl_c)
        term_rect = QRectF(dr.x(), dr.y() + 2, dr.width(), dr.height() * 0.35)
        painter.drawText(term_rect, Qt.AlignmentFlag.AlignCenter, r_term)

        if r_net:
            painter.setFont(net_font)
            net_rect = QRectF(dr.x(), dr.y() + dr.height() * 0.4,
                              dr.width(), dr.height() * 0.55)
            painter.drawText(net_rect, Qt.AlignmentFlag.AlignCenter, r_net)

        # ── Selection highlight ──────────────────────────────────
        if self.isSelected():
            painter.setPen(QPen(QColor("#ffcc00"), 2.5))
            painter.setBrush(QBrush(QColor(255, 204, 0, 40)))
            painter.drawRect(QRectF(x0, y0, total_w, h).adjusted(1, 1, -1, -1))
