import sys
import json
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsView,
    QGraphicsScene
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter

from device_item import DeviceItem


class SymbolicEditor(QGraphicsView):

    def __init__(self, placement_file):
        super().__init__()

        # Create scene
        self.scene = QGraphicsScene()
        self.setScene(self.scene)

        # Better rendering
        self.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Enable selection box
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)

        # Enable pan with middle mouse
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

        # Zoom parameters
        self.zoom_factor = 1.15

        # Load placement
        self.load_placement(placement_file)

        # Scene size
        self.scene.setSceneRect(self.scene.itemsBoundingRect())

    # -------------------------------------------------
    # Load AI JSON Placement
    # -------------------------------------------------
    def load_placement(self, placement_file):

        with open(placement_file) as f:
            data = json.load(f)

        if "nodes" not in data:
            raise ValueError("JSON must contain 'nodes' key")

        nodes = data["nodes"]

        scale = 80  # visual scaling

        for node in nodes:

            geom = node.get("geometry", {})

            x = geom.get("x", 0) * scale
            y = -geom.get("y", 0) * scale   # invert Y axis

            width = geom.get("width", 1) * scale
            height = geom.get("height", 0.5) * scale

            item = DeviceItem(
                node.get("id", "unknown"),
                node.get("type", "nmos"),
                x,
                y,
                width,
                height
            )

            self.scene.addItem(item)

    # -------------------------------------------------
    # Zoom with Mouse Wheel
    # -------------------------------------------------
    def wheelEvent(self, event):

        if event.angleDelta().y() > 0:
            self.scale(self.zoom_factor, self.zoom_factor)
        else:
            self.scale(1 / self.zoom_factor, 1 / self.zoom_factor)

    # -------------------------------------------------
    # Pan with Middle Mouse
    # -------------------------------------------------
    def mousePressEvent(self, event):

        if event.button() == Qt.MouseButton.MiddleButton:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            fake_event = event
            fake_event = type(event)(
                event.type(),
                event.position(),
                event.globalPosition(),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                event.modifiers()
            )
            super().mousePressEvent(fake_event)
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):

        if event.button() == Qt.MouseButton.MiddleButton:
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        else:
            super().mouseReleaseEvent(event)


# -------------------------------------------------
# Main Entry
# -------------------------------------------------
if __name__ == "__main__":

    app = QApplication(sys.argv)

    editor = SymbolicEditor("xor_initial_placement.json")

    editor.setWindowTitle("Symbolic Layout Editor")
    editor.resize(1200, 900)
    editor.show()

    sys.exit(app.exec())