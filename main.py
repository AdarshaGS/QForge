import sys
import os
import json
import signal
import time

from PySide6.QtCore import Qt, QSize, QEvent
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabBar, QStackedWidget, QPushButton, QMessageBox, QProgressDialog,
    QMenu, QDialog, QDialogButtonBox, QTextBrowser, QLabel,
)
from PySide6.QtGui import QShortcut, QKeySequence, QColor, QIcon, QAction

from services.db_service import DbService
from services.query_history import QueryHistory
from services.saved_queries import SavedQueries
from services.entitlements import Edition, Feature, entitlements
from services.license_manager import license_manager
from ui.command_palette import show_command_palette
from ui.connection_dialog import ConnectionDialog
from ui.connection_panel import ConnectionPanel
from ui.quick_search_dialog import QuickSearchDialog
from ui.license_dialog import LicenseActionWorker, LicenseDialog
from ui.theme_manager import ThemeManager
from ui.perf_overlay import PerfOverlayWidget
from utils.logger import setup_logger, get_logger
from utils.updater import UpdateChecker, APP_VERSION
from utils.self_updater import UpdateInstaller, running_app_bundle_path, relaunch
from utils.entitlement_fetcher import EntitlementConfigFetcher
from utils.homebrew_updater import HomebrewUpdateInstaller
from utils import install_source
from utils.paths import app_data_dir
from utils import environment
from utils import schema_cache
from utils import perf_metrics

logger = setup_logger()


