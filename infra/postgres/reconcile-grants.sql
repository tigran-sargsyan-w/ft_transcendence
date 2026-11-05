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
    public._prisma_migrations
FROM transcendence_app;

-- Backend: application data access only.
GRANT SELECT, INSERT, UPDATE, DELETE
ON TABLE public.users, public.sessions
TO transcendence_app;

-- Backend: read-only Prisma migration status.
GRANT SELECT
ON TABLE public._prisma_migrations
TO transcendence_app;

COMMIT;
