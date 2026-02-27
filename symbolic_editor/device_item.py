from PySide6.QtWidgets import (
    QGraphicsRectItem,
    QGraphicsItem,
    QStyleOptionGraphicsItem,
    QStyle,
)
from PySide6.QtGui import QBrush, QPen, QColor, QFont, QPainter
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
        self.device_type = dev_type
        self.signals = DeviceSignals()

        self._drag_active = False
        self._drag_start_pos = QPointF()

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)

        if dev_type == "nmos":
            self._fill = QColor("#d6eaf8")
            self._border = QColor("#5b9bd5")
        else:
            self._fill = QColor("#fadbd8")
            self._border = QColor("#e74c3c")

        self.setBrush(QBrush(self._fill))
        self.setPen(QPen(self._border, 1.5))

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
    # Painting
    # --------------------------------------------------
    def paint(self, painter: QPainter, option, widget=None):
        # Suppress the default dashed selection rectangle
        my_option = QStyleOptionGraphicsItem(option)
        my_option.state &= ~QStyle.StateFlag.State_Selected

        # Draw the rectangle with suppressed selection
        super().paint(painter, my_option, widget)

        # Draw bright selection border if selected
        if self.isSelected():
            pen = QPen(QColor("#FFB300"), 3.0, Qt.PenStyle.SolidLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect().adjusted(1, 1, -1, -1))

        # Draw device name centered inside the rectangle
        rect = self.rect()
        painter.setPen(QColor("#000000"))

        # Pick a font size that fits inside the device width
        font_size = max(4, min(12, int(rect.width() / 4)))
        font = QFont("Segoe UI", font_size, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.device_name)