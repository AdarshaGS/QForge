"""Shared live-Postgres fixture for benchmark modules that need a real
connection to measure (issue #60/#58's sqlite variants moved here once
sqlite support was removed from the app — see git history). Same
qforge_test@localhost:5432 convention tests/test_db_service_postgresql.py
and tests/test_query_cost.py already use, so a developer who has that
running for the test suite gets working benchmarks for free.

Not a live server the moment you clone the repo, unlike sqlite: callers
must check `available()` first and skip/report cleanly (see bench_db_service.py)
rather than let a bare psycopg2 connection error surface as a crash.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

_ADMIN_PARAMS = dict(host="localhost", port=5432, user="qforge_test",
                      password="qforge_test_pw", database="postgres")


def available() -> bool:
    try:
        import psycopg2
        conn = psycopg2.connect(connect_timeout=2, **_ADMIN_PARAMS)
        conn.close()
        return True
    except Exception:
        return False


UNAVAILABLE_MSG = (
    "Skipped — no local Postgres reachable as qforge_test@localhost:5432 "
    "(see tests/test_db_service_postgresql.py for setup)."
)


@contextmanager
def temp_database(config_name: str):
    """Create a throwaway database, yield a connect() config dict for it,
    then drop it. Mirrors tests/test_query_cost.py's TestPostgresLive.pg_db
    fixture, adapted for a plain script (no pytest fixture teardown)."""
    import psycopg2

    name = f"qforge_bench_{uuid.uuid4().hex[:12]}"
    admin = psycopg2.connect(connect_timeout=5, **_ADMIN_PARAMS)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    admin.close()

    try:
        yield {
            "type": "postgresql", "name": config_name, "host": "localhost",
            "port": 5432, "user": "qforge_test", "password": "qforge_test_pw",
            "database": name,
        }
    finally:
        admin = psycopg2.connect(connect_timeout=5, **_ADMIN_PARAMS)
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.close()
