# Running the tests

```
pytest tests/
```

QForge supports MySQL and PostgreSQL only (sqlite support was removed).
Pure-logic suites need no live database at all, but any suite that needs a
real engine to exercise (schema diff/migration, data diff, ERD, export
worker, generated columns, `stream_table_rows`, schema snapshot, schema
cache invalidation, `services/query_cost.py`'s Postgres path) runs against
a real local PostgreSQL server — there's no in-process, no-setup stand-in
left the way sqlite used to be. These suites **skip themselves
automatically** if that server isn't reachable, so `pytest tests/` still
completes without one; you'll just see those cases skipped rather than
run. CI (`.github/workflows/tests.yml`) starts a matching Postgres service
container so they run for real there.

## Postgres fixture environment (issue #139)

Every live suite connects as the `qforge_test` role against
`localhost:5432`, creating and dropping its own uniquely-named throwaway
database per test for isolation (`tests/test_db_service_postgresql.py`'s
`pg_database` fixture is the reference pattern — follow it for any new
suite).

Start it with Docker Compose from the repo root:

```
docker compose up -d
```

This brings up:

| Service | Host:Port | User | Password | Database |
|---|---|---|---|---|
| `qforge-test-postgres` | `localhost:5432` | `qforge_test` (superuser) | `qforge_test_pw` | `qforge_test` |
| `qforge-test-mysql` | `localhost:3306` | `qforge_test` (full privileges) | `qforge_test_pw` | `qforge_test` |

Each is seeded once at first startup from `tests/fixtures/postgres_seed.sql`
/ `tests/fixtures/mysql_seed.sql` — a single throwaway `seed_probe` table,
not fixture data the tests themselves depend on.

MySQL coverage today is narrower (`tests/test_db_service_mysql_lenient_decoding.py`
is a pure unit test, no live server needed) — no test file in this repo yet
establishes a live-MySQL fixture convention the way Postgres has one. The
MySQL service above is started for when that convention gets added; useful
today for exercising real MySQL/Postgres engine quirks — quoting rules,
identifier length limits, encoding edge cases — that hand-built fixtures
can't catch, e.g. adversarial-identifier handling (issue #114) or import
hardening (issue #115).

Then just run pytest as normal:

```
pytest tests/
```

Tear down (including the containers' data volumes — nothing here is meant
to persist):

```
docker compose down -v
```

### Without Docker

Point the same suites at any local server you already have by creating a
matching role by hand instead:

```
createuser -s qforge_test --pwprompt   # password: qforge_test_pw
# or: psql postgres -c "CREATE ROLE qforge_test WITH LOGIN SUPERUSER PASSWORD 'qforge_test_pw';"
```

```
mysql -e "CREATE USER 'qforge_test'@'localhost' IDENTIFIED BY 'qforge_test_pw'; GRANT ALL PRIVILEGES ON *.* TO 'qforge_test'@'localhost'; FLUSH PRIVILEGES;"
```

`docker-compose.yml` is dev-only — it's never referenced by `build.sh`,
`QForge.spec`, or `.github/workflows/build-release.yml`, and ships nothing
to end users.
