-- Runs once, when the postgres data directory is first created.
-- MLflow keeps its own schema (PRD section 24): separate database, same server.
CREATE DATABASE mlflow OWNER triad;

-- Least privilege for the audit trail (PRD section 24.4): the application role may read and
-- append, never update or delete. The role is created here; grants are applied by the
-- migration once the table exists.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'triad_app') THEN
        CREATE ROLE triad_app;
    END IF;
END
$$;
