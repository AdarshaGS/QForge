"""Detached remote mysqldump for SSH-tunnelled MySQL connections.

The dump runs *on the SSH host* under tmux (or nohup when tmux is absent), so
neither a dropped SSH session nor closing QForge kills it. Job state lives in a
directory on the remote host (<home>/.qforge-backups/<jobId>/):

    run.sh      the script (generated; values shell-quoted)
    my.cnf      0600 client credentials, deleted by run.sh on exit
    status      RUNNING | DONE | FAILED <rc> | CANCELLED  (written only by run.sh/cancel)
    pid         run.sh's pid
    units/uNNNN.sql   one logical unit per table (+ u9999 = routines/events),
                      renamed from .part only after exit 0 AND the
                      "-- Dump completed" trailer is present
    manifest    "<idx>\t<label>\t<sha256>" per verified unit; a re-run skips these
    current / failed   unit in flight / unit that failed
    dump.sql    units concatenated once every unit is verified
    err.log     mysqldump --verbose stderr (last lines shown on failure)

A job is COMPLETED locally only after the file is downloaded and verified
(size + sha256). A vanished process with no DONE status is FAILED, never
COMPLETED. Network errors are CONNECTION_LOST, never FAILED.
"""
import hashlib
import os
import re
import shlex
import subprocess
import time

from services import backup_job as bj
from utils.logger import get_logger

logger = get_logger()
REMOTE_ROOT = ".qforge-backups"
_TABLE_RE = re.compile(r"Retrieving table structure for table `?([^`\s]+?)`?\.\.\.\s*$")
_PID_GRACE_SECONDS = 30  # run.sh may not have written its pid yet right after launch


class BackupError(Exception):
    """A backup problem that is not a network drop (bad setup, refused overwrite, ...)."""


# ─── transports ─────────────────────────────────────────────────────────────

class ParamikoRemote:
    """SSH transport reusing the tunnel's auth settings (key or password)."""

    def __init__(self, ssh_config: dict):
        import paramiko
        c = ssh_config
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # same trust as sshtunnel
        kwargs = dict(hostname=c["host"], port=int(c.get("port", 22)), username=c["user"],
                      timeout=15, banner_timeout=15, auth_timeout=15)
        key = (c.get("key_path") or "").strip()
        if c.get("use_key") and key:
            kwargs["key_filename"] = os.path.expanduser(key)
        elif c.get("password"):
            kwargs["password"] = c["password"]
        else:
            raise BackupError("SSH requires either password or private key")
        self._client.connect(**kwargs)
        self._client.get_transport().set_keepalive(30)
        self._sftp = None

    def _sftp_client(self):
        if self._sftp is None:
            self._sftp = self._client.open_sftp()
        return self._sftp

    def run(self, cmd, timeout=60):
        _, out, err = self._client.exec_command(cmd, timeout=timeout)
        rc = out.channel.recv_exit_status()
        return rc, out.read().decode("utf-8", "replace"), err.read().decode("utf-8", "replace")

    def put(self, path, data: bytes, mode=0o600):
        f = self._sftp_client().file(path, "w")
        self._sftp_client().chmod(path, mode)  # tighten before any secret is written
        f.write(data)
        f.close()

    def get(self, remote_path, local_path):
        self._sftp_client().get(remote_path, local_path)

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass


class LocalRemote:
    """Runs the same scripts on this machine; used by tests as a stand-in host."""

    def __init__(self, home, env=None):
        self.home = home
        self._env = dict(os.environ, HOME=home, **(env or {}))

    def run(self, cmd, timeout=60):
        p = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True,
                           env=self._env, timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout, p.stderr

    def put(self, path, data: bytes, mode=0o600):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "wb") as f:
            f.write(data)

    def get(self, remote_path, local_path):
        with open(remote_path, "rb") as s, open(local_path, "wb") as d:
            d.write(s.read())

    def close(self):
        pass


