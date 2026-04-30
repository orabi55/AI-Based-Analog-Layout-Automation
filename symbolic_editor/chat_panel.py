"""
Chat Panel — GUI widget for the AI assistant sidebar.

Uses the Worker-Object Pattern: LLM inference runs on a dedicated
QThread via ``OrchestratorWorker`` (multi-agent) or ``LLMWorker``
(single-agent fallback); the ChatPanel communicates with them
exclusively through Qt Signals/Slots.

Keyword routing:
  Words like "optimize", "improve", "auto-place", "fix drc", "reduce"
  trigger the 4-stage OrchestratorWorker pipeline.
  All other queries use the standard single-agent LLMWorker path.
"""

import os
import re
import json
from datetime import datetime

# Set default Vertex AI env vars so the chat works without opening
# the AI Model Settings dialog first.
if "VERTEX_PROJECT_ID" not in os.environ:
    os.environ["VERTEX_PROJECT_ID"] = "project-03484c74-0ab0-4f9e-b48"
if "VERTEX_LOCATION" not in os.environ:
    os.environ["VERTEX_LOCATION"] = "global"

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
from PySide6.QtCore import Qt, Signal, QTimer, QThread, Slot
from PySide6.QtGui import QFont

from ai_agent.llm.workers import OrchestratorWorker, build_system_prompt
from ai_agent.tools.cmd_parser import extract_cmd_blocks
try:
    from .icons import icon_panel_toggle
except ImportError:
    from icons import icon_panel_toggle

# ---------------------------------------------------------------------------
# Keywords that trigger the multi-agent Orchestrator pipeline
# ---------------------------------------------------------------------------
_ORCHESTRATOR_KEYWORDS = re.compile(
    r"\b("
    r"optimi[sz]e|optimis|improve|auto.?place|auto.?layout|"
    r"fix.?drc|drc.?fix|reduce.?crossings|reduce.?routing|"
    r"rearrange|reorder|reorgani[sz]e|minimise|minimize|"
    r"suggest.?placement|better.?placement|swap.?all|pipeline"
    r")\b",
    re.IGNORECASE,
)

