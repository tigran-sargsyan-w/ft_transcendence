\set ON_ERROR_STOP on

BEGIN;

-- Serialize concurrent provisioning attempts.
SELECT pg_advisory_xact_lock(
    hashtext('ft_transcendence_postgres_roles')
);

-- Create missing roles without enabling passwordless login.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles
        WHERE rolname = 'transcendence_app'
    ) THEN
        CREATE ROLE transcendence_app
            NOLOGIN
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOREPLICATION
            NOBYPASSRLS;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_roles
        WHERE rolname = 'transcendence_migrator'
    ) THEN
        CREATE ROLE transcendence_migrator
            NOLOGIN
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOREPLICATION
            NOBYPASSRLS;
    END IF;
END
$$;

-- Fail if either role has unexpected administrative privileges.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_roles
        WHERE rolname IN (
            'transcendence_app',
            'transcendence_migrator'
        )
        AND (
            rolsuper
            OR rolcreatedb
            OR rolcreaterole
            OR rolreplication
            OR rolbypassrls
        )
    ) THEN
        RAISE EXCEPTION
            'Application or migrator role has forbidden administrative privileges';
    END IF;
END
$$;

-- Application runtime: schema usage without object creation.
GRANT USAGE ON SCHEMA public
TO transcendence_app;

REVOKE CREATE ON SCHEMA public
FROM transcendence_app;

-- Prisma Migrator: required permissions before migrations.
GRANT USAGE, CREATE ON SCHEMA public
TO transcendence_migrator;

COMMIT;
