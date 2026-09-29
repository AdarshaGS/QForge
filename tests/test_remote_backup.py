"""Resilient remote backup: runs the real generated run.sh against a fake
mysqldump on this machine (LocalRemote stands in for the SSH host)."""
import os
import stat
import time

import pytest

from services import backup_job as bj
from services import remote_backup as rb

FAKE = """#!/bin/sh
# usage: mysqldump ... DB [table]; MODE via $FAKE_MODE; last arg = unit's table (or DB for routines)
for a; do last=$a; done
[ -n "$FAKE_LOG" ] && echo "$last" >> "$FAKE_LOG"
echo "-- Retrieving table structure for table $last..." >&2
case "$FAKE_MODE" in
  slow) exec sleep 30 ;;
  fail) echo "mysqldump: Got error: 1045: Access denied" >&2; exit 2 ;;
  failat) if [ "$last" = "$FAKE_FAIL_TABLE" ]; then echo "mysqldump: lost connection" >&2; exit 3; fi ;;
esac
echo "CREATE TABLE $last;"
[ "$FAKE_MODE" = truncated ] && exit 0
echo "-- Dump completed on now"
"""
CONFIG = {"type": "mysql", "user": "u", "password": "s3cr3t-pw", "host": "db", "port": 3306,
          "ssh_tunnel": {"enabled": True, "host": "bastion", "user": "me"}}


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "mysqldump"
    exe.write_text(FAKE)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    home = tmp_path / "home"
    home.mkdir()
    store = bj.JobStore(str(tmp_path / "jobs"))

    def remote(mode="ok", **extra):
        return rb.LocalRemote(str(home), {"PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_MODE": mode,
                                          "FAKE_LOG": str(tmp_path / "calls.log"), **extra})

    def launch(mode="ok", name="a.sql", **kw):
        job = bj.new_job("c1", "shop", str(tmp_path / name), ["t1", "t2", "t3"], **kw)
        r = remote(mode)
        rb.start(job, CONFIG, r, store)
        return job, r

    return tmp_path, store, remote, launch


