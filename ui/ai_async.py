"""
ai_async
========
The one shared Qt async-call helper for every AI Assistance capability,
following the same QObject-worker-on-QThread idiom already used by
_CostWorker/_ProfileWorker in ui/query_analyzer_dialog.py (not
ConnectionPanel._run_bg_db, which is DB-connection-shaped, not
generic-call-shaped). Every feature calls start_ai_call() instead of
hand-rolling its own thread plumbing.

Threads started here are deliberately NOT Qt-parented to the widget that
requested them. A QThread's destructor aborts the whole process if it's
destroyed while still running — and an AI call (a `claude` subprocess) can
easily still be in flight when a user closes the tab/dialog that started
it, which Qt's normal parent-child ownership would otherwise trigger the
instant that widget is destroyed. Each thread is kept alive by a plain
Python reference in _live_threads (removed once it actually finishes) so
it runs to completion independently of whatever UI created it.

IMPORTANT: *on_done* must be a bound method of a real QObject (a QWidget,
or an AiCallManager — see below), not a bare closure/lambda. Qt's
AutoConnection only queues a cross-thread signal delivery onto the
*receiving QObject's* thread — it detects that from the bound method's
`__self__`. A bare closure has no QObject to check, so connecting one
directly resolves to a same-thread (direct) call, meaning on_done — and
any Qt widget code it touches — would silently run on the worker thread,
which Qt widgets are not safe for. This is also why AiCallManager below is
itself a QObject rather than a plain Python class: its bookkeeping needs a
real slot to attach the eventual user callback to.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QThread, Signal

from services import ai_client

_live_threads: set = set()


class AiWorker(QObject):
    done = Signal(object)  # AiResult

    def __init__(self, prompt: str, *, json_schema: dict = None, timeout: float = 30.0,
                 cancel_event: threading.Event = None):
        super().__init__()
        self._prompt = prompt
        self._json_schema = json_schema
        self._timeout = timeout
        self._cancel_event = cancel_event

    def run(self):
        result = ai_client.run_prompt(
            self._prompt, json_schema=self._json_schema, timeout=self._timeout,
            cancel_event=self._cancel_event,
        )
        self.done.emit(result)


def run_worker(worker: QObject, on_done):
    """Runs *worker* (already constructed, exposing a `done = Signal(object)`
    and a `run()` method) on its own un-parented QThread and delivers the
    result to *on_done* (a bound method of a QObject — see module
    docstring) via Qt's normal AutoConnection."""
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.done.connect(on_done)
    worker.done.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(lambda: _live_threads.discard(thread))
    thread.finished.connect(thread.deleteLater)
    _live_threads.add(thread)
    thread.start()
    return thread, worker


def start_ai_call(owner: QObject, prompt: str, *, on_done, json_schema: dict = None,
                   timeout: float = 30.0, cancel_event: threading.Event = None):
    """Runs *prompt* through the local Claude CLI on a background thread and
    calls on_done(AiResult) back on the main/Qt thread. *owner* is not used
    for Qt parenting (see module docstring) — it exists so call sites read
    the same way _CostProfileTab._start_estimate's thread/worker wiring
    does. Returns (thread, worker) — callers that want to track "is a call
    in flight" should use AiCallManager rather than inspecting these
    directly."""
    worker = AiWorker(prompt, json_schema=json_schema, timeout=timeout, cancel_event=cancel_event)
    return run_worker(worker, on_done)


class AiCallManager(QObject):
    """Per-owner (per-tab/per-dialog) guard against a second concurrent AI
    call from the *same* widget — a double-click can't spawn a second
    subprocess. Does not impose a single app-wide lock; independent owners
    (different tabs/dialogs) can each have one in-flight call at once,
    mirroring how _run_query_in_tab already allows concurrent per-tab
    queries. The cross-widget backstop is services.ai_client's own
    semaphore cap.

    A QObject (parented to *owner*) rather than a plain class — its
    _on_worker_done is the actual slot AiWorker.done connects to, so Qt's
    AutoConnection can detect real thread affinity (see ai_async module
    docstring) and, as a bonus, Qt automatically disconnects/no-ops the
    eventual delivery if *owner* (and this manager along with it) is
    destroyed while a call is still in flight — no dangling callback into
    a deleted widget is possible."""

    def __init__(self, owner: QObject):
        super().__init__(owner)
        self._owner = owner
        self._thread = None
        self._worker = None
        self._cancel_event: threading.Event | None = None
        self._pending_on_done = None

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def start(self, prompt: str, *, on_done, json_schema: dict = None, timeout: float = 30.0) -> bool:
        """Returns False (and does nothing) if a call is already in flight
        for this owner."""
        if self.busy:
            return False
        self._cancel_event = threading.Event()
        self._pending_on_done = on_done
        self._thread, self._worker = start_ai_call(
            self._owner, prompt, on_done=self._on_worker_done, json_schema=json_schema,
            timeout=timeout, cancel_event=self._cancel_event,
        )
        return True

    def _on_worker_done(self, result):
        self._thread = None
        self._worker = None
        self._cancel_event = None
        on_done, self._pending_on_done = self._pending_on_done, None
        on_done(result)

    def cancel(self):
        if self._cancel_event is not None:
            self._cancel_event.set()
