"""Application startup benchmarks (issue #59).

Constructs a real MainWindow under QT_QPA_PLATFORM=offscreen, with
ConnectionDialog stubbed to a pre-accepted sqlite :memory: profile — same
pattern proven in tests/test_main_window_focus_after_connect.py — so this
runs unattended with no live database and no interactive prompt.

Every construction is isolated to a fresh temporary app-data directory
(utils.paths.app_data_dir patched before `main` is first imported, since
main.py/utils/schema_cache.py/utils/pinned_tabs.py all resolve their file
paths at import time): this benchmark never reads or writes the developer's
real session.json/pinned_tabs.json/schema_cache.json.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from benchmarks.harness import summarize
from utils import perf_metrics

_STAGES = ("window_created", "connection_manager_ready", "ui_interactive")
_REPS = 5


def _make_noop_thread_stub():
    """Stands in for utils.updater.UpdateChecker /
    utils.entitlement_fetcher.EntitlementConfigFetcher: a real QThread (so
    .start()/.connect() work exactly as MainWindow expects) whose run() does
    nothing, so repeated MainWindow construction in this benchmark never
    fires real network requests. Built lazily (only once PySide6 is
    importable) rather than at module scope."""
    from PySide6.QtCore import QThread, Signal

    class _NoOpThread(QThread):
        update_available = Signal(str, str, str)
        config_loaded = Signal(dict)

        def __init__(self, *args, **kwargs):
            super().__init__()

        def run(self):
            pass

    return _NoOpThread


class _StubConnectionDialog:
    """Stands in for ui.connection_dialog.ConnectionDialog: a pre-accepted
    dialog offering one sqlite :memory: profile, so MainWindow.__init__'s
    blocking _prompt_new_connection() returns immediately."""

    def __init__(self, auto_connect_last=False, parent=None):
        pass

    def exec(self):
        from PySide6.QtWidgets import QDialog
        return QDialog.Accepted

    def get_selected_connection(self):
        return {"type": "sqlite", "name": "bench-startup", "database": ":memory:"}


def _construct_one_main_window(main_mod) -> None:
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    win = main_mod.MainWindow()
    win.close()
    win.deleteLater()


def benchmarks() -> list:
    # utils.paths.app_data_dir() is read once, at import time, by main.py and
    # by utils/schema_cache.py, utils/pinned_tabs.py — patching it must
    # happen before `main` is first imported in this process, and it only
    # needs to happen once (re-patching per-rep would leave the *first*
    # rep's now-deleted tempdir permanently baked into those modules'
    # already-computed path constants, which then get silently
    # recreated on every write — harmless but sloppy). One shared tempdir
    # for the whole run keeps every rep's file I/O contained and cleans up
    # in one place; the developer's real app-data dir is never touched.
    stage_samples: dict[str, list[float]] = {stage: [] for stage in _STAGES}

    with tempfile.TemporaryDirectory() as tmpdir:
        with mock.patch("utils.paths.app_data_dir", return_value=Path(tmpdir)):
            import main as main_mod

            noop_thread_cls = _make_noop_thread_stub()
            main_mod.ConnectionDialog = _StubConnectionDialog
            main_mod.UpdateChecker = noop_thread_cls
            main_mod.EntitlementConfigFetcher = noop_thread_cls

            for _ in range(_REPS):
                perf_metrics.reset()
                _construct_one_main_window(main_mod)
                snap = perf_metrics.snapshot().get("startup", {})
                for stage in _STAGES:
                    if stage in snap:
                        stage_samples[stage].append(snap[stage]["last"])

    results = []
    for stage, samples in stage_samples.items():
        if not samples:
            continue
        cold, warm = samples[0], (samples[1:] or samples)
        results.append(summarize(f"startup.{stage}", warm, cold_ms=cold))
    return results


if __name__ == "__main__":
    for r in benchmarks():
        print(
            f"{r.name}: cold={r.cold_ms:.1f}ms median={r.median_ms:.1f}ms "
            f"p95={r.p95_ms:.1f}ms (n={r.iterations})"
        )
