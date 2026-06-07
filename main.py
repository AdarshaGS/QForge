import sys
import os
import json
import signal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabBar, QStackedWidget, QPushButton, QMessageBox, QProgressDialog,
)
from PySide6.QtGui import QShortcut, QKeySequence

from services.db_service import DbService
from services.query_history import QueryHistory
from ui.connection_dialog import ConnectionDialog
from ui.connection_panel import ConnectionPanel
from ui.theme_manager import ThemeManager
from utils.logger import setup_logger, get_logger

logger = setup_logger()

_SESSION_FILE = os.path.join(
    os.path.expanduser("~"), "Library", "Application Support", "QForge", "session.json"
)


class MainWindow(QMainWindow):
    """
    Top-level window.

    A QTabBar at the top holds one tab per open database connection.
    Each tab is backed by a ConnectionPanel (sidebar + inner tabs).
    Click "+" or press Ctrl+N to add another connection simultaneously.
    """

    def __init__(self):
        super().__init__()

        self._panels: list[ConnectionPanel] = []
        self.query_history = QueryHistory()
        self.current_theme = "dark"

        self.apply_theme()
        self._init_ui()

        # Open first connection (blocks until success or user quits)
        self._prompt_new_connection(allow_cancel_quit=True)

        # Keyboard shortcuts
        QShortcut(QKeySequence("Ctrl+T"), self).activated.connect(
            lambda: self._current_panel() and self._current_panel().add_new_tab()
        )
        QShortcut(QKeySequence("Ctrl+P"), self).activated.connect(
            lambda: self._current_panel() and self._current_panel().show_quick_search()
        )
        QShortcut(QKeySequence("F5"), self).activated.connect(
            lambda: self._current_panel() and self._current_panel().refresh_current_view()
        )
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(
            lambda: self._current_panel() and self._current_panel().refresh_current_view()
        )
        QShortcut(QKeySequence("Ctrl+W"), self).activated.connect(
            self._close_current_content_tab
        )
        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(
            lambda: self._prompt_new_connection()
        )

        self.restore_session()

    # ─── UI ──────────────────────────────────────────────────────────────────

    def _init_ui(self):
        self.setWindowTitle("QForge")
        self.resize(1600, 900)
        self.setMinimumSize(1200, 700)

        self._create_menu_bar()

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Connection tab bar (top row) ──────────────────────────────────────
        tab_row = QWidget()
        tab_row_layout = QHBoxLayout(tab_row)
        tab_row_layout.setContentsMargins(4, 4, 4, 0)
        tab_row_layout.setSpacing(0)

        self.conn_tab_bar = QTabBar()
        self.conn_tab_bar.setTabsClosable(True)
        self.conn_tab_bar.setMovable(True)
        self.conn_tab_bar.tabCloseRequested.connect(self._close_connection_tab)
        self.conn_tab_bar.currentChanged.connect(self._on_connection_tab_changed)
        tab_row_layout.addWidget(self.conn_tab_bar, 1)

        add_conn_btn = QPushButton("+")
        add_conn_btn.setToolTip("Open new connection (Ctrl+N)")
        add_conn_btn.setFixedSize(28, 28)
        add_conn_btn.clicked.connect(self._prompt_new_connection)
        tab_row_layout.addWidget(add_conn_btn)

        root.addWidget(tab_row)

        # ── Stacked panel area ────────────────────────────────────────────────
        self.stack = QStackedWidget()
        root.addWidget(self.stack)

        self.statusBar().hide()

    # ─── Panel helpers ────────────────────────────────────────────────────────

    def _current_panel(self):
        idx = self.conn_tab_bar.currentIndex()
        if 0 <= idx < len(self._panels):
            return self._panels[idx]
        return None

    def _add_panel(self, panel: ConnectionPanel):
        self._panels.append(panel)
        self.stack.addWidget(panel)
        tab_idx = self.conn_tab_bar.addTab(panel.label)
        self.conn_tab_bar.setCurrentIndex(tab_idx)
        self.stack.setCurrentWidget(panel)
        self._update_window_title()

    def _on_connection_tab_changed(self, index: int):
        if 0 <= index < len(self._panels):
            self.stack.setCurrentWidget(self._panels[index])
            self._update_window_title()

    def _update_window_title(self):
        panel = self._current_panel()
        self.setWindowTitle(f"QForge — {panel.label}" if panel else "QForge")

    # ─── Open connection ──────────────────────────────────────────────────────

    def _prompt_new_connection(self, allow_cancel_quit: bool = False):
        while True:
            dialog = ConnectionDialog(auto_connect_last=(len(self._panels) == 0))
            if not dialog.exec():
                if allow_cancel_quit and not self._panels:
                    sys.exit()
                return

            config = dialog.get_selected_connection()
            if not config:
                continue

            try:
                from PySide6.QtCore import QCoreApplication
                progress = QProgressDialog("Connecting…", None, 0, 0)
                progress.setWindowTitle("Connecting")
                progress.setWindowModality(Qt.WindowModal)
                progress.setCancelButton(None)
                progress.setMinimumDuration(0)
                progress.show()
                QCoreApplication.processEvents()

                db_service = DbService()
                db_service.connect(config)

                progress.setLabelText("Loading schema…")
                QCoreApplication.processEvents()

                panel = ConnectionPanel(
                    config=config,
                    db_service=db_service,
                    query_history=self.query_history,
                    parent=self
                )
                panel.update_theme(self.current_theme == "dark")
                progress.close()

                self._add_panel(panel)
                return

            except Exception as ex:
                logger.error(f"Connection failed: {ex}")
                if 'progress' in dir():
                    progress.close()
                QMessageBox.critical(None, "Connection Error", str(ex))

    # ─── Close connection tab ─────────────────────────────────────────────────

    def _close_connection_tab(self, index: int):
        if len(self._panels) == 1:
            QMessageBox.warning(self, "Warning",
                                "Cannot close the last connection.")
            return

        panel = self._panels[index]
        reply = QMessageBox.question(
            self, "Close Connection",
            f"Close connection to '{panel.label}'?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        panel.disconnect()
        self.stack.removeWidget(panel)
        panel.deleteLater()
        self._panels.pop(index)
        self.conn_tab_bar.removeTab(index)

    def _close_current_content_tab(self):
        panel = self._current_panel()
        if panel:
            idx = panel.tabs.currentIndex()
            if idx >= 0:
                panel.tabs.removeTab(idx)

    # ─── Session ─────────────────────────────────────────────────────────────

    def save_session(self):
        data = []
        for panel in self._panels:
            data.append({
                "connection_name": panel.label,
                "config": panel.config,
                "tabs": panel.get_session_tabs()
            })
        try:
            os.makedirs(os.path.dirname(_SESSION_FILE), exist_ok=True)
            with open(_SESSION_FILE, "w") as f:
                json.dump(data, f, indent=2)
            logger.info(f"Session saved: {len(data)} connections")
        except Exception as ex:
            logger.error(f"Failed to save session: {ex}")

    def restore_session(self):
        if not os.path.exists(_SESSION_FILE):
            return
        try:
            with open(_SESSION_FILE) as f:
                data = json.load(f)
        except Exception as ex:
            logger.error(f"Failed to read session: {ex}")
            return

        for entry in data:
            conn_name = entry.get("connection_name", "")
            panel = next((p for p in self._panels if p.label == conn_name), None)
            if panel:
                panel.restore_session_tabs(entry.get("tabs", []))

        logger.info("Session restored")

    def closeEvent(self, event):
        self.save_session()
        event.accept()

    # ─── Menu bar ────────────────────────────────────────────────────────────

    def _create_menu_bar(self):
        menubar = self.menuBar()

        # File
        file_menu = menubar.addMenu("File")

        act = file_menu.addAction("New Query Tab")
        act.setShortcut("Ctrl+T")
        act.triggered.connect(
            lambda: self._current_panel() and self._current_panel().add_new_tab()
        )

        act = file_menu.addAction("Close Tab")
        act.setShortcut("Ctrl+W")
        act.triggered.connect(self._close_current_content_tab)

        file_menu.addSeparator()

        act = file_menu.addAction("New Connection")
        act.setShortcut("Ctrl+N")
        act.triggered.connect(self._prompt_new_connection)

        file_menu.addSeparator()

        act = file_menu.addAction("Export Data…")
        act.setShortcut("Ctrl+E")
        act.triggered.connect(self._export_current_data)

        act = file_menu.addAction("Import Data…")
        act.setShortcut("Ctrl+Shift+I")
        act.triggered.connect(self._import_data)

        file_menu.addSeparator()

        act = file_menu.addAction("Quit")
        act.setShortcut("Ctrl+Q")
        act.triggered.connect(self.close)

        # View
        view_menu = menubar.addMenu("View")

        act = view_menu.addAction("Refresh")
        act.setShortcut("Ctrl+R")
        act.triggered.connect(
            lambda: self._current_panel() and self._current_panel().refresh_current_view()
        )

        view_menu.addSeparator()

        act = view_menu.addAction("Quick Search")
        act.setShortcut("Ctrl+P")
        act.triggered.connect(
            lambda: self._current_panel() and self._current_panel().show_quick_search()
        )

        view_menu.addSeparator()

        self.theme_action = view_menu.addAction("Switch to Light Theme")
        self.theme_action.triggered.connect(self.toggle_theme)

        view_menu.addSeparator()

        act = view_menu.addAction("Zoom In")
        act.setShortcut("Ctrl++")
        act.triggered.connect(lambda: self._zoom(+1))

        act = view_menu.addAction("Zoom Out")
        act.setShortcut("Ctrl+-")
        act.triggered.connect(lambda: self._zoom(-1))

        act = view_menu.addAction("Reset Zoom")
        act.setShortcut("Ctrl+0")
        act.triggered.connect(lambda: self._zoom(0))

        # Help
        help_menu = menubar.addMenu("Help")
        act = help_menu.addAction("Keyboard Shortcuts")
        act.triggered.connect(self._show_shortcuts)

    # ─── Theme ───────────────────────────────────────────────────────────────

    def apply_theme(self):
        if self.current_theme == "dark":
            QApplication.instance().setStyleSheet(ThemeManager.get_dark_theme())
        else:
            QApplication.instance().setStyleSheet(ThemeManager.get_light_theme())
        is_dark = self.current_theme == "dark"
        for panel in self._panels:
            panel.update_theme(is_dark)

    def toggle_theme(self):
        if self.current_theme == "dark":
            self.current_theme = "light"
            self.theme_action.setText("Switch to Dark Theme")
        else:
            self.current_theme = "dark"
            self.theme_action.setText("Switch to Light Theme")
        self.apply_theme()

    # ─── Zoom ────────────────────────────────────────────────────────────────

    def _zoom(self, direction: int):
        panel = self._current_panel()
        if not panel:
            return
        from ui.table_view_widget import TableViewWidget
        current_tab = panel.tabs.currentWidget()
        if hasattr(current_tab, "editor") and current_tab.editor is not None:
            font = current_tab.editor.font()
            size = font.pointSize()
            if direction == 0:
                font.setPointSize(13)
            elif direction > 0 and size < 30:
                font.setPointSize(max(size, 1) + 1)
            elif direction < 0 and size > 8:
                font.setPointSize(size - 1)
            current_tab.editor.setFont(font)
        elif isinstance(current_tab, TableViewWidget) and hasattr(current_tab, "data_table"):
            font = current_tab.data_table.font()
            size = font.pointSize()
            if direction == 0:
                font.setPointSize(13)
            elif direction > 0 and size < 30:
                font.setPointSize(max(size, 1) + 1)
            elif direction < 0 and size > 8:
                font.setPointSize(size - 1)
            current_tab.data_table.setFont(font)

    # ─── File operations ─────────────────────────────────────────────────────

    def _export_current_data(self):
        panel = self._current_panel()
        if not panel:
            return
        from ui.sql_tab import SqlTab
        w = panel.tabs.currentWidget()
        if isinstance(w, SqlTab) and w.current_df is not None:
            w.export_data()
        else:
            QMessageBox.information(self, "Export",
                                    "Open a query tab with results to export.")

    def _import_data(self):
        panel = self._current_panel()
        if not panel:
            return
        from ui.sql_tab import SqlTab
        w = panel.tabs.currentWidget()
        if isinstance(w, SqlTab):
            w.import_data()
        else:
            QMessageBox.information(self, "Import",
                                    "Open a query tab to import data.")

    # ─── Help ────────────────────────────────────────────────────────────────

    def _show_shortcuts(self):
        text = """<b>QForge Keyboard Shortcuts</b>

<b>Connections:</b>
• Ctrl+N  —  Open new connection (adds a tab)

<b>Tabs:</b>
• Ctrl+T  —  New query tab
• Ctrl+W  —  Close current tab

<b>Query:</b>
• Ctrl+Return  —  Run query
• Ctrl+I       —  Beautify SQL
• Ctrl+Space   —  Autocomplete

<b>Navigation:</b>
• Ctrl+P / Cmd+P  —  Quick search tables/views
• Ctrl+R / F5     —  Refresh current view

<b>Application:</b>
• Ctrl+Q  —  Quit"""
        QMessageBox.information(self, "Keyboard Shortcuts", text)


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    logger.info("Starting QForge")
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
