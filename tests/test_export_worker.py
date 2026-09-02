"""Tests for _ExportWorker (issue #158): the producer/queue pipeline that
streams table structure/content to the export file on a background thread
instead of export_database()'s old synchronous, whole-table-at-once loop.

Live-Postgres integration (skipped if no local server, mirrors
tests/test_db_service_postgresql.py's fixture)."""
import gzip
import os
import threading
import uuid

import psycopg2
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from services.db_service import DbService
from ui.connection_panel import _ExportWorker
from utils import perf_metrics

_app = QApplication.instance() or QApplication([])

_PG_HOST = "localhost"
_PG_PORT = 5432
_PG_USER = "qforge_test"
_PG_PASSWORD = "qforge_test_pw"
_ADMIN_PARAMS = dict(host=_PG_HOST, port=_PG_PORT, user=_PG_USER, password=_PG_PASSWORD, database="postgres")


def _postgres_available() -> bool:
    try:
        conn = psycopg2.connect(connect_timeout=2, **_ADMIN_PARAMS)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_available(),
    reason="No local Postgres reachable as qforge_test@localhost:5432 — see "
           "tests/test_db_service_postgresql.py's module docstring for setup.",
)


def _new_db_config(name_hint: str) -> dict:
    name = f"qforge_test_{name_hint}_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(connect_timeout=5, **_ADMIN_PARAMS)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    admin.close()
    return {
        "type": "postgresql", "name": name_hint, "host": _PG_HOST, "port": _PG_PORT,
        "user": _PG_USER, "password": _PG_PASSWORD, "database": name,
    }


def _drop_db_config(config: dict) -> None:
    admin = psycopg2.connect(connect_timeout=5, **_ADMIN_PARAMS)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()", (config["database"],))
        cur.execute(f'DROP DATABASE IF EXISTS "{config["database"]}"')
    admin.close()


