"""
hierarchy_group_item.py
=======================
Visual wrapper for hierarchical device groups (arrays, multipliers, fingers).

After AI placement, devices like MM5 (m=3, nf=4) consist of individual
finger DeviceItems.  HierarchyGroupItem wraps them as a single big
bounding box that can be dragged (moves all children together) and
double-clicked (descends/ascends the hierarchy).

Z-value is set BELOW DeviceItems so device drag events are not blocked.
"""

from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsItem
from PySide6.QtGui import QBrush, QPen, QColor, QFont, QPainter
from PySide6.QtCore import Qt, QRectF, QObject, Signal


class HierarchyGroupSignals(QObject):
    """Helper QObject so HierarchyGroupItem can emit signals."""
    drag_started = Signal()
    drag_finished = Signal()
    position_changed = Signal(object)
    descend_requested = Signal(object)
    ascend_requested = Signal(object)


class HierarchyGroupItem(QGraphicsRectItem):
    """
    Draggable bounding box around a group of DeviceItems.

    - Drag the header bar or empty parts of the box → moves all children
    - Click/drag on a DeviceItem → moves that device normally (Z-order ensures
      DeviceItems are ABOVE this overlay so they catch events first)
    - Double-click the header bar → descend / ascend hierarchy
    """

    def __init__(self, parent_name, device_items, hierarchy_info,
                 color=None, border_color=None):
        self._device_items = device_items
        self._hierarchy_info = hierarchy_info
        self._parent_name = parent_name

        # Compute bounding box from children
        union = QRectF()
        if device_items:
            union = device_items[0].sceneBoundingRect()
            for it in device_items[1:]:
                union = union.united(it.sceneBoundingRect())

        # Header height for click detection
        self._header_height = min(20.0, union.height() * 0.35)
        if self._header_height < 12:
            self._header_height = 12

        super().__init__(0, 0, union.width(), union.height())
        self.setPos(union.x(), union.y())

        self.signals = HierarchyGroupSignals()

        # Colors
        self._fill_color = color or QColor(30, 40, 60, 60)
        self._border_color = border_color or QColor(100, 140, 200, 180)

        # State
        self._drag_active = False
        self._drag_start_pos = self.pos()
        self._last_pos = self.pos()
        self._is_descended = False
        self._child_groups = []
        self._parent_group = None

        # Flags — movable, selectable, below DeviceItems in Z-order
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setZValue(1)  # BELOW DeviceItems (default Z=0 for devices is actually scene-default)
        # Actually put it BELOW: negative Z
        self.setZValue(-1)

        self.setVisible(True)

    def has_children(self):
        return bool(self._child_groups)

    def descend(self):
        if not self.has_children():
            return
        self._is_descended = True
        self.setVisible(False)
        for child in self._child_groups:
            child.setVisible(True)
        self.signals.descend_requested.emit(self)

    def ascend(self):
        self._is_descended = False
        self.setVisible(True)
        for child in self._child_groups:
            child.setVisible(False)
        self.signals.ascend_requested.emit(self)

    def _is_in_header(self, pos):
        """Check if a local position is in the header bar."""
        return pos.y() <= self._header_height

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            new_pos = self.pos()
            delta = new_pos - self._last_pos
            if not delta.isNull():
                for dev in self._device_items:
                    dev.setPos(dev.pos() + delta)
                for child in self._child_groups:
                    child.setPos(child.pos() + delta)
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
        """Double-click header bar to descend/ascend."""
        if self._is_in_header(event.pos()):
            if self._is_descended and self._parent_group:
                self._parent_group.ascend()
                event.accept()
                return
            if self._child_groups:
                self.descend()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def paint(self, painter, option, widget=None):
        if not self.isVisible():
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        is_selected = self.isSelected()

        # Main fill
        painter.setBrush(QBrush(self._fill_color))
        border = self._border_color.lighter(150) if is_selected else self._border_color
        pen = QPen(border, 2.0 if is_selected else 1.5, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 4.0, 4.0)

        # Header bar
        hh = self._header_height
        header_rect = QRectF(rect.x(), rect.y(), rect.width(), hh)

        painter.setBrush(QBrush(border))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(header_rect, 4.0, 4.0)
        if hh < rect.height():
            painter.drawRect(QRectF(rect.x(), rect.y() + hh - 4.0,
                                     rect.width(), 4.0))

        # Label
        hi = self._hierarchy_info
        m = hi.get("m", 1)
        nf = hi.get("nf", 1)
        is_array = hi.get("is_array", False)

        if is_array:
            label = f"  {self._parent_name}  (array={m})"
        elif m > 1 and nf > 1:
            label = f"  {self._parent_name}  (m={m}, nf={nf})"
        elif m > 1:
            label = f"  {self._parent_name}  (m={m})"
        elif nf > 1:
            label = f"  {self._parent_name}  (nf={nf})"
        else:
            label = f"  {self._parent_name}"

        painter.setPen(QPen(QColor("#ffffff")))
        font_size = max(7, min(10, int(hh * 0.6)))
        font = QFont("Segoe UI", font_size, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(header_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)

        # Drill-down indicator
        indicator = ""
        if self._is_descended:
            indicator = "▲"
        elif self._child_groups or len(self._device_items) > 1:
            indicator = "▼"

        if indicator:
            painter.setPen(QPen(QColor("#aaddff")))
            painter.setFont(QFont("Segoe UI", font_size, QFont.Weight.Bold))
            painter.drawText(
                QRectF(rect.x() + rect.width() - 28, rect.y(), 24, hh),
                Qt.AlignmentFlag.AlignCenter, indicator)
