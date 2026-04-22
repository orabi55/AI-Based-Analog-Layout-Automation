from PySide6.QtWidgets import QWidget, QVBoxLayout, QFrame, QLabel
from PySide6.QtCore import Qt, QTimer

class LoadingOverlay(QWidget):
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
        """)
        
        card_layout = QVBoxLayout(self.card)
        card_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.spinner = QLabel("⠋")
        self.spinner.setObjectName("spinner")
        self.spinner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.message_label = QLabel("Loading...")
        self.message_label.setObjectName("message")
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        card_layout.addWidget(self.spinner)
        card_layout.addWidget(self.message_label)
        
        layout.addWidget(self.card)

        self._dots = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self._dot_index = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)

    def _animate(self):
        self._dot_index = (self._dot_index + 1) % len(self._dots)
        self.spinner.setText(self._dots[self._dot_index])

    def show_message(self, text):
        self.message_label.setText(text)
        self._timer.start(100)
        self.show()
        self.raise_()

    def hide_overlay(self):
        self._timer.stop()
        self.hide()
