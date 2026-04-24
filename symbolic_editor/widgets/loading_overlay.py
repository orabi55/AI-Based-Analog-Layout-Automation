from PySide6.QtWidgets import QWidget, QVBoxLayout, QFrame, QLabel, QPushButton
from PySide6.QtCore import Qt, QTimer, Signal

class LoadingOverlay(QWidget):
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background-color: rgba(20, 24, 34, 180);")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.card = QFrame()
        self.card.setStyleSheet("""
            QFrame {
                background-color: #1e2636;
                border: 1px solid #3d5066;
                border-radius: 12px;
                padding: 30px;
            }
            QLabel#spinner {
                font-size: 32px;
                color: #4a90d9;
            }
            QLabel#message {
                font-size: 14px;
                font-family: 'Segoe UI';
                color: #e0e8f0;
                margin-top: 10px;
            }
            QPushButton#cancelBtn {
                background-color: #3d5066;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 20px;
                margin-top: 15px;
                font-weight: bold;
            }
            QPushButton#cancelBtn:hover {
                background-color: #e74c3c;
            }
        """)
        
        card_layout = QVBoxLayout(self.card)
        card_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.spinner = QLabel("⠋")
        self.spinner.setObjectName("spinner")
        self.spinner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.message_label = QLabel("Loading...")
        self.message_label.setObjectName("message")
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("cancelBtn")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self.cancel_requested.emit)
        
        card_layout.addWidget(self.spinner)
        card_layout.addWidget(self.message_label)
        card_layout.addWidget(self.cancel_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        
        layout.addWidget(self.card)

        self._dots = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self._dot_index = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)

    def _animate(self):
        self._dot_index = (self._dot_index + 1) % len(self._dots)
        self.spinner.setText(self._dots[self._dot_index])

    def show_message(self, text, show_cancel=False):
        self.message_label.setText(text)
        self.cancel_btn.setVisible(show_cancel)
        self._timer.start(100)
        self.show()
        self.raise_()

    def hide_overlay(self):
        self._timer.stop()
        self.hide()
