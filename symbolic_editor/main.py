import sys
import os
import json
import re
import copy
import math
import threading
from datetime import datetime
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
    QToolBar,
    QFileDialog,
    QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QObject, QTimer
from PySide6.QtGui import QPainter, QColor, QFont, QIcon, QAction, QKeySequence

from device_item import DeviceItem


# -------------------------------------------------
# Signal bridge for thread-safe LLM responses
# -------------------------------------------------
class LLMSignals(QObject):
    response_ready = Signal(str)
    error_occurred = Signal(str)


# -------------------------------------------------
# Auto-resizing Input Widget
# -------------------------------------------------
class ChatInputEdit(QTextEdit):
    """A QTextEdit that acts like a single-line input but grows up to 4 lines.
    Enter sends, Shift+Enter inserts a newline."""

    submit_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setPlaceholderText("Ask the AI assistant…")
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.document().contentsChanged.connect(self._adjust_height)
        self._min_h = 36
        self._max_h = 100
        self.setFixedHeight(self._min_h)

    def _adjust_height(self):
        doc_height = int(self.document().size().height()) + 12
        self.setFixedHeight(max(self._min_h, min(doc_height, self._max_h)))

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.submit_requested.emit()
                return
        super().keyPressEvent(event)


# -------------------------------------------------
# Chat Widget (Right Panel)
# -------------------------------------------------
class ChatPanel(QWidget):
    """Chat panel for interacting with the LLM."""

    command_requested = Signal(dict)  # emits parsed command dicts

    def __init__(self, parent=None):
        super().__init__(parent)
        self.llm_signals = LLMSignals()
        self.llm_signals.response_ready.connect(self._on_llm_response)
        self.llm_signals.error_occurred.connect(self._on_llm_error)
        self._layout_context = None
        self._chat_history = []  # multi-turn: list of {"role", "content"}
        self._thinking_timer = None
        self._thinking_dots = 0
        self._init_ui()
        self._show_welcome()

    def set_layout_context(self, nodes, edges=None):
        """Store the layout data so the LLM can reference it."""
        self._layout_context = {"nodes": nodes}
        if edges:
            self._layout_context["edges"] = edges

    def _build_system_prompt(self):
        """Build a system prompt that includes layout context."""
        prompt = (
            "You are an AI assistant embedded in an Analog Layout Editor. "
            "You help the user understand and optimize their circuit placement.\n\n"
            "RULES:\n"
            "1. Keep responses SHORT and conversational (1-3 sentences). "
            "NEVER output the full JSON layout.\n"
            "2. When the user asks you to perform an action (swap, move devices), "
            "include a command tag in your response using this exact format:\n"
            '   [CMD]{"action": "swap", "device_a": "ID1", "device_b": "ID2"}[/CMD]\n'
            '   [CMD]{"action": "move", "device": "ID", "x": 1.0, "y": 0.5}[/CMD]\n'
            "3. The command tag will be parsed and executed automatically. "
            "The user will NOT see the [CMD] block, only your conversational text.\n"
            "4. You may include multiple [CMD] blocks in one response if needed.\n"
            "5. Only use device IDs that exist in the layout data.\n"
            "6. You may use markdown formatting: **bold**, *italic*, "
            "- bullet lists, `code`.\n"
        )
        if self._layout_context:
            prompt += (
                "\nCurrent layout data (JSON):\n"
                f"```json\n{json.dumps(self._layout_context, indent=2)}\n```\n"
            )
        return prompt

    # -----------------------------------------
    # UI
    # -----------------------------------------
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QFrame()
        header.setFixedHeight(44)
        header.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #1e2a3a, stop:1 #2d3f54);"
            "border-bottom: 1px solid #4a90d9;"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 8, 0)

        title = QLabel("🤖 AI Assistant")
        title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        title.setStyleSheet("color: #e0e8f0;")
        header_layout.addWidget(title)

        header_layout.addStretch()

        # Clear chat button
        clear_btn = QPushButton("🗑️")
        clear_btn.setFixedSize(30, 30)
        clear_btn.setToolTip("Clear conversation")
        clear_btn.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                border: none;
                font-size: 16px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: rgba(255,255,255,0.15);
            }
            """
        )
        clear_btn.clicked.connect(self._clear_chat)
        header_layout.addWidget(clear_btn)

        layout.addWidget(header)

        # Chat display
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setTextInteractionFlags(
            Qt.TextInteractionFlag.NoTextInteraction
        )
        self.chat_display.setStyleSheet(
            """
            QTextEdit {
                background-color: #1a2332;
                border: none;
                padding: 8px;
                font-family: 'Segoe UI', sans-serif;
                font-size: 13px;
                color: #d0d8e0;
            }
            QScrollBar:vertical {
                width: 6px;
                background: transparent;
            }
            QScrollBar::handle:vertical {
                background: #3d5066;
                border-radius: 3px;
                min-height: 30px;
            }
            """
        )
        layout.addWidget(self.chat_display)

        # Input area
        input_frame = QFrame()
        input_frame.setStyleSheet(
            "background-color: #1e2a3a; border-top: 1px solid #2d3f54;"
        )
        input_layout = QHBoxLayout(input_frame)
        input_layout.setContentsMargins(8, 8, 8, 8)
        input_layout.setSpacing(6)

        self.input_field = ChatInputEdit()
        self.input_field.setStyleSheet(
            """
            QTextEdit {
                border: 1px solid #3d5066;
                border-radius: 8px;
                padding: 6px 10px;
                font-size: 13px;
                font-family: 'Segoe UI';
                background: #253445;
                color: #e0e8f0;
            }
            QTextEdit:focus {
                border-color: #4a90d9;
            }
            """
        )
        self.input_field.submit_requested.connect(self.send_message)
        input_layout.addWidget(self.input_field)

        send_btn = QPushButton("➤")
        send_btn.setFixedSize(36, 36)
        send_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #4a90d9;
                color: white;
                border: none;
                border-radius: 18px;
                font-size: 16px;
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
    # Markdown to HTML (lightweight)
    # -----------------------------------------
    @staticmethod
    def _md_to_html(text):
        """Convert basic markdown to HTML."""
        # Escape HTML entities first
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # Code blocks (```)
        text = re.sub(
            r'```(\w*)\n(.*?)```',
            r'<pre style="background:#2d2d2d;color:#f8f8f2;padding:8px;'
            r'border-radius:6px;font-size:12px;overflow-x:auto;">\2</pre>',
            text, flags=re.DOTALL,
        )
        # Inline code
        text = re.sub(
            r'`([^`]+)`',
            r'<code style="background:#e8ecf0;padding:1px 5px;'
            r'border-radius:3px;font-size:12px;">\1</code>',
            text,
        )
        # Bold
        text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
        # Italic
        text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
        # Bullet lists
        text = re.sub(
            r'(?m)^[\-\*]\s+(.+)$',
            r'<div style="margin:2px 0 2px 12px;">• \1</div>',
            text,
        )
        # Numbered lists
        text = re.sub(
            r'(?m)^(\d+)\.\s+(.+)$',
            r'<div style="margin:2px 0 2px 12px;">\1. \2</div>',
            text,
        )
        # Line breaks
        text = text.replace("\n", "<br>")
        return text

    # -----------------------------------------
    # Welcome message
    # -----------------------------------------
    def _show_welcome(self):
        welcome = (
            "Welcome! I'm your layout assistant. Here's what I can do:<br><br>"
            "<div style='margin-left:12px;'>"
            "• <b>Swap devices</b> — \"Swap MM28 with MM25\"<br>"
            "• <b>Move devices</b> — \"Move MM3 to x=0.5 y=0.3\"<br>"
            "• <b>Analyze layout</b> — \"How many NMOS devices?\"<br>"
            "• <b>Optimize</b> — \"Suggest a better placement\"<br>"
            "</div><br>"
            "<i>Tip: I remember our conversation, so feel free to ask follow-ups!</i>"
        )
        self._append_bubble("ai", welcome, is_html=True)

    # -----------------------------------------
    # Bubble rendering
    # -----------------------------------------
    def _append_bubble(self, role, text, is_html=False):
        """Render a modern chat bubble.  role = 'user' | 'ai' | 'system'."""
        now = datetime.now().strftime("%H:%M")
        content = text if is_html else self._md_to_html(text)

        if role == "user":
            html = f"""
            <div style="text-align:right; margin:4px 0;">
                <div style="display:inline-block; max-width:82%; text-align:left;">
                    <div style="
                        background: #4a90d9;
                        color: white;
                        padding: 10px 14px;
                        border-radius: 14px 14px 4px 14px;
                        font-size: 13px;
                        line-height: 1.4;
                    ">
                        {content}
                    </div>
                    <div style="font-size:10px; color:#667788; text-align:right; margin-top:2px;">
                        {now}
                    </div>
                </div>
            </div>
            """
        else:
            avatar = "🤖" if role == "ai" else "ℹ️"
            bg = "#253445" if role == "ai" else "#3a3520"
            border_col = "#3d5066" if role == "ai" else "#5a5030"
            text_col = "#e0e8f0" if role == "ai" else "#f0e8c0"
            html = f"""
            <div style="text-align:left; margin:4px 0;">
                <div style="display:inline-block; max-width:88%; text-align:left;">
                    <div style="font-size:10px; color:#667788; margin-bottom:2px;">
                        {avatar} AI Assistant
                    </div>
                    <div style="
                        background: {bg};
                        color: {text_col};
                        padding: 10px 14px;
                        border-radius: 4px 14px 14px 14px;
                        font-size: 13px;
                        line-height: 1.5;
                        border: 1px solid {border_col};
                    ">
                        {content}
                    </div>
                    <div style="font-size:10px; color:#667788; margin-top:2px;">
                        {now}
                    </div>
                </div>
            </div>
            """
        self.chat_display.append(html)
        self.chat_display.verticalScrollBar().setValue(
            self.chat_display.verticalScrollBar().maximum()
        )

    # -----------------------------------------
    # Messaging
    # -----------------------------------------
    def send_message(self):
        text = self.input_field.toPlainText().strip()
        if not text:
            return
        self._append_bubble("user", text)
        self._chat_history.append({"role": "user", "content": text})
        self.input_field.clear()
        self._call_llm(text)

    def _clear_chat(self):
        """Clear the chat display and history."""
        self.chat_display.clear()
        self._chat_history.clear()
        self._show_welcome()

    # keep backward-compat for external callers
    def _append_message(self, sender, text, bg_color, text_color):
        role = "user" if sender == "User" else "ai"
        self._append_bubble(role, text)

    # -----------------------------------------
    # Animated thinking indicator
    # -----------------------------------------
    def _start_thinking(self):
        self._thinking_dots = 0
        self._append_bubble("ai", "Thinking")
        self._thinking_timer = QTimer(self)
        self._thinking_timer.timeout.connect(self._animate_thinking)
        self._thinking_timer.start(400)

    def _animate_thinking(self):
        self._thinking_dots = (self._thinking_dots + 1) % 4
        dots = "." * self._thinking_dots
        # Update the last bubble text
        html = self.chat_display.toHtml()
        idx = html.rfind("Thinking")
        if idx != -1:
            # Find the end of "Thinking..." text
            end = html.find("<", idx)
            if end != -1:
                html = html[:idx] + "Thinking" + dots + html[end:]
                self.chat_display.setHtml(html)
                self.chat_display.verticalScrollBar().setValue(
                    self.chat_display.verticalScrollBar().maximum()
                )

    def _stop_thinking(self):
        if self._thinking_timer:
            self._thinking_timer.stop()
            self._thinking_timer = None

    # -----------------------------------------
    # LLM integration (runs in background thread)
    # -----------------------------------------
    def _call_llm(self, user_message):
        """Send user_message to the configured LLM in a background thread."""
        self._start_thinking()
        thread = threading.Thread(
            target=self._llm_worker, args=(user_message,), daemon=True
        )
        thread.start()

    def _llm_worker(self, user_message):
        """Worker that runs in a background thread."""
        system_prompt = self._build_system_prompt()

        # Build multi-turn context
        history_text = ""
        for msg in self._chat_history[-10:]:  # last 10 messages for context
            role_label = "User" if msg["role"] == "user" else "Assistant"
            history_text += f"{role_label}: {msg['content']}\n"

        full_prompt = f"{system_prompt}\n\nConversation history:\n{history_text}"

        try:
            # Try Gemini first
            api_key = "AIzaSyApwhWPssGbI6L5siyrfn24AYQWe52NW2E"
            if api_key:
                from google import genai

                client = genai.Client(api_key=api_key)
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=full_prompt,
                )
                if response and response.text:
                    self.llm_signals.response_ready.emit(response.text.strip())
                    return

            # Try OpenAI
            api_key = os.environ.get("OPENAI_API_KEY")
            if api_key:
                from openai import OpenAI

                client = OpenAI(api_key=api_key)
                messages = [{"role": "system", "content": system_prompt}]
                for msg in self._chat_history[-10:]:
                    messages.append(msg)
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=messages,
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
                        "prompt": full_prompt,
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
        self._stop_thinking()
        # Remove the thinking bubble
        self._remove_last_message()

        # Parse and execute any [CMD]...[/CMD] blocks
        display_text, commands = self._parse_commands(text)
        for cmd in commands:
            self.command_requested.emit(cmd)

        clean = display_text.strip()
        self._chat_history.append({"role": "assistant", "content": clean})
        self._append_bubble("ai", clean)

    def _parse_commands(self, text):
        """Extract [CMD]...[/CMD] blocks, return (display_text, list_of_cmds)."""
        commands = []
        pattern = r'\[CMD\](.*?)\[/CMD\]'
        for match in re.finditer(pattern, text, re.DOTALL):
            try:
                cmd = json.loads(match.group(1).strip())
                commands.append(cmd)
            except json.JSONDecodeError:
                pass
        display_text = re.sub(pattern, '', text, flags=re.DOTALL)
        return display_text, commands

    def _on_llm_error(self, error_text):
        self._stop_thinking()
        self._remove_last_message()
        self._append_bubble("ai", f"⚠️ Error: {error_text}")

    def _remove_last_message(self):
        """Remove the last appended message (the thinking bubble)."""
        html = self.chat_display.toHtml()
        # Find and remove last bubble div
        idx = html.rfind('<div style="text-align:')
        if idx != -1:
            self.chat_display.setHtml(html[:idx])


# -------------------------------------------------
# Device Tree Panel (Left Panel)
# -------------------------------------------------
class DeviceTreePanel(QWidget):
    """Left panel showing devices and hierarchy."""

    device_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

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

        # Enable caching to speed up grid drawing
        self.setCacheMode(QGraphicsView.CacheModeFlag.CacheBackground)

        # Enable selection box
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)

        # Enable pan with middle mouse
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

        # Zoom parameters
        self.zoom_factor = 1.15
        self._zoom_level = 1.0

        # Device items lookup by id
        self.device_items = {}

        # Grid settings
        self._grid_size = 20   # base grid spacing in scene coords
        self._grid_color = QColor("#dce1e8")
        self._grid_color_major = QColor("#b8c0cc")

        self.setStyleSheet("border: none; background-color: #f0f2f5;")

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

        # Set scene rect large enough for panning, grid draws everywhere
        bounds = self.scene.itemsBoundingRect()
        self.scene.setSceneRect(bounds.adjusted(-500, -500, 500, 500))

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

    def swap_devices(self, id_a, id_b):
        """Swap the positions of two devices on the canvas."""
        item_a = self.device_items.get(id_a)
        item_b = self.device_items.get(id_b)
        if item_a and item_b:
            pos_a = item_a.pos()
            pos_b = item_b.pos()
            item_a.setPos(pos_b)
            item_b.setPos(pos_a)
            return True
        return False

    def move_device(self, dev_id, x, y):
        """Move a device to an absolute position (in layout coordinates)."""
        item = self.device_items.get(dev_id)
        if item:
            item.setPos(x * self.scale_factor, -y * self.scale_factor)
            return True
        return False

    def _on_selection_changed(self):
        """Emit device_clicked when user selects a device on the canvas."""
        selected = self.scene.selectedItems()
        if selected and hasattr(selected[0], 'device_name'):
            self.device_clicked.emit(selected[0].device_name)

    def fit_to_view(self):
        """Zoom and pan to fit all devices in the viewport."""
        if not self.device_items:
            return
        # Compute bounding rect from device items only
        rects = [item.sceneBoundingRect() for item in self.device_items.values()]
        union = rects[0]
        for r in rects[1:]:
            union = union.united(r)
        margin = max(union.width(), union.height()) * 0.08
        margin = max(margin, 30)
        self.fitInView(
            union.adjusted(-margin, -margin, margin, margin),
            Qt.AspectRatioMode.KeepAspectRatio,
        )
        # Track zoom level for grid
        self._zoom_level = self.transform().m11()

    def highlight_device(self, dev_id):
        """Highlight a device by its id without moving the view."""
        # Block signals to avoid feedback loop
        self.scene.blockSignals(True)
        self.scene.clearSelection()
        item = self.device_items.get(dev_id)
        if item:
            item.setSelected(True)
        self.scene.blockSignals(False)

    # -------------------------------------------------
    # -------------------------------------------------
    # Background Grid
    # -------------------------------------------------
    def drawBackground(self, painter: QPainter, rect):
        """Draw a dot-grid background."""
        super().drawBackground(painter, rect)

        gs = self._grid_size
        left = int(math.floor(rect.left() / gs)) * gs
        top = int(math.floor(rect.top() / gs)) * gs
        right = int(math.ceil(rect.right() / gs)) * gs
        bottom = int(math.ceil(rect.bottom() / gs)) * gs

        # Minor dots
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._grid_color)
        dot_radius = max(1.0, 1.5 / max(self._zoom_level, 0.1))
        x = left
        while x <= right:
            y = top
            while y <= bottom:
                painter.drawEllipse(float(x) - dot_radius / 2,
                                    float(y) - dot_radius / 2,
                                    dot_radius, dot_radius)
                y += gs
            x += gs

        # Major dots every 5 grid units
        major = gs * 5
        left_m = int(math.floor(rect.left() / major)) * major
        top_m = int(math.floor(rect.top() / major)) * major
        painter.setBrush(self._grid_color_major)
        big_radius = dot_radius * 2
        x = left_m
        while x <= right:
            y = top_m
            while y <= bottom:
                painter.drawEllipse(float(x) - big_radius / 2,
                                    float(y) - big_radius / 2,
                                    big_radius, big_radius)
                y += major
            x += major

    # -------------------------------------------------
    # Zoom with Mouse Wheel
    # -------------------------------------------------
    def wheelEvent(self, event):
        if event.angleDelta().y() > 0:
            self.scale(self.zoom_factor, self.zoom_factor)
        else:
            self.scale(1 / self.zoom_factor, 1 / self.zoom_factor)
        self._zoom_level = self.transform().m11()
        self.resetCachedContent()

    def zoom_in(self):
        self.scale(self.zoom_factor, self.zoom_factor)
        self._zoom_level = self.transform().m11()
        self.resetCachedContent()

    def zoom_out(self):
        self.scale(1 / self.zoom_factor, 1 / self.zoom_factor)
        self._zoom_level = self.transform().m11()
        self.resetCachedContent()

    def zoom_reset(self):
        self.resetTransform()
        self._zoom_level = 1.0
        self.resetCachedContent()

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

        # Undo / Redo stacks
        self._undo_stack = []
        self._redo_stack = []
        self._current_file = placement_file

        # Load placement data
        self._load_data(placement_file)

        # --- Create panels ---
        self.device_tree = DeviceTreePanel()
        self.editor = SymbolicEditor()
        self.chat_panel = ChatPanel()

        # --- Toolbar ---
        self._create_toolbar()

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
        self._refresh_panels()

        # Fit view after initial load
        QTimer.singleShot(100, self.editor.fit_to_view)

        # Connect device tree selection to canvas highlight
        self.device_tree.device_selected.connect(self.editor.highlight_device)

        # Connect canvas selection to tree highlight
        self.editor.device_clicked.connect(self.device_tree.highlight_device)

        # Connect AI command execution
        self.chat_panel.command_requested.connect(self._handle_ai_command)

    # -------------------------------------------------
    # Toolbar
    # -------------------------------------------------
    def _create_toolbar(self):
        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(toolbar.iconSize())
        toolbar.setStyleSheet(
            """
            QToolBar {
                background-color: #1e2a3a;
                border-bottom: 1px solid #2d3f54;
                spacing: 4px;
                padding: 4px 8px;
            }
            QToolButton {
                color: #c8d6e5;
                background: transparent;
                border: 1px solid transparent;
                border-radius: 4px;
                padding: 5px 10px;
                font-family: 'Segoe UI';
                font-size: 12px;
            }
            QToolButton:hover {
                background-color: #2d3f54;
                border-color: #4a90d9;
            }
            QToolButton:pressed {
                background-color: #4a90d9;
            }
            QToolButton:disabled {
                color: #556677;
            }
            """
        )
        self.addToolBar(toolbar)

        # Load JSON
        act_load = QAction("📂 Load", self)
        act_load.setShortcut(QKeySequence("Ctrl+O"))
        act_load.setToolTip("Load JSON placement file (Ctrl+O)")
        act_load.triggered.connect(self._on_load)
        toolbar.addAction(act_load)

        # Save
        act_save = QAction("💾 Save", self)
        act_save.setShortcut(QKeySequence("Ctrl+S"))
        act_save.setToolTip("Save to current file (Ctrl+S)")
        act_save.triggered.connect(self._on_save)
        toolbar.addAction(act_save)

        # Save As
        act_save_as = QAction("💾 Save As", self)
        act_save_as.setShortcut(QKeySequence("Ctrl+Shift+S"))
        act_save_as.setToolTip("Save to a new file (Ctrl+Shift+S)")
        act_save_as.triggered.connect(self._on_save_as)
        toolbar.addAction(act_save_as)

        # Export JSON
        act_export = QAction("📤 Export", self)
        act_export.setShortcut(QKeySequence("Ctrl+E"))
        act_export.setToolTip("Export layout as formatted JSON (Ctrl+E)")
        act_export.triggered.connect(self._on_export)
        toolbar.addAction(act_export)

        toolbar.addSeparator()

        # Undo
        self._act_undo = QAction("↩ Undo", self)
        self._act_undo.setShortcuts([QKeySequence("Ctrl+Z")])
        self._act_undo.setToolTip("Undo last action (Ctrl+Z)")
        self._act_undo.setEnabled(False)
        self._act_undo.triggered.connect(self._on_undo)
        toolbar.addAction(self._act_undo)

        # Redo (Ctrl+Y and Ctrl+Shift+Z)
        self._act_redo = QAction("↪ Redo", self)
        self._act_redo.setShortcuts(
            [QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")]
        )
        self._act_redo.setToolTip("Redo last undone action (Ctrl+Y / Ctrl+Shift+Z)")
        self._act_redo.setEnabled(False)
        self._act_redo.triggered.connect(self._on_redo)
        toolbar.addAction(self._act_redo)

        toolbar.addSeparator()

        # Fit to View
        act_fit = QAction("🔲 Fit", self)
        act_fit.setShortcut(QKeySequence("F"))
        act_fit.setToolTip("Fit all devices in view (F)")
        act_fit.triggered.connect(self.editor.fit_to_view)
        toolbar.addAction(act_fit)

        toolbar.addSeparator()

        # Zoom In
        act_zoom_in = QAction("🔍+ Zoom In", self)
        act_zoom_in.setShortcut(QKeySequence("Ctrl+="))
        act_zoom_in.setToolTip("Zoom in (Ctrl++)")
        act_zoom_in.triggered.connect(self.editor.zoom_in)
        toolbar.addAction(act_zoom_in)

        # Zoom Out
        act_zoom_out = QAction("🔍- Zoom Out", self)
        act_zoom_out.setShortcut(QKeySequence("Ctrl+-"))
        act_zoom_out.setToolTip("Zoom out (Ctrl+-)")
        act_zoom_out.triggered.connect(self.editor.zoom_out)
        toolbar.addAction(act_zoom_out)

        # Zoom Reset
        act_zoom_reset = QAction("🔍 Reset Zoom", self)
        act_zoom_reset.setShortcut(QKeySequence("Ctrl+0"))
        act_zoom_reset.setToolTip("Reset zoom (Ctrl+0)")
        act_zoom_reset.triggered.connect(self.editor.zoom_reset)
        toolbar.addAction(act_zoom_reset)

        toolbar.addSeparator()

        # Select All
        act_select_all = QAction("☐ Select All", self)
        act_select_all.setShortcut(QKeySequence("Ctrl+A"))
        act_select_all.setToolTip("Select all devices (Ctrl+A)")
        act_select_all.triggered.connect(self._select_all_devices)
        toolbar.addAction(act_select_all)

        # Delete
        act_delete = QAction("🗑 Delete", self)
        act_delete.setShortcut(QKeySequence("Delete"))
        act_delete.setToolTip("Delete selected devices (Delete)")
        act_delete.triggered.connect(self._delete_selected)
        toolbar.addAction(act_delete)

    # -------------------------------------------------
    # Data helpers
    # -------------------------------------------------
    def _load_data(self, filepath):
        """Load placement JSON into internal state."""
        with open(filepath) as f:
            data = json.load(f)
        if "nodes" not in data:
            raise ValueError("JSON must contain 'nodes' key")
        self._original_data = data
        self.nodes = data["nodes"]

    def _refresh_panels(self):
        """Refresh all panels from self.nodes."""
        self.device_tree.load_devices(self.nodes)
        self.editor.load_placement(self.nodes)
        self.chat_panel.set_layout_context(
            self.nodes, self._original_data.get("edges")
        )
        # Wire up drag signals on each device for undo tracking
        for item in self.editor.device_items.values():
            item.signals.drag_started.connect(self._on_device_drag_start)
            item.signals.drag_finished.connect(self._on_device_drag_end)

    def _build_output_data(self):
        """Build the output dict with updated positions."""
        self._sync_node_positions()
        output = {"nodes": copy.deepcopy(self.nodes)}
        if "edges" in self._original_data:
            output["edges"] = self._original_data["edges"]
        return output

    # -------------------------------------------------
    # Undo / Redo
    # -------------------------------------------------
    def _push_undo(self):
        """Snapshot current positions onto the undo stack."""
        snapshot = copy.deepcopy(self.nodes)
        self._undo_stack.append(snapshot)
        self._redo_stack.clear()
        self._update_undo_redo_state()

    def _update_undo_redo_state(self):
        self._act_undo.setEnabled(bool(self._undo_stack))
        self._act_redo.setEnabled(bool(self._redo_stack))

    def _on_device_drag_start(self):
        """Called when the user starts dragging a device — push undo."""
        self._sync_node_positions()
        self._push_undo()

    def _on_device_drag_end(self):
        """Called when the user finishes dragging a device — sync positions."""
        self._sync_node_positions()

    def _on_undo(self):
        if not self._undo_stack:
            return
        # Make sure current canvas positions are synced before saving to redo
        self._sync_node_positions()
        self._redo_stack.append(copy.deepcopy(self.nodes))
        # Restore previous state
        self.nodes = self._undo_stack.pop()
        self._original_data["nodes"] = self.nodes
        self._refresh_panels()
        self._update_undo_redo_state()

    def _on_redo(self):
        if not self._redo_stack:
            return
        self._sync_node_positions()
        self._undo_stack.append(copy.deepcopy(self.nodes))
        # Restore redo state
        self.nodes = self._redo_stack.pop()
        self._original_data["nodes"] = self.nodes
        self._refresh_panels()
        self._update_undo_redo_state()

    # -------------------------------------------------
    # Select All / Delete
    # -------------------------------------------------
    def _select_all_devices(self):
        """Select all devices on the canvas."""
        for item in self.editor.device_items.values():
            item.setSelected(True)

    def _delete_selected(self):
        """Remove selected devices from the canvas and data."""
        selected = self.editor.scene.selectedItems()
        if not selected:
            return
        self._sync_node_positions()
        self._push_undo()
        for item in selected:
            if hasattr(item, 'device_name'):
                dev_id = item.device_name
                self.nodes = [
                    n for n in self.nodes if n.get('id') != dev_id
                ]
                self._original_data['nodes'] = self.nodes
                if dev_id in self.editor.device_items:
                    del self.editor.device_items[dev_id]
                self.editor.scene.removeItem(item)
        self.device_tree.load_devices(self.nodes)
        self._update_undo_redo_state()

    # -------------------------------------------------
    # Load / Save / Export
    # -------------------------------------------------
    def _on_load(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open Placement JSON", "", "JSON Files (*.json)"
        )
        if not file_path:
            return
        self._push_undo()
        self._current_file = file_path
        self._load_data(file_path)
        self._refresh_panels()
        self.setWindowTitle(f"Symbolic Layout Editor — {os.path.basename(file_path)}")

    def _on_save(self):
        """Save to the current file (overwrite)."""
        if not self._current_file:
            self._on_save_as()
            return
        self._write_json(self._current_file)

    def _on_save_as(self):
        """Save to a new file via dialog."""
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Layout As", "", "JSON Files (*.json)"
        )
        if not file_path:
            return
        self._current_file = file_path
        self._write_json(file_path)
        self.setWindowTitle(f"Symbolic Layout Editor — {os.path.basename(file_path)}")

    def _on_export(self):
        """Export layout as a pretty-printed JSON."""
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Layout JSON", "", "JSON Files (*.json)"
        )
        if not file_path:
            return
        self._write_json(file_path)

    def _write_json(self, file_path):
        """Write the current layout to a JSON file."""
        output = self._build_output_data()
        with open(file_path, "w") as f:
            json.dump(output, f, indent=4)
        self.chat_panel._append_message(
            "AI",
            f"Layout saved to {os.path.basename(file_path)}",
            "#e8f4fd",
            "#1a1a2e",
        )

    # -------------------------------------------------
    # AI command execution
    # -------------------------------------------------
    def _handle_ai_command(self, cmd):
        """Execute a command dict from the AI on the canvas."""
        action = cmd.get("action")
        self._push_undo()
        try:
            if action == "swap":
                id_a = cmd["device_a"]
                id_b = cmd["device_b"]
                ok = self.editor.swap_devices(id_a, id_b)
                if ok:
                    self._sync_node_positions()
            elif action == "move":
                dev_id = cmd["device"]
                x, y = cmd["x"], cmd["y"]
                ok = self.editor.move_device(dev_id, x, y)
                if ok:
                    self._sync_node_positions()
        except (KeyError, TypeError) as e:
            self.chat_panel._append_message(
                "AI", f"Could not execute command: {e}", "#fde8e8", "#a00"
            )

    def _sync_node_positions(self):
        """Sync canvas positions back to self.nodes and update layout context."""
        positions = self.editor.get_updated_positions()
        for node in self.nodes:
            dev_id = node.get("id")
            if dev_id in positions:
                node["geometry"]["x"] = positions[dev_id][0]
                node["geometry"]["y"] = positions[dev_id][1]
        # Refresh the chat panel's context with updated positions
        self.chat_panel.set_layout_context(
            self.nodes, self._original_data.get("edges")
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