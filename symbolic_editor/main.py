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
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsSimpleTextItem,
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
    QSpinBox,
)
from PySide6.QtCore import Qt, Signal, QObject, QTimer, QPointF, QLineF
from PySide6.QtGui import QPainter, QPen, QPainterPath, QColor, QFont, QIcon, QAction, QKeySequence, QBrush

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
            # Build a compact summary instead of dumping full JSON
            nodes = self._layout_context.get("nodes", [])
            edges = self._layout_context.get("edges", [])
            dev_lines = []
            for n in nodes:
                pos = n.get("position", {})
                dev_lines.append(
                    f"  {n.get('id','?')} ({n.get('type','?')}) "
                    f"at ({pos.get('x',0):.1f}, {pos.get('y',0):.1f})"
                )
            nets = sorted({e.get("net", "") for e in edges if e.get("net")})
            prompt += f"\nDevices ({len(nodes)}):\n" + "\n".join(dev_lines) + "\n"
            if nets:
                prompt += f"Nets: {', '.join(nets)}\n"
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
        """Worker that runs in a background thread.
        Cascading fallback: tries multiple models per provider.
        """
        system_prompt = self._build_system_prompt()

        # Build multi-turn context
        history_text = ""
        for msg in self._chat_history[-4:]:
            role_label = "User" if msg["role"] == "user" else "Assistant"
            history_text += f"{role_label}: {msg['content']}\n"

        full_prompt = f"{system_prompt}\n\nConversation history:\n{history_text}"

        # Build chat messages for OpenAI-compatible APIs
        chat_messages = [{"role": "system", "content": system_prompt}]
        for msg in self._chat_history[-4:]:
            chat_messages.append(msg)

        errors = []

        # ============================================
        # Priority 1: Ollama (local, instant, no rate limits)
        # ============================================
        try:
            import requests
            resp = requests.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": "llama3.2",
                    "prompt": full_prompt,
                    "stream": False,
                },
                timeout=120,
            )
            resp.raise_for_status()
            result = resp.json()
            reply = result.get("response", "")
            if reply:
                print("[LLM] ✓ Ollama/llama3.2 responded")
                self.llm_signals.response_ready.emit(reply.strip())
                return
        except Exception as e:
            errors.append(f"Ollama: {e}")
            print(f"[LLM] ✗ Ollama: {e}")

        # ============================================
        # Priority 2: Gemini (multiple models)
        # ============================================
        gemini_models = ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-flash-8b"]
        for model_name in gemini_models:
            try:
                from google import genai
                from google.genai import types as genai_types
                client = genai.Client(api_key="AIzaSyApwhWPssGbI6L5siyrfn24AYQWe52NW2E")
                response = client.models.generate_content(
                    model=model_name,
                    contents=full_prompt,
                    config=genai_types.GenerateContentConfig(
                        max_output_tokens=256,
                        temperature=0.3,
                    ),
                )
                if response and response.text:
                    print(f"[LLM] ✓ Gemini/{model_name} responded")
                    self.llm_signals.response_ready.emit(response.text.strip())
                    return
            except Exception as e:
                errors.append(f"Gemini/{model_name}: {e}")
                print(f"[LLM] ✗ Gemini/{model_name}: {e}")

        # ============================================
        # Priority 3: OpenAI (multiple models)
        # ============================================
        openai_models = ["gpt-4o-mini", "gpt-3.5-turbo"]
        for model_name in openai_models:
            try:
                from openai import OpenAI
                client = OpenAI(
                    api_key=os.environ.get("OPENAI_API_KEY")
                )
                response = client.chat.completions.create(
                    model=model_name,
                    messages=chat_messages,
                    temperature=0.3,
                    max_tokens=256,
                )
                reply = response.choices[0].message.content
                if reply:
                    print(f"[LLM] ✓ OpenAI/{model_name} responded")
                    self.llm_signals.response_ready.emit(reply.strip())
                    return
            except Exception as e:
                errors.append(f"OpenAI/{model_name}: {e}")
                print(f"[LLM] ✗ OpenAI/{model_name}: {e}")

        # ============================================
        # Priority 4: DeepSeek
        # ============================================
        try:
            from openai import OpenAI
            client = OpenAI(
                api_key=os.environ.get("DEEPSEEK_API_KEY"),
                base_url="https://api.deepseek.com",
            )
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=chat_messages,
                temperature=0.3,
                max_tokens=256,
            )
            reply = response.choices[0].message.content
            if reply:
                print("[LLM] ✓ DeepSeek/deepseek-chat responded")
                self.llm_signals.response_ready.emit(reply.strip())
                return
        except Exception as e:
            errors.append(f"DeepSeek: {e}")
            print(f"[LLM] ✗ DeepSeek: {e}")

        # All backends exhausted
        self.llm_signals.error_occurred.emit(
            "All AI models exhausted. Please wait a minute and try again."
        )

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
    """Left panel showing devices, hierarchy, and terminal connectivity."""

    device_selected = Signal(str)
    connection_selected = Signal(str, str, str)  # (dev_id, net_name, other_dev_id)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._terminal_nets = {}  # dev_id -> {"D": net, "G": net, "S": net}
        self._edges = []
        self._conn_map = {}  # dev_id -> [(other_id, net), ...]
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header with gradient
        header = QFrame()
        header.setFixedHeight(44)
        header.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #1e2a3a, stop:1 #2d3f54);"
            "border-bottom: 1px solid #4a90d9;"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 12, 0)
        title = QLabel("📋 Device Hierarchy")
        title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        title.setStyleSheet("color: #e0e8f0;")
        header_layout.addWidget(title)
        layout.addWidget(header)

        # Tree widget
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(18)
        self.tree.setAnimated(True)
        self.tree.setStyleSheet(
            """
            QTreeWidget {
                background-color: #1a2332;
                border: none;
                color: #c8d6e5;
                font-family: 'Segoe UI', sans-serif;
                font-size: 12px;
                padding: 4px;
            }
            QTreeWidget::item {
                padding: 4px 6px;
                border-radius: 3px;
                margin: 1px 2px;
            }
            QTreeWidget::item:hover {
                background-color: #2d3f54;
            }
            QTreeWidget::item:selected {
                background-color: #3a6fa0;
                color: white;
            }
            QTreeWidget::branch {
                background-color: #1a2332;
            }
            QTreeWidget::branch:has-children:!has-siblings:closed,
            QTreeWidget::branch:closed:has-children:has-siblings {
                image: none;
                border-image: none;
            }
            QTreeWidget::branch:open:has-children:!has-siblings,
            QTreeWidget::branch:open:has-children:has-siblings {
                image: none;
                border-image: none;
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
        self.tree.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.tree)

    def set_edges(self, edges):
        """Store edge data and build connectivity lookup."""
        self._edges = edges or []
        self._conn_map.clear()
        for edge in self._edges:
            src = edge.get("source")
            tgt = edge.get("target")
            net = edge.get("net", "")
            if src and tgt:
                self._conn_map.setdefault(src, []).append((tgt, net))
                self._conn_map.setdefault(tgt, []).append((src, net))

    def set_terminal_nets(self, terminal_nets):
        """Store terminal-to-net mapping per device.
        terminal_nets: {dev_id: {'D': net, 'G': net, 'S': net}}
        """
        self._terminal_nets = terminal_nets or {}

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
            nmos_root = QTreeWidgetItem(
                self.tree, [f"⬜ NMOS Devices ({len(nmos_devices)})"]
            )
            nmos_root.setFont(0, QFont("Segoe UI", 11, QFont.Weight.Bold))
            nmos_root.setForeground(0, QColor("#7ec8e3"))
            for dev in nmos_devices:
                self._add_device_item(nmos_root, dev)
            nmos_root.setExpanded(True)

        # PMOS group
        if pmos_devices:
            pmos_root = QTreeWidgetItem(
                self.tree, [f"⬜ PMOS Devices ({len(pmos_devices)})"]
            )
            pmos_root.setFont(0, QFont("Segoe UI", 11, QFont.Weight.Bold))
            pmos_root.setForeground(0, QColor("#e87474"))
            for dev in pmos_devices:
                self._add_device_item(pmos_root, dev)
            pmos_root.setExpanded(True)

    def _add_device_item(self, parent, dev):
        """Add a device and its terminal connections as tree items."""
        dev_id = dev.get("id", "unknown")
        elec = dev.get("electrical", {})
        info = f"🔷 {dev_id}  (nf={elec.get('nf', 1)}, nfin={elec.get('nfin', '?')})"
        item = QTreeWidgetItem(parent, [info])
        item.setData(0, Qt.ItemDataRole.UserRole, dev_id)
        item.setFont(0, QFont("Segoe UI", 11))

        term_nets = self._terminal_nets.get(dev_id, {})
        connections = self._conn_map.get(dev_id, [])

        # Build net -> [connected devices] map
        net_to_devs = {}
        for other_id, net in connections:
            net_to_devs.setdefault(net, []).append(other_id)

        # Show each terminal with its net and connected devices
        for term_label, term_key, icon in [
            ("Gate", "G", "🟦"),
            ("Drain", "D", "🟩"),
            ("Source", "S", "🟨"),
        ]:
            net_name = term_nets.get(term_key, "?")
            connected = net_to_devs.get(net_name, [])
            if connected:
                devs_str = ", ".join(connected)
                text = f"{icon} {term_label} ({net_name}) → {devs_str}"
            else:
                text = f"{icon} {term_label} ({net_name})"

            sub = QTreeWidgetItem(item, [text])
            sub.setForeground(0, QColor("#8899aa"))
            sub.setFont(0, QFont("Segoe UI", 10))
            # Store data for click-to-highlight
            sub.setData(0, Qt.ItemDataRole.UserRole, None)  # not a device
            sub.setData(0, Qt.ItemDataRole.UserRole + 1, dev_id)
            sub.setData(0, Qt.ItemDataRole.UserRole + 2, net_name)

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
            # Device item clicked
            self.device_selected.emit(dev_id)
        else:
            # Connection sub-item clicked
            parent_dev = item.data(0, Qt.ItemDataRole.UserRole + 1)
            net_name = item.data(0, Qt.ItemDataRole.UserRole + 2)
            if parent_dev and net_name:
                self.device_selected.emit(parent_dev)
                self.connection_selected.emit(parent_dev, net_name, "")


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
        self.scene.changed.connect(self._on_scene_changed)

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

        # Connectivity data
        self._edges = []          # raw edge list from JSON
        self._conn_map = {}       # device_id -> [(other_id, net_name), ...]
        self._conn_lines = []     # active QGraphicsPathItem items
        self._terminal_nets = {}  # {dev_id: {'D': net, 'G': net, 'S': net}}

        # Net color palette
        self._net_colors = {
            '__palette': [
                QColor("#e74c3c"),  # red
                QColor("#3498db"),  # blue
                QColor("#2ecc71"),  # green
                QColor("#9b59b6"),  # purple
                QColor("#f39c12"),  # orange
                QColor("#1abc9c"),  # teal
                QColor("#e67e22"),  # dark orange
                QColor("#e84393"),  # pink
                QColor("#00cec9"),  # cyan
                QColor("#6c5ce7"),  # indigo
            ]
        }

        # Grid settings
        self._grid_size = 20   # base grid spacing in scene coords
        self._grid_color = QColor("#dce1e8")
        self._grid_color_major = QColor("#b8c0cc")
        self._snap_grid = self._grid_size
        self._row_pitch = self._grid_size * 3

        # Dummy placement mode
        self._dummy_mode = False
        self._dummy_preview = None
        self._dummy_place_callback = None

        self.setStyleSheet("border: none; background-color: #f0f2f5;")

    def set_dummy_mode(self, enabled):
        """Enable/disable click-to-place dummy mode."""
        self._dummy_mode = bool(enabled)
        if not self._dummy_mode:
            self._clear_dummy_preview()

    def set_dummy_place_callback(self, callback):
        """Callback called with candidate dict when user places a dummy."""
        self._dummy_place_callback = callback

    def _snap_value(self, value):
        return round(value / self._snap_grid) * self._snap_grid

    def _snap_row(self, value):
        return round(value / self._row_pitch) * self._row_pitch

    def _snap_point(self, x, y):
        return QPointF(self._snap_value(x), self._snap_row(y))

    def _on_scene_changed(self, _regions):
        """Keep occupancy guides fresh when devices move/add/remove."""
        self.resetCachedContent()

    def _compute_dummy_candidate(self, scene_pos):
        """Build a preview candidate aligned to nearest NMOS/PMOS row."""
        type_items = {"nmos": [], "pmos": []}
        for item in self.device_items.values():
            dev_type = str(getattr(item, "device_type", "")).strip().lower()
            if dev_type in type_items:
                type_items[dev_type].append(item)

        if not type_items["nmos"] and not type_items["pmos"]:
            return None

        rows = []
        for dev_type, items in type_items.items():
            if not items:
                continue
            avg_y = sum(it.pos().y() for it in items) / len(items)
            rows.append((dev_type, avg_y))

        target_type, target_y = min(rows, key=lambda r: abs(scene_pos.y() - r[1]))
        ref_item = type_items[target_type][0]
        width = ref_item.rect().width()
        height = ref_item.rect().height()
        x = self.find_nearest_free_x(
            row_y=target_y,
            width=width,
            target_x=self._snap_value(scene_pos.x()),
            exclude_id=None,
        )
        y = self._snap_row(target_y)
        return {
            "type": str(target_type).lower(),
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        }

    def _clear_dummy_preview(self):
        if self._dummy_preview is not None:
            try:
                if self._dummy_preview.scene() is self.scene:
                    self.scene.removeItem(self._dummy_preview)
            except RuntimeError:
                pass
            self._dummy_preview = None

    def _update_dummy_preview(self, scene_pos):
        candidate = self._compute_dummy_candidate(scene_pos)
        if not candidate:
            self._clear_dummy_preview()
            return

        if self._dummy_preview is None:
            self._dummy_preview = QGraphicsRectItem()
            self._dummy_preview.setZValue(1000)
            self.scene.addItem(self._dummy_preview)

        fill = QColor(255, 154, 210, 105)
        border = QColor("#d14d94")
        self._dummy_preview.setBrush(QBrush(fill))
        self._dummy_preview.setPen(QPen(border, 1.2, Qt.PenStyle.DashLine))
        self._dummy_preview.setRect(0, 0, candidate["width"], candidate["height"])
        self._dummy_preview.setPos(candidate["x"], candidate["y"])

    def _commit_dummy_at(self, scene_pos):
        if not self._dummy_place_callback:
            return False
        candidate = self._compute_dummy_candidate(scene_pos)
        if not candidate:
            return False
        self._dummy_place_callback(candidate)
        return True

    # -------------------------------------------------
    # Load AI JSON Placement
    # -------------------------------------------------
    def load_placement(self, nodes):
        """Load placement from a list of node dicts."""
        self._clear_dummy_preview()
        self.scene.clear()
        self.device_items.clear()

        self.scale_factor = 80  # visual scaling
        widths = []
        heights = []

        for node in nodes:
            geom = node.get("geometry", {})

            x = geom.get("x", 0) * self.scale_factor
            y = geom.get("y", 0) * self.scale_factor

            width = geom.get("width", 1) * self.scale_factor
            height = geom.get("height", 0.5) * self.scale_factor
            widths.append(width)
            heights.append(height)

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

        # Abutted rows horizontally + visible spacing between rows.
        if widths:
            min_w = min(widths)
            col_gap = 0.0
            self._snap_grid = max(1.0, min_w + col_gap)
        if heights:
            max_h = max(heights)
            row_gap = max(24.0, max_h * 0.55)
            self._row_pitch = max(1.0, max_h + row_gap)

        for item in self.device_items.values():
            item.set_snap_grid(self._snap_grid, self._row_pitch)
            item.setPos(self._snap_point(item.pos().x(), item.pos().y()))

        self._compact_rows_abutted()

        # Practically unlimited canvas.
        self.scene.setSceneRect(-1000000, -1000000, 2000000, 2000000)

    def get_updated_positions(self):
        """Return a dict mapping device id -> (x, y) in original coordinates."""
        positions = {}
        for dev_id, item in self.device_items.items():
            pos = item.pos()
            positions[dev_id] = (
                pos.x() / self.scale_factor,
                pos.y() / self.scale_factor,
            )
        return positions

    def _abut_pair_score(self, left_item, right_item):
        """Score how desirable it is to place left_item immediately before right_item."""
        left_nets = self._terminal_nets.get(left_item.device_name, {})
        right_nets = self._terminal_nets.get(right_item.device_name, {})
        if not left_nets or not right_nets:
            return 0

        score = 0

        def add_if_equal(term_a, term_b, weight):
            nonlocal score
            net_a = left_nets.get(term_a)
            net_b = right_nets.get(term_b)
            if net_a and net_b and net_a == net_b:
                score += weight

        # Strong preference for common drain/source sharing.
        add_if_equal("D", "D", 9)
        add_if_equal("S", "S", 7)
        add_if_equal("D", "S", 4)
        add_if_equal("S", "D", 4)
        # Gate commonality is weaker.
        add_if_equal("G", "G", 1)
        return score

    def _order_row_items(self, items):
        """Order row items so net-sharing neighbors (especially D-common) abut."""
        ordered_by_x = sorted(items, key=lambda it: it.pos().x())
        if len(ordered_by_x) <= 1 or not self._terminal_nets:
            return ordered_by_x

        with_nets = [
            it for it in ordered_by_x if self._terminal_nets.get(it.device_name)
        ]
        if len(with_nets) < 2:
            return ordered_by_x

        remaining = list(ordered_by_x)

        def total_score(candidate):
            return sum(
                self._abut_pair_score(candidate, other)
                + self._abut_pair_score(other, candidate)
                for other in remaining
                if other is not candidate
            )

        seed = max(
            remaining,
            key=lambda it: (total_score(it), -abs(it.pos().x())),
        )
        row = [seed]
        remaining.remove(seed)

        while remaining:
            left_anchor = row[0]
            right_anchor = row[-1]
            best_item = None
            best_side = None
            best_rank = None

            for cand in remaining:
                score_left = self._abut_pair_score(cand, left_anchor)
                score_right = self._abut_pair_score(right_anchor, cand)
                if score_left > score_right:
                    side = "left"
                    score = score_left
                    anchor = left_anchor
                else:
                    side = "right"
                    score = score_right
                    anchor = right_anchor

                # Prefer higher net score, then closer current position to anchor.
                rank = (score, -abs(cand.pos().x() - anchor.pos().x()))
                if best_rank is None or rank > best_rank:
                    best_rank = rank
                    best_item = cand
                    best_side = side

            if best_side == "left":
                row.insert(0, best_item)
            else:
                row.append(best_item)
            remaining.remove(best_item)

        # If no useful net signal exists, keep geometric order.
        adjacency_gain = sum(
            self._abut_pair_score(row[i], row[i + 1])
            for i in range(len(row) - 1)
        )
        if adjacency_gain <= 0:
            return ordered_by_x
        return row

    def _compact_rows_abutted(self, row_keys=None):
        """Pack row devices edge-to-edge to emulate abutted placement rows."""
        rows = {}
        for item in self.device_items.values():
            row_y = self._snap_row(item.pos().y())
            key = (getattr(item, "device_type", ""), row_y)
            if row_keys is not None and key not in row_keys:
                continue
            rows.setdefault(key, []).append(item)

        for (_, row_y), items in rows.items():
            if not items:
                continue
            ordered = self._order_row_items(items)
            x_cursor = self._snap_value(min(it.pos().x() for it in ordered))
            for it in ordered:
                it.setPos(x_cursor, row_y)
                span = max(1, int(math.ceil(it.rect().width() / self._snap_grid)))
                x_cursor += span * self._snap_grid

    def swap_devices(self, id_a, id_b):
        """Swap the positions of two devices on the canvas."""
        item_a = self.device_items.get(id_a)
        item_b = self.device_items.get(id_b)
        if item_a and item_b:
            pos_a = item_a.pos()
            pos_b = item_b.pos()
            item_a.setPos(self._snap_point(pos_b.x(), pos_b.y()))
            item_b.setPos(self._snap_point(pos_a.x(), pos_a.y()))
            self._compact_rows_abutted()
            return True
        return False

    def move_device(self, dev_id, x, y):
        """Move a device to an absolute position (in layout coordinates)."""
        item = self.device_items.get(dev_id)
        if item:
            old_row_key = (getattr(item, "device_type", ""), self._snap_row(item.pos().y()))
            pt = self._snap_point(x * self.scale_factor, y * self.scale_factor)
            free_x = self.find_nearest_free_x(
                row_y=pt.y(),
                width=item.rect().width(),
                target_x=pt.x(),
                exclude_id=dev_id,
            )
            item.setPos(free_x, pt.y())
            new_row_key = (getattr(item, "device_type", ""), self._snap_row(pt.y()))
            self._compact_rows_abutted({old_row_key, new_row_key})
            return True
        return False

    def move_device_to_grid(self, dev_id, row, col):
        """Move one device to explicit grid row/col indices."""
        item = self.device_items.get(dev_id)
        if not item:
            return False
        old_row_key = (getattr(item, "device_type", ""), self._snap_row(item.pos().y()))
        x = col * self._snap_grid
        y = row * self._row_pitch
        pt = self._snap_point(x, y)
        free_x = self.find_nearest_free_x(
            row_y=pt.y(),
            width=item.rect().width(),
            target_x=pt.x(),
            exclude_id=dev_id,
        )
        item.setPos(free_x, pt.y())
        new_row_key = (getattr(item, "device_type", ""), self._snap_row(pt.y()))
        self._compact_rows_abutted({old_row_key, new_row_key})
        return True

    def find_nearest_free_x(self, row_y, width, target_x, exclude_id=None):
        """Return nearest free x-slot on the target row without moving other devices."""
        row_y = self._snap_row(row_y)
        span = max(1, int(math.ceil(width / self._snap_grid)))
        desired = int(round(self._snap_value(target_x) / self._snap_grid))

        intervals = []
        for dev_id, item in self.device_items.items():
            if exclude_id and dev_id == exclude_id:
                continue
            if self._snap_row(item.pos().y()) != row_y:
                continue
            start = int(round(self._snap_value(item.pos().x()) / self._snap_grid))
            other_span = max(1, int(math.ceil(item.rect().width() / self._snap_grid)))
            intervals.append((start, start + other_span - 1))

        def free(start_slot):
            end_slot = start_slot + span - 1
            for s, e in intervals:
                if not (end_slot < s or start_slot > e):
                    return False
            return True

        dist = 0
        while True:
            candidates = [desired] if dist == 0 else [desired - dist, desired + dist]
            for c in candidates:
                if free(c):
                    return c * self._snap_grid
            dist += 1

    def ensure_grid_extent(self, row_count, col_count):
        """Ensure scene rect is large enough for requested row/col counts."""
        rect = self.scene.sceneRect()
        margin = 120
        min_right = max(rect.right(), (max(col_count, 1) + 1) * self._snap_grid + margin)
        min_bottom = max(rect.bottom(), (max(row_count, 1) + 1) * self._row_pitch + margin)
        self.scene.setSceneRect(rect.left(), rect.top(), min_right - rect.left(), min_bottom - rect.top())

    def get_row_col(self, dev_id):
        item = self.device_items.get(dev_id)
        if not item:
            return None
        row = int(round(item.pos().y() / self._row_pitch))
        col = int(round(item.pos().x() / self._snap_grid))
        return row, col

    def selected_device_ids(self):
        ids = []
        try:
            for it in self.scene.selectedItems():
                if hasattr(it, "device_name"):
                    ids.append(it.device_name)
        except RuntimeError:
            return []
        return ids

    def flip_devices_h(self, dev_ids):
        for dev_id in dev_ids:
            item = self.device_items.get(dev_id)
            if item and hasattr(item, "flip_horizontal"):
                item.flip_horizontal()

    def flip_devices_v(self, dev_ids):
        for dev_id in dev_ids:
            item = self.device_items.get(dev_id)
            if item and hasattr(item, "flip_vertical"):
                item.flip_vertical()

    def _interval_overlap(self, a_start, a_end, b_start, b_end):
        return not (a_end < b_start or b_end < a_start)

    def _item_slot_span(self, item):
        start = int(round(self._snap_value(item.pos().x()) / self._snap_grid))
        span = max(1, int(math.ceil(item.rect().width() / self._snap_grid)))
        return start, start + span - 1, span

    def resolve_overlaps(self, anchor_ids=None):
        """Resolve overlaps locally around anchors so unaffected devices stay put."""
        anchors = set(anchor_ids or [])
        rows = {}
        for item in self.device_items.values():
            row_y = self._snap_row(item.pos().y())
            key = (getattr(item, "device_type", ""), row_y)
            rows.setdefault(key, []).append(item)

        for (_, row_y), items in rows.items():
            if not items:
                continue

            if anchors:
                row_anchors = [it for it in items if it.device_name in anchors]
                if not row_anchors:
                    continue
            else:
                row_anchors = sorted(items, key=lambda it: it.device_name)

            queue = list(row_anchors)
            seen = set()
            while queue:
                current = queue.pop(0)
                cur_start, cur_end, _ = self._item_slot_span(current)
                cur_x = current.pos().x()
                for other in items:
                    if other is current:
                        continue
                    oth_start, oth_end, oth_span = self._item_slot_span(other)
                    if not self._interval_overlap(cur_start, cur_end, oth_start, oth_end):
                        continue

                    # Push overlapped neighbors away from the collision side.
                    if other.pos().x() >= cur_x:
                        new_start = cur_end + 1
                    else:
                        new_start = cur_start - oth_span

                    target_x = new_start * self._snap_grid
                    if abs(other.pos().x() - target_x) > 1e-6:
                        other.setPos(target_x, row_y)
                        if other not in seen:
                            queue.append(other)
                seen.add(current)
        self._compact_rows_abutted()

    def set_edges(self, edges):
        """Store edge data and build connectivity lookup."""
        self._edges = edges or []
        self._conn_map.clear()
        for edge in self._edges:
            src = edge.get("source")
            tgt = edge.get("target")
            net = edge.get("net", "")
            if src and tgt:
                self._conn_map.setdefault(src, []).append((tgt, net))
                self._conn_map.setdefault(tgt, []).append((src, net))

    def set_terminal_nets(self, terminal_nets):
        """Store terminal-net mapping: {dev_id: {'D': net, 'G': net, 'S': net}}"""
        self._terminal_nets = terminal_nets or {}
        # Re-pack with net-aware adjacency as soon as terminal nets are available.
        if self.device_items:
            self._compact_rows_abutted()
            self.resetCachedContent()

    def _get_terminal_for_net(self, dev_id, net_name):
        """Return which terminal ('S','G','D') of dev_id connects to net_name."""
        term_map = self._terminal_nets.get(dev_id, {})
        for term, net in term_map.items():
            if net == net_name:
                return term
        return "G"  # fallback

    def _get_net_color(self, net_name):
        """Return a consistent color for a given net name."""
        if net_name not in self._net_colors:
            palette = self._net_colors['__palette']
            idx = (len(self._net_colors) - 1) % len(palette)
            self._net_colors[net_name] = palette[idx]
        return self._net_colors[net_name]

    def _clear_connections(self):
        """Remove all connection lines and labels from the scene."""
        if self._conn_lines:
            self.scene.blockSignals(True)
            for item in self._conn_lines:
                self.scene.removeItem(item)
            self._conn_lines.clear()
            self.scene.blockSignals(False)

    def _show_connections(self, dev_id):
        """Draw curved lines from dev_id terminals to connected device terminals."""
        self._clear_connections()
        connections = self._conn_map.get(dev_id, [])
        if not connections:
            return

        src_item = self.device_items.get(dev_id)
        if not src_item:
            return

        src_anchors = src_item.terminal_anchors()

        self.scene.blockSignals(True)
        for i, (other_id, net_name) in enumerate(connections):
            tgt_item = self.device_items.get(other_id)
            if not tgt_item:
                continue

            tgt_anchors = tgt_item.terminal_anchors()
            color = self._get_net_color(net_name)

            # Look up correct terminals from SPICE data
            src_term = self._get_terminal_for_net(dev_id, net_name)
            tgt_term = self._get_terminal_for_net(other_id, net_name)
            p1 = src_anchors[src_term]
            p2 = tgt_anchors[tgt_term]

            # Build a curved bezier path
            path = QPainterPath()
            path.moveTo(p1)
            dx = p2.x() - p1.x()
            dy = p2.y() - p1.y()
            offset = max(abs(dx), abs(dy)) * 0.3
            sign = 1.0 if i % 2 == 0 else -1.0
            if abs(dx) > abs(dy):
                ctrl1 = QPointF(p1.x() + dx * 0.33, p1.y() + sign * offset)
                ctrl2 = QPointF(p1.x() + dx * 0.66, p2.y() + sign * offset)
            else:
                ctrl1 = QPointF(p1.x() + sign * offset, p1.y() + dy * 0.33)
                ctrl2 = QPointF(p2.x() + sign * offset, p1.y() + dy * 0.66)
            path.cubicTo(ctrl1, ctrl2, p2)

            path_item = QGraphicsPathItem(path)
            pen = QPen(color, 0.5, Qt.PenStyle.DashLine)
            path_item.setPen(pen)
            path_item.setZValue(10)
            path_item.setFlag(QGraphicsPathItem.GraphicsItemFlag.ItemIsSelectable, False)
            self.scene.addItem(path_item)
            self._conn_lines.append(path_item)
        self.scene.blockSignals(False)

    def _show_net_connections(self, dev_id, net_name):
        """Highlight only connections for a specific net from a device."""
        self._clear_connections()
        connections = [(oid, n) for oid, n in self._conn_map.get(dev_id, [])
                       if n == net_name]
        if not connections:
            return

        src_item = self.device_items.get(dev_id)
        if not src_item:
            return

        src_anchors = src_item.terminal_anchors()
        src_term = self._get_terminal_for_net(dev_id, net_name)
        color = self._get_net_color(net_name)

        self.scene.blockSignals(True)
        for i, (other_id, _) in enumerate(connections):
            tgt_item = self.device_items.get(other_id)
            if not tgt_item:
                continue

            tgt_anchors = tgt_item.terminal_anchors()
            tgt_term = self._get_terminal_for_net(other_id, net_name)
            p1 = src_anchors[src_term]
            p2 = tgt_anchors[tgt_term]

            path = QPainterPath()
            path.moveTo(p1)
            dx = p2.x() - p1.x()
            dy = p2.y() - p1.y()
            offset = max(abs(dx), abs(dy)) * 0.25
            sign = 1.0 if i % 2 == 0 else -1.0
            if abs(dx) > abs(dy):
                ctrl1 = QPointF(p1.x() + dx * 0.33, p1.y() + sign * offset)
                ctrl2 = QPointF(p1.x() + dx * 0.66, p2.y() + sign * offset)
            else:
                ctrl1 = QPointF(p1.x() + sign * offset, p1.y() + dy * 0.33)
                ctrl2 = QPointF(p2.x() + sign * offset, p1.y() + dy * 0.66)
            path.cubicTo(ctrl1, ctrl2, p2)

            path_item = QGraphicsPathItem(path)
            pen = QPen(color, 0.5, Qt.PenStyle.DashLine)
            path_item.setPen(pen)
            path_item.setZValue(10)
            path_item.setFlag(QGraphicsPathItem.GraphicsItemFlag.ItemIsSelectable, False)
            self.scene.addItem(path_item)
            self._conn_lines.append(path_item)
        self.scene.blockSignals(False)

    def _on_selection_changed(self):
        """Emit device_clicked when user selects a device on the canvas."""
        try:
            selected = [s for s in self.scene.selectedItems()
                        if hasattr(s, 'device_name')]
        except RuntimeError:
            return  # scene deleted during shutdown
        if selected:
            dev_id = selected[0].device_name
            self.device_clicked.emit(dev_id)
            self._show_connections(dev_id)
        else:
            self._clear_connections()

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
        """Draw occupied row tracks only (abut style)."""
        super().drawBackground(painter, rect)

        if not self.device_items:
            return

        rows = {}
        for it in self.device_items.values():
            row_y = self._snap_row(it.pos().y())
            rows.setdefault(row_y, []).append(it)

        track_fill = QBrush(QColor("#f7f9fc"))
        track_pen = QPen(QColor("#c5ccd8"), 1.0)
        frame_pen = QPen(QColor("#b5bcc8"), 1.1)

        outer_left = None
        outer_top = None
        outer_right = None
        outer_bottom = None

        for row_y in sorted(rows.keys()):
            items = rows[row_y]
            min_x = min(it.pos().x() for it in items)
            max_x = max(it.pos().x() + it.rect().width() for it in items)
            row_h = max(it.rect().height() for it in items)

            band_x = min_x - 8.0
            band_y = row_y - 6.0
            band_w = (max_x - min_x) + 16.0
            band_h = row_h + 12.0

            if outer_left is None:
                outer_left = band_x
                outer_top = band_y
                outer_right = band_x + band_w
                outer_bottom = band_y + band_h
            else:
                outer_left = min(outer_left, band_x)
                outer_top = min(outer_top, band_y)
                outer_right = max(outer_right, band_x + band_w)
                outer_bottom = max(outer_bottom, band_y + band_h)

            if (
                band_x > rect.right()
                or band_x + band_w < rect.left()
                or band_y > rect.bottom()
                or band_y + band_h < rect.top()
            ):
                continue

            painter.setPen(track_pen)
            painter.setBrush(track_fill)
            painter.drawRoundedRect(band_x, band_y, band_w, band_h, 1.5, 1.5)

        if outer_left is not None:
            painter.setPen(frame_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(
                outer_left - 8.0,
                outer_top - 8.0,
                (outer_right - outer_left) + 16.0,
                (outer_bottom - outer_top) + 16.0,
            )

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
        if self._dummy_mode and event.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.mapToScene(event.pos())
            if self._commit_dummy_at(scene_pos):
                self._update_dummy_preview(scene_pos)
                return
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

    def mouseMoveEvent(self, event):
        if self._dummy_mode:
            self._update_dummy_preview(self.mapToScene(event.pos()))
        super().mouseMoveEvent(event)


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
        self._terminal_nets = {}  # {dev_id: {'D': net, 'G': net, 'S': net}}
        self._rows_virtual_min = 0
        self._cols_virtual_min = 0
        self._ignore_grid_spin_change = False

        # Load placement data
        self._load_data(placement_file)

        # --- Create panels ---
        self.device_tree = DeviceTreePanel()
        self.editor = SymbolicEditor()
        self.chat_panel = ChatPanel()

        # --- Toolbar ---
        self._create_menu_bar()
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

        # Connect tree connection click to canvas net highlight
        self.device_tree.connection_selected.connect(self._on_connection_selected)

        # Connect canvas selection to tree highlight
        self.editor.device_clicked.connect(self.device_tree.highlight_device)
        self.editor.device_clicked.connect(self._on_canvas_device_clicked)
        self.editor.scene.selectionChanged.connect(self._on_selection_count_changed)

        # Connect AI command execution
        self.chat_panel.command_requested.connect(self._handle_ai_command)
        self.editor.set_dummy_place_callback(self._add_dummy_device)

    # -------------------------------------------------
    # Menu Bar
    # -------------------------------------------------
    def _create_menu_bar(self):
        mb = self.menuBar()
        mb.setStyleSheet(
            """
            QMenuBar {
                background: #ececec;
                color: #222;
                border-bottom: 1px solid #c8c8c8;
                padding: 1px 4px;
                font-family: 'Segoe UI';
                font-size: 9pt;
            }
            QMenuBar::item {
                background: transparent;
                padding: 3px 8px;
            }
            QMenuBar::item:selected {
                background: #d8d8d8;
            }
            QMenu {
                background: #f4f4f4;
                border: 1px solid #bdbdbd;
                font-family: 'Segoe UI';
                font-size: 9pt;
            }
            QMenu::item:selected {
                background: #d8e7ff;
            }
            """
        )

        file_menu = mb.addMenu("File")
        self._act_file_load = QAction("Load", self)
        self._act_file_load.setShortcut(QKeySequence("Ctrl+O"))
        self._act_file_load.triggered.connect(self._on_load)
        file_menu.addAction(self._act_file_load)

        self._act_file_save = QAction("Save", self)
        self._act_file_save.setShortcut(QKeySequence("Ctrl+S"))
        self._act_file_save.triggered.connect(self._on_save)
        file_menu.addAction(self._act_file_save)

        self._act_file_save_as = QAction("Save As", self)
        self._act_file_save_as.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self._act_file_save_as.triggered.connect(self._on_save_as)
        file_menu.addAction(self._act_file_save_as)

        self._act_file_export = QAction("Export", self)
        self._act_file_export.setShortcut(QKeySequence("Ctrl+E"))
        self._act_file_export.triggered.connect(self._on_export)
        file_menu.addAction(self._act_file_export)

        for name in ["Design", "View", "Edit", "Options", "Window", "Help"]:
            menu = mb.addMenu(name)
            a = QAction(f"{name} Placeholder", self)
            a.setEnabled(False)
            menu.addAction(a)

    # -------------------------------------------------
    # Toolbar
    # -------------------------------------------------
    def _create_toolbar(self):
        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(toolbar.iconSize())
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        toolbar.setStyleSheet(
            """
            QToolBar {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #1e2a3a, stop:1 #2d3f54);
                border-top: 1px solid #4a90d9;
                border-bottom: 1px solid #4a90d9;
                spacing: 3px;
                padding: 3px 6px;
            }
            QToolButton {
                color: #e0e8f0;
                background: #253445;
                border: 1px solid #3d5066;
                border-radius: 2px;
                padding: 1px 4px;
                font-family: 'Segoe UI';
                font-size: 9px;
                min-width: 18px;
            }
            QToolButton:hover {
                background-color: #2d3f54;
                border-color: #4a90d9;
            }
            QToolButton:pressed {
                background-color: #4a90d9;
            }
            QToolButton:checked {
                background-color: #4a90d9;
                border-color: #9cc7f0;
                color: #ffffff;
                font-weight: 600;
            }
            QToolButton:disabled {
                color: #7b8a9c;
            }
            QSpinBox {
                font-family: 'Segoe UI';
                font-size: 9px;
                padding: 0px 2px;
                min-height: 18px;
                background-color: #253445;
                color: #e0e8f0;
                border: 1px solid #3d5066;
                border-radius: 2px;
                selection-background-color: #4a90d9;
            }
            QSpinBox:focus {
                border: 1px solid #9cc7f0;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 14px;
                background: #2d3f54;
                border-left: 1px solid #3d5066;
            }
            """
        )
        self.addToolBar(toolbar)

        # File operations are in the File menu.
        toolbar.addSeparator()

        # Undo
        self._act_undo = QAction("↩", self)
        self._act_undo.setShortcuts([QKeySequence("Ctrl+Z")])
        self._act_undo.setToolTip("Undo last action (Ctrl+Z)")
        self._act_undo.setEnabled(False)
        self._act_undo.triggered.connect(self._on_undo)
        toolbar.addAction(self._act_undo)

        # Redo (Ctrl+Y and Ctrl+Shift+Z)
        self._act_redo = QAction("↪", self)
        self._act_redo.setShortcuts(
            [QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")]
        )
        self._act_redo.setToolTip("Redo last undone action (Ctrl+Y / Ctrl+Shift+Z)")
        self._act_redo.setEnabled(False)
        self._act_redo.triggered.connect(self._on_redo)
        toolbar.addAction(self._act_redo)

        toolbar.addSeparator()

        # Fit to View
        act_fit = QAction("▢", self)
        act_fit.setShortcut(QKeySequence("F"))
        act_fit.setToolTip("Fit all devices in view (F)")
        act_fit.triggered.connect(self.editor.fit_to_view)
        toolbar.addAction(act_fit)

        toolbar.addSeparator()

        # Zoom In
        act_zoom_in = QAction("＋", self)
        act_zoom_in.setShortcut(QKeySequence("Ctrl+="))
        act_zoom_in.setToolTip("Zoom in (Ctrl++)")
        act_zoom_in.triggered.connect(self.editor.zoom_in)
        toolbar.addAction(act_zoom_in)

        # Zoom Out
        act_zoom_out = QAction("－", self)
        act_zoom_out.setShortcut(QKeySequence("Ctrl+-"))
        act_zoom_out.setToolTip("Zoom out (Ctrl+-)")
        act_zoom_out.triggered.connect(self.editor.zoom_out)
        toolbar.addAction(act_zoom_out)

        # Zoom Reset
        act_zoom_reset = QAction("◯", self)
        act_zoom_reset.setShortcut(QKeySequence("Ctrl+0"))
        act_zoom_reset.setToolTip("Reset zoom (Ctrl+0)")
        act_zoom_reset.triggered.connect(self.editor.zoom_reset)
        toolbar.addAction(act_zoom_reset)

        toolbar.addSeparator()

        # Select All
        act_select_all = QAction("☐", self)
        act_select_all.setShortcut(QKeySequence("Ctrl+A"))
        act_select_all.setToolTip("Select all devices (Ctrl+A)")
        act_select_all.triggered.connect(self._select_all_devices)
        toolbar.addAction(act_select_all)

        # Delete
        act_delete = QAction("✖", self)
        act_delete.setShortcut(QKeySequence("Delete"))
        act_delete.setToolTip("Delete selected devices (Delete)")
        act_delete.triggered.connect(self._delete_selected)
        toolbar.addAction(act_delete)

        # Swap selected (need exactly 2)
        act_swap = QAction("⇄", self)
        act_swap.setShortcut(QKeySequence("Ctrl+W"))
        act_swap.setToolTip("Swap 2 selected devices (Ctrl+W)")
        act_swap.triggered.connect(self._swap_selected_devices)
        toolbar.addAction(act_swap)

        # Flip selected
        act_flip_h = QAction("⇋", self)
        act_flip_h.setShortcut(QKeySequence("H"))
        act_flip_h.setToolTip("Flip selected devices horizontally (H)")
        act_flip_h.triggered.connect(self._flip_selected_h)
        toolbar.addAction(act_flip_h)

        act_flip_v = QAction("⇅", self)
        act_flip_v.setShortcut(QKeySequence("V"))
        act_flip_v.setToolTip("Flip selected devices vertically (V)")
        act_flip_v.triggered.connect(self._flip_selected_v)
        toolbar.addAction(act_flip_v)

        # Merge helpers
        act_merge_ss = QAction("SS", self)
        act_merge_ss.setShortcut(QKeySequence("M"))
        act_merge_ss.setToolTip("Merge 2 devices by S-S (M)")
        act_merge_ss.triggered.connect(self._merge_selected_ss)
        toolbar.addAction(act_merge_ss)

        act_merge_dd = QAction("DD", self)
        act_merge_dd.setShortcut(QKeySequence("Shift+M"))
        act_merge_dd.setToolTip("Merge 2 devices by D-D (Shift+M)")
        act_merge_dd.triggered.connect(self._merge_selected_dd)
        toolbar.addAction(act_merge_dd)

        toolbar.addSeparator()

        self._sel_label = QLabel("Sel: 0", self)
        self._sel_label.setStyleSheet("color: #d0d9e5; font-size: 10px;")
        toolbar.addWidget(self._sel_label)

        toolbar.addSeparator()

        # Row / Col controls (counts + growth targets)
        self._row_spin = QSpinBox(self)
        self._row_spin.setRange(0, 9999)
        self._row_spin.setPrefix("Row ")
        self._row_spin.setFixedWidth(96)
        self._row_spin.valueChanged.connect(self._on_row_target_changed)
        toolbar.addWidget(self._row_spin)

        self._col_spin = QSpinBox(self)
        self._col_spin.setRange(0, 9999)
        self._col_spin.setPrefix("Col ")
        self._col_spin.setFixedWidth(96)
        self._col_spin.valueChanged.connect(self._on_col_target_changed)
        toolbar.addWidget(self._col_spin)

        toolbar.addSeparator()

        # Add Dummy mode
        self._act_add_dummy = QAction("D", self)
        self._act_add_dummy.setCheckable(True)
        self._act_add_dummy.setShortcut(QKeySequence("D"))
        self._act_add_dummy.setToolTip(
            "Toggle dummy placement mode (D). Hover a PMOS/NMOS row and click to place."
        )
        self._act_add_dummy.toggled.connect(self._on_toggle_add_dummy)
        toolbar.addAction(self._act_add_dummy)

    def keyPressEvent(self, event):
        """Esc releases active modes and selection."""
        if event.key() == Qt.Key.Key_Escape:
            released = False
            if hasattr(self, "_act_add_dummy") and self._act_add_dummy.isChecked():
                self._act_add_dummy.setChecked(False)
                released = True
            try:
                if self.editor and self.editor.scene.selectedItems():
                    self.editor.scene.clearSelection()
                    self._on_selection_count_changed()
                    released = True
            except RuntimeError:
                pass
            if released:
                event.accept()
                return
        super().keyPressEvent(event)

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
        # Try to find and parse matching SPICE file for terminal nets
        self._terminal_nets = self._parse_spice_terminals(filepath)

    @staticmethod
    def _parse_spice_terminals(json_path):
        """Parse .sp files in the same directory to extract terminal-net mapping.
        MOSFET format: name drain gate source bulk model ...
        Returns: {dev_id: {'D': net, 'G': net, 'S': net}}
        """
        terminal_nets = {}
        sp_dir = os.path.dirname(json_path)
        sp_files = [f for f in os.listdir(sp_dir) if f.endswith('.sp')]
        for sp_file in sp_files:
            try:
                with open(os.path.join(sp_dir, sp_file)) as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('*') or line.startswith('.'):
                            continue
                        tokens = line.split()
                        if len(tokens) >= 5 and tokens[0].startswith('M'):
                            dev_name = tokens[0]
                            terminal_nets[dev_name] = {
                                'D': tokens[1],
                                'G': tokens[2],
                                'S': tokens[3],
                            }
            except Exception:
                pass
        return terminal_nets

    def _refresh_panels(self):
        """Refresh all panels from self.nodes."""
        edges = self._original_data.get("edges")
        self.device_tree.set_edges(edges)
        self.device_tree.set_terminal_nets(self._terminal_nets)
        self.device_tree.load_devices(self.nodes)
        self.editor.load_placement(self.nodes)
        self.editor.set_edges(edges)
        self.editor.set_terminal_nets(self._terminal_nets)
        self.chat_panel.set_layout_context(
            self.nodes, self._original_data.get("edges")
        )
        # Wire up drag signals on each device for undo tracking
        for item in self.editor.device_items.values():
            item.signals.drag_started.connect(self._on_device_drag_start)
            item.signals.drag_finished.connect(self._on_device_drag_end)
        self._update_grid_counts()
        self._on_selection_count_changed()

    def _on_connection_selected(self, dev_id, net_name, _other):
        """When a connection sub-item is clicked in the tree, highlight that net."""
        self.editor.highlight_device(dev_id)
        self.editor._show_net_connections(dev_id, net_name)
        self._update_row_col_for_device(dev_id)

    def _on_canvas_device_clicked(self, dev_id):
        self._update_row_col_for_device(dev_id)

    def _update_row_col_for_device(self, dev_id):
        if not hasattr(self, "_row_spin") or not hasattr(self, "_col_spin"):
            return
        self._update_grid_counts()

    def _on_selection_count_changed(self):
        if not hasattr(self, "_sel_label"):
            return
        count = len(self.editor.selected_device_ids())
        self._sel_label.setText(f"Sel: {count}")

    def _update_grid_counts(self):
        if not hasattr(self, "_row_spin") or not hasattr(self, "_col_spin"):
            return
        row_idx = {
            int(round(item.pos().y() / self.editor._row_pitch))
            for item in self.editor.device_items.values()
        }
        col_idx = {
            int(round(item.pos().x() / self.editor._snap_grid))
            for item in self.editor.device_items.values()
        }

        actual_rows = len(row_idx)
        actual_cols = len(col_idx)
        shown_rows = max(actual_rows, self._rows_virtual_min)
        shown_cols = max(actual_cols, self._cols_virtual_min)

        self._ignore_grid_spin_change = True
        self._row_spin.setValue(shown_rows)
        self._col_spin.setValue(shown_cols)
        self._ignore_grid_spin_change = False

    def _on_row_target_changed(self, value):
        if self._ignore_grid_spin_change:
            return
        row_idx = {
            int(round(it.pos().y() / self.editor._row_pitch))
            for it in self.editor.device_items.values()
        }
        col_idx = {
            int(round(it.pos().x() / self.editor._snap_grid))
            for it in self.editor.device_items.values()
        }
        actual = len(row_idx)
        self._rows_virtual_min = max(actual, value)
        cols = max(len(col_idx), self._cols_virtual_min, 1)
        self.editor.ensure_grid_extent(self._rows_virtual_min, cols)
        self._update_grid_counts()

    def _on_col_target_changed(self, value):
        if self._ignore_grid_spin_change:
            return
        col_idx = {
            int(round(it.pos().x() / self.editor._snap_grid))
            for it in self.editor.device_items.values()
        }
        row_idx = {
            int(round(it.pos().y() / self.editor._row_pitch))
            for it in self.editor.device_items.values()
        }
        actual = len(col_idx)
        self._cols_virtual_min = max(actual, value)
        rows = max(len(row_idx), self._rows_virtual_min, 1)
        self.editor.ensure_grid_extent(rows, self._cols_virtual_min)
        self._update_grid_counts()

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
        """Called when drag ends; snap dragged items to nearest free slot."""
        try:
            for it in self.editor.scene.selectedItems():
                if not hasattr(it, "device_name"):
                    continue
                row_y = self.editor._snap_row(it.pos().y())
                target_x = self.editor._snap_value(it.pos().x())
                free_x = self.editor.find_nearest_free_x(
                    row_y=row_y,
                    width=it.rect().width(),
                    target_x=target_x,
                    exclude_id=it.device_name,
                )
                it.setPos(free_x, row_y)
        except RuntimeError:
            pass
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

    def _swap_selected_devices(self):
        selected = self.editor.selected_device_ids()
        if len(selected) != 2:
            self.chat_panel._append_message(
                "AI",
                "Select exactly 2 devices to swap.",
                "#fde8e8",
                "#a00",
            )
            return
        self._sync_node_positions()
        self._push_undo()
        self.editor.swap_devices(selected[0], selected[1])
        self._sync_node_positions()

    def _merge_selected_ss(self):
        self._merge_selected_devices(mode="SS")

    def _merge_selected_dd(self):
        self._merge_selected_devices(mode="DD")

    def _merge_selected_devices(self, mode="SS"):
        selected = self.editor.selected_device_ids()
        if len(selected) != 2:
            self.chat_panel._append_message(
                "AI",
                "Select exactly 2 devices to merge.",
                "#fde8e8",
                "#a00",
            )
            return

        id_a, id_b = selected[0], selected[1]
        a = self.editor.device_items.get(id_a)
        b = self.editor.device_items.get(id_b)
        if not a or not b:
            return
        if getattr(a, "device_type", None) != getattr(b, "device_type", None):
            self.chat_panel._append_message(
                "AI",
                "Merge requires same device type.",
                "#fde8e8",
                "#a00",
            )
            return

        self._sync_node_positions()
        self._push_undo()

        y = self.editor._snap_row((a.pos().y() + b.pos().y()) / 2.0)
        wa = a.rect().width()
        wb = b.rect().width()

        if mode == "SS":
            # A keeps S on left. B flips so S is on right, then sits left of A.
            if hasattr(a, "set_flip_h"):
                a.set_flip_h(False)
            if hasattr(b, "set_flip_h"):
                b.set_flip_h(True)
            ax = self.editor._snap_value(a.pos().x())
            bx = self.editor._snap_value(ax - wb)
            a.setPos(ax, y)
            b.setPos(bx, y)
        else:
            # A keeps D on right. B flips so D is on left, then sits right of A.
            if hasattr(a, "set_flip_h"):
                a.set_flip_h(False)
            if hasattr(b, "set_flip_h"):
                b.set_flip_h(True)
            ax = self.editor._snap_value(a.pos().x())
            bx = self.editor._snap_value(ax + wa)
            a.setPos(ax, y)
            b.setPos(bx, y)

        self.editor.resolve_overlaps(anchor_ids=[id_a, id_b])
        self._sync_node_positions()

    def _flip_selected_h(self):
        selected = self.editor.selected_device_ids()
        if not selected:
            return
        self._sync_node_positions()
        self._push_undo()
        self.editor.flip_devices_h(selected)
        self._sync_node_positions()

    def _flip_selected_v(self):
        selected = self.editor.selected_device_ids()
        if not selected:
            return
        self._sync_node_positions()
        self._push_undo()
        self.editor.flip_devices_v(selected)
        self._sync_node_positions()

    def _apply_row_col_to_selected(self):
        selected = self.editor.selected_device_ids()
        if len(selected) != 1:
            self.chat_panel._append_message(
                "AI",
                "Select one device to apply Row/Col.",
                "#fde8e8",
                "#a00",
            )
            return
        row = self._row_spin.value()
        col = self._col_spin.value()
        self._sync_node_positions()
        self._push_undo()
        self.editor.move_device_to_grid(selected[0], row, col)
        self._sync_node_positions()

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

    def _on_toggle_add_dummy(self, enabled):
        self.editor.set_dummy_mode(enabled)
        msg = (
            "Dummy mode ON: move over PMOS/NMOS row and click to place."
            if enabled
            else "Dummy mode OFF."
        )
        self.chat_panel._append_message("AI", msg, "#e8f4fd", "#1a1a2e")

    def _next_dummy_id(self, dev_type):
        prefix = "DUMMYP" if dev_type == "pmos" else "DUMMYN"
        used = {n.get("id", "") for n in self.nodes}
        i = 1
        while f"{prefix}{i}" in used:
            i += 1
        return f"{prefix}{i}"

    def _build_dummy_node(self, candidate):
        dev_type = str(candidate["type"]).strip().lower()
        template = next(
            (
                n
                for n in self.nodes
                if str(n.get("type", "")).strip().lower() == dev_type
            ),
            None,
        )
        electrical = {"l": 1.4e-08, "nf": 1, "nfin": 1}
        if template:
            electrical = copy.deepcopy(template.get("electrical", electrical))

        x = candidate["x"] / self.editor.scale_factor
        y = candidate["y"] / self.editor.scale_factor
        width = candidate["width"] / self.editor.scale_factor
        height = candidate["height"] / self.editor.scale_factor

        return {
            "id": self._next_dummy_id(dev_type),
            "type": dev_type,
            "is_dummy": True,
            "electrical": electrical,
            "geometry": {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "orientation": "R0",
            },
        }

    def _add_dummy_device(self, candidate):
        self._sync_node_positions()
        self._push_undo()
        candidate = dict(candidate)
        candidate["type"] = str(candidate.get("type", "")).strip().lower()
        candidate["y"] = self.editor._snap_row(candidate["y"])
        candidate["x"] = self.editor._snap_value(candidate["x"])

        col_capacity = max(1, int(self._col_spin.value())) if hasattr(self, "_col_spin") else 1
        dev_type = candidate.get("type")

        def row_type_count(row_y):
            return sum(
                1
                for it in self.editor.device_items.values()
                if self.editor._snap_row(it.pos().y()) == row_y
                and getattr(it, "device_type", None) == dev_type
            )

        while row_type_count(candidate["y"]) >= col_capacity:
            candidate["y"] += self.editor._row_pitch
            candidate["x"] = 0.0

        candidate["x"] = self.editor.find_nearest_free_x(
            row_y=candidate["y"],
            width=candidate["width"],
            target_x=candidate["x"],
            exclude_id=None,
        )
        dummy = self._build_dummy_node(candidate)
        self.nodes.append(dummy)
        self._original_data["nodes"] = self.nodes
        self._refresh_panels()
        self._sync_node_positions()
        self.chat_panel._append_message(
            "AI",
            f"Added dummy {dummy['id']} ({dummy['type']}).",
            "#e8f4fd",
            "#1a1a2e",
        )

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
                item = self.editor.device_items.get(dev_id)
                if item and hasattr(item, "orientation_string"):
                    node["geometry"]["orientation"] = item.orientation_string()
        # Refresh the chat panel's context with updated positions
        self.chat_panel.set_layout_context(
            self.nodes, self._original_data.get("edges")
        )
        self._update_grid_counts()
        self._on_selection_count_changed()


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
