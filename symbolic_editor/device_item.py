from PySide6.QtWidgets import (
    QGraphicsRectItem,
    QGraphicsItem
)
from PySide6.QtGui import QBrush, QPen, QColor, QFont, QPainter
from PySide6.QtCore import Qt, QRectF


class DeviceItem(QGraphicsRectItem):

    def __init__(self, name, dev_type, x, y, width, height):

        super().__init__(0, 0, width, height)

        self.setPos(x, y)
        self.device_name = name
        self.device_type = dev_type

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)

        if dev_type == "nmos":
            self._fill = QColor("#d6eaf8")
            self._border = QColor("#5b9bd5")
        else:
            self._fill = QColor("#fadbd8")
            self._border = QColor("#e74c3c")

        self.setBrush(QBrush(self._fill))
        self.setPen(QPen(self._border, 1.5))

    def paint(self, painter: QPainter, option, widget=None):
        # Draw the rectangle first
        super().paint(painter, option, widget)

        # Draw device name centered inside the rectangle
        rect = self.rect()
        painter.setPen(QColor("#000000"))

        # Pick a font size that fits inside the device width
        font_size = max(4, min(12, int(rect.width() / 4)))
        font = QFont("Segoe UI", font_size, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.device_name)