"""ST-03: a refusal has to leave a trace, and the trace has to survive the refusal.

`audit.record` flushes rather than commits, on purpose: an entry then commits atomically with
whatever state change it describes, so a rolled-back action leaves no audit entry. That is the
right rule for an action which either happens or does not.

It is the wrong rule for a **refusal**, because a refusal is delivered *by* raising. The
request's transactional scope rolls back on the exception and takes the entry with it, so the
one thing an auditor needs - that someone was refused - is the one thing not recorded.

Measured on a running instance before the fix: three failed logins against
`POST /api/v1/auth/token` each returned 401, and `SELECT count(*) FROM audit_logs WHERE
action = 'auth.login_failed'` stayed at 0.

These tests use a real `session_scope`, not the rolled-back fixture session, because the
property under test is precisely what an outer rollback does to the entry. A savepoint-based
fixture cannot observe it.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import delete, select, text

from triad_core import audit
from triad_core.db import session_scope
from triad_core.models import AuditLog


def _archive(session, condition) -> None:
    """Delete audit rows the way the runbook says an administrator does.

    `audit_logs` has a BEFORE UPDATE OR DELETE trigger that refuses both, for every role
    including the owner the application connects as. A delete has to opt in on the session,
    which is deliberate and session-scoped; there is no opt-in for an update at all.
    """
    session.execute(text("SET LOCAL triad.allow_audit_archival = 'on'"))
    session.execute(delete(AuditLog).where(condition))


@pytest.fixture()
def unique_action():
    """A one-off action name, so the assertions cannot read another test's rows."""
    action = f"test.refusal_{uuid.uuid4().hex[:12]}"
    yield action
    with session_scope() as session:
        _archive(session, AuditLog.action == action)


def _rows(action: str) -> list[AuditLog]:
    with session_scope() as session:
        return list(session.execute(select(AuditLog).where(AuditLog.action == action)).scalars())


def test_a_refusal_entry_survives_the_rollback_that_delivers_the_refusal(
    postgres_available, unique_action
) -> None:
    if not postgres_available:
        pytest.skip("postgres service is not reachable")

    class Refused(Exception):
        pass

    with pytest.raises(Refused):
        with session_scope() as session:
            audit.record_and_commit(
                session,
                actor_type="user",
                actor_id="someone@triad.test",
                action=unique_action,
                resource_type="user",
                resource_id="unknown",
                outcome="refused",
                details={"reason": "invalid credentials"},
            )
            raise Refused("this is how a 401 reaches the client")

    kept = _rows(unique_action)
    assert len(kept) == 1, "the entry documenting the refusal was discarded with the refusal"
    assert kept[0].outcome == "refused"


def test_plain_record_is_still_atomic_with_the_action_it_describes(
    postgres_available, unique_action
) -> None:
    """The fix must not make every entry survive: that property is what the design relies on.

    An action that rolls back has not happened, and an audit trail claiming it did would be
    worse than one missing it. Only refusal paths opt out, by calling a different function.
    """
    if not postgres_available:
        pytest.skip("postgres service is not reachable")

    class Failed(Exception):
        pass

    with pytest.raises(Failed):
        with session_scope() as session:
            audit.record(
                session,
                actor_type="system",
                actor_id="worker",
                action=unique_action,
                resource_type="run",
                resource_id="run-2026-01-01-001",
                outcome="ok",
            )
            raise Failed("the action did not complete")

    assert _rows(unique_action) == [], "a rolled-back action must leave no audit entry"


def test_the_chain_still_verifies_after_a_committed_refusal(
    postgres_available, unique_action
) -> None:
    """Committing early releases the chain's advisory lock early. The chain must still hold."""
    if not postgres_available:
        pytest.skip("postgres service is not reachable")

    with session_scope() as session:
        for _ in range(3):
            audit.record_and_commit(
                session,
                actor_type="user",
                actor_id="someone@triad.test",
                action=unique_action,
                resource_type="user",
                resource_id="unknown",
                outcome="refused",
            )

    assert len(_rows(unique_action)) == 3

    with session_scope() as session:
        result = audit.verify_chain(session)
    assert result.ok, f"chain broken at entry {result.broken_at_id}"


