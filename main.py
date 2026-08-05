import sys
import os
import json
import signal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabBar, QStackedWidget, QPushButton, QMessageBox, QProgressDialog,
    QMenu,
)
from PySide6.QtGui import QShortcut, QKeySequence, QColor, QIcon

from services.db_service import DbService
from services.query_history import QueryHistory
from ui.connection_dialog import ConnectionDialog
from ui.connection_panel import ConnectionPanel
from ui.theme_manager import ThemeManager
from utils.logger import setup_logger, get_logger
from utils.updater import UpdateChecker, APP_VERSION
from utils.self_updater import UpdateInstaller, running_app_bundle_path, relaunch
from utils.paths import app_data_dir
from utils import environment

logger = setup_logger()


def _asset_path(name: str) -> str:
    """Resolve a bundled asset both when running from source and when frozen
    by PyInstaller (which extracts/collects data files next to `sys._MEIPASS`)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


_SESSION_FILE = os.path.join(app_data_dir(), "session.json")


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

        # Keyboard shortcuts. Ctrl+T/P/R/N/Q are NOT bound here — each already
        # has an identical-key QAction in the menu bar (_create_menu_bar,
        # below), and a bare QShortcut plus a QAction sharing one key
        # sequence makes Qt treat it as ambiguous. That's what was actually
        # causing issue #25 (Ctrl+T only jumped to a new Space when pressed
        # as a keyboard shortcut, not when the same add_new_tab() was
        # triggered via the "+" button or the menu item) — the same
        # duplicate-binding bug already fixed for Ctrl+W under issue #40.
        # F5 has no menu counterpart, so it's the only one still bound here.
        QShortcut(QKeySequence("F5"), self).activated.connect(
            lambda: self._current_panel() and self._current_panel().refresh_current_view()
        )

        self.restore_session()
        self._start_update_check()

    # ─── Update checker ───────────────────────────────────────────────────────

    def _start_update_check(self):
        self._update_checker = UpdateChecker()
        self._update_checker.update_available.connect(self._on_update_available)
        self._update_checker.start()
        self._release_url = ""
        self._update_tag = ""
        self._dmg_url = ""

    def _on_update_available(self, tag: str, url: str, dmg_url: str):
        self._release_url = url
        self._update_tag = tag
        self._dmg_url = dmg_url
        action = "download & install" if self._can_self_update() else "download"
        self._update_label.setText(
            f"\u2B06  QForge {tag} is available \u2014 click to {action}"
        )
        self._update_banner.show()

    def _can_self_update(self) -> bool:
        """Self-update needs an actual .dmg asset and a running .app bundle
        to replace \u2014 running from source (dev) has neither, so that case
        falls back to opening the release page instead."""
        return bool(self._dmg_url) and running_app_bundle_path() is not None

    def _open_release_url(self):
        if self._release_url:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl(self._release_url))

    def _handle_update_click(self):
        if self._can_self_update():
            self._start_self_update()
        else:
            self._open_release_url()

    def _dismiss_update_banner(self):
        self._update_banner.hide()

    def _start_self_update(self):
        self._dismiss_update_banner()
        self._update_progress = QProgressDialog(
            "Downloading update\u2026", None, 0, 100, self
        )
        self._update_progress.setWindowTitle("Updating QForge")
        self._update_progress.setWindowModality(Qt.WindowModal)
        self._update_progress.setCancelButton(None)
        self._update_progress.setMinimumDuration(0)
        self._update_progress.setValue(0)
        self._update_progress.show()

        self._update_installer = UpdateInstaller(self._update_tag, self._dmg_url, parent=self)
        self._update_installer.progress.connect(self._on_update_progress)
        self._update_installer.failed.connect(self._on_update_failed)
        self._update_installer.ready_to_restart.connect(self._on_update_ready)
        self._update_installer.start()

    def _on_update_progress(self, percent: int):
        self._update_progress.setValue(percent)
        self._update_progress.setLabelText(f"Downloading update\u2026 {percent}%")

    def _on_update_failed(self, message: str):
        self._update_progress.close()
        QMessageBox.critical(
            self, "Update Failed", f"Could not install the update:\n\n{message}"
        )

    def _on_update_ready(self, app_path: str):
        self._update_progress.close()
        reply = QMessageBox.question(
            self, "Update Downloaded",
            "QForge has downloaded and installed the update.\n\n"
            "Restart now to finish?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.save_session()
            relaunch(app_path)

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
        self.conn_tab_bar.setObjectName("conn_tab_bar")
        self.conn_tab_bar.setTabsClosable(True)
        self.conn_tab_bar.setMovable(True)
        self.conn_tab_bar.tabCloseRequested.connect(self._close_connection_tab)
        self.conn_tab_bar.currentChanged.connect(self._on_connection_tab_changed)
        # Right-click context menu on connection tabs
        self.conn_tab_bar.setContextMenuPolicy(Qt.CustomContextMenu)
        self.conn_tab_bar.customContextMenuRequested.connect(self._conn_tab_context_menu)
        tab_row_layout.addWidget(self.conn_tab_bar, 1)

        add_conn_btn = QPushButton("＋")
        add_conn_btn.setToolTip("Open new connection (Ctrl+N)")
        add_conn_btn.setFixedSize(30, 26)
        add_conn_btn.clicked.connect(self._prompt_new_connection)
        add_conn_btn.setStyleSheet("""
            QPushButton {
                background: #2c2c2e;
                color: #e5e5ea;
                border: 1px solid #48484a;
                border-radius: 6px;
                font-size: 16px;
                font-weight: 400;
                padding: 0;
            }
            QPushButton:hover  { background: #3a3a3c; color: #ffffff; border-color: #636366; }
            QPushButton:pressed { background: #1c1c1e; }
        """)
        tab_row_layout.addWidget(add_conn_btn)

        root.addWidget(tab_row)

        # ── Stacked panel area ────────────────────────────────────────────────
        self.stack = QStackedWidget()
        root.addWidget(self.stack)

        # ── Update notification banner (hidden until an update is found) ──────
        self._update_banner = QWidget()
        self._update_banner.setFixedHeight(36)
        self._update_banner.hide()
        self._update_banner.setStyleSheet(
            "background:#1c3a1c; border-bottom:1px solid #2d6a2d;"
        )
        banner_layout = QHBoxLayout(self._update_banner)
        banner_layout.setContentsMargins(12, 0, 8, 0)
        banner_layout.setSpacing(8)

        self._update_label = QPushButton()
        self._update_label.setFlat(True)
        self._update_label.setCursor(Qt.PointingHandCursor)
        self._update_label.setStyleSheet(
            "color:#5cdb5c; font-size:13px; font-weight:600;"
            " text-align:left; border:none; background:transparent;"
        )
        self._update_label.clicked.connect(self._handle_update_click)
        banner_layout.addWidget(self._update_label, 1)

        dismiss_btn = QPushButton("✕")
        dismiss_btn.setFixedSize(22, 22)
        dismiss_btn.setFlat(True)
        dismiss_btn.setCursor(Qt.PointingHandCursor)
        dismiss_btn.setStyleSheet(
            "color:#5cdb5c; font-size:12px; border:none; background:transparent;"
            " QPushButton:hover{color:#ffffff;}"
        )
        dismiss_btn.clicked.connect(self._dismiss_update_banner)
        banner_layout.addWidget(dismiss_btn)

        root.addWidget(self._update_banner)

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
        # Wire health indicator and reconnect toast
        panel.health_changed.connect(
            lambda status, p=panel: self._on_health_changed(p, status)
        )
        panel.reconnected.connect(self._show_reconnect_toast)
        panel.label_changed.connect(self._on_panel_label_changed)
        # Set initial green dot immediately
        self._on_health_changed(panel, 'idle')

    def _on_panel_label_changed(self, panel, new_label: str):
        if panel not in self._panels:
            return
        idx = self._panels.index(panel)
        self.conn_tab_bar.setTabText(idx, new_label)
        if self._current_panel() is panel:
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
            # Always parent to the main window: an unparented dialog is a
            # fully independent top-level window to macOS, so opening one
            # while the main window is in native full-screen kicks the app
            # out to a new desktop/Space instead of staying put (issue #15).
            dialog = ConnectionDialog(auto_connect_last=(len(self._panels) == 0), parent=self)
            if not dialog.exec():
                if allow_cancel_quit and not self._panels:
                    sys.exit()
                return

            config = dialog.get_selected_connection()
            if not config:
                continue

            try:
                from PySide6.QtCore import QCoreApplication
                progress = QProgressDialog("Connecting…", None, 0, 0, self)
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
                # Server version arrives asynchronously via the schema-load
                # result (panel.label_changed) rather than being fetched here —
                # fetching it on this thread would race the schema-loading
                # background thread over the same shared connection.
                progress.close()

                self._add_panel(panel)
                panel.ensure_at_least_one_tab()
                return

            except Exception as ex:
                logger.error(f"Connection failed: {ex}")
                if 'progress' in dir():
                    progress.close()
                QMessageBox.critical(self, "Connection Error", str(ex))

    # ─── Close connection tab ────────────────────────────────────────────────

    def _close_connection_tab(self, index: int):
        self._close_connection_at(index)
        # If we closed the last panel, show connection dialog so app is never empty
        if not self._panels:
            self._prompt_new_connection(allow_cancel_quit=True)

    def _close_connection_at(self, index: int):
        """Close the connection panel at *index* without confirmation."""
        if index < 0 or index >= len(self._panels):
            return
        panel = self._panels[index]
        panel.disconnect()
        self.stack.removeWidget(panel)
        panel.deleteLater()
        self._panels.pop(index)
        self.conn_tab_bar.removeTab(index)

    def _close_other_connections(self, keep_index: int):
        """Close all connection tabs except the one at *keep_index*."""
        if len(self._panels) <= 1:
            return
        for i in reversed(range(len(self._panels))):
            if i != keep_index:
                self._close_connection_at(i)

    def _smart_close(self):
        """Cmd+W: close current content tab; if that was the last tab for
        this connection, close the connection too in the same keypress
        (issue #40) rather than leaving an empty connected workspace that
        needs a second Cmd+W."""
        panel = self._current_panel()
        if not panel:
            return
        if panel.tabs.count() > 0:
            idx = panel.tabs.currentIndex()
            if idx >= 0:
                panel.tabs.removeTab(idx)
            if panel.tabs.count() > 0:
                return
        self._close_connection_tab(self.conn_tab_bar.currentIndex())

    def _conn_tab_context_menu(self, pos):
        """Right-click menu on a connection tab."""
        idx = self.conn_tab_bar.tabAt(pos)
        if idx < 0:
            return
        menu = QMenu(self)
        refresh_schema_act  = menu.addAction("↺  Refresh Schema")
        reconnect_act       = menu.addAction("⟳  Reconnect")
        menu.addSeparator()
        close_act           = menu.addAction("Close Connection")
        close_others_act    = menu.addAction("Close Other Connections")
        close_others_act.setEnabled(len(self._panels) > 1)
        action = menu.exec(self.conn_tab_bar.mapToGlobal(pos))
        if action == refresh_schema_act:
            if 0 <= idx < len(self._panels):
                self._panels[idx].load_schema()
        elif action == reconnect_act:
            if 0 <= idx < len(self._panels):
                self._panels[idx]._do_reconnect()
        elif action == close_act:
            self._close_connection_tab(idx)
        elif action == close_others_act:
            self._close_other_connections(keep_index=idx)

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

        # Restore pinned tabs for all panels (persists across sessions)
        for panel in self._panels:
            panel.restore_pinned_tabs()
            panel.ensure_at_least_one_tab()

        logger.info("Session restored")

    # ─── Connection health indicator ───────────────────────────────────────────────

    _HEALTH_DOT = {'idle': '● ', 'running': '● ', 'disconnected': '● '}
    _HEALTH_COLOR = {
        'idle':         '#30d158',  # green
        'running':      '#ff9f0a',  # amber
        'disconnected': '#ff453a',  # red
    }

    def _tab_text_color(self, panel: ConnectionPanel, status: str) -> str:
        """Idle connections show their environment's semantic colour (the
        safety cue from ai/ui-design.md); an active/disconnected status is
        a more urgent, transient signal and takes priority over it."""
        if status == 'idle':
            env = environment.normalize(panel.config.get("environment"))
            if env != environment.UNCLASSIFIED:
                _, text_color, _ = ThemeManager.env_colors(env, self.current_theme == "dark")
                return text_color
        return self._HEALTH_COLOR.get(status, '#8e8e93')

    def _on_health_changed(self, panel: ConnectionPanel, status: str):
        try:
            idx = self._panels.index(panel)
        except ValueError:
            return
        dot   = self._HEALTH_DOT.get(status, '● ')
        color = self._tab_text_color(panel, status)
        base_label = panel.label
        self.conn_tab_bar.setTabText(idx, dot + base_label)
        self.conn_tab_bar.setTabTextColor(idx, QColor(color))

    # ─── Reconnect toast ───────────────────────────────────────────────────────────

    def _show_reconnect_toast(self, message: str):
        """Briefly show a green banner at the top when a dropped connection recovers."""
        # Re-use the update banner widget (already exists, different style)
        from PySide6.QtCore import QTimer
        self._update_label.setText(f"↺  {message}")
        self._update_banner.setStyleSheet(
            "background:#1c2e1c; border-bottom:1px solid #2d5a2d;"
        )
        self._update_banner.show()
        QTimer.singleShot(4000, self._dismiss_update_banner)

    def _check_for_updates_manual(self):
        """Triggered by Help → Check for Updates. Shows a dialog with result."""
        from PySide6.QtCore import QEventLoop
        checker = UpdateChecker()
        found = {"tag": None, "url": None, "dmg_url": None}

        def _got(tag, url, dmg_url):
            found["tag"] = tag
            found["url"] = url
            found["dmg_url"] = dmg_url

        checker.update_available.connect(_got)
        checker.start()
        checker.wait(10_000)   # max 10 s

        if found["tag"]:
            self._release_url = found["url"]
            self._update_tag = found["tag"]
            self._dmg_url = found["dmg_url"]
            can_self_update = self._can_self_update()
            action = "download & install" if can_self_update else "download"
            self._update_label.setText(
                f"\u2B06  QForge {found['tag']} is available \u2014 click to {action}"
            )
            self._update_banner.show()
            question = (
                "Download and install it now?" if can_self_update
                else "Open the download page?"
            )
            reply = QMessageBox.question(
                self,
                "Update Available",
                f"QForge <b>{found['tag']}</b> is available.\n\n"
                f"You are on <b>v{APP_VERSION}</b>.\n\n{question}",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self._handle_update_click()
        else:
            QMessageBox.information(
                self,
                "No Updates",
                f"You are on the latest version (v{APP_VERSION}).",
            )

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
        act.triggered.connect(self._smart_close)

        file_menu.addSeparator()

        act = file_menu.addAction("New Connection")
        act.setShortcut("Ctrl+N")
        act.triggered.connect(self._prompt_new_connection)

        file_menu.addSeparator()

        act = file_menu.addAction("Export Data…")
        act.setShortcut("Ctrl+E")
        act.triggered.connect(self._export_current_data)

        act = file_menu.addAction("Import Data…")
        act.setShortcut("Ctrl+Shift+E")
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

        # Database
        db_menu = menubar.addMenu("Database")

        act = db_menu.addAction("Create Database…")
        act.triggered.connect(
            lambda: self._current_panel() and self._current_panel().create_database()
        )

        act = db_menu.addAction("Refresh Databases")
        act.triggered.connect(
            lambda: self._current_panel() and self._current_panel().refresh_databases()
        )

        db_menu.addSeparator()

        act = db_menu.addAction("Drop Database…")
        act.triggered.connect(
            lambda: self._current_panel() and self._current_panel().drop_database()
        )

        # Help
        help_menu = menubar.addMenu("Help")
        act = help_menu.addAction("Keyboard Shortcuts")
        act.triggered.connect(self._show_shortcuts)

        help_menu.addSeparator()

        act = help_menu.addAction("Check for Updates…")
        act.triggered.connect(self._check_for_updates_manual)

    # ─── Theme ───────────────────────────────────────────────────────────────

    def apply_theme(self):
        if self.current_theme == "dark":
            QApplication.instance().setStyleSheet(ThemeManager.get_dark_theme())
        else:
            QApplication.instance().setStyleSheet(ThemeManager.get_light_theme())
        is_dark = self.current_theme == "dark"
        for panel in self._panels:
            panel.update_theme(is_dark)
            # Re-derive the tab text colour so an environment-tinted tab
            # (see _tab_text_color) flips to the other theme's tones too.
            self._on_health_changed(panel, getattr(panel, '_last_health', 'idle'))

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
            self._apply_zoom(current_tab.editor, direction)
        elif isinstance(current_tab, TableViewWidget) and hasattr(current_tab, "data_table"):
            self._apply_zoom(current_tab.data_table, direction)

    @staticmethod
    def _apply_zoom(widget, direction: int):
        """Adjust *widget*'s font size relative to its last zoom level.

        Reading QFont.pointSize() back off the widget isn't reliable here:
        once the app-wide theme stylesheet's `font-size` CSS property has
        touched a widget, Qt reports pointSize() as -1 (its sentinel for
        "size was set in pixels"), regardless of what setFont() was last
        called with. That made every zoom action compute its step from -1
        instead of the actual current size — collapsing the font to ~2pt
        on the very first Zoom In (issue #11). Track the size ourselves
        instead of trusting the widget to report it back accurately.
        """
        size = getattr(widget, "_qforge_zoom_pt", 13)
        if direction == 0:
            size = 13
        elif direction > 0:
            size = min(size + 1, 30)
        elif direction < 0:
            size = max(size - 1, 8)
        widget._qforge_zoom_pt = size
        font = widget.font()
        font.setPointSize(size)
        widget.setFont(font)

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
    app.setWindowIcon(QIcon(_asset_path("logo.png")))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
