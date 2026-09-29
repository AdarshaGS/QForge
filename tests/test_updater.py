"""Regression test for utils/updater.py's UpdateChecker: a failed request
(no network, GitHub rate-limit, DNS, ...) must be reported distinctly from
"checked fine, nothing newer" via check_failed, not silently swallowed —
the Help -> Check for Updates dialog used to report "you are on the latest
version" even when the check itself never actually completed."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from utils.updater import UpdateChecker

_app = QApplication.instance() or QApplication([])


def test_request_failure_emits_check_failed_not_silence(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise OSError("Name or service not known")

    monkeypatch.setattr("urllib.request.urlopen", _boom)

    checker = UpdateChecker()
    seen = {"available": None, "failed": None}
    checker.update_available.connect(lambda *a: seen.__setitem__("available", a))
    checker.check_failed.connect(lambda reason: seen.__setitem__("failed", reason))

    checker.run()  # synchronous — no need to spin up the QThread for this

    assert seen["available"] is None
    assert seen["failed"] == "Name or service not known"


def test_newer_tag_emits_update_available(monkeypatch):
    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"tag_name": "v99.0.0", "html_url": "https://example.test", "assets": []}'

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeResponse())

    checker = UpdateChecker()
    seen = {"available": None, "failed": None}
    checker.update_available.connect(lambda *a: seen.__setitem__("available", a))
    checker.check_failed.connect(lambda reason: seen.__setitem__("failed", reason))

    checker.run()

    assert seen["failed"] is None
    assert seen["available"][0] == "v99.0.0"