@pytest.fixture
def db_config():
    config = _new_db_config("export")
    setup = DbService()
    setup.connect(config)
    setup.execute_update("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    setup.execute_update("CREATE TABLE orders (id INTEGER PRIMARY KEY, total INTEGER)")
    for i in range(1, 6):
        setup.execute_update(f"INSERT INTO users (id, name) VALUES ({i}, 'user{i}')")  # nosec B608
    setup.execute_update("INSERT INTO orders (id, total) VALUES (1, 100)")
    setup.disconnect()
    yield config
    _drop_db_config(config)


def _export_db(db_config):
    export_db = DbService()
    export_db.connect(db_config)
    return export_db


def _run(db_config, table_opts, out_path, **kwargs):
    events = {"progress": [], "finished": None, "errored": None, "cancelled": False}
    worker = _ExportWorker(
        _export_db(db_config), table_opts, out_path,
        export_format=kwargs.get("export_format", "sql"),
        blob_as_hex=kwargs.get("blob_as_hex", True),
        batch_kib=kwargs.get("batch_kib"),
        gzip_output=kwargs.get("gzip_output", False),
        use_bom=kwargs.get("use_bom", False),
        include_auto_increment=kwargs.get("include_auto_increment", True),
        strip_generated=kwargs.get("strip_generated", False),
        cancel_flag=kwargs.get("cancel_flag") or threading.Event(),
    )
    worker.progress.connect(lambda t, c: events["progress"].append((t, c)))
    worker.finished.connect(lambda f: events.__setitem__("finished", f))
    worker.errored.connect(lambda m: events.__setitem__("errored", m))
    worker.cancelled.connect(lambda: events.__setitem__("cancelled", True))
    worker.run()
    return events


def test_writes_structure_and_content_for_every_table(db_config, tmp_path):
    out = str(tmp_path / "out.sql")
    opts = {
        "users": {"structure": True, "content": True, "drop": False},
        "orders": {"structure": True, "content": True, "drop": False},
    }
    events = _run(db_config, opts, out)

    assert events["finished"] == []
    text = open(out, encoding="utf-8").read()
    assert "CREATE TABLE" in text and "users" in text
    assert "INSERT INTO \"users\"" in text
    assert "INSERT INTO \"orders\"" in text
    assert events["progress"] == [("users", 1), ("orders", 2)]


def test_run_records_perf_metrics_and_clears_active_task(db_config, tmp_path):
    """Regression guard for issue #172 (timing) and #171 (in-flight
    gauge): run() must record duration under import_export/export and
    never leave the task marked as still in-flight after it returns."""
    perf_metrics.reset()
    out = str(tmp_path / "out.sql")
    opts = {"users": {"structure": False, "content": True, "drop": False}}

    _run(db_config, opts, out)

    assert perf_metrics.active_tasks() == {}
    stats = perf_metrics.snapshot().get("import_export", {}).get("export")
    assert stats is not None
    assert stats["count"] == 1
    assert stats["last"] >= 0


def test_drop_statement_written_when_requested(db_config, tmp_path):
    out = str(tmp_path / "out.sql")
    opts = {"users": {"structure": False, "content": False, "drop": True}}
    _run(db_config, opts, out)
    text = open(out, encoding="utf-8").read()
    assert text.strip() == 'DROP TABLE IF EXISTS "users";'


def test_gzip_output_is_readable(db_config, tmp_path):
    out = str(tmp_path / "out.sql.gz")
    opts = {"users": {"structure": False, "content": True, "drop": False}}
    events = _run(db_config, opts, out, gzip_output=True)
    assert events["finished"] == []
    with gzip.open(out, "rt", encoding="utf-8") as fh:
        text = fh.read()
    assert "INSERT INTO \"users\"" in text
    assert text.count("INSERT INTO") == 5  # one row per statement (batch_kib=None)


def test_bom_written_when_requested(db_config, tmp_path):
    out = str(tmp_path / "out.sql")
    opts = {"users": {"structure": False, "content": True, "drop": False}}
    _run(db_config, opts, out, use_bom=True)
    with open(out, "rb") as fh:
        assert fh.read(3) == b"\xef\xbb\xbf"


def test_batching_reduces_insert_statement_count(db_config, tmp_path):
    out = str(tmp_path / "out.sql")
    opts = {"users": {"structure": False, "content": True, "drop": False}}
    _run(db_config, opts, out, batch_kib=1024)
    text = open(out, encoding="utf-8").read()
    assert text.count("INSERT INTO") == 1


def test_cancel_removes_partial_output_file(db_config, tmp_path):
    out = str(tmp_path / "out.sql")
    flag = threading.Event()
    flag.set()  # simulate cancel requested before/near the start of the run
    opts = {"users": {"structure": True, "content": True, "drop": False},
            "orders": {"structure": True, "content": True, "drop": False}}
    events = _run(db_config, opts, out, cancel_flag=flag)

    assert events["cancelled"] is True
    assert events["finished"] is None
    assert not os.path.exists(out)


def test_table_with_read_error_is_reported_but_others_still_export(db_config, tmp_path):
    out = str(tmp_path / "out.sql")
    opts = {
        "does_not_exist": {"structure": False, "content": True, "drop": False},
        "users": {"structure": False, "content": True, "drop": False},
    }
    events = _run(db_config, opts, out)

    assert events["finished"] is not None
    assert len(events["finished"]) == 1
    assert "does_not_exist" in events["finished"][0]
    text = open(out, encoding="utf-8").read()
    assert "INSERT INTO \"users\"" in text


def test_csv_format_writes_one_zip_member_per_table(db_config, tmp_path):
    import zipfile

    out = str(tmp_path / "out.zip")
    opts = {
        "users": {"structure": False, "content": True, "drop": False},
        "orders": {"structure": False, "content": True, "drop": False},
    }
    events = _run(db_config, opts, out, export_format="csv")

    assert events["finished"] == []
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        assert names == {"users.csv", "orders.csv"}
        users_csv = zf.read("users.csv").decode("utf-8")
        assert users_csv.splitlines()[0] == "id,name"
        assert len(users_csv.splitlines()) == 6  # header + 5 rows


def test_csv_export_sanitizes_path_traversal_table_name(db_config, tmp_path, monkeypatch):
    """Regression for issue #163 (Zip Slip): a table name containing path
    separators/".." must not survive into the zip member name.

    stream_table_rows()'s own unquoted `SELECT * FROM {table_name}` means a
    "/"-bearing identifier can't actually round-trip through a real query
    today, so that incidental gate is bypassed here via monkeypatch to test
    the zip-entry sanitizer in isolation — it's a defense-in-depth fix, not
    contingent on that unrelated query-construction detail staying broken."""
    import zipfile

    malicious_table = "../../../../tmp/evil"
    monkeypatch.setattr(
        DbService, "stream_table_rows",
        lambda self, table_name, chunk_size=2000: iter([(["id"], [(1,)])]),
    )

    out = str(tmp_path / "out.zip")
    opts = {malicious_table: {"structure": False, "content": True, "drop": False}}
    events = _run(db_config, opts, out, export_format="csv")

    assert events["finished"] == []
    with zipfile.ZipFile(out) as zf:
        for name in zf.namelist():
            assert "/" not in name and "\\" not in name


def test_xml_format_writes_one_zip_member_per_table(db_config, tmp_path):
    import zipfile

    out = str(tmp_path / "out.zip")
    opts = {"users": {"structure": False, "content": True, "drop": False}}
    events = _run(db_config, opts, out, export_format="xml")

    assert events["finished"] == []
    with zipfile.ZipFile(out) as zf:
        assert zf.namelist() == ["users.xml"]
        text = zf.read("users.xml").decode("utf-8")
        assert '<table name="users">' in text
        assert text.count("<row>") == 5


def test_csv_bom_written_per_zip_member(db_config, tmp_path):
    import zipfile

    out = str(tmp_path / "out.zip")
    opts = {"users": {"structure": False, "content": True, "drop": False}}
    _run(db_config, opts, out, export_format="csv", use_bom=True)

    with zipfile.ZipFile(out) as zf:
        raw = zf.read("users.csv")
        assert raw.startswith(b"\xef\xbb\xbf")


def test_dot_format_writes_schema_diagram(db_config, tmp_path):
    out = str(tmp_path / "out.dot")
    opts = {
        "users": {"structure": True, "content": False, "drop": False},
        "orders": {"structure": True, "content": False, "drop": False},
    }
    events = _run(db_config, opts, out, export_format="dot")

    assert events["finished"] == []
    assert events["progress"] == [("schema", 1)]
    text = open(out, encoding="utf-8").read()
    assert text.startswith("digraph schema {")
    assert '"users"' in text and '"orders"' in text


def test_dot_format_cancel_removes_partial_file(db_config, tmp_path):
    out = str(tmp_path / "out.dot")
    flag = threading.Event()
    flag.set()
    opts = {"users": {"structure": True, "content": False, "drop": False}}
    events = _run(db_config, opts, out, export_format="dot", cancel_flag=flag)

    assert events["cancelled"] is True
    assert not os.path.exists(out)


@pytest.fixture
def db_config_with_generated_column():
    config = _new_db_config("gen")
    setup = DbService()
    setup.connect(config)
    setup.execute_update(
        "CREATE TABLE items (id INTEGER PRIMARY KEY, price REAL, qty REAL, "
        "total REAL GENERATED ALWAYS AS (price * qty) STORED)"
    )
    setup.execute_update("INSERT INTO items (id, price, qty) VALUES (1, 10.0, 2.0)")
    setup.disconnect()
    yield config
    _drop_db_config(config)


def test_sql_format_excludes_generated_column_from_insert(db_config_with_generated_column, tmp_path):
    out = str(tmp_path / "out.sql")
    opts = {"items": {"structure": False, "content": True, "drop": False}}
    events = _run(db_config_with_generated_column, opts, out)

    assert events["finished"] == []
    text = open(out, encoding="utf-8").read()
    assert "total" not in text
    assert '"price"' in text and '"qty"' in text


def test_csv_format_keeps_generated_column(db_config_with_generated_column, tmp_path):
    import zipfile

    out = str(tmp_path / "out.zip")
    opts = {"items": {"structure": False, "content": True, "drop": False}}
    _run(db_config_with_generated_column, opts, out, export_format="csv")

    with zipfile.ZipFile(out) as zf:
        header = zf.read("items.csv").decode("utf-8").splitlines()[0]
    assert "total" in header


def test_strip_generated_removes_clause_from_structure(db_config_with_generated_column, tmp_path):
    out = str(tmp_path / "out.sql")
    opts = {"items": {"structure": True, "content": False, "drop": False}}
    _run(db_config_with_generated_column, opts, out, strip_generated=True)

    text = open(out, encoding="utf-8").read()
    assert "GENERATED" not in text
