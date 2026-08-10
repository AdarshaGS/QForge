"""Backend benchmarks: DB connection establishment + query execution (sqlite).

Only sqlite is benchmarked here — it's the one db_type that needs no live
server, so it's the only one that can run unattended/offline. MySQL/Postgres
variants need a reachable server and should be added as a separate, opt-in
module once one is available.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from benchmarks.harness import run_bench
from services.db_service import DbService

ROW_COUNT = 10_000


def _make_sqlite_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, value REAL)")
    conn.executemany(
        "INSERT INTO items (name, value) VALUES (?, ?)",
        [(f"item-{i}", i * 1.5) for i in range(ROW_COUNT)],
    )
    conn.commit()
    conn.close()


def benchmarks() -> list:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "bench.sqlite")
        _make_sqlite_db(db_path)
        config = {"type": "sqlite", "name": "bench", "database": db_path}

        def connect_once():
            svc = DbService()
            svc.connect(config)
            svc.connection.close()

        results = [run_bench("db_connect.sqlite", connect_once, iterations=20, warmup=2)]

        svc = DbService()
        svc.connect(config)
        try:
            results.append(
                run_bench(
                    "query_execute.sqlite_select_10k",
                    lambda: svc.execute_query("SELECT * FROM items"),
                    iterations=10,
                    warmup=2,
                )
            )
            results.append(
                run_bench(
                    "query_execute.sqlite_select_filtered",
                    lambda: svc.execute_query("SELECT * FROM items WHERE value > 5000"),
                    iterations=10,
                    warmup=2,
                )
            )
        finally:
            svc.connection.close()

        return results


if __name__ == "__main__":
    for r in benchmarks():
        print(f"{r.name}: median={r.median_ms:.3f}ms mean={r.mean_ms:.3f}ms (n={r.iterations})")