def open_remote(config: dict):
    ssh = config.get("ssh_tunnel") or {}
    if not ssh.get("enabled"):
        raise BackupError("Resilient backup requires an SSH-tunnelled connection.")
    return ParamikoRemote(ssh)


def supports_remote_backup(config: dict) -> bool:
    return (config.get("type", "mysql").lower() == "mysql"
            and bool((config.get("ssh_tunnel") or {}).get("enabled")))


# ─── script generation ──────────────────────────────────────────────────────

def _cnf(config: dict) -> bytes:
    pw = str(config.get("password", "")).replace("\\", "\\\\").replace('"', '\\"')
    return (f'[client]\nuser={config["user"]}\npassword="{pw}"\n'
            f'host={config["host"]}\nport={config["port"]}\n').encode()


_META_IDX = "u9999"


def _script(job: bj.BackupJob, d: str) -> bytes:
    q = shlex.quote
    db = q(job.database)
    units = "\n".join(f"run_unit u{i:04d} {q(t)} {db} {q(t)}" for i, t in enumerate(job.tables, 1))
    return f"""#!/bin/sh
D={q(d)}
cd "$D" || exit 1
trap 'rm -f "$D/my.cnf"' EXIT
echo $$ > pid
mkdir -p units
: > err.log
rm -f failed
sha() {{ sha256sum "$1" 2>/dev/null || shasum -a 256 "$1"; }}
run_unit() {{
  idx=$1; label=$2; shift 2
  if [ -f "units/$idx.sql" ] && grep -q "^$idx[[:space:]]" manifest 2>/dev/null; then return 0; fi
  echo "$label" > current
  mysqldump --defaults-extra-file="$D/my.cnf" --verbose --single-transaction "$@" \
    > "units/$idx.sql.part" 2>> err.log
  rc=$?
  [ "$(cat status 2>/dev/null)" = CANCELLED ] && exit 0
  if [ $rc -eq 0 ] && tail -n 1 "units/$idx.sql.part" | grep -q '^-- Dump completed'; then
    mv "units/$idx.sql.part" "units/$idx.sql"
    printf '%s\t%s\t%s\n' "$idx" "$label" "$(sha "units/$idx.sql" | cut -d' ' -f1)" >> manifest
  else
    echo "$label" > failed
    echo "FAILED $rc" > status
    exit 0
  fi
}}
{units}
run_unit {_META_IDX} __routines__ --no-data --no-create-info --skip-triggers --routines --events {db}
cat units/u*.sql > dump.sql.part
echo "-- Dump completed (QForge assembled)" >> dump.sql.part
mv dump.sql.part dump.sql
sha dump.sql | cut -d' ' -f1 > dump.sha256
echo DONE > status
""".encode()


# ─── operations ─────────────────────────────────────────────────────────────

def _home(remote) -> str:
    rc, out, err = remote.run("echo $HOME")
    if rc != 0 or not out.strip():
        raise BackupError(f"Could not resolve remote home directory: {err.strip()}")
    return out.strip()


def _launch(job, config, remote, d):
    """(Re)write credentials + script into *d* and launch run.sh detached."""
    session = f"qforge-{job.jobId}"
    remote.run(f"rm -f {shlex.quote(d)}/pid {shlex.quote(d)}/failed")  # stale state from a previous run
    remote.put(f"{d}/my.cnf", _cnf(config), 0o600)
    remote.put(f"{d}/run.sh", _script(job, d), 0o700)
    remote.put(f"{d}/status", b"RUNNING\n", 0o600)
    launch = (f"if command -v tmux >/dev/null 2>&1; then "
              f"tmux new-session -d -s {shlex.quote(session)} 'sh {shlex.quote(d)}/run.sh'; echo tmux; "
              f"else nohup sh {shlex.quote(d)}/run.sh </dev/null >/dev/null 2>&1 & echo nohup; fi")
    rc, out, err = remote.run(launch)
    if rc != 0:
        remote.run(f"rm -f {shlex.quote(d)}/my.cnf")  # never leave credentials behind
        raise BackupError(f"Could not start remote backup: {err.strip()}")
    ssh = config.get("ssh_tunnel") or {}
    job.remote = {"host": ssh.get("host", ""), "user": ssh.get("user", ""),
                  "dir": d, "session": session, "mode": out.strip()}
    job.status, job.error = bj.RUNNING, ""


