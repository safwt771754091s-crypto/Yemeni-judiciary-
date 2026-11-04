-- ---------------------------------------------------------------------------
-- 006 — Portal roles on the user_role enum
--
-- The five specialized legal portals (judge, lawyer, prosecutor, researcher)
-- and the "viewer" default were added to the application's Role enum, but the
-- database type created in 003_auth_rbac.sql only knew the five
-- administration roles. Any signup or admin edit that picked a portal role
-- therefore failed at the insert with
--   invalid input value for enum user_role: "lawyer".
--
-- `add value if not exists` keeps this idempotent, so re-running the schema
-- against an existing database is a no-op.
-- ---------------------------------------------------------------------------
alter type user_role add value if not exists 'judge';
alter type user_role add value if not exists 'lawyer';
alter type user_role add value if not exists 'prosecutor';
alter type user_role add value if not exists 'researcher';
