import sys
import os
import json
import threading
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsView,
    QGraphicsScene,
    QMainWindow,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTextEdit,
    QLineEdit,
    QPushButton,
    QLabel,
    QFrame,
)
from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QPainter, QColor, QFont, QIcon
from PySide6.QtWidgets import QFileDialog

from device_item import DeviceItem


# -------------------------------------------------
# Signal bridge for thread-safe LLM responses
# -------------------------------------------------
class LLMSignals(QObject):
    response_ready = Signal(str)
    error_occurred = Signal(str)


# -------------------------------------------------
# Chat Widget (Right Panel)
# -------------------------------------------------
class ChatPanel(QWidget):
    """Chat panel for interacting with the LLM."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.llm_signals = LLMSignals()
        self.llm_signals.response_ready.connect(self._on_llm_response)
        self.llm_signals.error_occurred.connect(self._on_llm_error)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QFrame()
        header.setFixedHeight(40)
        header.setStyleSheet(
            "background-color: #f0f4f8; border-bottom: 1px solid #d0d7de;"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 12, 0)
        title = QLabel("AI Assistant")
        title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        title.setStyleSheet("color: #1a1a2e;")
        header_layout.addWidget(title)
        layout.addWidget(header)

        # Chat display
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setStyleSheet(
            """
            QTextEdit {
                background-color: #ffffff;
                border: none;
                padding: 12px;
                font-family: 'Segoe UI', sans-serif;
                font-size: 13px;
                color: #333;
            }
            """
        )
        layout.addWidget(self.chat_display)

        # Input area
        input_frame = QFrame()
        input_frame.setStyleSheet(
            "background-color: #f0f4f8; border-top: 1px solid #d0d7de;"
        )
        input_layout = QHBoxLayout(input_frame)
        input_layout.setContentsMargins(8, 8, 8, 8)
        input_layout.setSpacing(6)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Ask the AI assistant...")
        self.input_field.setStyleSheet(
            """
            QLineEdit {
                border: 1px solid #d0d7de;
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 13px;
                background: white;
            }
            QLineEdit:focus {
                border-color: #4a90d9;
            }
            """
        )
        self.input_field.returnPressed.connect(self.send_message)
        input_layout.addWidget(self.input_field)

        send_btn = QPushButton("Send")
        send_btn.setFixedWidth(60)
        send_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #4a90d9;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #357abd;
            }
            QPushButton:pressed {
                background-color: #2a5f9e;
            }
            """
        )
        send_btn.clicked.connect(self.send_message)
        input_layout.addWidget(send_btn)

        layout.addWidget(input_frame)

    # -----------------------------------------
    # Messaging
    # -----------------------------------------
    def send_message(self):
        text = self.input_field.text().strip()
        if not text:
            return
        self._append_message("User", text, "#f7f7f7", "#333")
        self.input_field.clear()
        self._call_llm(text)

    def _append_message(self, sender, text, bg_color, text_color):
        align = "right" if sender == "User" else "left"
        bubble_bg = bg_color
        html = f"""
        <div style="text-align:{align}; margin:6px 0;">
            <span style="
                display:inline-block;
                background:{bubble_bg};
                color:{text_color};
                padding:8px 14px;
                border-radius:12px;
                font-size:13px;
                max-width:85%;
                text-align:left;
                border:1px solid #e0e0e0;
            ">
                <b>{sender}:</b> {text}
            </span>
        </div>
        """
        self.chat_display.append(html)
        self.chat_display.verticalScrollBar().setValue(
            self.chat_display.verticalScrollBar().maximum()
        )

    # -----------------------------------------
    # LLM integration (runs in background thread)
    # -----------------------------------------
    def _call_llm(self, user_message):
        """Send user_message to the configured LLM in a background thread."""
        self._append_message("AI", "Thinking...", "#e8f4fd", "#1a1a2e")
        thread = threading.Thread(
            target=self._llm_worker, args=(user_message,), daemon=True
        )
        thread.start()

    def _llm_worker(self, user_message):
        """Worker that runs in a background thread."""
        try:
            # Try Gemini first
            api_key = os.environ.get("GEMINI_API_KEY")
            if api_key:
                from google import genai

                client = genai.Client(api_key=api_key)
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=user_message,
                )
                if response and response.text:
                    self.llm_signals.response_ready.emit(response.text.strip())
                    return

            # Try OpenAI
            api_key = os.environ.get("OPENAI_API_KEY")
            if api_key:
                from openai import OpenAI

                client = OpenAI(api_key=api_key)
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": user_message}],
                    temperature=0.3,
                )
                reply = response.choices[0].message.content
                self.llm_signals.response_ready.emit(reply.strip())
                return

            # Try Ollama (local)
            import requests

            try:
                resp = requests.post(
                    "http://localhost:11434/api/generate",
                    json={
                        "model": "llama3.2",
                        "prompt": user_message,
                        "stream": False,
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                result = resp.json()
                reply = result.get("response", "")
                if reply:
                    self.llm_signals.response_ready.emit(reply.strip())
                    return
            except Exception:
                pass

            self.llm_signals.error_occurred.emit(
                "No LLM configured. Set GEMINI_API_KEY, OPENAI_API_KEY, "
                "or run Ollama locally."
            )
        except Exception as e:
            self.llm_signals.error_occurred.emit(str(e))

    def _on_llm_response(self, text):
        # Remove the "Thinking..." message
        self._remove_last_message()
        self._append_message("AI", text, "#e8f4fd", "#1a1a2e")

    def _on_llm_error(self, error_text):
        self._remove_last_message()
        self._append_message("AI", f"Error: {error_text}", "#fde8e8", "#a00")

    def _remove_last_message(self):
        """Remove the last appended message (the 'Thinking...' bubble)."""
        cursor = self.chat_display.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.movePosition(
            cursor.MoveOperation.StartOfBlock, cursor.MoveMode.KeepAnchor
        )
        # Select back enough to remove the last HTML block
        html = self.chat_display.toHtml()
        # Find and remove last <div> block
        idx = html.rfind("<div style=")
        if idx != -1:
            self.chat_display.setHtml(html[:idx])


# -------------------------------------------------
# Device Tree Panel (Left Panel)
# -------------------------------------------------
class DeviceTreePanel(QWidget):
    """Left panel showing devices and hierarchy."""

    device_selected = Signal(str)
    save_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Save button
        save_btn = QPushButton("Save Layout")
        save_btn.setFixedHeight(36)
        save_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #2e7d32;
                color: white;
                border: none;
                font-weight: bold;
                font-size: 13px;
                font-family: 'Segoe UI';
            }
            QPushButton:hover {
                background-color: #388e3c;
            }
            QPushButton:pressed {
                background-color: #1b5e20;
            }
            """
        )
        save_btn.clicked.connect(self.save_requested.emit)
        layout.addWidget(save_btn)

        # Header
        header = QFrame()
        header.setFixedHeight(40)
        header.setStyleSheet(
            "background-color: #1e2a3a; border-bottom: 1px solid #2d3f54;"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 12, 0)
        title = QLabel("Device Hierarchy")
        title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        title.setStyleSheet("color: #c8d6e5;")
        header_layout.addWidget(title)
        layout.addWidget(header)

        # Tree widget
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setStyleSheet(
            """
            QTreeWidget {
                background-color: #1a2332;
                border: none;
                color: #c8d6e5;
                font-family: 'Segoe UI', sans-serif;
                font-size: 13px;
                padding: 4px;
            }
            QTreeWidget::item {
                padding: 6px 8px;
                border-radius: 4px;
            }
            QTreeWidget::item:hover {
                background-color: #2d3f54;
            }
            QTreeWidget::item:selected {
                background-color: #4a90d9;
                color: white;
            }
            QTreeWidget::branch {
                background-color: #1a2332;
            }
            """
        )
        self.tree.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.tree)

    def load_devices(self, nodes):
        """Populate tree from the placement JSON nodes."""
        self.tree.clear()

        # Group devices by type
        nmos_devices = []
        pmos_devices = []
        for node in nodes:
            dev_type = node.get("type", "unknown")
            if dev_type == "nmos":
                nmos_devices.append(node)
            elif dev_type == "pmos":
                pmos_devices.append(node)

        # NMOS group
        if nmos_devices:
            nmos_root = QTreeWidgetItem(self.tree, ["NMOS Devices"])
            nmos_root.setFont(0, QFont("Segoe UI", 11, QFont.Weight.Bold))
            nmos_root.setForeground(0, QColor("#7ec8e3"))
            for dev in nmos_devices:
                dev_id = dev.get("id", "unknown")
                elec = dev.get("electrical", {})
                info = f"{dev_id}  (nf={elec.get('nf',1)}, nfin={elec.get('nfin','?')})"
                item = QTreeWidgetItem(nmos_root, [info])
                item.setData(0, Qt.ItemDataRole.UserRole, dev_id)
            nmos_root.setExpanded(True)

        # PMOS group
        if pmos_devices:
            pmos_root = QTreeWidgetItem(self.tree, ["PMOS Devices"])
            pmos_root.setFont(0, QFont("Segoe UI", 11, QFont.Weight.Bold))
            pmos_root.setForeground(0, QColor("#e87474"))
            for dev in pmos_devices:
                dev_id = dev.get("id", "unknown")
                elec = dev.get("electrical", {})
                info = f"{dev_id}  (nf={elec.get('nf',1)}, nfin={elec.get('nfin','?')})"
                item = QTreeWidgetItem(pmos_root, [info])
                item.setData(0, Qt.ItemDataRole.UserRole, dev_id)
            pmos_root.setExpanded(True)

    def highlight_device(self, dev_id):
        """Highlight the tree item matching the given device id."""
        self.tree.blockSignals(True)
        self.tree.clearSelection()
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            group = root.child(i)
            for j in range(group.childCount()):
                child = group.child(j)
                if child.data(0, Qt.ItemDataRole.UserRole) == dev_id:
                    child.setSelected(True)
                    self.tree.scrollToItem(child)
                    self.tree.blockSignals(False)
                    return
        self.tree.blockSignals(False)

    def _on_item_clicked(self, item, column):
        dev_id = item.data(0, Qt.ItemDataRole.UserRole)
        if dev_id:
            self.device_selected.emit(dev_id)


# -------------------------------------------------
# Graphics Canvas (Center Panel)
# -------------------------------------------------
class SymbolicEditor(QGraphicsView):

    device_clicked = Signal(str)

    def __init__(self):
        super().__init__()

        # Create scene
        self.scene = QGraphicsScene()
        self.setScene(self.scene)
        self.scene.selectionChanged.connect(self._on_selection_changed)

        # Better rendering
        self.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Enable selection box
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)

        # Enable pan with middle mouse
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

        # Zoom parameters
        self.zoom_factor = 1.15

        # Device items lookup by id
        self.device_items = {}

        self.setStyleSheet("border: none; background-color: #fafbfc;")

    # -------------------------------------------------
    # Load AI JSON Placement
    # -------------------------------------------------
    def load_placement(self, nodes):
        """Load placement from a list of node dicts."""
        self.scene.clear()
        self.device_items.clear()

        self.scale_factor = 80  # visual scaling

        for node in nodes:
            geom = node.get("geometry", {})

            x = geom.get("x", 0) * self.scale_factor
            y = -geom.get("y", 0) * self.scale_factor  # invert Y axis

            width = geom.get("width", 1) * self.scale_factor
            height = geom.get("height", 0.5) * self.scale_factor

            item = DeviceItem(
                node.get("id", "unknown"),
                node.get("type", "nmos"),
                x,
                y,
                width,
                height,
            )

            self.scene.addItem(item)
            self.device_items[node.get("id", "unknown")] = item

        # Scene size
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-50, -50, 50, 50))

    def get_updated_positions(self):
        """Return a dict mapping device id -> (x, y) in original coordinates."""
        positions = {}
        for dev_id, item in self.device_items.items():
            pos = item.pos()
            positions[dev_id] = (
                pos.x() / self.scale_factor,
                -pos.y() / self.scale_factor,  # un-invert Y axis
            )
        return positions

    def _on_selection_changed(self):
        """Emit device_clicked when user selects a device on the canvas."""
        selected = self.scene.selectedItems()
        if selected and hasattr(selected[0], 'device_name'):
            self.device_clicked.emit(selected[0].device_name)

    def highlight_device(self, dev_id):
        """Highlight and center on a device by its id."""
        # Block signals to avoid feedback loop
        self.scene.blockSignals(True)
        self.scene.clearSelection()
        item = self.device_items.get(dev_id)
        if item:
            item.setSelected(True)
            self.centerOn(item)
        self.scene.blockSignals(False)

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
            fake_event = type(event)(
                event.type(),
                event.position(),
                event.globalPosition(),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                event.modifiers(),
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
# Main Window
# -------------------------------------------------
class MainWindow(QMainWindow):

    def __init__(self, placement_file):
        super().__init__()
        self.setWindowTitle("Symbolic Layout Editor")
        self.resize(1400, 900)

        # Load placement data
        with open(placement_file) as f:
            data = json.load(f)
        if "nodes" not in data:
            raise ValueError("JSON must contain 'nodes' key")
        self._original_data = data
        self.nodes = data["nodes"]

        # --- Create panels ---
        self.device_tree = DeviceTreePanel()
        self.editor = SymbolicEditor()
        self.chat_panel = ChatPanel()

        # --- Splitter layout ---
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.device_tree)
        splitter.addWidget(self.editor)
        splitter.addWidget(self.chat_panel)

        # Set proportions: left ~200px, center stretches, right ~300px
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([220, 860, 320])

        splitter.setStyleSheet(
            """
            QSplitter::handle {
                background-color: #d0d7de;
                width: 1px;
            }
            """
        )

        self.setCentralWidget(splitter)

        # Populate panels
        self.device_tree.load_devices(self.nodes)
        self.editor.load_placement(self.nodes)

        # Connect device tree selection to canvas highlight
        self.device_tree.device_selected.connect(self.editor.highlight_device)

        # Connect canvas selection to tree highlight
        self.editor.device_clicked.connect(self.device_tree.highlight_device)

        # Connect save button
        self.device_tree.save_requested.connect(self._save_layout)

    def _save_layout(self):
        """Save the current layout to a new JSON file."""
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Layout", "", "JSON Files (*.json)"
        )
        if not file_path:
            return

        import copy
        updated_nodes = copy.deepcopy(self.nodes)
        positions = self.editor.get_updated_positions()

        for node in updated_nodes:
            dev_id = node.get("id")
            if dev_id in positions:
                x, y = positions[dev_id]
                node["geometry"]["x"] = x
                node["geometry"]["y"] = y

        output = {"nodes": updated_nodes}
        # Preserve edges if they exist in original data
        if hasattr(self, '_original_data') and "edges" in self._original_data:
            output["edges"] = self._original_data["edges"]

        with open(file_path, "w") as f:
            json.dump(output, f, indent=4)

        self.chat_panel._append_message(
            "AI", f"Layout saved to {os.path.basename(file_path)}", "#e8f4fd", "#1a1a2e"
        )


# -------------------------------------------------
# Main Entry
# -------------------------------------------------
if __name__ == "__main__":

    app = QApplication(sys.argv)

    # Global application style
    app.setStyle("Fusion")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    placement_path = os.path.join(script_dir, "..", "Xor_initial_placement.json")

    window = MainWindow(placement_path)
    window.show()

    sys.exit(app.exec())