def start(job: bj.BackupJob, config: dict, remote, store: bj.JobStore):
    """Launch *job* detached on the remote host and persist reconnect info."""
    if not job.tables:
        raise BackupError("No tables selected for backup.")
    if os.path.exists(job.destination) and not job.overwrite:
        raise BackupError(f"Destination already exists: {job.destination}")
    if remote.run("command -v mysqldump")[0] != 0:
        raise BackupError("mysqldump was not found on the SSH host. Install it there, "
                          "or use the regular Export Database instead.")
    d = f"{_home(remote)}/{REMOTE_ROOT}/{job.jobId}"
    rc, _, err = remote.run(f"mkdir -p {shlex.quote(d)} && chmod 700 {shlex.quote(d)}")
    if rc != 0:
        raise BackupError(f"Could not create remote job directory: {err.strip()}")
    _launch(job, config, remote, d)
    store.save(job)


def poll(job: bj.BackupJob, remote) -> dict:
    d = shlex.quote(job.remote["dir"])
    rc, out, _ = remote.run(
        f"cd {d} 2>/dev/null || {{ echo MISSING; exit 0; }}; "
        f"echo \"S:$(cat status 2>/dev/null)\"; "
        f"p=$(cat pid 2>/dev/null); if [ -n \"$p\" ] && kill -0 \"$p\" 2>/dev/null; then echo A:1; "
        f"elif [ -z \"$p\" ]; then echo A:?; else echo A:0; fi; "
        f"echo \"C:$(cat current 2>/dev/null)\"; echo \"F:$(cat failed 2>/dev/null)\"; "
        f"cat manifest 2>/dev/null | sed 's/^/M:/'; echo E:; tail -n 5 err.log 2>/dev/null")
    if out.startswith("MISSING"):
        return {"status": "MISSING", "alive": "0", "done": {}, "current": "", "failed": "",
                "err": "remote job directory is gone"}
    st, alive, cur, failed, done, err_tail, in_err = "", "0", "", "", {}, [], False
    for l in out.splitlines():
        if in_err:
            err_tail.append(l)
        elif l == "E:":
            in_err = True
        elif l.startswith("S:"):
            st = l[2:].strip()
        elif l.startswith("A:"):
            alive = l[2:]
        elif l.startswith("C:"):
            cur = l[2:]
        elif l.startswith("F:"):
            failed = l[2:]
        elif l.startswith("M:"):
            parts = l[2:].split("\t")
            if len(parts) == 3 and parts[1] != "__routines__":
                done[parts[1]] = parts[2]
    return {"status": st, "alive": alive, "done": done, "current": cur, "failed": failed,
            "err": "\n".join(err_tail).strip()}


