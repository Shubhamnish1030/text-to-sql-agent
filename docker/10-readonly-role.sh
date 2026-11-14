#!/bin/sh
# Runs after the Chinook dump. Creates the two demo logins:
#   analyst   SELECT-only  -> what the agent should use in production
#   rw        full rights  -> used by tests to prove the session read-only guard
set -e
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d chinook <<'SQL'
CREATE ROLE analyst LOGIN PASSWORD 'analyst';
GRANT CONNECT ON DATABASE chinook TO analyst;
GRANT USAGE ON SCHEMA public TO analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analyst;
ALTER ROLE analyst SET default_transaction_read_only = on;
CREATE ROLE rw LOGIN PASSWORD 'rw';
GRANT ALL ON ALL TABLES IN SCHEMA public TO rw;
GRANT USAGE ON SCHEMA public TO rw;
SQL