def _asset_path(name: str) -> str:
    """Resolve a bundled asset both when running from source and when frozen
    by PyInstaller (which extracts/collects data files next to `sys._MEIPASS`)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


def _is_remote_connection(config: dict) -> bool:
    """True for connections where establishing db_service.connect() is slow
    enough (SSH tunnel setup, WAN round-trip) that blocking the UI behind a
    modal "Connecting…" dialog is actually felt — as opposed to local MySQL/
    Postgres, which connect in well under human-perceptible time."""
    if config.get("ssh_tunnel", {}).get("enabled"):
        return True
    return config.get("host", "") not in _LOOPBACK_HOSTS


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

        # Startup-stage timing (issue #59) — read by benchmarks/bench_startup.py
        # and shown live in the dev overlay (issue #43, ui/perf_overlay.py).
        # Process start itself (interpreter launch, `import` of this module)
        # happens before this constructor runs at all, so it isn't a stage
        # here — only stages reachable from inside MainWindow are timed.
        _startup_t0 = time.perf_counter()
        perf_metrics.start_suspend_watchdog()

        self._panels: list[ConnectionPanel] = []
        self.query_history = QueryHistory()
        self.saved_queries = SavedQueries()
        self.current_theme = "dark"

        self.apply_theme()
        self._init_ui()
        perf_metrics.record("startup", "window_created", (time.perf_counter() - _startup_t0) * 1000)

        # Open first connection (blocks until success or user quits).
        # _prompt_new_connection accumulates the modal dialog's own exec()
        # time into this — see its comment (issue #173).
        self._dialog_wait_ms = 0.0
        self._prompt_new_connection(allow_cancel_quit=True)
        _elapsed_ms = (time.perf_counter() - _startup_t0) * 1000
        perf_metrics.record("startup", "dialog_wait", self._dialog_wait_ms)
        perf_metrics.record("startup", "connection_manager_ready", _elapsed_ms - self._dialog_wait_ms)

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

        # Developer performance overlay (issue #43) — hidden from normal
        # users on purpose: no menu item, not in the shortcuts-help dialog.
        # Ctrl+Shift+Alt+P toggles it; QFORGE_DEV_OVERLAY=1 auto-shows it.
        self._perf_overlay = PerfOverlayWidget(self)
        QShortcut(QKeySequence("Ctrl+Shift+Alt+P"), self).activated.connect(self._perf_overlay.toggle)
        if os.environ.get("QFORGE_DEV_OVERLAY") == "1":
            self._perf_overlay.toggle()

        self.restore_session()
        perf_metrics.record(
            "startup", "ui_interactive",
            (time.perf_counter() - _startup_t0) * 1000 - self._dialog_wait_ms,
        )
        self._start_update_check()
        self._start_entitlement_config_check()
        self._start_license_revalidation()

        # TEMP DIAGNOSTIC (space-switch investigation, issue #25 follow-up):
        # log every window-state/activation transition and every app-wide
        # active/inactive transition, so a "+"-click that slides the
        # fullscreen window out to another Space shows up as a
        # [SPACE-DEBUG] line bracketing whichever log lines the click
        # itself produced. Remove once the trigger is confirmed/fixed.
        QApplication.instance().applicationStateChanged.connect(self._log_app_state_change)

    def _log_win_state(self, tag: str):
        """[SPACE-DEBUG] snapshot of this window's state, for correlating
        against the [SPACE-DEBUG] transition lines logged by changeEvent()
        and _log_app_state_change() below."""
        logger.info(
            f"[SPACE-DEBUG] {tag}: t={time.perf_counter():.4f} "
            f"isFullScreen={self.isFullScreen()} isActiveWindow={self.isActiveWindow()} "
            f"isVisible={self.isVisible()} windowState={self.windowState()!r}"
        )

    def _log_app_state_change(self, state):
        logger.info(f"[SPACE-DEBUG] applicationStateChanged: t={time.perf_counter():.4f} state={state!r}")

    def changeEvent(self, event):
        if event.type() == QEvent.WindowStateChange:
            logger.info(
                f"[SPACE-DEBUG] changeEvent(WindowStateChange): t={time.perf_counter():.4f} "
                f"oldState={event.oldState()!r} newState={self.windowState()!r} "
                f"isFullScreen={self.isFullScreen()}"
            )
        elif event.type() == QEvent.ActivationChange:
            logger.info(
                f"[SPACE-DEBUG] changeEvent(ActivationChange): t={time.perf_counter():.4f} "
                f"isActiveWindow={self.isActiveWindow()} isFullScreen={self.isFullScreen()}"
            )
        super().changeEvent(event)

    # ─── License revalidation (offline grace period) ───────────────────────────

    def _start_license_revalidation(self):
        """Background, best-effort re-check with the licensing service —
        at most once per launch, only for locally-Pro installs (Free has
        nothing to revalidate). See LicenseManager.revalidate_online() for
        the grace-period/revocation contract; a stale or unreachable
        server just leaves the existing grace period running."""
        if entitlements.edition() is not Edition.PRO:
            return
        self._license_revalidation_worker = LicenseActionWorker(license_manager.revalidate_online)
        self._license_revalidation_worker.finished_with_result.connect(lambda _: self._refresh_pro_menu_labels())
        self._license_revalidation_worker.start()

    # ─── Entitlement config (Free/Pro limits, live-overridable) ────────────────

    def _start_entitlement_config_check(self):
        """Background, best-effort fetch of the remote entitlement override
        (services/entitlement_config.py). Never blocks startup and never
        required — offline installs simply keep the bundled/cached values."""
        self._entitlement_fetcher = EntitlementConfigFetcher()
        self._entitlement_fetcher.config_loaded.connect(self._on_entitlement_config_loaded)
        self._entitlement_fetcher.start()

    def _on_entitlement_config_loaded(self, raw: dict):
        entitlements.apply_remote_config(raw)
        self._refresh_pro_menu_labels()

    # ─── Update checker ───────────────────────────────────────────────────────

    def _start_update_check(self):
        self._update_checker = UpdateChecker()
        self._update_checker.update_available.connect(self._on_update_available)
        self._update_checker.start()
        self._release_url = ""
        self._update_tag = ""
        self._dmg_url = ""
        self._install_source = None

    def _on_update_available(self, tag: str, url: str, dmg_url: str):
        self._release_url = url
        self._update_tag = tag
        self._dmg_url = dmg_url
        self._update_label.setText(
            f"\u2B06  QForge {tag} is available \u2014 click to {self._update_action_text()}"
        )
        self._update_banner.show()

    def _detect_install_source(self) -> str:
        """Detected once per session and cached (issue #79) \u2014 a local
        `brew list` call, not worth re-running on every click."""
        if self._install_source is None:
            self._install_source = install_source.detect()
        return self._install_source

    def _can_self_update(self) -> bool:
        """Self-update needs an actual .dmg asset and a running .app bundle
        to replace \u2014 running from source (dev) has neither, so that case
        falls back to opening the release page instead."""
        return bool(self._dmg_url) and running_app_bundle_path() is not None

    def _update_action_text(self) -> str:
        """What clicking Update will actually do, for the banner/dialog
        text \u2014 a Homebrew-managed install is upgraded through Homebrew,
        never by replacing the bundle directly (issue #79)."""
        if running_app_bundle_path() is None:
            return "download"
        source = self._detect_install_source()
        if source == install_source.HOMEBREW:
            return "update via Homebrew"
        if source == install_source.UNKNOWN:
            return "see update instructions"
        return "download & install" if self._dmg_url else "download"

    def _open_release_url(self):
        if self._release_url:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl(self._release_url))

    def _handle_update_click(self):
        if running_app_bundle_path() is None:
            self._open_release_url()  # dev/source run \u2014 no bundle to update
            return

        source = self._detect_install_source()
        if source == install_source.HOMEBREW:
            self._start_homebrew_update()
        elif source == install_source.UNKNOWN:
            QMessageBox.warning(
                self, "Unknown Installation",
                "QForge could not determine how it was installed.\n\n"
                "Please update QForge using your original installation "
                "method, or run:\n\n    brew upgrade --cask qforge\n\n"
                f"or download the latest release:\n{self._release_url}",
            )
        elif self._can_self_update():
            self._start_self_update()
        else:
            self._open_release_url()

    def _dismiss_update_banner(self):
        self._update_banner.hide()

    def _start_homebrew_update(self):
        self._dismiss_update_banner()
        brew = install_source.brew_path()
        if not brew:
            QMessageBox.warning(
                self, "Homebrew Not Found",
                "QForge is managed by Homebrew, but the `brew` command "
                "could not be located.\n\nRun this in a terminal instead:\n\n"
                "    brew upgrade --cask qforge",
            )
            return

        self._update_progress = QProgressDialog(
            "Updating via Homebrew\u2026", None, 0, 0, self
        )
        self._update_progress.setWindowTitle("Updating QForge")
        self._update_progress.setWindowModality(Qt.WindowModal)
        self._update_progress.setCancelButton(None)
        self._update_progress.setMinimumDuration(0)
        self._update_progress.show()

        self._homebrew_installer = HomebrewUpdateInstaller(brew, parent=self)
        self._homebrew_installer.done.connect(self._on_homebrew_update_done)
        self._homebrew_installer.failed.connect(self._on_homebrew_update_failed)
        self._homebrew_installer.start()

    def _on_homebrew_update_done(self):
        self._update_progress.close()
        reply = QMessageBox.question(
            self, "Update Complete",
            "Homebrew has upgraded QForge.\n\nRestart now to finish?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.save_session()
            relaunch(running_app_bundle_path())

    def _on_homebrew_update_failed(self, message: str):
        self._update_progress.close()
        QMessageBox.critical(
            self, "Update Failed", f"Homebrew upgrade failed:\n\n{message}"
        )

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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        overlay = getattr(self, "_perf_overlay", None)
        if overlay is not None and overlay.isVisible():
            overlay.reposition()

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
        self.conn_tab_bar.setElideMode(Qt.ElideRight)
        self.conn_tab_bar.setUsesScrollButtons(True)
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
            "QPushButton{color:#5cdb5c; font-size:14px; font-weight:600;"
            " border:none; background:transparent; padding:0;}"
            "QPushButton:hover{color:#ffffff;}"
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

    # ─── Cross-connection Quick Search (issue #243) ────────────────────────────

    def show_quick_search(self):
        """With one connection open, defer entirely to its own Quick Search
        (unchanged UX). With several, default to searching just the active
        connection (issue #266 — results from unrelated DBs otherwise
        pollute the list); a checkbox in the dialog lets the user
        explicitly broaden to all open connections at once, each result
        then tagged with which connection it came from. Selecting a result
        switches to its connection's tab before acting on it."""
        if len(self._panels) <= 1:
            if self._panels:
                self._panels[0].show_quick_search()
            return

        active_idx = self.conn_tab_bar.currentIndex()
        if not (0 <= active_idx < len(self._panels)):
            active_idx = 0
        sources = [panel.label for panel in self._panels]

        active_panel = self._panels[active_idx]
        scoped_items = [(t, d, p, active_idx) for t, d, p in active_panel._gather_quick_search_items()]
        scoped_columns = [(t, d, p, active_idx) for t, d, p in active_panel._gather_column_items()]

        all_items = []
        column_items = []
        for idx, panel in enumerate(self._panels):
            all_items += [(t, d, p, idx) for t, d, p in panel._gather_quick_search_items()]
            column_items += [(t, d, p, idx) for t, d, p in panel._gather_column_items()]

        if not scoped_items and not scoped_columns and not all_items and not column_items:
            QMessageBox.information(self, "No Items", "Nothing to search yet")
            return

        dialog = QuickSearchDialog(
            scoped_items, self, column_items=scoped_columns, sources=sources,
            broaden_items=all_items, broaden_column_items=column_items,
        )
        dialog.item_selected.connect(self._on_quick_search_cross_panel)
        dialog.exec()

    def _on_quick_search_cross_panel(self, item_type, display_text, payload, source_idx):
        if not (0 <= source_idx < len(self._panels)):
            return
        self.conn_tab_bar.setCurrentIndex(source_idx)
        self._panels[source_idx]._on_quick_search(item_type, display_text, payload)

    # ─── Open connection ──────────────────────────────────────────────────────

    def _prompt_new_connection(self, allow_cancel_quit: bool = False):
        while True:
            # Always parent to the main window: an unparented dialog is a
            # fully independent top-level window to macOS, so opening one
            # while the main window is in native full-screen kicks the app
            # out to a new desktop/Space instead of staying put (issue #15).
            dialog = ConnectionDialog(auto_connect_last=(len(self._panels) == 0), parent=self)
            # dialog.exec()'s blocking modal loop is the user picking a
            # connection and clicking Connect — real human think-time, not
            # app overhead. Accumulated (the while loop can re-prompt on an
            # invalid/empty selection) and subtracted back out of the
            # startup-stage timings in __init__ (issue #173) so "app took
            # 5 seconds to start" isn't actually "you took 5 seconds to
            # click a connection."
            _dialog_t0 = time.perf_counter()
            accepted = dialog.exec()
            self._dialog_wait_ms += (time.perf_counter() - _dialog_t0) * 1000
            if not accepted:
                if allow_cancel_quit and not self._panels:
                    sys.exit()
                return

            config = dialog.get_selected_connection()
            if not config:
                continue

            # A previously-visited remote/SSH connection with a warm schema
            # cache: skip the blocking "Connecting…" modal entirely and open
            # the panel straight away showing cached schema/autocomplete —
            # db_service.connect() (TCP + auth + SSH tunnel, the actual
            # source of the lag) runs on a background thread inside the
            # panel itself instead (ConnectionPanel._connect_in_background).
            # Local MySQL/Postgres connect fast enough that this
            # wouldn't be felt, so they keep the simpler blocking path.
            optimistic = (
                _is_remote_connection(config)
                and schema_cache.load(config.get("id", ""), config.get("database", "")) is not None
            )

            try:
                db_service = DbService()

                if optimistic:
                    panel = ConnectionPanel(
                        config=config,
                        db_service=db_service,
                        query_history=self.query_history,
                        saved_queries=self.saved_queries,
                        parent=self,
                        already_connected=False,
                    )
                else:
                    from PySide6.QtCore import QCoreApplication
                    progress = QProgressDialog("Connecting…", None, 0, 0, self)
                    progress.setWindowTitle("Connecting")
                    progress.setWindowModality(Qt.WindowModal)
                    progress.setCancelButton(None)
                    progress.setMinimumDuration(0)
                    progress.show()
                    QCoreApplication.processEvents()

                    db_service.connect(config)

                    progress.setLabelText("Loading schema…")
                    QCoreApplication.processEvents()

                    panel = ConnectionPanel(
                        config=config,
                        db_service=db_service,
                        query_history=self.query_history,
                        saved_queries=self.saved_queries,
                        parent=self,
                    )
                    progress.close()

                panel.update_theme(self.current_theme == "dark")
                # Server version arrives asynchronously via the schema-load
                # result (panel.label_changed) rather than being fetched here —
                # fetching it on this thread would race the schema-loading
                # background thread over the same shared connection.

                # ConnectionDialog just closed via dialog.exec() — on macOS
                # that leaves no window "active" at the OS level (verified:
                # QApplication.focusWidget() is None afterward and stays
                # None), so the editor.setFocus() inside ensure_at_least_
                # one_tab()/add_new_tab() below is a no-op until something
                # reclaims window activation explicitly.
                self.activateWindow()
                self.raise_()
                # Realize this panel's first tab BEFORE _add_panel below
                # ever makes it part of the visible window (i.e. while
                # `panel` is still just an unshown QWidget parented to
                # `self`, not yet a page of `self.stack`). Opening a new
                # connection can happen at any point in the app's life,
                # including while the main window is already in native
                # full screen — unlike every other panel's first tab,
                # which is always realized at startup/session-restore,
                # before full screen is reachable at all (see
                # ConnectionPanel.ensure_at_least_one_tab). Populating the
                # tab while off-screen means _add_panel reveals an
                # already-fully-populated panel in one shot, instead of
                # showing an empty panel and then adding brand-new native
                # tab content to it live — the latter is what slides an
                # already-full-screen window out to reveal another Space
                # (issue #25).
                self._log_win_state("_prompt_new_connection: before ensure_at_least_one_tab")
                panel.ensure_at_least_one_tab()
                self._log_win_state("_prompt_new_connection: after ensure_at_least_one_tab")
                self._add_panel(panel)
                self._log_win_state("_prompt_new_connection: after _add_panel")
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
        """Close the connection panel at *index*. No confirmation unless
        one of its tabs has an open transaction (Slice 4, ai/load-
        context.md) — closing would silently roll it back otherwise."""
        if index < 0 or index >= len(self._panels):
            return
        panel = self._panels[index]
        if panel.has_open_transactions():
            reply = QMessageBox.question(
                self, "Open Transaction",
                f"'{panel.config.get('name', 'This connection')}' has an "
                "open transaction — closing it will roll back any "
                "uncommitted changes.\n\nClose anyway?",
                QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
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
            count_before = panel.tabs.count()
            if idx >= 0:
                # Routes through ConnectionPanel._close_tab rather than
                # tabs.removeTab() directly, so the open-transaction warning
                # applies here too (Slice 4, ai/load-context.md).
                panel._close_tab(idx)
            if panel.tabs.count() == count_before:
                return  # user cancelled the close (open-transaction warning)
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
                self._panels[idx].load_schema(notify=True)
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
            # Restoring N tabs in a loop leaves the last-restored one
            # active/focused (issue #149) — reset to tab 1 once restoration
            # for this panel is fully done.
            panel.focus_first_tab()

        logger.info("Session restored")

    # ─── Connection health indicator ───────────────────────────────────────────────

    _HEALTH_DOT = {'idle': '● ', 'running': '● ', 'disconnected': '● ', 'connecting': '● '}
    _HEALTH_COLOR = {
        'idle':         '#30d158',  # green
        'running':      '#ff9f0a',  # amber
        'disconnected': '#ff453a',  # red
        'connecting':   '#0a84ff',  # blue — optimistic open, background connect in flight
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
            action = self._update_action_text()
            self._update_label.setText(
                f"\u2B06  QForge {found['tag']} is available \u2014 click to {action}"
            )
            self._update_banner.show()
            question = f"Click Yes to {action}."
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

        act = file_menu.addAction("Export Database…")
        act.setShortcut("Ctrl+E")
        act.triggered.connect(self._export_database)

        act = file_menu.addAction("Import Data…")
        act.setShortcut("Ctrl+Shift+E")
        act.triggered.connect(self._import_data)

        file_menu.addSeparator()

        file_menu.addSeparator()

        act = file_menu.addAction("Preferences…")
        act.setShortcut("Ctrl+,")
        act.triggered.connect(self.show_preferences)
        # issue #264: PreferencesRole relocates this into the app menu on
        # macOS ("QForge > Settings…") instead of leaving it in File.
        act.setMenuRole(QAction.MenuRole.PreferencesRole)

        act = file_menu.addAction("Quit")
        act.setShortcut("Ctrl+Q")
        act.triggered.connect(self.close)
        # Explicit rather than relying on Qt's text-heuristic role matching
        # (which happens to catch "Quit" today) — this keeps app-menu
        # placement guaranteed even if the label is ever reworded.
        act.setMenuRole(QAction.MenuRole.QuitRole)

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
        act.triggered.connect(self.show_quick_search)

        act = view_menu.addAction("Command Palette")
        act.setShortcut("Ctrl+Shift+P")
        act.triggered.connect(lambda: show_command_palette(menubar, self))

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

        act = db_menu.addAction("New Connection")
        act.triggered.connect(self._prompt_new_connection)

        act = db_menu.addAction("Reconnect")
        act.triggered.connect(
            lambda: self._current_panel() and self._current_panel()._do_reconnect()
        )

        act = db_menu.addAction("Disconnect")
        act.triggered.connect(
            lambda: self._close_connection_tab(self.conn_tab_bar.currentIndex())
        )

        db_menu.addSeparator()

        act = db_menu.addAction("Refresh Schema")
        act.triggered.connect(
            lambda: self._current_panel() and self._current_panel().load_schema(notify=True)
        )

        act = db_menu.addAction("ER Diagram")
        act.triggered.connect(
            lambda: self._current_panel() and self._current_panel().open_erd_view()
        )

        act = db_menu.addAction("Visual Query Builder…")
        act.triggered.connect(
            lambda: self._current_panel() and self._current_panel().open_query_builder()
        )

        self.schema_compare_action = db_menu.addAction("Compare Schemas…")
        self.schema_compare_action.triggered.connect(
            lambda: self._current_panel() and self._current_panel().open_schema_compare()
        )

        self.data_compare_action = db_menu.addAction("Compare Data…")
        self.data_compare_action.triggered.connect(
            lambda: self._current_panel() and self._current_panel().open_data_compare()
        )

        act = db_menu.addAction("Analyze Query…")
        act.triggered.connect(
            lambda: self._current_panel() and self._current_panel().open_query_analyzer()
        )

        act = db_menu.addAction("Run All Statements")
        act.setShortcut("Ctrl+Shift+Return")
        act.triggered.connect(
            lambda: self._current_panel() and self._current_panel().run_all_statements()
        )

        db_menu.addSeparator()

        act = db_menu.addAction("Create Database…")
        act.triggered.connect(
            lambda: self._current_panel() and self._current_panel().create_database()
        )

        act = db_menu.addAction("Refresh Databases")
        # issue #246: the Command Palette shows *why* a disabled action is
        # unavailable rather than omitting it outright — statusTip() is
        # empty by default (unlike toolTip(), which defaults to the
        # action's own text), so setting it here is what makes this a real
        # reason instead of the palette's generic fallback.
        act.setStatusTip("A database refresh is already running")

        def _refresh_databases():
            panel = self._current_panel()
            if not panel:
                return
            # Issue #138: disable for the duration so a slow/remote fetch
            # can't be re-triggered mid-flight from a second click.
            act.setEnabled(False)
            try:
                panel.refresh_databases()
            finally:
                act.setEnabled(True)

        act.triggered.connect(_refresh_databases)

        db_menu.addSeparator()

        act = db_menu.addAction("Drop Database…")
        act.triggered.connect(
            lambda: self._current_panel() and self._current_panel().drop_database()
        )

        # Help
        help_menu = menubar.addMenu("Help")

        act = help_menu.addAction("About QForge")
        act.triggered.connect(self._show_about)
        # AboutRole (rather than relying on Qt's text-heuristic matching)
        # guarantees this lands in the QForge app menu on macOS regardless
        # of which QMenu it's added to here.
        act.setMenuRole(QAction.MenuRole.AboutRole)

        help_menu.addSeparator()

        act = help_menu.addAction("Keyboard Shortcuts")
        act.triggered.connect(self._show_shortcuts)

        help_menu.addSeparator()

        act = help_menu.addAction("Check for Updates…")
        act.triggered.connect(self._check_for_updates_manual)

        help_menu.addSeparator()

        self.license_action = help_menu.addAction("")
        self.license_action.triggered.connect(self._open_license_dialog)

        self._refresh_pro_menu_labels()

    def _open_license_dialog(self):
        dlg = LicenseDialog(parent=self)
        dlg.exec()
        self._refresh_pro_menu_labels()

    def _refresh_pro_menu_labels(self):
        """Keeps the menu bar's Pro-affordance labels in sync with the
        current edition — called after license activate/deactivate and
        after a remote entitlement-config fetch changes what's gated."""
        schema_compare_gated = not entitlements.is_enabled(Feature.SCHEMA_COMPARE)
        self.schema_compare_action.setText(
            "Compare Schemas… (Pro)" if schema_compare_gated else "Compare Schemas…"
        )
        self.license_action.setText(
            "Upgrade to Pro…" if entitlements.edition() is Edition.FREE else "License…"
        )

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
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        self.apply_theme()

    def show_preferences(self):
        from ui.preferences_dialog import PreferencesDialog
        dialog = PreferencesDialog(self, self)
        dialog.exec()

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

    def _export_database(self):
        panel = self._current_panel()
        if not panel:
            return
        panel.export_database()

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

    def _show_about(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("About QForge")
        layout = QVBoxLayout(dlg)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(QIcon(_asset_path("logo.png")).pixmap(QSize(64, 64)))
        icon_lbl.setAlignment(Qt.AlignHCenter)
        layout.addWidget(icon_lbl)

        browser = QTextBrowser(dlg)
        browser.setOpenExternalLinks(False)
        browser.setFrameStyle(0)
        browser.setHtml(
            '<div style="text-align:center;">'
            f'<h2 style="margin-bottom:2px;">QForge</h2>'
            f'<p style="margin-top:0;color:#8e8e93;">Version {APP_VERSION}</p>'
            '<p>A native macOS SQL client.</p>'
            '</div>'
        )
        browser.setMaximumHeight(140)
        layout.addWidget(browser)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok, parent=dlg)
        buttons.accepted.connect(dlg.accept)
        layout.addWidget(buttons)
        dlg.resize(320, 260)
        dlg.exec()

    def _show_shortcuts(self):
        sections = [
            ("Connections", [
                ("Cmd+N", "Open new connection (adds a tab)"),
            ]),
            ("Tabs", [
                ("Cmd+T", "New query tab"),
                ("Cmd+W", "Close current tab"),
            ]),
            ("Query", [
                ("Cmd+Return", "Run query"),
                ("Cmd+I", "Beautify SQL"),
                ("Cmd+Space", "Autocomplete"),
            ]),
            ("Navigation", [
                ("Cmd+P", "Quick search tables, columns, views, functions, history, snippets"),
                ("Cmd+R / F5", "Refresh current view"),
            ]),
            ("Application", [
                ("Cmd+Q", "Quit"),
            ]),
        ]

        rows = []
        for heading, shortcuts in sections:
            rows.append(f'<tr><td colspan="2" style="padding-top:10px;"><b>{heading}</b></td></tr>')
            for keys, desc in shortcuts:
                rows.append(
                    '<tr>'
                    f'<td style="padding:2px 16px 2px 12px; white-space:nowrap;"><code>{keys}</code></td>'
                    f'<td style="padding:2px 0;">{desc}</td>'
                    '</tr>'
                )
        html = f'<table cellspacing="0">{"".join(rows)}</table>'

        dlg = QDialog(self)
        dlg.setWindowTitle("Keyboard Shortcuts")
        layout = QVBoxLayout(dlg)
        browser = QTextBrowser(dlg)
        browser.setHtml(html)
        browser.setOpenExternalLinks(False)
        layout.addWidget(browser)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok, parent=dlg)
        buttons.accepted.connect(dlg.accept)
        layout.addWidget(buttons)
        dlg.resize(480, 420)
        dlg.exec()


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    logger.info("Starting QForge")
    app = QApplication(sys.argv)
    # Force Fusion so every widget is drawn from our stylesheet/palette alone
    # (issue #52): native styles (QMacStyle in particular) paint some chrome
    # — e.g. QTabBar's own background — from the live OS theme regardless of
    # our QSS, which is why two machines on the same version and Dark Mode
    # setting rendered different tab colors depending on macOS version.
    # QFORGE_NATIVE_STYLE=1 is a #235 spike escape hatch to compare native
    # rendering against Fusion; do not flip the default without a recorded
    # go/no-go verdict on that issue.
    if os.environ.get("QFORGE_NATIVE_STYLE") != "1":
        app.setStyle("Fusion")
    app.setWindowIcon(QIcon(_asset_path("logo.png")))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
