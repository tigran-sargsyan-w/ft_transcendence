\set ON_ERROR_STOP on

BEGIN;

-- Required roles and tables must already exist.
-- Run this script after Prisma migrations.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles
        WHERE rolname = 'transcendence_app'
    ) THEN
        RAISE EXCEPTION 'Missing role: transcendence_app';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_roles
        WHERE rolname = 'transcendence_migrator'
    ) THEN
        RAISE EXCEPTION 'Missing role: transcendence_migrator';
    END IF;

    IF to_regclass('public.users') IS NULL
       OR to_regclass('public.sessions') IS NULL
       OR to_regclass('public._prisma_migrations') IS NULL
       OR to_regclass('public.incidents') IS NULL
       OR to_regclass('public.incident_events') IS NULL
       OR to_regclass('public.friendships') IS NULL
       OR to_regclass('public.incident_events_seq_seq') IS NULL
    THEN
        RAISE EXCEPTION 'Required Prisma tables are missing';
    END IF;
END
$$;

-- Backend: schema access, but no object creation.
GRANT USAGE ON SCHEMA public
TO transcendence_app;

REVOKE CREATE ON SCHEMA public
FROM transcendence_app;

-- Migrator: permission to create and modify schema objects.
GRANT USAGE, CREATE ON SCHEMA public
TO transcendence_migrator;

-- Remove previous direct table grants before applying the allowlist.
REVOKE ALL PRIVILEGES
ON TABLE
    public.users,
    public.sessions,
    public._prisma_migrations,
    public.incidents,
    public.incident_events,
    public.friendships
FROM transcendence_app;

-- Remove previous sequence privileges before applying the allowlist.
REVOKE ALL PRIVILEGES
ON SEQUENCE public.incident_events_seq_seq
FROM transcendence_app;

-- Backend: application data access only.
GRANT SELECT, INSERT, UPDATE, DELETE
ON TABLE public.users, public.sessions
TO transcendence_app;

-- Backend: read-only Prisma migration status.
GRANT SELECT
ON TABLE public._prisma_migrations
TO transcendence_app;

-- Incident API: read, create and update incidents.
GRANT SELECT, INSERT, UPDATE
ON TABLE public.incidents
TO transcendence_app;

-- Incident timeline: append-only events.
GRANT SELECT, INSERT
ON TABLE public.incident_events
TO transcendence_app;

-- Friends API: a user can create, accept and delete friendships.
-- Row ownership is enforced by the NestJS service, not by RLS in v1.
GRANT SELECT, INSERT, UPDATE, DELETE
ON TABLE public.friendships
TO transcendence_app;

-- Required for the SERIAL event sequence.
GRANT USAGE
ON SEQUENCE public.incident_events_seq_seq
TO transcendence_app;

COMMIT;
