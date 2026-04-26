"""
KLayout Preview Panel — a QWidget that renders an OAS layout preview
using KLayout's headless renderer and displays it in the symbolic editor.
"""

import os
import sys
import subprocess

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QGraphicsView,
    QGraphicsScene,
    QGraphicsPixmapItem,
    QSizePolicy,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QPainter, QWheelEvent

# Add project root for imports
_project_root = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


class _ZoomableView(QGraphicsView):
    """QGraphicsView subclass with mouse-wheel zoom and middle-button pan."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(Qt.GlobalColor.black)
        self.setStyleSheet(
            "QGraphicsView { background-color: #0e1219; border: none; }"
        )
        self._zoom_factor = 1.0

    def wheelEvent(self, event: QWheelEvent):
        factor = 1.15
        if event.angleDelta().y() > 0:
            self.scale(factor, factor)
            self._zoom_factor *= factor
        else:
            self.scale(1 / factor, 1 / factor)
            self._zoom_factor /= factor

    def fit_to_view(self):
        """Fit the scene contents to the viewport."""
        if self.scene() and not self.scene().items():
            return
        self.fitInView(self.scene().itemsBoundingRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom_factor = 1.0


class KLayoutPanel(QWidget):
    """Panel that shows a KLayout-rendered preview of the physical layout."""

    refresh_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._oas_path = None
        self._pixmap_item = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ---------- Header bar ----------
        header = QWidget()
        header.setFixedHeight(34)
        header.setStyleSheet(
            "background-color: #1a1f2b; border-bottom: 1px solid #2d3548;"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 0, 6, 0)
        header_layout.setSpacing(6)

        title = QLabel("KLayout Preview")
        title.setStyleSheet(
            "color: #8899aa; font-family: 'Segoe UI'; font-size: 10pt; "
            "font-weight: 600; border: none;"
        )
        header_layout.addWidget(title)
        header_layout.addStretch()

        # Fit button
        self._btn_fit = QPushButton("Fit")
        self._btn_fit.setFixedSize(50, 24)
        self._btn_fit.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_fit.setStyleSheet(self._button_style())
        self._btn_fit.clicked.connect(self.fit_to_view)
        header_layout.addWidget(self._btn_fit)

        # Refresh button
        self._btn_refresh = QPushButton("Refresh")
        self._btn_refresh.setFixedSize(70, 24)
        self._btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_refresh.setStyleSheet(self._button_style())
        self._btn_refresh.clicked.connect(self._on_refresh)
        header_layout.addWidget(self._btn_refresh)

        # View in KLayout button
        self._btn_open = QPushButton("Open in KLayout")
        self._btn_open.setFixedSize(110, 24)
        self._btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_open.setStyleSheet(self._button_style())
        self._btn_open.clicked.connect(self._on_open_klayout)
        header_layout.addWidget(self._btn_open)

        layout.addWidget(header)

        # ---------- Zoomable image area ----------
        self._gfx_scene = QGraphicsScene(self)
        self._gfx_view = _ZoomableView(self)
        self._gfx_view.setScene(self._gfx_scene)
        self._gfx_view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        # Placeholder text (shown via a simple text item)
        self._placeholder = self._gfx_scene.addText(
            "No layout rendered yet.\nUse Export to OAS, then Refresh."
        )
        self._placeholder.setDefaultTextColor(Qt.GlobalColor.darkGray)

        layout.addWidget(self._gfx_view)

        # ---------- Status bar ----------
        self._status = QLabel("")
        self._status.setFixedHeight(22)
        self._status.setStyleSheet(
            "color: #556677; font-family: 'Segoe UI'; font-size: 8pt; "
            "background-color: #12161f; border-top: 1px solid #2d3548; "
            "padding: 0 8px;"
        )
        layout.addWidget(self._status)

    @staticmethod
    def _button_style():
        return """
            QPushButton {
                background-color: #232a38;
                color: #c8d0dc;
                border: 1px solid #3d5066;
                border-radius: 4px;
                font-family: 'Segoe UI';
                font-size: 8pt;
                padding: 2px 8px;
            }
            QPushButton:hover {
                background-color: #2d3f54;
                border-color: #4a90d9;
                color: #ffffff;
            }
            QPushButton:pressed {
                background-color: #4a90d9;
            }
        """

    # ── Public API ──────────────────────────────────────────────

    def set_oas_path(self, oas_path):
        """Set the OAS file path to render."""
        self._oas_path = oas_path
        if oas_path:
            self._status.setText(f"File: {os.path.basename(oas_path)}")
        else:
            self._status.setText("")

    def fit_to_view(self):
        """Fit the KLayout preview image to the viewport."""
        self._gfx_view.fit_to_view()

    def refresh_preview(self, oas_path=None):
        """Re-render the OAS file and update the preview image."""
        if oas_path:
            self._oas_path = oas_path

        if not self._oas_path or not os.path.isfile(self._oas_path):
            self._gfx_scene.clear()
            self._pixmap_item = None
            self._placeholder = self._gfx_scene.addText(
                "No OAS file available.\nExport to OAS first (File > Export to OAS)."
            )
            self._placeholder.setDefaultTextColor(Qt.GlobalColor.darkGray)
            self._status.setText("No file")
            return

        try:
            from export.klayout_renderer import render_oas_to_pixmap

            # Render at a high resolution for quality zoom
            panel_w = max(800, self._gfx_view.viewport().width() * 2)
            panel_h = max(600, self._gfx_view.viewport().height() * 2)
            pixmap = render_oas_to_pixmap(
                self._oas_path, panel_w, panel_h
            )

            if pixmap and not pixmap.isNull():
                self._gfx_scene.clear()
                self._placeholder = None
                self._pixmap_item = QGraphicsPixmapItem(pixmap)
                self._gfx_scene.addItem(self._pixmap_item)
                self._gfx_scene.setSceneRect(self._pixmap_item.boundingRect())
                self._status.setText(
                    f"File: {os.path.basename(self._oas_path)}  |  "
                    f"{pixmap.width()}×{pixmap.height()} px  |  Scroll to zoom"
                )
                # Auto-fit after loading
                from PySide6.QtCore import QTimer
                QTimer.singleShot(50, self.fit_to_view)
            else:
                self._gfx_scene.clear()
                self._pixmap_item = None
                t = self._gfx_scene.addText("Render failed.")
                t.setDefaultTextColor(Qt.GlobalColor.darkGray)
                self._status.setText("Render error")

        except Exception as e:
            self._gfx_scene.clear()
            self._pixmap_item = None
            t = self._gfx_scene.addText(f"Error: {e}")
            t.setDefaultTextColor(Qt.GlobalColor.red)
            self._status.setText("Error")
            import traceback
            traceback.print_exc()

    def _on_refresh(self):
        """Handle refresh button click."""
        self.refresh_preview()
        self.refresh_requested.emit()

    def _on_open_klayout(self):
        """Launch KLayout with the current OAS file."""
        if not self._oas_path or not os.path.isfile(self._oas_path):
            return

        try:
            # Try to find KLayout executable
            klayout_exe = self._find_klayout_exe()
            if klayout_exe:
                subprocess.Popen(
                    [klayout_exe, self._oas_path],
                    creationflags=subprocess.CREATE_NO_WINDOW
                    if sys.platform == "win32" else 0,
                )
            else:
                # Fallback: try os.startfile on Windows (opens with default app)
                if sys.platform == "win32":
                    os.startfile(self._oas_path)
                else:
                    subprocess.Popen(["xdg-open", self._oas_path])
        except Exception as e:
            print(f"[KLayout Panel] Could not launch KLayout: {e}")

    @staticmethod
    def _find_klayout_exe():
        """Try to locate the KLayout executable."""
        import shutil

        # Check PATH first
        exe = shutil.which("klayout")
        if exe:
            return exe

        # Common install locations on Windows
        if sys.platform == "win32":
            candidates = [
                os.path.join(os.environ.get("PROGRAMFILES", ""), "KLayout", "klayout_app.exe"),
                os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "KLayout", "klayout_app.exe"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "KLayout", "klayout_app.exe"),
            ]
            for path in candidates:
                if os.path.isfile(path):
                    return path

        return None
