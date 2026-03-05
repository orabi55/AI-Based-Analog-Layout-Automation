"""
Chat Panel — GUI widget for the AI assistant sidebar.

Uses the Worker-Object Pattern: LLM inference runs on a dedicated
QThread via ``LLMWorker``; the ChatPanel communicates with it
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
)
from PySide6.QtCore import Qt, Signal, QTimer, QThread
from PySide6.QtGui import QFont

from ai_agent.llm_worker import LLMWorker, build_system_prompt


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
        request_inference(str, list): internal signal used to dispatch
            work to the ``LLMWorker`` living on another QThread.
    """

    command_requested = Signal(dict)  # emits parsed command dicts

    # Private signal: triggers LLMWorker.process_request across threads
    request_inference = Signal(str, list)

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

        # Connect cross-thread signals
        self.request_inference.connect(self._llm_worker.process_request)
        self._llm_worker.response_ready.connect(self._on_llm_response)
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
    def set_layout_context(self, nodes, edges=None):
        """Store the layout data so the LLM can reference it."""
        self._layout_context = {"nodes": nodes}
        if edges:
            self._layout_context["edges"] = edges

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
    # LLM dispatch (via QThread signal)
    # -----------------------------------------
    def _call_llm(self, user_message):
        """Build prompts and dispatch the request to the worker thread."""
        self._start_thinking()

        system_prompt = build_system_prompt(self._layout_context)

        # Build multi-turn context for non-chat APIs (Ollama, Gemini)
        history_text = ""
        for msg in self._chat_history[-4:]:
            role_label = "User" if msg["role"] == "user" else "Assistant"
            history_text += f"{role_label}: {msg['content']}\n"
        full_prompt = f"{system_prompt}\n\nConversation history:\n{history_text}"

        # Build chat messages for OpenAI-compatible APIs
        chat_messages = [{"role": "system", "content": system_prompt}]
        for msg in self._chat_history[-4:]:
            chat_messages.append(msg)

        # Emit signal → crosses thread boundary → runs on worker thread
        self.request_inference.emit(full_prompt, chat_messages)

    # -----------------------------------------
    # Response handling (GUI thread)
    # -----------------------------------------
    def _on_llm_response(self, text):
        self._stop_thinking()
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
        idx = html.rfind('<div style="text-align:')
        if idx != -1:
            self.chat_display.setHtml(html[:idx])
