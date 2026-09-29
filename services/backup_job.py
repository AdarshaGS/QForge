"""Persistent local state for resilient (remote, detached) database backups.

One JSON file per job under <app_data>/backup_jobs/. Holds no secrets: SSH/DB
passwords are re-resolved from the credential store by connectionId whenever
QForge (re)connects to the job.
"""
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field, fields

from utils.paths import app_data_dir

QUEUED = "QUEUED"
RUNNING = "RUNNING"
CONNECTION_LOST = "CONNECTION_LOST"
RECONNECTING = "RECONNECTING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
CANCELLED = "CANCELLED"

TERMINAL = frozenset({COMPLETED, FAILED, CANCELLED})


@dataclass
class BackupJob:
    jobId: str
    connectionId: str
    database: str
    destination: str
    tables: list = field(default_factory=list)   # [] = whole database
    status: str = QUEUED
    startedAt: float = 0.0
    updatedAt: float = 0.0
    progress: float = 0.0                        # 0..1
    totalObjects: int = 0
    currentObject: str = ""
    completedObjects: list = field(default_factory=list)
    error: str = ""
    overwrite: bool = False
    # Non-secret reconnect info: {"host","user","dir","session","mode"}.
    remote: dict = field(default_factory=dict)
    # Reserved for Phase 4 (per-table units): {unit: {"state","checksum"}}.
    manifest: dict = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.status not in TERMINAL


def new_job(connection_id, database, destination, tables=(), overwrite=False) -> BackupJob:
    now = time.time()
    return BackupJob(
        jobId=uuid.uuid4().hex[:12], connectionId=connection_id, database=database,
        destination=destination, tables=list(tables), overwrite=overwrite,
        startedAt=now, updatedAt=now, totalObjects=len(tables),
    )


class JobStore:
    def __init__(self, directory=None):
        self._dir = directory or os.path.join(str(app_data_dir()), "backup_jobs")

    def _path(self, job_id):
        return os.path.join(self._dir, f"{job_id}.json")

    def save(self, job: BackupJob):
        job.updatedAt = time.time()
        os.makedirs(self._dir, exist_ok=True)
        tmp = self._path(job.jobId) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(asdict(job), f, indent=2)
        os.replace(tmp, self._path(job.jobId))  # atomic: a crash never leaves a torn job file

    def load(self, job_id) -> BackupJob | None:
        try:
            with open(self._path(job_id)) as f:
                raw = json.load(f)
            known = {f.name for f in fields(BackupJob)}
            return BackupJob(**{k: v for k, v in raw.items() if k in known})
        except (OSError, ValueError, TypeError):
            return None

    def list(self) -> list[BackupJob]:
        if not os.path.isdir(self._dir):
            return []
        jobs = [self.load(n[:-5]) for n in os.listdir(self._dir) if n.endswith(".json")]
        return sorted((j for j in jobs if j), key=lambda j: j.startedAt, reverse=True)

    def delete(self, job_id):
        try:
            os.remove(self._path(job_id))
        except OSError:
            pass