def _download(job: bj.BackupJob, remote):
    d = job.remote["dir"]
    if os.path.exists(job.destination) and not job.overwrite:
        raise BackupError(f"Destination already exists: {job.destination}")
    part = job.destination + ".part"
    remote.get(f"{d}/dump.sql", part)
    _, sha, _ = remote.run(f"cat {shlex.quote(d)}/dump.sha256")
    h = hashlib.sha256()
    with open(part, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if sha.strip() and h.hexdigest() != sha.strip():
        os.remove(part)
        raise BackupError("Downloaded backup failed checksum verification; it was discarded.")
    os.replace(part, job.destination)
    remote.run(f"rm -rf {shlex.quote(d)}")  # clean the remote copy only after verified local copy


def refresh(job: bj.BackupJob, open_remote_fn, store: bj.JobStore) -> bj.BackupJob:
    """Advance *job* one step by asking the remote host. *open_remote_fn* is a
    zero-arg callable returning a transport (raises OSError-ish on network loss)."""
    if not job.active:
        return job
    was_lost = job.status == bj.CONNECTION_LOST
    remote = None
    try:
        if was_lost:
            job.status = bj.RECONNECTING
            store.save(job)
        remote = open_remote_fn()
        info = poll(job, remote)
        _apply(job, info, remote)
    except BackupError as ex:
        job.status, job.error = bj.FAILED, str(ex)
    except Exception as ex:  # network / SSH / SFTP: the job itself is untouched
        logger.warning(f"Backup {job.jobId}: connection lost: {ex}")
        job.status, job.error = bj.CONNECTION_LOST, f"SSH connection lost: {ex}"
    finally:
        if remote:
            remote.close()
    store.save(job)
    return job


def _apply(job, info, remote):
    st = info["status"]
    job.manifest = {t: {"state": "completed", "checksum": c} for t, c in info["done"].items()}
    job.completedObjects = list(info["done"])
    if st == "DONE":
        job.currentObject = "Downloading…"
        _download(job, remote)
        job.status, job.progress, job.currentObject, job.error = bj.COMPLETED, 1.0, "", ""
    elif st.startswith("FAILED"):
        if info["failed"]:
            job.manifest[info["failed"]] = {"state": "failed"}
        job.status = bj.FAILED
        job.currentObject = info["failed"]
        job.error = f"Table {info['failed']} failed: {info['err'] or st}"
    elif st == "CANCELLED":
        job.status = bj.CANCELLED
    elif info["alive"] == "0" or st == "MISSING" or info["alive"] == "?" and \
            time.time() - job.startedAt > _PID_GRACE_SECONDS:
        # process gone without a DONE status: incomplete output, not success
        job.status = bj.FAILED
        job.error = "Remote backup process ended without completing. " + info["err"]
    else:
        job.status, job.error = bj.RUNNING, ""
        job.currentObject = info["current"] if info["current"] != "__routines__" else "routines and events"
        job.progress = min(len(info["done"]) / (len(job.tables) + 1), 0.99)


def cancel(job: bj.BackupJob, open_remote_fn, store: bj.JobStore) -> bj.BackupJob:
    """Kill the remote process and delete remote artifacts (incl. credentials)."""
    if not job.active:
        return job
    remote = open_remote_fn()  # a failed connection propagates: job stays as-is, user can retry
    try:
        d = shlex.quote(job.remote["dir"])
        remote.run(
            f"cd {d} 2>/dev/null && echo CANCELLED > status; "
            f"p=$(cat {d}/pid 2>/dev/null); "
            f"if [ -n \"$p\" ]; then pkill -P \"$p\" 2>/dev/null; kill \"$p\" 2>/dev/null; fi; "
            f"tmux kill-session -t {shlex.quote(job.remote['session'])} 2>/dev/null; "
            f"rm -f {d}/my.cnf {d}/dump.sql.part")
    finally:
        remote.close()
    job.status, job.currentObject = bj.CANCELLED, ""
    store.save(job)
    return job


def retry(job: bj.BackupJob, config: dict, open_remote_fn, store: bj.JobStore) -> bj.BackupJob:
    """Re-run a FAILED/CANCELLED job in its existing remote directory so verified
    units are skipped. If the remote directory is gone, start a fresh job."""
    remote = open_remote_fn()
    try:
        if job.remote and poll(job, remote)["status"] != "MISSING":
            _launch(job, config, remote, job.remote["dir"])
            store.save(job)
            return job
        new = bj.new_job(job.connectionId, job.database, job.destination, job.tables, job.overwrite)
        start(new, config, remote, store)
        return new
    finally:
        remote.close()
