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
        self.device_type = dev_type
        self.signals = DeviceSignals()

        self._drag_active = False
        self._drag_start_pos = QPointF()

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)

        # --- Color palette per device type ---
        if dev_type == "nmos":
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

        # Divide into 3 vertical sections: Source | Gate | Drain
        source_w = w * 0.30
        gate_w = w * 0.40
        drain_w = w * 0.30

        source_rect = QRectF(x0, y0, source_w, h)
        gate_rect = QRectF(x0 + source_w, y0, gate_w, h)
        drain_rect = QRectF(x0 + source_w + gate_w, y0, drain_w, h)

        # --- Draw Source (left) ---
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self._source_color))
        painter.drawRect(source_rect)

        # --- Draw Gate (center) — full fill with gradient ---
        gradient = QLinearGradient(gate_rect.topLeft(), gate_rect.bottomLeft())
        gradient.setColorAt(0.0, self._gate_color.lighter(115))
        gradient.setColorAt(0.5, self._gate_color)
        gradient.setColorAt(1.0, self._gate_color.darker(115))
        painter.setBrush(QBrush(gradient))
        painter.drawRect(gate_rect)

        # --- Draw Drain (right) ---
        painter.setBrush(QBrush(self._drain_color))
        painter.drawRect(drain_rect)

        # --- Outer border (sharp corners) ---
        border_pen = QPen(self._border, 1.5)
        painter.setPen(border_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))

        # --- Vertical separator lines ---
        sep_pen = QPen(self._border.darker(120), 1.0)
        painter.setPen(sep_pen)
        painter.drawLine(
            QPointF(x0 + source_w, y0),
            QPointF(x0 + source_w, y0 + h)
        )
        painter.drawLine(
            QPointF(x0 + source_w + gate_w, y0),
            QPointF(x0 + source_w + gate_w, y0 + h)
        )

        # --- Terminal labels (S, G, D) ---
        term_font_size = max(3, min(9, int(min(source_w, h) / 3)))
        term_font = QFont("Segoe UI", term_font_size, QFont.Weight.Bold)
        painter.setFont(term_font)

        # S label
        painter.setPen(self._label_color)
        painter.drawText(source_rect, Qt.AlignmentFlag.AlignCenter, "S")

        # G label (lower portion of gate)
        g_label_rect = QRectF(gate_rect.x(), gate_rect.y() + gate_rect.height() * 0.45,
                              gate_rect.width(), gate_rect.height() * 0.55)
        painter.setPen(self._terminal_label_color)
        painter.drawText(g_label_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, "G")

        # D label
        painter.setPen(self._label_color)
        painter.drawText(drain_rect, Qt.AlignmentFlag.AlignCenter, "D")

        # --- Device name inside gate area, spanning full width (drawn last = on top) ---
        name_font_size = max(3, min(10, int(w / 5)))
        name_font = QFont("Segoe UI", name_font_size, QFont.Weight.Bold)
        painter.setFont(name_font)
        painter.setPen(QColor("#FFFFFF"))

        # Span full device width so long names aren't clipped
        name_rect = QRectF(x0, gate_rect.y() + 2, w, gate_rect.height() * 0.50)
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self.device_name)

        # --- Selection highlight (thin black border inside rect) ---
        if self.isSelected():
            sel_pen = QPen(QColor("#000000"), 1.0, Qt.PenStyle.SolidLine)
            painter.setPen(sel_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect.adjusted(1, 1, -1, -1))

    def terminal_anchors(self):
        """Return scene positions for S, G, D terminal centers."""
        rect = self.rect()
        w = rect.width()
        h = rect.height()
        x0 = rect.x()
        y0 = rect.y()

        source_w = w * 0.30
        gate_w = w * 0.40

        # Center of each terminal section, mapped to scene coords
        s_local = QPointF(x0 + source_w / 2, y0 + h / 2)
        g_local = QPointF(x0 + source_w + gate_w / 2, y0 + h / 2)
        d_local = QPointF(x0 + source_w + gate_w + (w * 0.30) / 2, y0 + h / 2)

        return {
            "S": self.mapToScene(s_local),
            "G": self.mapToScene(g_local),
            "D": self.mapToScene(d_local),
        }