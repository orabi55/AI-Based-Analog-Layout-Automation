from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsItem
from PySide6.QtGui import QBrush, QPen, QColor, QFont, QPainter, QLinearGradient, QFontMetricsF
from PySide6.QtCore import Qt, QRectF, QObject, Signal, QPointF

class BlockSignals(QObject):
    """Helper QObject so BlockItem (a QGraphicsRectItem) can emit signals."""
    drag_started = Signal()
    drag_finished = Signal()
    position_changed = Signal(object) # Emit self when moved
    hover_entered = Signal(object)    # Emit self when hovered
    hover_left = Signal(object)       # Emit self when hover leaves
    double_clicked = Signal(object)   # Emit self when double clicked

def get_colors_for_subckt(subckt_name):
    """Returns (fill_color, border_color, header_color) for a given subcircuit name.
    Uses curated pastel/neon HSL palettes to suit modern dark themes.
    """
    name = str(subckt_name).lower().strip()
    
    # Format: (fill_h, fill_s, fill_l, border_h, border_s, border_l)
    predefined = {
        "inv": (180, 0.65, 0.20, 180, 0.85, 0.65),       # Cyan / Teal
        "inverter": (180, 0.65, 0.20, 180, 0.85, 0.65),
        "nand": (35, 0.75, 0.22, 35, 0.90, 0.68),         # Amber / Orange
        "nor": (350, 0.70, 0.22, 350, 0.85, 0.68),        # Soft Red
        "xor": (275, 0.65, 0.22, 275, 0.85, 0.70),        # Lavender / Violet
        "xnor": (290, 0.65, 0.22, 290, 0.85, 0.70),       # Orchid / Magenta
        "and": (110, 0.60, 0.20, 110, 0.80, 0.62),        # Emerald Green
        "or": (145, 0.60, 0.20, 145, 0.80, 0.62),         # Mint Green
        "mux": (210, 0.70, 0.22, 210, 0.90, 0.70),        # Sky Blue
        "buf": (195, 0.65, 0.20, 195, 0.85, 0.65),        # Turquoise
        "buffer": (195, 0.65, 0.20, 195, 0.85, 0.65),
    }
    
    # Try finding exact or partial substring match
    for key, val in predefined.items():
        if key in name:
            h1, s1, l1, h2, s2, l2 = val
            fill = QColor.fromHslF(h1 / 360.0, s1, l1, 0.18)       # 18% opacity glassmorphism
            border = QColor.fromHslF(h2 / 360.0, s2, l2, 0.75)     # Semi-solid border
            header = QColor.fromHslF(h2 / 360.0, s2, l2, 0.90)     # Solid header
            return fill, border, header
            
    # Deterministic fallback based on hashing the subcircuit name
    h = abs(hash(name)) % 360
    fill = QColor.fromHslF(h / 360.0, 0.60, 0.20, 0.18)
    border = QColor.fromHslF(h / 360.0, 0.75, 0.60, 0.75)
    header = QColor.fromHslF(h / 360.0, 0.75, 0.60, 0.90)
    return fill, border, header

