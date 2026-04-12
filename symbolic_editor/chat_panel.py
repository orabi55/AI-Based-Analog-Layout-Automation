"""
Chat Panel — GUI widget for the AI assistant sidebar.

Uses the Worker-Object Pattern: LLM inference runs on a dedicated
QThread via ``LLMWorker``; the ChatPanel communicates with them
exclusively through Qt Signals/Slots.
"""

import re
import json
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTextEdit,
    QPushButton,
    QLabel,
    QFrame,
    QComboBox,
)
from PySide6.QtCore import Qt, Signal, QTimer, QThread
from PySide6.QtGui import QFont

from ai_agent.ai_chat_bot.llm_worker import LLMWorker, build_system_prompt
from icons import icon_panel_toggle


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
# Chat Panel Widget (Right Panel)
# -------------------------------------------------
class ChatPanel(QWidget):
    """Chat panel for interacting with the LLM.

    Signals:
        command_requested(dict): emitted when the AI response contains
            a [CMD]...[/CMD] block that was successfully parsed.
        request_inference(str, list, str): dispatches to
            LLM worker thread for chat.
    """

    command_requested = Signal(dict)  # emits parsed command dicts
    toggle_requested = Signal()        # emitted when the user clicks the panel-toggle button

    request_inference = Signal(str, list, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._layout_context = None
        self._chat_history = []  # multi-turn: list of {"role", "content"}
        self._thinking_timer = None
        self._thinking_dots = 0

        # --- Worker-Object Pattern: QThread + LLMWorker ---
        self._worker_thread = QThread()
        self._llm_worker = LLMWorker()
        self._llm_worker.moveToThread(self._worker_thread)

        # Single-agent path
        self.request_inference.connect(self._llm_worker.process_request)
        # Shared response signals back to GUI
        self._llm_worker.response_ready.connect(self._on_llm_response)
        self._llm_worker.command_ready.connect(self.command_requested.emit)
        self._llm_worker.error_occurred.connect(self._on_llm_error)

        # Start the worker thread's event loop
        self._worker_thread.start()

        self._init_ui()
        self._show_welcome()

    # -----------------------------------------
    # Cleanup
    # -----------------------------------------
    def shutdown(self):
        """Gracefully stop the worker thread.  Call before the
        application exits or this widget is destroyed."""
        self._worker_thread.quit()
        self._worker_thread.wait()

    # -----------------------------------------
    # Layout context
    # -----------------------------------------
    def set_layout_context(self, nodes, edges=None, terminal_nets=None):
        """Store the layout data so the LLM can reference it."""
        self._layout_context = {"nodes": nodes}
        if edges:
            self._layout_context["edges"] = edges
        if terminal_nets:
            self._layout_context["terminal_nets"] = terminal_nets
        # Forward to multi-agent worker so orchestrator has fresh context
        self._llm_worker.set_layout_context(self._layout_context)

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

        # Model selector dropdown
        self.model_combo = QComboBox()
        self.model_combo.addItems(["Gemini", "OpenAI", "Ollama", "Groq", "DeepSeek"])
        self.model_combo.setToolTip("Select AI Model for Chat")
        self.model_combo.setStyleSheet(
            """
            QComboBox {
                background-color: #2d3548;
                color: #e0e8f0;
                border: 1px solid #4a90d9;
                border-radius: 4px;
                padding: 2px 5px;
                font-size: 11px;
            }
            QComboBox::drop-down {
                border: none;
            }
            """
        )
        self.model_combo.setFixedWidth(80)
        header_layout.addWidget(self.model_combo)

        # Ollama sub-model selector (hidden by default unless Ollama is chosen)
        self.ollama_model_combo = QComboBox()
        self.ollama_model_combo.setEditable(True)
        self.ollama_model_combo.addItems(["llama3.2", "qwen3.5:latest", "deepseek-coder:6.7b"])
        self.ollama_model_combo.setToolTip("Select Local Ollama Model")
        self.ollama_model_combo.setStyleSheet(self.model_combo.styleSheet())
        self.ollama_model_combo.setFixedWidth(110)
        self.ollama_model_combo.setVisible(False)
        header_layout.addWidget(self.ollama_model_combo)

        def _on_provider_changed(text):
            self.ollama_model_combo.setVisible(text == "Ollama")
        self.model_combo.currentTextChanged.connect(_on_provider_changed)

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

        # Panel toggle button
        toggle_btn = QPushButton()
        toggle_btn.setIcon(icon_panel_toggle())
        toggle_btn.setFixedSize(28, 28)
        toggle_btn.setToolTip("Hide panel")
        toggle_btn.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: rgba(255,255,255,0.12);
            }
            QPushButton:pressed {
                background-color: rgba(255,255,255,0.20);
            }
            """
        )
        toggle_btn.clicked.connect(self.toggle_requested.emit)
        header_layout.addWidget(toggle_btn)

        layout.addWidget(header)

        # Chat display
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setTextInteractionFlags(
            Qt.TextInteractionFlag.NoTextInteraction
        )
        self.chat_display.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.chat_display.setStyleSheet(
            """
            QTextEdit {
                background-color: #111621;
                border: none;
                padding: 10px;
                font-family: 'Segoe UI', sans-serif;
                font-size: 13px;
                color: #d0d8e0;
            }
            QScrollBar:vertical {
                width: 6px;
                background: transparent;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #2d3548;
                border-radius: 3px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: #3d5066;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            """
        )
        layout.addWidget(self.chat_display, 1)  # stretch factor = 1 → fills space

        # Input area
        input_frame = QFrame()
        input_frame.setStyleSheet(
            "background-color: #1a1f2b; border-top: 1px solid #2d3548;"
        )
        input_layout = QHBoxLayout(input_frame)
        input_layout.setContentsMargins(10, 10, 10, 10)
        input_layout.setSpacing(8)

        self.input_field = ChatInputEdit()
        self.input_field.setStyleSheet(
            """
            QTextEdit {
                border: 1px solid #2d3548;
                border-radius: 12px;
                padding: 8px 14px;
                font-size: 13px;
                font-family: 'Segoe UI';
                background: #232a38;
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
        send_btn.setFixedSize(38, 38)
        send_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #4a90d9;
                color: white;
                border: none;
                border-radius: 19px;
                font-size: 17px;
            }
            QPushButton:hover {
                background-color: #5a9fe8;
            }
            QPushButton:pressed {
                background-color: #357abd;
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
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # Code blocks
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
            <div style="text-align:right; margin:6px 0;">
                <div style="display:inline-block; max-width:82%; text-align:left;">
                    <div style="
                        background-color: #4a90d9;
                        color: white;
                        padding: 10px 16px;
                        border-radius: 16px 16px 4px 16px;
                        font-size: 13px;
                        line-height: 1.45;
                    ">
                        {content}
                    </div>
                    <div style="font-size:10px; color:#556677; text-align:right; margin-top:3px;">
                        {now}
                    </div>
                </div>
            </div>
            """
        else:
            avatar = "🤖" if role == "ai" else "ℹ️"
            bg = "#1a2230" if role == "ai" else "#2a2518"
            border_col = "#2d3548" if role == "ai" else "#4a4020"
            text_col = "#d0d8e0" if role == "ai" else "#e8ddb8"
            html = f"""
            <div style="text-align:left; margin:6px 0;">
                <div style="display:inline-block; max-width:88%; text-align:left;">
                    <div style="font-size:10px; color:#556677; margin-bottom:3px;">
                        {avatar}  AI Assistant
                    </div>
                    <div style="
                        background: {bg};
                        color: {text_col};
                        padding: 10px 16px;
                        border-radius: 4px 16px 16px 16px;
                        font-size: 13px;
                        line-height: 1.5;
                        border: 1px solid {border_col};
                    ">
                        {content}
                    </div>
                    <div style="font-size:10px; color:#556677; margin-top:3px;">
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

        # --- Execute commands from user text IMMEDIATELY ---
        user_cmds = self._infer_commands_from_text(text)
        if user_cmds:
            print(f"[CHAT] Direct user commands: {user_cmds}")
            for cmd in user_cmds:
                self.command_requested.emit(cmd)
            self._user_cmds_executed = True
        else:
            self._user_cmds_executed = False

        # --- Route to single-agent ----------------------
        self._call_llm(text)


    def _clear_chat(self):
        """Clear the chat display and history."""
        self.chat_display.clear()
        self._chat_history.clear()
        # Reset multi-agent pipeline state so the Refiner doesn't
        # think we're mid-conversation after a chat clear.
        self._llm_worker.reset_pipeline()
        self._show_welcome()

    # keep backward-compat for external callers (main.py uses this)
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
        # Original dot animation
        self._thinking_dots = (self._thinking_dots + 1) % 4
        dots = "." * self._thinking_dots
        html = self.chat_display.toHtml()
        idx = html.rfind("Thinking")
        if idx != -1:
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
    # LLM dispatch helpers
    # -----------------------------------------


    def _call_llm(self, user_message):
        """Build prompts and dispatch the request to the single-agent worker thread."""
        self._start_thinking()

        system_prompt = build_system_prompt(self._layout_context)

        # Trim history: last 4 msgs, strip old [CMD] blocks & error noise
        def _clean(content):
            c = re.sub(r'\[CMD\].*?\[/CMD\]', '', content, flags=re.DOTALL)
            if c.startswith("⚠️ Error:"):
                return "(error – skipped)"
            return c.strip()

        recent = self._chat_history[-4:]

        # Build conversation-only text (NO system prompt mixed in)
        history_text = ""
        for msg in recent:
            role_label = "User" if msg["role"] == "user" else "Assistant"
            history_text += f"{role_label}: {_clean(msg['content'])}\n"

        # full_prompt = system + conversation (for providers that need one blob)
        full_prompt = f"{system_prompt}\n\nConversation:\n{history_text}"

        # Build chat messages for OpenAI-compatible APIs
        chat_messages = [{"role": "system", "content": system_prompt}]
        for msg in recent:
            chat_messages.append({
                "role": msg["role"],
                "content": _clean(msg["content"]),
            })

        # Emit signal → crosses thread boundary → runs on worker thread
        selected_model = self.model_combo.currentText()
        ollama_model = self.ollama_model_combo.currentText()
        self.request_inference.emit(full_prompt, chat_messages, selected_model, ollama_model)

    # -----------------------------------------
    # Response handling (GUI thread)
    # -----------------------------------------
    @staticmethod
    def _infer_commands_from_text(text):
        """Extract swap/move intents from natural language text.

        Works on both user messages and AI responses so that commands
        are executed even when the model forgets [CMD] blocks.
        Handles many natural-language variations.
        """
        commands = []
        if not text:
            return commands

        # --- Swap detection (many variations) ---
        swap_patterns = [
            # "swap MM28 with MM25", "swap 28 and 25"
            re.compile(
                r"swap(?:ped|ping)?\s+([A-Za-z]*\d+)\s+(?:with|and|&)\s+([A-Za-z]*\d+)",
                re.IGNORECASE,
            ),
            # "swap between MM28 and MM25"
            re.compile(
                r"swap(?:ped|ping)?\s+between\s+([A-Za-z]*\d+)\s+(?:and|&)\s+([A-Za-z]*\d+)",
                re.IGNORECASE,
            ),
            # "MM28 and MM25 have been swapped" / "MM28 and MM25 are swapped"
            re.compile(
                r"([A-Za-z]*\d+)\s+(?:and|&|with)\s+([A-Za-z]*\d+)\s+(?:have been|are|were|got)\s+swap",
                re.IGNORECASE,
            ),
            # "swapped MM28 and MM25" (at start of sentence)
            re.compile(
                r"swapped\s+([A-Za-z]*\d+)\s+(?:and|&|with)\s+([A-Za-z]*\d+)",
                re.IGNORECASE,
            ),
            # "I've/I have swapped MM28 and MM25"
            re.compile(
                r"(?:I'?ve|I\s+have)\s+swapped\s+([A-Za-z]*\d+)\s+(?:and|&|with)\s+([A-Za-z]*\d+)",
                re.IGNORECASE,
            ),
        ]
        for pat in swap_patterns:
            for m in pat.finditer(text):
                commands.append({
                    "action": "swap",
                    "device_a": m.group(1),
                    "device_b": m.group(2),
                })
            if commands:
                break  # don't double-count from multiple patterns

        # --- Move detection ---
        move_patterns = [
            # "move MM3 to x=0.5 y=0.3" / "move MM3 to x=0.5, y=0.3"
            re.compile(
                r"mov(?:e|ed|ing)\s+([A-Za-z]*\d+)\s+to\s+"
                r"x\s*=\s*(-?\d+(?:\.\d+)?)\s*,?\s*"
                r"y\s*=\s*(-?\d+(?:\.\d+)?)",
                re.IGNORECASE,
            ),
            # "move MM3 to (0.5, 0.3)" / "move MM3 to 0.5 0.3"
            re.compile(
                r"mov(?:e|ed|ing)\s+([A-Za-z]*\d+)\s+to\s+"
                r"\(?\s*(-?\d+(?:\.\d+)?)\s*[,\s]\s*(-?\d+(?:\.\d+)?)\s*\)?",
                re.IGNORECASE,
            ),
        ]
        for pat in move_patterns:
            for m in pat.finditer(text):
                commands.append({
                    "action": "move",
                    "device": m.group(1),
                    "x": float(m.group(2)),
                    "y": float(m.group(3)),
                })
            if commands:
                break

        # --- Dummy detection ---
        # "add dummy nmos", "add 3 nmos dummies", "add 2 pmos dummy"
        if not commands:
            dummy_patterns = [
                # "add 3 nmos dummies" / "add 3 nmos dummy"
                re.compile(
                    r"add\s+(\d+)\s+(nmos|pmos)\s+dumm(?:y|ies)",
                    re.IGNORECASE,
                ),
                # "add nmos dummy" / "add pmos dummies"
                re.compile(
                    r"add\s+(nmos|pmos)\s+dumm(?:y|ies)",
                    re.IGNORECASE,
                ),
                # "add dummy nmos" / "add dummies pmos"
                re.compile(
                    r"add\s+dumm(?:y|ies)\s+(nmos|pmos)",
                    re.IGNORECASE,
                ),
                # "add 3 dummies nmos"
                re.compile(
                    r"add\s+(\d+)\s+dumm(?:y|ies)\s+(nmos|pmos)",
                    re.IGNORECASE,
                ),
                # bare "add dummy" / "add dummies"
                re.compile(
                    r"add\s+(?:(\d+)\s+)?dumm(?:y|ies)",
                    re.IGNORECASE,
                ),
            ]
            for pat in dummy_patterns:
                m = pat.search(text)
                if m:
                    groups = m.groups()
                    count = 1
                    dev_type = "nmos"
                    for g in groups:
                        if g is None:
                            continue
                        if g.isdigit():
                            count = int(g)
                        elif g.lower() in ("nmos", "pmos"):
                            dev_type = g.lower()
                    # Detect side hint (left / right)
                    side = "left"
                    if re.search(r"\bright\b", text, re.IGNORECASE):
                        side = "right"
                    commands.append({
                        "action": "add_dummy",
                        "type": dev_type,
                        "count": count,
                        "side": side,
                    })
                    break

        return commands

    @staticmethod
    def _ai_response_is_affirmative(text):
        """Check if the AI response indicates it performed/confirmed an action."""
        if not text:
            return False
        affirmative = re.search(
            r"(?:okay|ok|sure|done|swapped|moved|I.ve|I have|certainly|"
            r"of course|completed|executed|here you go|right away)",
            text, re.IGNORECASE,
        )
        return affirmative is not None

    def _on_llm_response(self, text):
        self._stop_thinking()
        self._remove_last_message()

        print(f"[CHAT] Raw LLM response: {text[:300]}")

        # Only execute explicit [CMD]...[/CMD] blocks from the AI,
        # and ONLY if we didn't already execute the user's commands directly.
        # Otherwise a duplicated swap would undo itself.
        display_text, commands = self._parse_commands(text)
        if commands and not getattr(self, '_user_cmds_executed', False):
            print(f"[CHAT] Parsed [CMD] blocks from AI: {commands}")
            for cmd in commands:
                self.command_requested.emit(cmd)
        else:
            reason = "user cmds already ran" if getattr(self, '_user_cmds_executed', False) else "none found"
            print(f"[CHAT] Skipping AI [CMD] blocks ({reason}).")
        self._user_cmds_executed = False   # reset for next turn

        clean = display_text.strip()
        self._chat_history.append({"role": "assistant", "content": clean})
        self._append_bubble("ai", clean)

    def _parse_commands(self, text):
        """Extract [CMD]...[/CMD] blocks, return (display_text, list_of_cmds)."""
        pattern = r'\[CMD\].*?\[/CMD\]'
        display_text = re.sub(pattern, '', text, flags=re.DOTALL | re.IGNORECASE)
        
        commands = []
        for match in re.finditer(pattern, text, flags=re.DOTALL | re.IGNORECASE):
            # Clean up the matched block
            block = match.group(0)
            # Remove tags (case-insensitive)
            block = re.sub(r'\[/?CMD\]', '', block, flags=re.IGNORECASE).strip()
            if not block:
                continue

            try:
                import json
                parsed = json.loads(block)
                if isinstance(parsed, dict):
                    commands.append(parsed)
                elif isinstance(parsed, list):
                    commands.extend([c for c in parsed if isinstance(c, dict)])
            except Exception as e:
                print(f"[CHAT] Failed to parse command block: {block[:50]}... Error: {e}")

        return display_text, commands

    def _on_llm_error(self, error_text):
        self._stop_thinking()
        self._remove_last_message()
        self._user_cmds_executed = False          # reset so next turn works
        err_msg = f"⚠️ Error: {error_text}"
        self._chat_history.append({"role": "assistant", "content": err_msg})
        self._append_bubble("ai", err_msg)
        
    def _remove_last_message(self):
        """Remove the last appended message (the thinking bubble)."""
        html = self.chat_display.toHtml()
        idx = html.rfind('<div style="text-align:')
        if idx != -1:
            self.chat_display.setHtml(html[:idx])
