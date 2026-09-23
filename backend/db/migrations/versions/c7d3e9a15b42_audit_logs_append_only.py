"""Make the audit trail append-only in the database, not only in the documents.

`docker/postgres-init.sql` creates the role `triad_app` and says "grants are applied by the
migration once the table exists". No migration applied any. The role was referenced nowhere
else in the repository, so PRD section 24.4's `GRANT SELECT, INSERT ON audit_logs TO triad_app`
existed as a line in the PRD, a comment in the init script and a docstring on the model - and
as nothing at all in the database. `triad_core/models.py` calls the table "append-only,
hash-chained" and `docs/database-schema.md` says the application role "may read and append,
never update or delete", while the application connects as `triad`, which is a superuser and
could rewrite or truncate the trail at will.

Two things are done here, because the grant alone would not have helped.

**A trigger, which binds every role including the table's owner.** A GRANT does not constrain
the owner or a superuser, and the application connects as one, so a grant to a role nothing
connects as protects nothing. `UPDATE` is refused outright: a row whose hash is derived from
its own contents has no legitimate edit, and `verify_chain` would report the edit as tampering
anyway - failing the write is better than detecting it afterwards. `DELETE` is refused unless
`triad.allow_audit_archival` is set on the session, because `docs/database-schema.md` names
whole-chain archival as a manual administrator procedure and a trail that cannot be archived
cannot meet a retention limit. Setting it is deliberate, session-scoped, and has to be written
down by whoever does it:

    BEGIN;
    SET LOCAL triad.allow_audit_archival = 'on';
    DELETE FROM audit_logs WHERE run_id = '...';   -- whole chains, never single rows
    COMMIT;

Deleting part of a chain leaves the rest unverifiable, which the trigger cannot prevent and
the runbook says not to do.

**The grant the PRD specifies**, so the role it names has the rights it describes. It is not
yet the role the application connects as; giving it a password and the other twenty tables'
grants is a deployment change rather than a schema one, and is recorded as a limitation.

Revision ID: c7d3e9a15b42
Revises: b1f2a3c4d5e6
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c7d3e9a15b42"
down_revision: str | None = "b1f2a3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


GUARD = """
CREATE OR REPLACE FUNCTION triad_audit_logs_append_only()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION
            'audit_logs is append-only: entry % cannot be modified', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- DELETE, allowed only for a session that has opted in.
    IF COALESCE(current_setting('triad.allow_audit_archival', true), 'off') <> 'on' THEN
        RAISE EXCEPTION
            'audit_logs is append-only: entry % cannot be deleted without '
            'SET LOCAL triad.allow_audit_archival = ''on''', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN OLD;
END;
$$;
"""


def upgrade() -> None:
    op.execute(GUARD)
    op.execute(
        """
        CREATE TRIGGER audit_logs_append_only
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION triad_audit_logs_append_only();
        """
    )
    # PRD section 24.4, verbatim. `triad_app` is created by docker/postgres-init.sql; on a
    # database where it is absent - a developer's own server, a test fixture - the grant is
    # skipped rather than failing the migration, because the trigger above is what enforces
    # the property and it does not depend on the role existing.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'triad_app') THEN
                GRANT SELECT, INSERT ON audit_logs TO triad_app;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_logs_append_only ON audit_logs;")
    op.execute("DROP FUNCTION IF EXISTS triad_audit_logs_append_only();")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'triad_app') THEN
                REVOKE SELECT, INSERT ON audit_logs FROM triad_app;
            END IF;
        END
        $$;
        """
    )
