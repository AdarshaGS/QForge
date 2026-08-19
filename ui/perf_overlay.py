"""Developer performance overlay (issue #43).

A hidden HUD showing live runtime metrics collected in utils/perf_metrics.py
plus a few values computed live here (memory, CPU%, thread count) — for
developer debugging only, never shown to a normal user by default. Toggle
with Ctrl+Shift+Alt+P (wired in main.py), or set QFORGE_DEV_OVERLAY=1 to
auto-show at startup.

No "AI Features" section, unlike the issue's suggested metric list: no such
feature exists anywhere in the shipped app yet (query_analyzer.py is an
unrelated offline CLI script, not wired into the UI) — omitted here rather
than faked, so it isn't mistaken for an oversight later.

The Application/Database/SQL Editor/Startup numbers reflect real user
interaction time where the underlying code path itself is user-driven
(e.g. the connection dialog) — they measure wall-clock elapsed, not pure
app overhead. See issue #173 for the specific startup-metric case.
"""

from __future__ import annotations

import resource
import sys
import threading
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from utils import perf_metrics

_STARTUP_STAGES = ("window_created", "connection_manager_ready", "ui_interactive")


def _maxrss_kb() -> float:
    """ru_maxrss is KB on Linux but bytes on macOS — normalize to KB (same
    normalization as benchmarks/harness.py's _maxrss_kb)."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_maxrss / 1024 if sys.platform == "darwin" else float(usage.ru_maxrss)


def _cpu_seconds() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


class PerfOverlayWidget(QWidget):
    """Frameless, semi-transparent HUD anchored to its parent's top-right
    corner. Read-only and click-through (WA_TransparentForMouseEvents) —
    it never intercepts input meant for the app underneath; dismissed with
    the same shortcut that opened it."""

    _WIDTH = 340
    _MARGIN = 12
    _TOP_OFFSET = 30  # clears the tab bar below the menu bar

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main_window = main_window
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setStyleSheet(
            "background-color: rgba(18, 18, 22, 225);"
            "color: #d6d6db;"
            "border: 1px solid rgba(255, 255, 255, 40);"
            "border-radius: 6px;"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        self._label = QLabel()
        self._label.setTextFormat(Qt.RichText)
        # issue #169: without word wrap, a stat line wider than the fixed
        # widget width was silently clipped mid-word instead of flowing to
        # a second line — confirmed by feeding real screenshot values
        # through this widget (label sizeHint 423px vs a 300px-wide widget).
        self._label.setWordWrap(True)
        self._label.setStyleSheet(
            "font-family: Menlo, Consolas, monospace; font-size: 11px; background: transparent; border: none;"
        )
        layout.addWidget(self._label)
        self.setFixedWidth(self._WIDTH)

        # issue #170: CPU% needs two rusage samples spread over wall-clock
        # time — seeded here so the very first refresh() has a baseline to
        # diff against instead of showing a meaningless first reading.
        self._prev_cpu_seconds = _cpu_seconds()
        self._prev_wall_time = time.perf_counter()

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.refresh)
        self.hide()

    def toggle(self):
        if self.isVisible():
            self.hide()
            self._timer.stop()
        else:
            self.refresh()
            self.reposition()
            self.show()
            self.raise_()
            self._timer.start()

    def reposition(self):
        parent = self.parentWidget()
        if not parent:
            return
        self.adjustSize()
        x = parent.width() - self.width() - self._MARGIN
        self.move(max(0, x), self._TOP_OFFSET)

    # ── Data formatting ──────────────────────────────────────────────────

    @staticmethod
    def _fmt_stat(stats: dict | None, unit: str = "ms") -> str:
        if not stats:
            return "—"
        return (
            f"last {stats['last']:.1f}{unit} · mean {stats['mean']:.1f}{unit} "
            f"· p95 {stats['p95']:.1f}{unit} (n={stats['count']})"
        )

    def _active_connections(self) -> int:
        panels = getattr(self._main_window, "_panels", [])
        return sum(1 for p in panels if getattr(p.db_service, "connection", None) is not None)

    def _cpu_percent(self) -> float:
        now_wall = time.perf_counter()
        now_cpu = _cpu_seconds()
        wall_delta = now_wall - self._prev_wall_time
        cpu_delta = now_cpu - self._prev_cpu_seconds
        self._prev_wall_time, self._prev_cpu_seconds = now_wall, now_cpu
        if wall_delta <= 0:
            return 0.0
        # Can exceed 100% for a multi-threaded process using >1 core at
        # once — same convention as Activity Monitor/htop, not a bug.
        return (cpu_delta / wall_delta) * 100

    def refresh(self):
        snap = perf_metrics.snapshot()
        startup = snap.get("startup", {})
        database = snap.get("database", {})
        sql_editor = snap.get("sql_editor", {})
        result_grid = snap.get("result_grid", {})
        import_export = snap.get("import_export", {})
        cache_counts = perf_metrics.counter_get("schema_cache")
        hits, total = cache_counts.get("hit", 0), sum(cache_counts.values())
        cache_ratio = f"{hits}/{total} hits" if total else "—"
        rows_rendered = result_grid.get("rows_rendered")
        active_tasks = perf_metrics.active_tasks()
        tasks_line = ", ".join(f"{n} {k}" for k, n in sorted(active_tasks.items())) or "idle"

        lines = [
            "<b>QForge Perf Overlay</b>",
            "<br><b>Application</b>",
            f"Memory (RSS): {_maxrss_kb():,.0f} KB",
            f"CPU: {self._cpu_percent():.0f}%",
            f"Threads: {threading.active_count()}",
        ]
        for stage in _STARTUP_STAGES:
            if stage in startup:
                lines.append(f"Startup — {stage}: {startup[stage]['last']:.0f} ms")

        lines += [
            "<br><b>Database</b>",
            f"Connect: {self._fmt_stat(database.get('db_connect'))}",
            f"Switch: {self._fmt_stat(database.get('db_switch'))}",
            f"Schema load: {self._fmt_stat(database.get('schema_load'))}",
            f"Active connections: {self._active_connections()}",
            "<br><b>SQL Editor</b>",
            f"Query execute: {self._fmt_stat(sql_editor.get('query_execute'))}",
            f"Autocomplete: {self._fmt_stat(sql_editor.get('autocomplete_latency'))}",
            "<br><b>Result Grid</b>",
            f"Page load: {self._fmt_stat(result_grid.get('page_load'))}",
            f"Rows rendered (last): {rows_rendered['last']:.0f}" if rows_rendered else "Rows rendered (last): —",
            "<br><b>Import / Export</b>",
            f"Export: {self._fmt_stat(import_export.get('export'))}",
            f"CSV import: {self._fmt_stat(import_export.get('csv_import'))}",
            f"File import (preview): {self._fmt_stat(import_export.get('file_import'))}",
            "<br><b>System</b>",
            f"Schema cache: {cache_ratio}",
            f"Background tasks: {tasks_line}",
        ]
        self._label.setText("<br>".join(lines))
        self.reposition()
