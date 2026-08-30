-- Throwaway seed schema for docker-compose's qforge-test-postgres service
-- (issue #139). Loaded once at container init. Actual tests create their
-- own uniquely-named databases (see tests/test_db_service_postgresql.py's
-- pg_database fixture) rather than relying on this table surviving.
CREATE TABLE IF NOT EXISTS seed_probe (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL
);
INSERT INTO seed_probe (name) VALUES ('qforge-fixture-alive');
