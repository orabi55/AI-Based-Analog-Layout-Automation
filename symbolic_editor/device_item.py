from PySide6.QtWidgets import (
    QGraphicsRectItem,
    QGraphicsTextItem,
    QGraphicsItem
)
from PySide6.QtGui import QBrush, QPen
from PySide6.QtCore import Qt


class DeviceItem(QGraphicsRectItem):

    def __init__(self, name, dev_type, x, y, width, height):

        super().__init__(0, 0, width, height)

        self.setPos(x, y)

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)

        if dev_type == "nmos":
            self.setBrush(QBrush(Qt.GlobalColor.blue))
        else:
            self.setBrush(QBrush(Qt.GlobalColor.red))

        self.setPen(QPen(Qt.GlobalColor.black))

        label = QGraphicsTextItem(name, self)
        label.setDefaultTextColor(Qt.GlobalColor.white)
        label.setPos(5, 5)