def test_an_approval_that_expires_during_a_decision_stays_expired(postgres_available) -> None:
    """The same defect, in the path that changes a row rather than only recording one.

    `expire_if_due` set `status = 'expired'`, flushed, and recorded the entry; `decide` then
    raised `NothingPending`, and the rollback undid both. The approval was still pending
    afterwards, so the next attempt repeated the work and the expiry was never recorded.
    """
    if not postgres_available:
        pytest.skip("postgres service is not reachable")

    from triad_core.config import settings
    from triad_core.governance import GovernanceService, NothingPending
    from triad_core.models import Approval, PipelineRun, User
    from triad_core.policy import PolicyEngine

    suffix = uuid.uuid4().hex[:8]
    now = dt.datetime.now(dt.UTC)

    with session_scope() as session:
        run = PipelineRun(
            run_key=f"run-expiry-{suffix}", pipeline_name="churn",
            state="RUNNING", trigger_type="manual", mode="full",
        )
        approver = User(
            email=f"approver-{suffix}@triad.test", display_name="approver",
            role="ml_engineer", password_hash="x", is_active=True,
        )
        session.add_all([run, approver])
        session.flush()
        approval = Approval(
            run_id=run.id, action_type="model_promotion", target_ref="churn_classifier:1",
            status="pending", requested_by=f"requester-{suffix}",
            evidence_uri=f"reports/run-expiry-{suffix}/evaluation.json",
            expires_at=now - dt.timedelta(hours=1),
        )
        session.add(approval)
        session.flush()
        approval_id, approver_id, run_id = approval.id, approver.id, run.id

    try:
        with pytest.raises(NothingPending):
            with session_scope() as session:
                approver = session.get(User, approver_id)
                GovernanceService(
                    session, PolicyEngine(settings().governance_policy_path)
                ).decide(
                    approval_id, approver=approver, approved=True, comment="too late",
                )

        with session_scope() as session:
            decided = session.get(Approval, approval_id)
            assert decided.status == "expired", (
                "the expiry was rolled back by the exception that reported it"
            )
            recorded = session.execute(
                select(AuditLog).where(
                    AuditLog.action == "approval.expired",
                    AuditLog.resource_id == str(approval_id),
                )
            ).scalars().all()
            assert len(recorded) == 1, "the expiry left no audit entry"
    finally:
        with session_scope() as session:
            _archive(session, AuditLog.run_id == run_id)
            session.execute(delete(Approval).where(Approval.id == approval_id))
            session.execute(delete(User).where(User.id == approver_id))
            session.execute(delete(PipelineRun).where(PipelineRun.id == run_id))


def test_the_audit_trail_refuses_an_update_from_every_role(postgres_available) -> None:
    """PRD 24.4 asks for append-only. A GRANT to `triad_app` would not have delivered it.

    The grant the PRD specifies now exists, but the application connects as `triad`, which owns
    the table, and a GRANT constrains neither the owner nor a superuser. A BEFORE UPDATE OR
    DELETE trigger constrains both. Before it, `UPDATE audit_logs SET details = '{}'` succeeded
    as the application's own user, so the tamper-evident trail could be rewritten by anything
    holding the application's credentials.
    """
    if not postgres_available:
        pytest.skip("postgres service is not reachable")

    from sqlalchemy.exc import DatabaseError

    with session_scope() as session:
        entry_id = session.execute(
            select(AuditLog.id).order_by(AuditLog.id.desc()).limit(1)
        ).scalar_one_or_none()
    if entry_id is None:
        pytest.skip("no audit entries to attempt to rewrite")

    with pytest.raises(DatabaseError, match="append-only"):
        with session_scope() as session:
            session.execute(
                text("UPDATE audit_logs SET outcome = 'tampered' WHERE id = :i"),
                {"i": entry_id},
            )
            session.flush()

    # There is no opt-in for an update: a row whose hash covers its own contents has no edit.
    with pytest.raises(DatabaseError, match="append-only"):
        with session_scope() as session:
            session.execute(text("SET LOCAL triad.allow_audit_archival = 'on'"))
            session.execute(
                text("UPDATE audit_logs SET outcome = 'tampered' WHERE id = :i"),
                {"i": entry_id},
            )
            session.flush()

    with session_scope() as session:
        still = session.get(AuditLog, entry_id)
        assert still is not None and still.outcome != "tampered"


def test_the_audit_trail_refuses_a_delete_unless_the_session_opts_in(
    postgres_available, unique_action
) -> None:
    """Archival has to remain possible, or a retention limit cannot be met. It has to be asked
    for explicitly, so it cannot happen by accident or by an ORM cascade."""
    if not postgres_available:
        pytest.skip("postgres service is not reachable")

    from sqlalchemy.exc import DatabaseError

    with session_scope() as session:
        audit.record_and_commit(
            session,
            actor_type="user", actor_id="someone@triad.test", action=unique_action,
            resource_type="user", resource_id="unknown", outcome="refused",
        )
    assert len(_rows(unique_action)) == 1

    with pytest.raises(DatabaseError, match="append-only"):
        with session_scope() as session:
            session.execute(delete(AuditLog).where(AuditLog.action == unique_action))
            session.flush()

    assert len(_rows(unique_action)) == 1, "the refused delete removed the row anyway"

    with session_scope() as session:
        _archive(session, AuditLog.action == unique_action)

    assert _rows(unique_action) == []