_ORCHESTRATOR_STAGES = [
    ("Topology Analyst",     "🔬 Stage 1/4 — Analysing circuit topology..."),
    ("Placement Specialist", "📐 Stage 2/4 — Computing optimal placement..."),
    ("DRC Critic",           "🔍 Stage 3/4 — Checking DRC violations..."),
    ("Routing Pre-Viewer",   "🔀 Stage 4/4 — Previewing routing & crossings..."),
]


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
        request_inference(str, list): single-agent path — dispatches to
            LLM worker thread for normal chat.
        request_orchestrated(str, str): multi-agent path — dispatches to
            OrchestratorWorker with (user_message, layout_context_json).
    """

    command_requested = Signal(dict)  # emits parsed command dicts
    toggle_requested = Signal()        # emitted when the user clicks the panel-toggle button

    # Single-agent path (normal chat)
    request_inference = Signal(str, list, str)
    # Multi-agent path (orchestrator pipeline)
    request_orchestrated = Signal(str, str, list, str)
    # Resume paths for LangGraph interrupts
    request_resume_strategy = Signal(str)
    request_resume_viewer = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._layout_context = None
        self._chat_history = []  # multi-turn: list of {"role", "content"}
        self._thinking_timer = None
        self._thinking_dots = 0
        self._thinking_stage = 0          # which pipeline stage label to show
        self._is_orchestrated = False     # True when orchestrator path is active
        self._awaiting_strategy_resume = False
        self._awaiting_visual_resume = False

        # Model preferences (synced from MainWindow)
        self.selected_model = "VertexGemini"
        

        # --- Worker-Object Pattern: QThread + OrchestratorWorker ---
        self._worker_thread = QThread()
        self._llm_worker = OrchestratorWorker()   # superset of LLMWorker
        self._llm_worker.moveToThread(self._worker_thread)

        # Single-agent path
        self.request_inference.connect(self._llm_worker.process_request)
        # Multi-agent (orchestrator) path
        self.request_orchestrated.connect(self._llm_worker.process_orchestrated_request)
        self.request_resume_strategy.connect(self._llm_worker.resume_with_strategy)
        self.request_resume_viewer.connect(self._llm_worker.resume_from_viewer)
        # Shared response signals back to GUI
        self._llm_worker.response_ready.connect(self._on_llm_response)
        self._llm_worker.error_occurred.connect(self._on_llm_error)
        
        # Human-in-the-loop pause signal
        self._llm_worker.topology_ready_for_review.connect(self._on_topology_review)
        self._llm_worker.visual_viewer_signal.connect(self._on_visual_viewer_signal)  # reuse same handler for viewer interrupts
        # Intent classification → switch thinking animation
        self._llm_worker.intent_classified.connect(self._on_intent_classified)

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

        # Model selector dropdown
        self._model_combo = QComboBox()
        self._model_combo.addItems(["VertexGemini", "Gemini", "Alibaba", "VertexClaude"])
        self._model_combo.setCurrentText(self.selected_model)
        self._model_combo.setFixedHeight(26)
        self._model_combo.setToolTip("Select AI model for chat")
        self._model_combo.setStyleSheet("""
            QComboBox {
                background-color: #1a2536;
                color: #b0c0d0;
                border: 1px solid #2d3f54;
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 10px;
                font-family: 'Segoe UI';
                min-width: 100px;
            }
            QComboBox:hover {
                border-color: #4a90d9;
            }
            QComboBox::drop-down {
                border: none;
                width: 18px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #6080a0;
                margin-right: 5px;
            }
            QComboBox QAbstractItemView {
                background-color: #1a2536;
                color: #b0c0d0;
                border: 1px solid #2d3f54;
                selection-background-color: #2a3a52;
                selection-color: #e0e8f0;
            }
        """)
        self._model_combo.currentTextChanged.connect(self._on_model_changed)
        header_layout.addWidget(self._model_combo)

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
        toggle_btn.setText(">")
        toggle_btn.setFixedSize(28, 28)
        toggle_btn.setToolTip("Hide panel")
        toggle_btn.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 4px;
                color: #9aa4b2;
                font-family: 'Segoe UI';
                font-size: 14px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: rgba(255,255,255,0.12);
                color: #ffffff;
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
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
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
        """Convert basic markdown to HTML (dark-theme aware)."""
        # Save original newlines for table parsing
        raw_text = text
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        # --- Premium Table Parsing (Terminal-to-Rich-UI) ---
        # Detect PLACEMENT SUMMARY block
        if "PLACEMENT SUMMARY" in text:
            summary_pattern = r"={10,}\s+PLACEMENT SUMMARY\s+={10,}(.*?)(?===|$)"
            match = re.search(summary_pattern, text, re.DOTALL)
            if match:
                inner = match.group(1).strip()
                rows_html = ""
                for line in inner.split("\n"):
                    if ":" in line:
                        k, v = line.split(":", 1)
                        rows_html += f"<tr><td style='color:#7cb7ff;padding:2px 8px;font-weight:bold;'>{k.strip()}</td>"
                        rows_html += f"<td style='color:#e0e8f0;padding:2px 8px;'>{v.strip()}</td></tr>"
                table_html = f"<table style='margin:10px 0;border-collapse:collapse;width:100%;background:#1a2030;border-radius:8px;border:1px solid #2d3548;'>{rows_html}</table>"
                text = text.replace(match.group(0), table_html)

        # Detect MATCHING & SYMMETRY QUALITY BENCHMARK block
        if "QUALITY BENCHMARK" in text:
            benchmark_pattern = r"={10,}\s+MATCHING &amp; SYMMETRY QUALITY BENCHMARK\s+={10,}(.*?)(?===|$)"
            match = re.search(benchmark_pattern, text, re.DOTALL)
            if match:
                inner = match.group(1).strip()
                rows_html = ""
                # Skip header lines
                lines = [l.strip() for l in inner.split("\n") if l.strip() and "----" not in l and "Matched pairs" not in l and "Metric" not in l]
                for line in lines:
                    # Match: Metric Score Progress Grade
                    # Example: Layout Y Symmetry 100.0% #################### A+
                    parts = re.split(r'\s{2,}', line)
                    if len(parts) >= 3:
                        metric = parts[0]
                        score = parts[1]
                        prog_chars = parts[2]
                        grade = parts[3] if len(parts) > 3 else ""
                        
                        # Convert progress bars to real UI bars
                        filled = prog_chars.count("#")
                        total = len(prog_chars)
                        pct = (filled / total * 100) if total > 0 else 0
                        bar_color = "#4caf50" if pct > 80 else "#ff9800" if pct > 50 else "#f44336"
                        bar_html = f"<div style='width:60px;height:6px;background:#2d3548;border-radius:3px;margin-top:4px;'><div style='width:{pct}%;height:100%;background:{bar_color};border-radius:3px;'></div></div>"
                        
                        rows_html += f"<tr style='border-bottom:1px solid #232a38;'>"
                        rows_html += f"<td style='padding:6px 8px;color:#d0d8e0;'>{metric}</td>"
                        rows_html += f"<td style='padding:6px 8px;color:#7cb7ff;font-weight:bold;'>{score}</td>"
                        rows_html += f"<td style='padding:6px 8px;'>{bar_html}</td>"
                        rows_html += f"<td style='padding:6px 8px;font-weight:bold;color:#fff;'>{grade}</td>"
                        rows_html += "</tr>"
                
                table_html = f"<div style='margin-top:15px;color:#7cb7ff;font-weight:bold;font-size:11px;'>MATCHING & SYMMETRY QUALITY</div>"
                table_html += f"<table style='margin:5px 0;border-collapse:collapse;width:100%;background:#1a2030;border-radius:8px;border:1px solid #2d3548;'>{rows_html}</table>"
                text = text.replace(match.group(0), table_html)

        # Code blocks (fallthrough for other terminal-style blocks)
        text = re.sub(
            r'```(\w*)\n(.*?)```',
            r'<pre style="background:#0d1117;color:#8b9eb0;padding:10px 12px;'
            r'border-radius:8px;font-size:11px;font-family:Consolas,monospace;'
            r'border:1px solid #2d3548;overflow-x:auto;margin:6px 0;">\2</pre>',
            text, flags=re.DOTALL,
        )
        # Inline code
        text = re.sub(
            r'`([^`]+)`',
            r'<code style="background:#1e2a3a;color:#7cb7ff;padding:2px 6px;'
            r'border-radius:4px;font-size:12px;font-family:Consolas,monospace;'
            r'border:1px solid #2d3f54;">\1</code>',
            text,
        )
        # Detect Routing Pre-Viewer block
        if "Routing Pre-Viewer" in text or "nets analyzed" in text:
            routing_pattern = r"(?:─{5,}|={5,})\s*Routing Pre-Viewer\s*(?:─{5,}|={5,})(.*?)(?=\[IP\]|PLACEMENT SUMMARY|$)"
            match = re.search(routing_pattern, text, re.DOTALL)
            if not match and "nets analyzed" in text:
                # Fallback: catch by keyword if the bars are missing/different
                routing_pattern = r"(9 nets analyzed.*?)(?=\[IP\]|PLACEMENT SUMMARY|$)"
                match = re.search(routing_pattern, text, re.DOTALL)

            if match:
                inner = match.group(1).strip()
                lines = inner.split("\n")
                html = "<div style='margin-top:15px;padding:12px;background:#1a1f2b;border-radius:10px;border:1px solid #30363d;font-family:Segoe UI, sans-serif;'>"
                html += "<div style='color:#4FC3F7;font-weight:bold;font-size:12px;margin-bottom:8px;display:flex;align-items:center;'><span>📡 ROUTING DENSITY REPORT</span></div>"
                
                in_worst = False
                in_bands = False
                
                for line in lines:
                    line = line.strip()
                    if not line: continue
                    
                    if "nets analyzed" in line:
                        html += f"<div style='color:#8b9eb0;font-size:11px;margin-bottom:8px;background:#0d1117;padding:4px 8px;border-radius:4px;'>{line}</div>"
                    elif "Worst nets" in line:
                        html += "<div style='color:#e0e8f0;font-weight:bold;font-size:11px;margin-top:10px;margin-bottom:5px;border-bottom:1px solid #2d3548;'>Critical Nets (Worst HPWL)</div>"
                        in_worst = True
                        in_bands = False
                    elif "Routing channels" in line:
                        html += "<div style='color:#e0e8f0;font-weight:bold;font-size:11px;margin-top:10px;margin-bottom:5px;border-bottom:1px solid #2d3548;'>Inter-Row Routing Tracks</div>"
                        in_worst = False
                        in_bands = True
                    elif "HPWL" in line or "crossings" in line or "cost" in line:
                        k, v = line.split(":", 1) if ":" in line else (line, "")
                        html += f"<div style='font-size:12px;'><b>{k.strip()}:</b> <span style='color:#7cb7ff;'>{v.strip()}</span></div>"
                    elif in_worst and ("critical" in line or "signal" in line):
                        # Format net details: NetName Type HPWL (Details) [Type]
                        net_match = re.match(r"(\w+)\s+(\w+)\s+(hpwl=[\d\.]+µm)\s+(.*)", line)
                        if net_match:
                            name, ntype, hpwl, details = net_match.groups()
                            color = "#ff5252" if ntype == "critical" else "#ffb74d"
                            html += f"<div style='margin-left:4px;font-size:11px;display:flex;justify-content:space-between;'>"
                            html += f"<span><b style='color:{color};'>{name}</b> <span style='color:#8899aa;'>{hpwl}</span></span>"
                            html += f"<span style='color:#5c6d7e;font-size:10px;'>{details}</span></div>"
                        else:
                            html += f"<div style='margin-left:4px;font-size:11px;color:#abb2bf;'>• {line}</div>"
                    elif in_bands and line.startswith("band"):
                        html += f"<div style='margin-left:4px;font-family:Consolas, monospace;font-size:11px;color:#7cb7ff;'>{line}</div>"
                    else:
                        html += f"<div style='margin-top:2px;font-size:11px;color:#abb2bf;'>{line}</div>"
                
                html += "</div>"
                text = text.replace(match.group(0), html)

        # Bold/Italic/Lists/Linebreaks
        text = re.sub(r'\*\*(.+?)\*\*', r'<b style="color:#e8f0ff;">\1</b>', text)
        text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
        text = re.sub(r'(?m)^[\-\*]\s+(.+)$', r'<div style="margin:3px 0 3px 14px;padding-left:2px;"><span style="color:#4a90d9;">•</span> \1</div>', text)
        text = re.sub(r'(?m)^(\d+)\.\s+(.+)$', r'<div style="margin:3px 0 3px 14px;"><span style="color:#4a90d9;">\1.</span> \2</div>', text)
        text = text.replace("\n", "<br>")
        return text

    # -----------------------------------------
    # Welcome message
    # -----------------------------------------
    def _show_welcome(self):
        welcome = (
            "<div style='margin-bottom:6px;'>"
            "<b style='color:#7cb7ff;font-size:14px;'>Welcome to the AI Layout Assistant</b>"
            "</div>"
            "I can help you with your analog IC layout. Here are some things to try:<br><br>"
            "<div style='margin-left:8px;'>"
            "<div style='margin:4px 0;'><span style='color:#4a90d9;'>◆</span> "
            "<b>Swap</b> — <i style='color:#8899aa;'>\"Swap MM28 with MM25\"</i></div>"
            "<div style='margin:4px 0;'><span style='color:#4a90d9;'>◆</span> "
            "<b>Move</b> — <i style='color:#8899aa;'>\"Move MM3 to x=0.5 y=0.3\"</i></div>"
            "<div style='margin:4px 0;'><span style='color:#4a90d9;'>◆</span> "
            "<b>Analyze</b> — <i style='color:#8899aa;'>\"How many NMOS devices?\"</i></div>"
            "<div style='margin:4px 0;'><span style='color:#4a90d9;'>◆</span> "
            "<b>Optimize</b> — <i style='color:#8899aa;'>\"Suggest a better placement\"</i></div>"
            "<div style='margin:4px 0;'><span style='color:#4a90d9;'>◆</span> "
            "<b>Add dummies</b> — <i style='color:#8899aa;'>\"Add 2 nmos dummies on left\"</i></div>"
            "</div><br>"
            "<div style='color:#5a6d82;font-size:11px;'>💡 I remember our conversation — ask follow-ups anytime.</div>"
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

        # --- Route to orchestrator or single-agent ----------------------
        # If we have a pending topology, ANY message goes to the Orchestrator
        # to resume the pipeline, regardless of keywords.
        # If a layout is loaded, always use the Orchestrator — it contains the
        # Classifier Agent which does fine-grained intent routing internally.
        if self._layout_context:
            if self._awaiting_strategy_resume:
                self._is_orchestrated = True
                self._start_thinking()
                self.request_resume_strategy.emit(text)
                self._awaiting_strategy_resume = False
                return

            if self._awaiting_visual_resume:
                self._is_orchestrated = True
                viewer_response = {
                    "approved": self._ai_response_is_affirmative(text),
                    "edits": [],
                }
                if not viewer_response["approved"]:
                    viewer_response["edits"] = self._infer_commands_from_text(text)
                self._start_thinking()
                self.request_resume_viewer.emit(viewer_response)
                self._awaiting_visual_resume = False
                return

            # Layout loaded → use orchestrator with classifier routing.
            # Set _is_orchestrated = False initially; only abstract intents
            # trigger the pipeline stage animation. The classifier runs
            # server-side and routes to chat/question/concrete/abstract.
            self._is_orchestrated = False
            self._call_orchestrator(text)
        else:
            # No layout loaded → single-agent mode
            self._is_orchestrated = False
            self._call_llm(text)


    def _clear_chat(self):
        """Clear the chat display and history."""
        self.chat_display.clear()
        self._chat_history.clear()
        self._show_welcome()

    def _on_model_changed(self, model_name: str):
        """Handle model selection change from the dropdown."""
        self.selected_model = model_name
        print(f"[CHAT] Model changed to: {model_name}")

    @Slot(str)
    def _on_intent_classified(self, intent: str):
        """Switch the thinking animation based on the classified intent.

        For 'abstract' intents, activate the 4-stage pipeline animation.
        For chat/question/concrete, keep the simple 'Thinking...' dots.
        """
        if intent == "abstract":
            # Switch to pipeline stage animation
            self._is_orchestrated = True
            self._stop_thinking()
            self._remove_last_message()
            # Restart with pipeline stage labels
            self._thinking_dots = 0
            self._thinking_stage = 0
            label = _ORCHESTRATOR_STAGES[0][1]
            self._append_bubble("ai", label)
            self._thinking_timer = QTimer(self)
            self._thinking_timer.timeout.connect(self._animate_thinking)
            self._thinking_timer.start(3800)

    # keep backward-compat for external callers (main.py uses this)
    def _append_message(self, sender, text, bg_color, text_color):
        role = "user" if sender == "User" else "ai"
        self._append_bubble(role, text)

    # -----------------------------------------
    # Animated thinking indicator
    # -----------------------------------------
    def _start_thinking(self):
        self._thinking_dots = 0
        self._thinking_stage = 0
        if self._is_orchestrated:
            label = _ORCHESTRATOR_STAGES[0][1]
        else:
            label = "Thinking"
        self._append_bubble("ai", label)
        self._thinking_timer = QTimer(self)
        self._thinking_timer.timeout.connect(self._animate_thinking)
        # Slower tick for orchestrator (stage labels change every ~4 s)
        interval = 3800 if self._is_orchestrated else 400
        self._thinking_timer.start(interval)

    def _animate_thinking(self):
        if self._is_orchestrated:
            # Cycle through pipeline stage labels
            self._thinking_stage = (self._thinking_stage + 1) % len(_ORCHESTRATOR_STAGES)
            label = _ORCHESTRATOR_STAGES[self._thinking_stage][1]
            html = self.chat_display.toHtml()
            # replace the last stage label with the next one
            for _, stage_text in _ORCHESTRATOR_STAGES:
                idx = html.rfind(stage_text.split("—")[0].strip())
                if idx != -1:
                    # find enclosing tag boundary
                    end = html.find("<", idx + 1)
                    if end == -1:
                        end = idx + len(stage_text)
                    html = html[:idx] + label + html[end:]
                    break
            self.chat_display.setHtml(html)
        else:
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
    def _call_orchestrator(self, user_message):
        """Serialize layout context and dispatch to OrchestratorWorker."""
        self._start_thinking()
        ctx = self._layout_context or {}
        try:
            ctx_json = json.dumps(ctx, default=str)
        except (TypeError, ValueError):
            ctx_json = "{}"
            
        def _clean(content):
            c = re.sub(r'\[CMD\].*?\[/CMD\]', '', content, flags=re.DOTALL)
            if c.startswith("⚠️ Error:"):
                return "(error – skipped)"
            return c.strip()

        recent = self._chat_history[-4:]
        chat_messages = []
        for msg in recent:
            chat_messages.append({
                "role": msg["role"],
                "content": _clean(msg["content"]),
            })

        print(f"[CHAT] → Orchestrator pipeline for: {user_message[:60]!r}")
        self.request_orchestrated.emit(
            user_message,
            ctx_json,
            chat_messages,
            self.selected_model,
        )

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
        self.request_inference.emit(
            full_prompt, chat_messages, self.selected_model
        )

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
        self._awaiting_strategy_resume = False
        self._awaiting_visual_resume = False

        # Normalize payloads defensively: worker/UI integrations may emit
        # dict/list payloads in some paths instead of plain strings.
        if isinstance(text, dict):
            if isinstance(text.get("content"), str):
                text = text.get("content", "")
            else:
                text = json.dumps(text, ensure_ascii=False, indent=2)
        elif isinstance(text, list):
            text = json.dumps(text, ensure_ascii=False, indent=2)
        elif text is None:
            text = ""
        else:
            text = str(text)

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
        # Strip all commands from display text using the original pattern
        pattern = r'\[CMD\].*?\[/CMD\]'
        display_text = re.sub(pattern, '', text, flags=re.DOTALL | re.IGNORECASE)
        
        # Use orchestrator's hardened parser to harvest robust command dicts
        commands = extract_cmd_blocks(text)
        return display_text, commands

    def _on_llm_error(self, error_text):
        self._stop_thinking()
        self._remove_last_message()
        self._user_cmds_executed = False          # reset so next turn works
        self._awaiting_strategy_resume = False
        self._awaiting_visual_resume = False
        # Show errors in a distinct warning style
        err_html = (
            f'<div style="background:#2d1a1a;border:1px solid #5a2a2a;'
            f'border-radius:8px;padding:8px 12px;margin:2px 0;">'
            f'<span style="color:#ff6b6b;">⚠️ Error:</span> '
            f'<span style="color:#d0a0a0;">{error_text}</span></div>'
        )
        self._chat_history.append({"role": "assistant", "content": f"Error: {error_text}"})
        self._append_bubble("ai", err_html, is_html=True)

    @Slot(dict)
    def _on_visual_viewer_signal(self, payload):
        """Handle visual-viewer command payloads directly (no CMD text parsing)."""
        self._stop_thinking()
        self._remove_last_message()
        self._awaiting_strategy_resume = False
        # Bug Fix #5: Only set to True if this is an interrupt review request
        self._awaiting_visual_resume = (payload.get("type") == "visual_review")

        cmd_list = []
        if isinstance(payload, dict):
            if isinstance(payload.get("commands"), list):
                cmd_list = [c for c in payload.get("commands", []) if isinstance(c, dict)]
            elif isinstance(payload.get("placement"), list):
                # Backward-compatible path used by existing worker payloads.
                cmd_list = [c for c in payload.get("placement", []) if isinstance(c, dict)]
            elif payload.get("action"):
                cmd_list = [payload]

        if cmd_list:
            print(f"[CHAT] Visual viewer commands: {cmd_list}")
            for cmd in cmd_list:
                self.command_requested.emit(cmd)
            info = f"Applied {len(cmd_list)} visual-review command(s)."
        else:
            info = "Visual review update received (no commands)."

        self._user_cmds_executed = False
        self._chat_history.append({"role": "assistant", "content": info})
        self._append_bubble("ai", info)
        
    def _on_topology_review(self, question):
        """Handler for when Stage 1 completes and asks for confirmation."""
        self._stop_thinking()
        self._remove_last_message()
        self._awaiting_strategy_resume = True
        self._awaiting_visual_resume = False
        
        # Don't reset user cmds here since we are pausing
        self._chat_history.append({"role": "assistant", "content": question})
        self._append_bubble("ai", question)

    def _remove_last_message(self):
        """Remove the last appended message (the thinking bubble)."""
        html = self.chat_display.toHtml()
        idx = html.rfind('<div style="text-align:')
        if idx != -1:
            self.chat_display.setHtml(html[:idx])
