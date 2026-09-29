"""Backup Jobs window: lists persisted resilient backup jobs, polls the
remote host for active ones, and offers Reconnect / Cancel / Retry / Remove.
Closing it never affects a running job — the dump lives on the SSH host."""
import threading
from datetime import datetime

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QProgressBar, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from services import backup_job as bj
from services import remote_backup as rb

_POLL_MS = 5000
_LABELS = {
    bj.QUEUED: "Queued",
    bj.RUNNING: "Running",
    bj.CONNECTION_LOST: "Connection lost — backup is still running remotely",
    bj.RECONNECTING: "Reconnecting to backup job…",
    bj.COMPLETED: "Completed",
    bj.FAILED: "Failed",
    bj.CANCELLED: "Cancelled",
}


def status_text(job: bj.BackupJob) -> str:
    return _LABELS.get(job.status, job.status)


def _remote_opener(job):
    """Fresh, credential-resolved SSH transport for *job*'s connection."""
    from ui.connection_dialog import ConnectionDialog
    config = ConnectionDialog.load_connection_by_id(job.connectionId)
    if config is None:
        raise rb.BackupError("The connection for this backup no longer exists.")
    return config, (lambda: rb.open_remote(config))


class BackupJobsDialog(QDialog):
    _changed = Signal()

    def __init__(self, parent=None, store: bj.JobStore = None):
        super().__init__(parent)
        self.setWindowTitle("Backup Jobs")
        self.resize(760, 380)
        self._store = store or bj.JobStore()
        self._busy = set()   # jobIds with an in-flight worker thread

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["Database", "Status", "Progress", "Current", "Started"])
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.itemSelectionChanged.connect(self._update_detail)
        self._detail = QLabel("")
        self._detail.setWordWrap(True)

        buttons = QHBoxLayout()
        for text, fn in (("Reconnect", self._reconnect), ("Cancel job", self._cancel),
                         ("Retry", self._retry), ("Remove", self._remove)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            buttons.addWidget(b)
        buttons.addStretch()

        lay = QVBoxLayout(self)
        lay.addWidget(self._table)
        lay.addWidget(self._detail)
        lay.addLayout(buttons)

        self._changed.connect(self._reload)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_active)
        self._timer.start(_POLL_MS)
        self._reload()
        self._poll_active()

    # ── data ────────────────────────────────────────────────────────────
    def _selected(self) -> bj.BackupJob | None:
        row = self._table.currentRow()
        item = self._table.item(row, 0) if row >= 0 else None
        return self._store.load(item.data(0x100)) if item else None

    def _reload(self):
        sel = self._selected()
        self._table.setRowCount(0)
        for job in self._store.list():
            r = self._table.rowCount()
            self._table.insertRow(r)
            first = QTableWidgetItem(job.database)
            first.setData(0x100, job.jobId)
            self._table.setItem(r, 0, first)
            self._table.setItem(r, 1, QTableWidgetItem(status_text(job)))
            bar = QProgressBar()
            bar.setValue(int(job.progress * 100))
            self._table.setCellWidget(r, 2, bar)
            cur = f"{job.currentObject} ({len(job.completedObjects)}/{job.totalObjects})" if job.totalObjects and job.currentObject else job.currentObject
            self._table.setItem(r, 3, QTableWidgetItem(cur))
            self._table.setItem(r, 4, QTableWidgetItem(datetime.fromtimestamp(job.startedAt).strftime("%Y-%m-%d %H:%M")))
            if sel and sel.jobId == job.jobId:
                self._table.selectRow(r)
        self._update_detail()

    def _update_detail(self):
        job = self._selected()
        self._detail.setText("" if not job else
                             f"{status_text(job)}" + (f"\n{job.error}" if job.error else "") +
                             f"\nSaving to: {job.destination}")

    def _work(self, job, fn):
        """Run fn(job) off the UI thread; never two workers on one job."""
        if job.jobId in self._busy:
            return
        self._busy.add(job.jobId)

        def run():
            try:
                fn(job)
            except rb.BackupError as ex:
                job.error = str(ex)
                self._store.save(job)
            except Exception as ex:  # network failure on cancel/retry: job state untouched
                job.error = f"Could not reach the SSH host: {ex}"
                self._store.save(job)
            finally:
                self._busy.discard(job.jobId)
                self._changed.emit()
        threading.Thread(target=run, daemon=True).start()

    def _refresh_job(self, job):
        try:
            _, opener = _remote_opener(job)
        except rb.BackupError as ex:
            job.status, job.error = bj.FAILED, str(ex)
            self._store.save(job)
            return
        rb.refresh(job, opener, self._store)

    def _poll_active(self):
        for job in self._store.list():
            if job.active and job.remote:
                self._work(job, self._refresh_job)

    # ── actions ─────────────────────────────────────────────────────────
    def _reconnect(self):
        job = self._selected()
        if job and job.active:
            self._work(job, self._refresh_job)

    def _cancel(self):
        job = self._selected()
        if job and job.active and QMessageBox.question(
                self, "Cancel backup", f"Stop the running backup of {job.database} on the server?"
        ) == QMessageBox.Yes:
            def go(j):
                _, opener = _remote_opener(j)
                rb.cancel(j, opener, self._store)
            self._work(job, go)

    def _retry(self):
        job = self._selected()
        if job and job.status in (bj.FAILED, bj.CANCELLED):
            def go(j):
                config, opener = _remote_opener(j)
                rb.retry(j, config, opener, self._store)
            self._work(job, go)

    def _remove(self):
        job = self._selected()
        if job and not job.active:
            self._store.delete(job.jobId)
            self._reload()
