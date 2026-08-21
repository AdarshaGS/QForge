# Running the tests

```
pytest tests/
```

The full suite runs against SQLite alone with no setup required, and that's
what CI (`.github/workflows/tests.yml`) runs — CI has no Docker/MySQL/
Postgres, and isn't meant to.

## Optional: MySQL/Postgres fixture environment (issue #139)

`tests/test_db_service_postgresql.py` and any future MySQL-specific
integration suite exercise real MySQL/Postgres engine quirks — quoting
rules, identifier length limits, encoding edge cases — that SQLite is too
permissive to catch. Useful when working on adversarial-identifier
handling (issue #114) or import hardening (issue #115). These suites
**skip themselves automatically** if the corresponding server isn't
reachable, so this section is entirely optional for everyday `pytest`
runs.

Start both with Docker Compose from the repo root:

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
not fixture data the tests themselves depend on. `test_db_service_postgresql.py`
creates and drops its own uniquely-named database per test for isolation;
follow the same pattern for any new MySQL integration suite.

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
