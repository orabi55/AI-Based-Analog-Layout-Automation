# -*- coding: utf-8 -*-
"""
Design Tabs — browser/VS Code-style tab bar for managing
multiple open design files.

Each tab represents a separate loaded JSON file.
Home tab shows a "Recent Designs" dashboard.
"""

import os
import json
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel,
    QFrame, QMenu, QScrollArea, QFileDialog, QToolButton,
    QSizePolicy, QGridLayout,
)
from PySide6.QtCore import Qt, Signal, QSize, QSettings
from PySide6.QtGui import QFont, QColor, QIcon, QKeySequence, QShortcut


class DesignTab(QPushButton):
    """Individual tab button with close button."""

    close_requested = Signal(int)  # tab index
    activated = Signal(int)  # tab index

    def __init__(self, title, file_path="", index=0, parent=None):
        super().__init__(parent)
        self.title = title
        self.file_path = file_path
        self.index = index
        self._unsaved = False
        self._is_active = False

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(32)
        self.setToolTip(file_path or title)
        self.clicked.connect(lambda: self.activated.emit(self.index))

        self._update_style()

    def set_active(self, active):
        self._is_active = active
        self._update_style()

    def set_unsaved(self, unsaved):
        self._unsaved = unsaved
        self._update_text()

    def _update_text(self):
        dot = " ●" if self._unsaved else ""
        self.setText(f"  {self.title}{dot}  ✕  ")

    def _update_style(self):
        self._update_text()
        if self._is_active:
            self.setStyleSheet("""
                QPushButton {
                    background-color: #1a1f2b;
                    color: #e0e8f0;
                    border: none;
                    border-bottom: 2px solid #4a9eff;
                    border-top-left-radius: 6px;
                    border-top-right-radius: 6px;
                    padding: 4px 8px;
                    font-family: 'Segoe UI';
                    font-size: 9pt;
                    font-weight: 600;
                    text-align: left;
                }
            """)
        else:
            self.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    color: #7b8a9c;
                    border: none;
                    border-bottom: 2px solid transparent;
                    border-top-left-radius: 6px;
                    border-top-right-radius: 6px;
                    padding: 4px 8px;
                    font-family: 'Segoe UI';
                    font-size: 9pt;
                    text-align: left;
                }
                QPushButton:hover {
                    background-color: rgba(255,255,255,0.05);
                    color: #c8d0dc;
                }
            """)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self.close_requested.emit(self.index)
            return
        # Check if close button area was clicked (rightmost 20px)
        if event.button() == Qt.MouseButton.LeftButton:
            text_width = self.fontMetrics().horizontalAdvance(self.text())
            close_area_start = min(text_width - 15, self.width() - 25)
            if event.pos().x() > close_area_start:
                self.close_requested.emit(self.index)
                return
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1e2636;
                border: 1px solid #3d5066;
                border-radius: 6px;
                padding: 4px;
                font-family: 'Segoe UI';
                font-size: 9pt;
                color: #c8d0dc;
            }
            QMenu::item {
                padding: 6px 20px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #4a90d9;
                color: #ffffff;
            }
        """)
        act_close = menu.addAction("Close")
        act_close_others = menu.addAction("Close Others")
        act_close_right = menu.addAction("Close to the Right")
        menu.addSeparator()
        act_close_all = menu.addAction("Close All")
        menu.addSeparator()
        act_path = menu.addAction("Copy Path")

        action = menu.exec(event.globalPos())
        if action == act_close:
            self.close_requested.emit(self.index)
        elif action == act_close_others:
            parent = self.parent()
            if hasattr(parent, '_close_others'):
                parent._close_others(self.index)
        elif action == act_close_right:
            parent = self.parent()
            if hasattr(parent, '_close_to_right'):
                parent._close_to_right(self.index)
        elif action == act_close_all:
            parent = self.parent()
            if hasattr(parent, '_close_all_tabs'):
                parent._close_all_tabs()
        elif action == act_path:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(self.file_path)


