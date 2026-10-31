-- Health Analytics: Metabase database & least-privilege reporting role.
-- Run as health_admin against the 'postgres' database, NOT in a transaction.
-- Does not modify existing health data or change health_admin's password.
\set ON_ERROR_STOP on

DO $roles$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'metabase_app') THEN
        CREATE ROLE metabase_app LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'metabase_reader') THEN
        CREATE ROLE metabase_reader LOGIN;
    END IF;
END
$roles$;

SELECT 'CREATE DATABASE metabase_app OWNER metabase_app'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'metabase_app')
\gexec

REVOKE CONNECT ON DATABASE metabase_app FROM PUBLIC;
GRANT CONNECT ON DATABASE metabase_app TO metabase_app;
GRANT CONNECT ON DATABASE health TO metabase_reader;

\connect health
GRANT USAGE ON SCHEMA analytics TO metabase_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO metabase_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE health_admin IN SCHEMA analytics
    GRANT SELECT ON TABLES TO metabase_reader;
ALTER ROLE metabase_reader SET default_transaction_read_only = on;

-- After running, assign passwords interactively (not in an SQL file):
--   \password metabase_app
--   \password metabase_reader
-- Set the metabase_app password to match METABASE_APP_PASSWORD in .env.
