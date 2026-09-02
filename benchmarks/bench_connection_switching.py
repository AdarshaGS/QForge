"""Connection & database switching benchmarks (issue #60), against a local
qforge_test@localhost:5432 PostgreSQL server (see benchmarks/_pg_fixture.py).
Was sqlite-only before sqlite support was removed from the app — sqlite was
originally chosen only because it needed no live server, and (per its own
prior comment here) never actually exercised MySQL's COM_INIT_DB fast path
anyway, since sqlite always took the same full disconnect+reconnect route
Postgres does — so this is a like-for-like replacement, not a narrower
benchmark. MySQL/SSH-tunnel variants need the same treatment once a
standard local-server convention exists for them too.

No config here carries an "id" key, so services/schema_snapshot.py's
schema-cache read/write (utils/schema_cache.py, keyed by config["id"]) is
always a no-op — this benchmark never touches disk cache state.
"""

from __future__ import annotations

from benchmarks import _pg_fixture
from benchmarks.harness import run_bench
from services.db_service import DbService
from services.schema_snapshot import fetch_schema_snapshot

TABLE_COUNT = 20
ROWS_PER_TABLE = 200


def _seed(service: DbService) -> None:
    for t in range(TABLE_COUNT):
        service.execute_update(
            f"CREATE TABLE t{t} (id SERIAL PRIMARY KEY, name TEXT, value REAL)")  # nosec B608 -- t is a local range(TABLE_COUNT) index, not external input
        service.execute_batch(
            f"INSERT INTO t{t} (name, value) VALUES (%s, %s)",  # nosec B608 -- t is a local range(TABLE_COUNT) index, not external input
            [(f"row-{i}", i * 1.5) for i in range(ROWS_PER_TABLE)],
        )


def benchmarks() -> list:
    if not _pg_fixture.available():
        print(f"bench_connection_switching: {_pg_fixture.UNAVAILABLE_MSG}")
        return []

    with _pg_fixture.temp_database("bench-a") as config_a, \
         _pg_fixture.temp_database("bench-b") as config_b:
        seed_svc = DbService()
        seed_svc.connect(config_a)
        _seed(seed_svc)
        seed_svc.disconnect()
        seed_svc = DbService()
        seed_svc.connect(config_b)
        _seed(seed_svc)
        seed_svc.disconnect()

        # PostgreSQL connections are bound to one database for their
        # lifetime (see services/db_service.py's select_db docstring), so
        # "switching between databases" is always disconnect+reconnect,
        # same as the real UI code path for this dialect.
        def switch_once():
            svc = DbService()
            svc.connect(config_a)
            svc.disconnect()
            svc.connect(config_b)
            svc.disconnect()

        results = [run_bench("db_switch.postgresql", switch_once, iterations=10, warmup=2)]

        # "Connection Manager responsiveness" proxy: schema loading is the
        # dominant real cost the UI pays on every connection/db switch
        # (ui/connection_panel.py's _spawn_schema_fetch, which this
        # benchmarks the same underlying call for).
        results.append(
            run_bench(
                "schema_load.postgresql",
                lambda: fetch_schema_snapshot(config_a),
                iterations=10,
                warmup=2,
            )
        )

        return results


if __name__ == "__main__":
    for r in benchmarks():
        print(f"{r.name}: median={r.median_ms:.3f}ms p95={r.p95_ms:.3f}ms (n={r.iterations})")