class BlockItem(QGraphicsRectItem):
    """Represents a hierarchical block grouping multiple devices.
    In Symbol View, this is visible and movable. Moving this moves
    its constituent devices.
    """
    def __init__(self, inst_name, subckt, device_items, color, border_color):
        self._device_items = device_items
        
        # Calculate initial bounding box based on children
        union = QRectF()
        if self._device_items:
            union = self._device_items[0].sceneBoundingRect()
            for it in self._device_items[1:]:
                union = union.united(it.sceneBoundingRect())

        w = union.width()
        h = union.height()
        
        super().__init__(0, 0, w, h)
        
        self.inst_name = inst_name
        self.subckt = subckt
        self.signals = BlockSignals()

        self._drag_active = False
        self._drag_start_pos = self.pos()
        self._last_pos = self.pos()
        self._snap_grid_x = None
        self._snap_grid_y = None

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)

        # Dynamic HSL Color assignment
        self._fill_color, self._border_color, self._header_color = get_colors_for_subckt(subckt)
        
        # Interactivity hooks
        self.setAcceptHoverEvents(True)
        self._hover_active = False
        self._collapsed = False  # Expanded in Symbol View by default

        # Z-value sits above grid but behind device labels
        self.setZValue(2)

        # CRITICAL: Prevent initial shift of children during constructor setPos!
        self._in_recalculate = True
        try:
            self.setPos(union.x(), union.y())
            self._last_pos = self.pos()
        finally:
            self._in_recalculate = False

    def set_snap_grid(self, grid_x, grid_y=None):
        self._snap_grid_x = float(grid_x) if grid_x else None
        self._snap_grid_y = float(grid_y) if grid_y else self._snap_grid_x

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self._snap_grid_x and self._snap_grid_y:
            x = round(value.x() / self._snap_grid_x) * self._snap_grid_x
            y = round(value.y() / self._snap_grid_y) * self._snap_grid_y
            return QPointF(x, y)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            if getattr(self, "_in_recalculate", False):
                self._last_pos = self.pos()
                return super().itemChange(change, value)
            # Move all child items by the delta
            new_pos = self.pos()
            delta = new_pos - self._last_pos
            if not delta.isNull():
                for dev in self._device_items:
                    dev.setPos(dev.pos() + delta)
                self._last_pos = new_pos
            self.signals.position_changed.emit(self)
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = self.pos()
            self._last_pos = self.pos()
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
            self._drag_start_pos = self.pos()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._collapsed = not self._collapsed
            self.recalculate_bounds()
            self.signals.double_clicked.emit(self)
            self.update()
        super().mouseDoubleClickEvent(event)

    def hoverEnterEvent(self, event):
        self._hover_active = True
        self.update()
        self.signals.hover_entered.emit(self)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hover_active = False
        self.update()
        self.signals.hover_left.emit(self)
        super().hoverLeaveEvent(event)

    def recalculate_bounds(self):
        """Recalculate the union bounding rect of child items to snap perfectly flush."""
        if not self._device_items:
            return
            
        self._in_recalculate = True
        self.signals.blockSignals(True)
        try:
            union = self._device_items[0].sceneBoundingRect()
            for it in self._device_items[1:]:
                union = union.united(it.sceneBoundingRect())
                
            w = union.width()
            h = union.height()
            
            self.setRect(0, 0, w, h)
            self.setPos(union.x(), union.y())
            self._last_pos = self.pos()
        finally:
            self._in_recalculate = False
            self.signals.blockSignals(False)
        self.update()

    def paint(self, painter: QPainter, option, widget=None):
        painter.save()  # Required: DontSavePainterState optimization is enabled
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        is_selected = self.isSelected()
        is_hovered = getattr(self, "_hover_active", False)
        
        # Border & glow aura styling
        border = self._border_color
        if is_selected:
            border = border.lighter(130)
        elif is_hovered:
            border = border.lighter(115)
            
        # Draw dynamic glow aura
        if is_selected or is_hovered:
            painter.save()
            glow_color = QColor(border)
            glow_color.setAlpha(40 if is_selected else 20)
            glow_pen = QPen(glow_color, 7.0 if is_selected else 4.0, Qt.PenStyle.SolidLine)
            painter.setPen(glow_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, 5.0, 5.0)
            painter.restore()

        if self._collapsed:
            # ----------------------------------------------------
            # RENDER COLLAPSED (MACRO WINDOW MODE)
            # ----------------------------------------------------
            # Translucent linear gradient fill (Glassmorphism)
            gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            c1 = self._fill_color.lighter(110)
            c1.setAlpha(70 if is_selected else (55 if is_hovered else 45))
            c2 = self._fill_color.darker(110)
            c2.setAlpha(45 if is_selected else (35 if is_hovered else 25))
            gradient.setColorAt(0.0, c1)
            gradient.setColorAt(1.0, c2)
            
            painter.setBrush(QBrush(gradient))
            pen = QPen(border, 2.5 if is_selected else (1.5 if is_hovered else 1.0), Qt.PenStyle.SolidLine)
            painter.setPen(pen)
            painter.drawRoundedRect(rect, 4.0, 4.0)

            # We no longer draw the header inside the block; the title is drawn above it.
            # Instead we just draw the title above the block.
            
            # --- Draw Title Above Block ---
            font = QFont("Segoe UI", 10, QFont.Weight.Bold)
            painter.setFont(font)
            title = self.inst_name
            fm = QFontMetricsF(font)
            text_rect = fm.boundingRect(title)
            
            title_pos = QPointF(rect.center().x() - text_rect.width() / 2, rect.y() - 5)
            
            painter.setPen(QPen(QColor("#ffffff")))
            painter.drawText(title_pos, title)
        else:
            # ----------------------------------------------------
            # RENDER EXPANDED (DETAILS MODE)
            # ----------------------------------------------------
            # Soft translucent background wash to show extent
            faint_fill = QColor(self._fill_color)
            faint_fill.setAlpha(12 if is_hovered else 6)
            painter.setBrush(QBrush(faint_fill))
            
            pen_style = Qt.PenStyle.DashLine
            pen = QPen(border, 2.0 if is_selected else (1.2 if is_hovered else 0.8), pen_style)
            painter.setPen(pen)
            painter.drawRoundedRect(rect, 4.0, 4.0)

            # Sleek centered label above the block
            flag_width = max(rect.width(), 200.0)
            flag_rect = QRectF(rect.center().x() - flag_width / 2.0, rect.y() - 14, flag_width, 12)
            painter.setPen(QPen(border.lighter(110)))
            font = QFont("Segoe UI", 7.0, QFont.Weight.Bold)
            painter.setFont(font)
            painter.drawText(flag_rect, Qt.AlignmentFlag.AlignCenter, f"{self.inst_name} ({self.subckt})")

        # ----------------------------------------------------
        # RENDER HOVER HUD TELEMETRY CARD
        # ----------------------------------------------------
        if is_hovered:
            painter.save()
            
            hud_width = 175
            hud_height = 85
            hud_y = rect.center().y() - (hud_height / 2.0)
            hud_x = rect.right() + 10
            
            hud_rect = QRectF(hud_x, hud_y, hud_width, hud_height)
            
            # HUD overlay background (neon glassmorphic box)
            hud_brush = QBrush(QColor(10, 16, 26, 240)) # Ice cold premium navy
            painter.setBrush(hud_brush)
            hud_border = QColor(border).lighter(120)
            hud_border.setAlpha(200)
            painter.setPen(QPen(hud_border, 1.2))
            painter.drawRoundedRect(hud_rect, 6.0, 6.0)
            
            # HUD header: Subcircuit type
            painter.setPen(QColor("#00f2fe")) # Electric neon cyan
            title_font = QFont("Segoe UI", 8, QFont.Weight.Bold)
            painter.setFont(title_font)
            painter.drawText(hud_rect.adjusted(10, 8, -10, -6), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, f"📊 {self.subckt.upper()}")
            
            # Compute PMOS/NMOS device counts
            pmos_cnt = sum(1 for it in self._device_items if str(getattr(it, 'device_type', '')).strip().lower() == 'pmos')
            nmos_cnt = sum(1 for it in self._device_items if str(getattr(it, 'device_type', '')).strip().lower() != 'pmos')
            
            # Telemetry text
            painter.setPen(QColor("#e0e6ed")) # Soft white
            detail_font = QFont("Segoe UI", 7.5, QFont.Weight.Normal)
            painter.setFont(detail_font)
            
            details_text = (
                f"Instance: {self.inst_name}\n"
                f"Devices: {len(self._device_items)} ({pmos_cnt}P / {nmos_cnt}N)\n"
                f"Width: {rect.width():.2f} um\n"
                f"Height: {rect.height():.2f} um"
            )
            painter.drawText(hud_rect.adjusted(10, 24, -10, -6), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, details_text)
            painter.restore()

        painter.restore()  # Balance top-level save()
