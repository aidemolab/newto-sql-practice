-- ====================================================================
-- Newto SQL Practice - pilot app
-- Creates the read-only database login the app uses.
-- Run once per server, as postgres, inside the retailhub database:
--     psql -U postgres -d retailhub -f db/create_practice_role.sql
-- Then set its password (it is never written in a file):
--     \password practice_ro
-- ====================================================================

CREATE ROLE practice_ro LOGIN CONNECTION LIMIT 12;

-- It may look at the tables, and nothing else.
GRANT CONNECT ON DATABASE retailhub TO practice_ro;
GRANT USAGE ON SCHEMA public TO practice_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO practice_ro;

-- Belt and braces: every session is read-only and time-limited.
ALTER ROLE practice_ro SET default_transaction_read_only = on;
ALTER ROLE practice_ro SET statement_timeout = '5s';
