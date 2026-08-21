-- Throwaway seed schema for docker-compose's qforge-test-mysql service
-- (issue #139). Loaded once at container init into the qforge_test
-- database. Tests are free to CREATE/DROP their own tables here — this
-- user has full privileges on this database (granted below).
CREATE TABLE IF NOT EXISTS seed_probe (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL
);
INSERT INTO seed_probe (name) VALUES ('qforge-fixture-alive');

-- MYSQL_USER already gets full privileges on MYSQL_DATABASE by default;
-- this also covers any other database a test creates for isolation
-- (mirroring the Postgres suite's per-test throwaway-database pattern).
GRANT ALL PRIVILEGES ON *.* TO 'qforge_test'@'%';
FLUSH PRIVILEGES;