class RecentDesignsDashboard(QWidget):
    """Home tab content showing recent designs."""

    open_design = Signal(str)  # file_path

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()
        self._load_recent()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(20)

        # Title
        title = QLabel("Recent Designs")
        title.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        title.setStyleSheet("color: #e0e8f0;")
        layout.addWidget(title)

        subtitle = QLabel("Open a recent design or create a new one")
        subtitle.setStyleSheet("color: #7b8a9c; font-family: 'Segoe UI'; font-size: 11pt;")
        layout.addWidget(subtitle)

        # Open button
        btn_row = QHBoxLayout()
        open_btn = QPushButton("Open Design...")
        open_btn.setFixedSize(160, 36)
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.setStyleSheet("""
            QPushButton {
                background-color: #4a9eff;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                font-family: 'Segoe UI';
                font-size: 10pt;
                font-weight: 600;
            }
            QPushButton:hover { background-color: #5dafff; }
        """)
        open_btn.clicked.connect(self._on_open_click)
        btn_row.addWidget(open_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Recent files list
        self._files_layout = QVBoxLayout()
        self._files_layout.setSpacing(4)
        layout.addLayout(self._files_layout)
        layout.addStretch()

    def _load_recent(self):
        settings = QSettings("SymbolicEditor", "RecentFiles")
        recent = settings.value("files", []) or []
        if isinstance(recent, str):
            recent = [recent]

        for fpath in recent[:10]:
            if not os.path.isfile(fpath):
                continue
            self._add_file_row(fpath)

    def _add_file_row(self, fpath):
        row = QPushButton()
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.setFixedHeight(50)

        name = os.path.splitext(os.path.basename(fpath))[0]
        folder = os.path.dirname(fpath)
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(fpath)).strftime("%b %d, %Y %H:%M")
        except Exception:
            mtime = "—"

        row.setText(f"  {name}\n  {folder}  •  {mtime}")
        row.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #c8d0dc;
                border: 1px solid #1a1f2b;
                border-radius: 8px;
                text-align: left;
                padding: 8px 14px;
                font-family: 'Segoe UI';
                font-size: 9pt;
            }
            QPushButton:hover {
                background-color: #1a2230;
                border-color: #2d3548;
            }
        """)
        row.clicked.connect(lambda _, p=fpath: self.open_design.emit(p))
        self._files_layout.addWidget(row)

    def _on_open_click(self):
        fpath, _ = QFileDialog.getOpenFileName(
            self, "Open Placement JSON", "", "JSON Files (*.json)"
        )
        if fpath:
            self.open_design.emit(fpath)

    @staticmethod
    def add_recent(file_path):
        """Add a file to recent list."""
        settings = QSettings("SymbolicEditor", "RecentFiles")
        recent = settings.value("files", []) or []
        if isinstance(recent, str):
            recent = [recent]
        if file_path in recent:
            recent.remove(file_path)
        recent.insert(0, file_path)
        settings.setValue("files", recent[:20])


class DesignTabBar(QWidget):
    """Tab bar for managing multiple design files."""

    tab_selected = Signal(int)     # index
    tab_closed = Signal(int)       # index
    tab_added = Signal()           # new tab requested
    home_clicked = Signal()        # home button

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(36)
        self.setStyleSheet(
            "background-color: #12161f; border-bottom: 1px solid #2d3548;"
        )

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(4, 0, 4, 0)
        self._layout.setSpacing(1)

        # Home button
        self._home_btn = QPushButton("⌂")
        self._home_btn.setFixedSize(32, 28)
        self._home_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._home_btn.setToolTip("Home — Recent Designs")
        self._home_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #7b8a9c;
                border: none;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(255,255,255,0.08);
                color: #e0e8f0;
            }
            QPushButton:checked {
                color: #4a9eff;
            }
        """)
        self._home_btn.setCheckable(True)
        self._home_btn.setChecked(True)
        self._home_btn.clicked.connect(self._on_home_clicked)
        self._layout.addWidget(self._home_btn)

        # Separator
        sep = QFrame()
        sep.setFixedSize(1, 20)
        sep.setStyleSheet("background-color: #2d3548;")
        self._layout.addWidget(sep)

        # Tab container
        self._tabs_container = QWidget()
        self._tabs_layout = QHBoxLayout(self._tabs_container)
        self._tabs_layout.setContentsMargins(0, 0, 0, 0)
        self._tabs_layout.setSpacing(1)
        self._layout.addWidget(self._tabs_container)

        # Add tab button
        self._add_btn = QPushButton("+")
        self._add_btn.setFixedSize(28, 28)
        self._add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_btn.setToolTip("Open Design (Ctrl+O)")
        self._add_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #7b8a9c;
                border: none;
                border-radius: 6px;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: rgba(255,255,255,0.08);
                color: #e0e8f0;
            }
        """)
        self._add_btn.clicked.connect(self.tab_added.emit)
        self._layout.addWidget(self._add_btn)

        self._layout.addStretch()

        # Overflow button (hidden by default)
        self._overflow_btn = QPushButton("···")
        self._overflow_btn.setFixedSize(32, 28)
        self._overflow_btn.setVisible(False)
        self._overflow_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #7b8a9c;
                border: none;
                border-radius: 6px;
                font-size: 12px;
            }
            QPushButton:hover { color: #e0e8f0; }
        """)
        self._layout.addWidget(self._overflow_btn)

        self._tabs: list[DesignTab] = []
        self._active_index = -1  # -1 = home

    # ── Public API ─────────────────────────────────────────────
    def add_tab(self, title, file_path=""):
        """Add a new design tab."""
        index = len(self._tabs)
        tab = DesignTab(title, file_path, index, self._tabs_container)
        tab.activated.connect(self._on_tab_activated)
        tab.close_requested.connect(self._on_tab_close)
        self._tabs.append(tab)
        self._tabs_layout.addWidget(tab)
        self.set_active_tab(index)
        return index

    def remove_tab(self, index):
        """Remove a tab by index."""
        if 0 <= index < len(self._tabs):
            tab = self._tabs.pop(index)
            self._tabs_layout.removeWidget(tab)
            tab.deleteLater()
            # Re-index remaining tabs
            for i, t in enumerate(self._tabs):
                t.index = i
            # Select adjacent tab or home
            if self._active_index == index:
                if self._tabs:
                    new_idx = min(index, len(self._tabs) - 1)
                    self.set_active_tab(new_idx)
                else:
                    self._active_index = -1
                    self._home_btn.setChecked(True)
                    self.home_clicked.emit()

    def set_active_tab(self, index):
        """Activate a tab by index."""
        self._active_index = index
        self._home_btn.setChecked(False)
        for i, tab in enumerate(self._tabs):
            tab.set_active(i == index)
        self.tab_selected.emit(index)

    def set_tab_unsaved(self, index, unsaved):
        """Toggle the unsaved indicator on a tab."""
        if 0 <= index < len(self._tabs):
            self._tabs[index].set_unsaved(unsaved)

    def tab_count(self):
        return len(self._tabs)

    def setup_shortcuts(self, parent):
        """Register Ctrl+Tab, Ctrl+Shift+Tab, Ctrl+W."""
        sc_next = QShortcut(QKeySequence("Ctrl+Tab"), parent)
        sc_next.activated.connect(self._cycle_next)

        sc_prev = QShortcut(QKeySequence("Ctrl+Shift+Tab"), parent)
        sc_prev.activated.connect(self._cycle_prev)

        sc_close = QShortcut(QKeySequence("Ctrl+W"), parent)
        sc_close.activated.connect(self._close_current)

    # ── Private ────────────────────────────────────────────────
    def _on_home_clicked(self):
        self._active_index = -1
        self._home_btn.setChecked(True)
        for tab in self._tabs:
            tab.set_active(False)
        self.home_clicked.emit()

    def _on_tab_activated(self, index):
        self.set_active_tab(index)

    def _on_tab_close(self, index):
        self.tab_closed.emit(index)

    def _cycle_next(self):
        if not self._tabs:
            return
        new_idx = (self._active_index + 1) % len(self._tabs)
        self.set_active_tab(new_idx)

    def _cycle_prev(self):
        if not self._tabs:
            return
        new_idx = (self._active_index - 1) % len(self._tabs)
        self.set_active_tab(new_idx)

    def _close_current(self):
        if self._active_index >= 0:
            self.tab_closed.emit(self._active_index)

    def _close_others(self, keep_index):
        indices_to_close = [i for i in range(len(self._tabs)) if i != keep_index]
        for idx in reversed(indices_to_close):
            self.tab_closed.emit(idx)

    def _close_to_right(self, from_index):
        indices_to_close = [i for i in range(from_index + 1, len(self._tabs))]
        for idx in reversed(indices_to_close):
            self.tab_closed.emit(idx)

    def _close_all_tabs(self):
        for idx in reversed(range(len(self._tabs))):
            self.tab_closed.emit(idx)
