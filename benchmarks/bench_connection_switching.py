"""Connection & database switching benchmarks (issue #60).

Sqlite-only, same reasoning as bench_db_service.py: it's the one db_type
that needs no live server, so it's the only one that can run unattended.
MySQL/Postgres/SSH-tunnel variants need a reachable server and should be
added as separate, opt-in modules once one is available.

No config here carries an "id" key, so services/schema_snapshot.py's
schema-cache read/write (utils/schema_cache.py, keyed by config["id"]) is
always a no-op — this benchmark never touches disk cache state.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from benchmarks.harness import run_bench
from services.db_service import DbService
from services.schema_snapshot import fetch_schema_snapshot

TABLE_COUNT = 20
ROWS_PER_TABLE = 200


def _make_sqlite_db(path: str) -> None:
    conn = sqlite3.connect(path)
    for t in range(TABLE_COUNT):
        conn.execute(f"CREATE TABLE t{t} (id INTEGER PRIMARY KEY, name TEXT, value REAL)")  # nosec B608 -- t is a local range(TABLE_COUNT) index, not external input
        conn.executemany(
            f"INSERT INTO t{t} (name, value) VALUES (?, ?)",  # nosec B608 -- t is a local range(TABLE_COUNT) index, not external input
            [(f"row-{i}", i * 1.5) for i in range(ROWS_PER_TABLE)],
        )
    conn.commit()
    conn.close()


def benchmarks() -> list:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_a = str(Path(tmpdir) / "a.sqlite")
        db_b = str(Path(tmpdir) / "b.sqlite")
        _make_sqlite_db(db_a)
        _make_sqlite_db(db_b)
        config_a = {"type": "sqlite", "name": "bench-a", "database": db_a}
        config_b = {"type": "sqlite", "name": "bench-b", "database": db_b}

        # "Switching between databases" — sqlite has no lightweight
        # COM_INIT_DB-style fast path (that's MySQL-only, see
        # ui/connection_panel.py's _switch_database), so this is always
        # disconnect+reconnect, same as the real UI code path.
        def switch_once():
            svc = DbService()
            svc.connect(config_a)
            svc.disconnect()
            svc.connect(config_b)
            svc.disconnect()

        results = [run_bench("db_switch.sqlite", switch_once, iterations=10, warmup=2)]

        # "Connection Manager responsiveness" proxy: schema loading is the
        # dominant real cost the UI pays on every connection/db switch
        # (ui/connection_panel.py's _spawn_schema_fetch, which this
        # benchmarks the same underlying call for).
        results.append(
            run_bench(
                "schema_load.sqlite",
                lambda: fetch_schema_snapshot(config_a),
                iterations=10,
                warmup=2,
            )
        )

        return results


if __name__ == "__main__":
    for r in benchmarks():
        print(f"{r.name}: median={r.median_ms:.3f}ms p95={r.p95_ms:.3f}ms (n={r.iterations})")
