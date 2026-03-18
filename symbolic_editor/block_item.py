from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsItem
from PySide6.QtGui import QBrush, QPen, QColor, QFont, QPainter
from PySide6.QtCore import Qt, QRectF, QObject, Signal, QPointF

class BlockSignals(QObject):
    """Helper QObject so BlockItem (a QGraphicsRectItem) can emit signals."""
    drag_started = Signal()
    drag_finished = Signal()
    position_changed = Signal(object) # Emit self when moved

class BlockItem(QGraphicsRectItem):
    """Represents a hierarchical block grouping multiple devices.
    In Symbol View, this is visible and movable. Moving this moves
    its constituent devices.
    """
    def __init__(self, inst_name, subckt, device_items, color, border_color):
        
        # Calculate initial bounding box based on children
        self._device_items = device_items
        
        union = QRectF()
        if self._device_items:
            union = self._device_items[0].sceneBoundingRect()
            for it in self._device_items[1:]:
                union = union.united(it.sceneBoundingRect())

        padding = 8.0
        label_height = 18.0
        w = union.width() + padding * 2
        h = union.height() + padding * 2 + label_height
        
        super().__init__(0, 0, w, h)
        
        # We set pos to the top-left of the union
        self.setPos(union.x() - padding, union.y() - padding - label_height)
        
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

        self._fill_color = color
        self._border_color = border_color
        
        # ZValue positive so it draws over grid
        self.setZValue(5)

    def set_snap_grid(self, grid_x, grid_y=None):
        self._snap_grid_x = float(grid_x) if grid_x else None
        self._snap_grid_y = float(grid_y) if grid_y else self._snap_grid_x

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self._snap_grid_x and self._snap_grid_y:
            x = round(value.x() / self._snap_grid_x) * self._snap_grid_x
            y = round(value.y() / self._snap_grid_y) * self._snap_grid_y
            return QPointF(x, y)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
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
            # Update drag start pos so next diff is correct
            self._drag_start_pos = self.pos()
        super().mouseReleaseEvent(event)

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        
        # Fill
        brush = QBrush(self._fill_color)
        painter.setBrush(brush)
        
        # Selection highlight
        is_selected = self.isSelected()
        border = self._border_color
        if is_selected:
            border = border.lighter(150)
            
        pen = QPen(border, 2.0 if is_selected else 1.5, Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        
        # Draw main block background
        painter.drawRoundedRect(rect, 4.0, 4.0)
        
        # Draw header bar
        header_height = 20.0
        header_rect = QRectF(rect.x(), rect.y(), rect.width(), header_height)
        
        painter.setBrush(QBrush(border))
        painter.setPen(Qt.PenStyle.NoPen)
        # We can just draw a rounded rect for the top overlapping perfectly
        painter.drawRoundedRect(header_rect, 4.0, 4.0)
        
        # Text
        painter.setPen(QPen(QColor("#ffffff")))
        font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        painter.setFont(font)
        
        title = f"{self.inst_name}: {self.subckt}"
        painter.drawText(header_rect, Qt.AlignmentFlag.AlignCenter, title)
        
        # Subtext details
        text_rect = QRectF(rect.x(), rect.y() + header_height, rect.width(), rect.height() - header_height)
        subfont = QFont("Segoe UI", 8)
        painter.setFont(subfont)
        painter.setPen(QPen(QColor("#e0e0e0")))
        dev_count = len(self._device_items)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, f"{dev_count} Devices")
