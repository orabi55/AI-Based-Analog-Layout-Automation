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
    QScrollArea,
    QSizePolicy,
)
from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QPixmap, QColor

# Add project root for imports
_project_root = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


class KLayoutPanel(QWidget):
    """Panel that shows a KLayout-rendered preview of the physical layout."""

    refresh_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._oas_path = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ---------- Header bar ----------
        header = QWidget()
        header.setFixedHeight(34)
        header.setStyleSheet(
            "background-color: #141a23; border-bottom: 1px solid #2d3a4f;"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 0, 6, 0)
        header_layout.setSpacing(6)

        title = QLabel("KLayout Preview")
        title.setStyleSheet(
            "color: #9fb0c7; font-family: 'Segoe UI'; font-size: 10pt; "
            "font-weight: 600; border: none;"
        )
        header_layout.addWidget(title)
        header_layout.addStretch()

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

        # ---------- Image area ----------
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._scroll.setStyleSheet(
            """
            QScrollArea {
                background-color: #0d121a;
                border: none;
            }
            QScrollBar:vertical {
                background: #111722; width: 8px; border: none;
            }
            QScrollBar::handle:vertical {
                background: #31435d; border-radius: 4px; min-height: 30px;
            }
            QScrollBar:horizontal {
                background: #111722; height: 8px; border: none;
            }
            QScrollBar::handle:horizontal {
                background: #31435d; border-radius: 4px; min-width: 30px;
            }
            QScrollBar::add-line, QScrollBar::sub-line {
                width: 0; height: 0;
            }
            """
        )

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setStyleSheet("background-color: #0d121a; border: none;")
        self._image_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        # Placeholder text
        self._image_label.setText("No layout rendered yet.\nUse Export to OAS, then Refresh.")
        self._image_label.setStyleSheet(
            "color: #8a9bb1; font-family: 'Segoe UI'; font-size: 10pt; "
            "background-color: #0d121a; border: none; padding: 20px;"
        )

        self._scroll.setWidget(self._image_label)
        layout.addWidget(self._scroll)

        # ---------- Status bar ----------
        self._status = QLabel("")
        self._status.setFixedHeight(22)
        self._status.setStyleSheet(
            "color: #8797ad; font-family: 'Segoe UI'; font-size: 8pt; "
            "background-color: #111722; border-top: 1px solid #2d3a4f; "
            "padding: 0 8px;"
        )
        layout.addWidget(self._status)

    @staticmethod
    def _button_style():
        return """
            QPushButton {
                background-color: #1f2a3a;
                color: #d7e1ef;
                border: 1px solid #364a67;
                border-radius: 4px;
                font-family: 'Segoe UI';
                font-size: 8pt;
                padding: 2px 8px;
            }
            QPushButton:hover {
                background-color: #2a3b53;
                border-color: #5a9bff;
                color: #ffffff;
            }
            QPushButton:pressed {
                background-color: #3f7fdd;
            }
        """

    def set_oas_path(self, oas_path):
        """Set the OAS file path to render."""
        self._oas_path = oas_path
        if oas_path:
            self._status.setText(f"File: {os.path.basename(oas_path)}")
        else:
            self._status.setText("")

    def refresh_preview(self, oas_path=None):
        """Re-render the OAS file and update the preview image."""
        if oas_path:
            self._oas_path = oas_path

        if not self._oas_path or not os.path.isfile(self._oas_path):
            self._image_label.setPixmap(QPixmap())
            self._image_label.setText(
                "No OAS file available.\n"
                "Export to OAS first (File > Export to OAS)."
            )
            self._image_label.setStyleSheet(
                "color: #8a9bb1; font-family: 'Segoe UI'; font-size: 10pt; "
                "background-color: #0d121a; border: none; padding: 20px;"
            )
            self._status.setText("No file")
            return

        try:
            from export.klayout_renderer import render_oas_to_pixmap

            # Render at a reasonable resolution
            panel_w = max(400, self._scroll.viewport().width())
            panel_h = max(300, self._scroll.viewport().height())
            pixmap = render_oas_to_pixmap(
                self._oas_path, panel_w, panel_h
            )

            if pixmap and not pixmap.isNull():
                self._image_label.setPixmap(pixmap)
                self._image_label.setText("")
                self._image_label.setStyleSheet(
                    "background-color: #0d121a; border: none;"
                )
                self._status.setText(
                    f"File: {os.path.basename(self._oas_path)}  |  "
                    f"{pixmap.width()}x{pixmap.height()} px"
                )
            else:
                self._image_label.setText("Render failed.")
                self._status.setText("Render error")

        except Exception as e:
            self._image_label.setText(f"Error: {e}")
            self._image_label.setStyleSheet(
                "color: #d46a6a; font-family: 'Segoe UI'; font-size: 9pt; "
                "background-color: #0d121a; border: none; padding: 20px;"
            )
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