def settle(job, store, remote_fn, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        rb.refresh(job, remote_fn, store)
        if not job.active:
            return job
        time.sleep(0.2)
    return job


def test_success_downloads_verified_file_and_cleans_remote(env):
    tmp, store, remote, launch = env
    job, r = launch()
    settle(job, store, lambda: remote())
    assert job.status == bj.COMPLETED and job.progress == 1.0
    assert "CREATE TABLE t3" in open(job.destination).read()
    assert not os.path.exists(job.remote["dir"])
    assert not os.path.exists(job.destination + ".part")


def test_no_secrets_persisted_locally(env):
    tmp, store, remote, launch = env
    job, _ = launch()
    assert "s3cr3t-pw" not in open(store._path(job.jobId)).read()
    settle(job, store, lambda: remote())


def test_disconnect_then_reconnect_after_app_restart(env):
    tmp, store, remote, launch = env
    job, _ = launch("slow")

    def dead():
        raise OSError("connection reset")

    rb.refresh(job, dead, store)
    assert job.status == bj.CONNECTION_LOST and job.active
    # "QForge restart": reload from disk with a brand-new store object
    job2 = bj.JobStore(store._dir).load(job.jobId)
    assert job2.status == bj.CONNECTION_LOST
    for _ in range(30):  # wait for the fake dump to emit its first table line
        rb.refresh(job2, lambda: remote("slow"), store)
        if job2.currentObject:
            break
        time.sleep(0.2)
    assert job2.status == bj.RUNNING and job2.currentObject == "t1"
    rb.cancel(job2, lambda: remote(), store)


def test_completed_while_disconnected_is_detected_on_reconnect(env):
    tmp, store, remote, launch = env
    job, _ = launch()
    rb.refresh(job, lambda: (_ for _ in ()).throw(OSError("down")), store)
    assert job.status == bj.CONNECTION_LOST
    settle(job, store, lambda: remote())
    assert job.status == bj.COMPLETED


def test_remote_failure_is_failed_with_reason(env):
    tmp, store, remote, launch = env
    job, _ = launch("fail")
    settle(job, store, lambda: remote())
    assert job.status == bj.FAILED and "Access denied" in job.error
    assert not os.path.exists(job.destination)


def test_truncated_dump_is_not_completed(env):
    tmp, store, remote, launch = env
    job, _ = launch("truncated")
    settle(job, store, lambda: remote())
    assert job.status == bj.FAILED and not os.path.exists(job.destination)


def test_killed_process_without_status_is_failed_not_completed(env):
    tmp, store, remote, launch = env
    job, r = launch("slow")
    time.sleep(0.5)
    pid = open(f"{job.remote['dir']}/pid").read().strip()
    r.run(f"pkill -P {pid}; kill -9 {pid}")
    time.sleep(0.3)
    rb.refresh(job, lambda: remote(), store)
    assert job.status == bj.FAILED and "without completing" in job.error


def test_cancel_kills_process_and_removes_credentials(env):
    tmp, store, remote, launch = env
    job, r = launch("slow")
    time.sleep(0.5)
    d = job.remote["dir"]
    pid = open(f"{d}/pid").read().strip()
    rb.cancel(job, lambda: remote(), store)
    time.sleep(0.3)
    assert job.status == bj.CANCELLED
    assert r.run(f"kill -0 {pid}")[0] != 0
    assert not os.path.exists(f"{d}/my.cnf")


def test_parallel_jobs_are_isolated(env):
    tmp, store, remote, launch = env
    a, _ = launch(name="a.sql")
    b, _ = launch(name="b.sql")
    assert a.remote["dir"] != b.remote["dir"]
    settle(a, store, lambda: remote())
    settle(b, store, lambda: remote())
    assert a.status == b.status == bj.COMPLETED
    assert os.path.exists(a.destination) and os.path.exists(b.destination)


def test_existing_destination_is_not_overwritten(env):
    tmp, store, remote, launch = env
    (tmp / "a.sql").write_text("precious")
    with pytest.raises(rb.BackupError):
        launch()
    assert (tmp / "a.sql").read_text() == "precious"


def test_missing_mysqldump_fails_up_front(env):
    tmp, store, remote, launch = env
    job = bj.new_job("c1", "shop", str(tmp / "z.sql"), ["t1"])
    empty = rb.LocalRemote(str(tmp / "home"), {"PATH": "/usr/bin:/bin"})
    if empty.run("command -v mysqldump")[0] == 0:
        pytest.skip("real mysqldump on PATH")
    with pytest.raises(rb.BackupError, match="mysqldump was not found"):
        rb.start(job, CONFIG, empty, store)


def test_retry_skips_verified_units_and_resumes(env):
    tmp, store, remote, launch = env
    job = bj.new_job("c1", "shop", str(tmp / "r.sql"), ["t1", "t2", "t3"])
    bad = lambda: remote("failat", FAKE_FAIL_TABLE="t2")
    rb.start(job, CONFIG, bad(), store)
    settle(job, store, bad)
    assert job.status == bj.FAILED and "t2" in job.error
    assert job.manifest["t1"]["state"] == "completed" and job.manifest["t2"]["state"] == "failed"
    calls_before = open(tmp / "calls.log").read().split()
    assert calls_before.count("t1") == 1

    rb.retry(job, CONFIG, lambda: remote(), store)  # same job, same remote dir
    settle(job, store, lambda: remote())
    assert job.status == bj.COMPLETED
    calls = open(tmp / "calls.log").read().split()
    assert calls.count("t1") == 1, "verified unit must not be re-dumped"
    body = open(job.destination).read()
    assert all(f"CREATE TABLE {t};" in body for t in ("t1", "t2", "t3"))


def test_retry_after_cancel_reuses_dir_and_clears_stale_state(env):
    tmp, store, remote, launch = env
    job, _ = launch("slow")
    time.sleep(0.5)
    rb.cancel(job, lambda: remote(), store)
    rb.retry(job, CONFIG, lambda: remote(), store)
    settle(job, store, lambda: remote())
    assert job.status == bj.COMPLETED
