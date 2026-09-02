"""Backend benchmarks: DB connection establishment + query execution
(PostgreSQL, against a local qforge_test@localhost:5432 server — see
benchmarks/_pg_fixture.py). Was sqlite-only before sqlite support was
removed from the app; sqlite was originally chosen only because it needed
no live server, not for any dialect-specific reason, so this is a like-for-
like replacement rather than a new benchmark. MySQL needs the same
treatment once a standard local-server convention exists for it too.
"""

from __future__ import annotations

from benchmarks import _pg_fixture
from benchmarks.harness import run_bench
from services.db_service import DbService

ROW_COUNT = 10_000


def _seed(service: DbService) -> None:
    service.execute_update("CREATE TABLE items (id SERIAL PRIMARY KEY, name TEXT, value REAL)")
    service.execute_batch(
        "INSERT INTO items (name, value) VALUES (%s, %s)",
        [(f"item-{i}", i * 1.5) for i in range(ROW_COUNT)],
    )


def benchmarks() -> list:
    if not _pg_fixture.available():
        print(f"bench_db_service: {_pg_fixture.UNAVAILABLE_MSG}")
        return []

    with _pg_fixture.temp_database("bench") as config:
        seed_svc = DbService()
        seed_svc.connect(config)
        _seed(seed_svc)
        seed_svc.disconnect()

        def connect_once():
            svc = DbService()
            svc.connect(config)
            svc.disconnect()

        results = [run_bench("db_connect.postgresql", connect_once, iterations=20, warmup=2)]

        svc = DbService()
        svc.connect(config)
        try:
            results.append(
                run_bench(
                    "query_execute.postgresql_select_10k",
                    lambda: svc.execute_query("SELECT * FROM items"),
                    iterations=10,
                    warmup=2,
                )
            )
            results.append(
                run_bench(
                    "query_execute.postgresql_select_filtered",
                    lambda: svc.execute_query("SELECT * FROM items WHERE value > 5000"),
                    iterations=10,
                    warmup=2,
                )
            )
        finally:
            svc.disconnect()

        return results


if __name__ == "__main__":
    for r in benchmarks():
        print(f"{r.name}: median={r.median_ms:.3f}ms mean={r.mean_ms:.3f}ms (n={r.iterations})")
