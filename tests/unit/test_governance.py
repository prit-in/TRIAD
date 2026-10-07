"""TC-075, TC-078 / ATC-02, ATC-12, ATC-17, AT-08, AT-13, AT-14, EXP-10.

Governance is the part of TRIAD that must not be clever: every one of these tests asserts a
refusal, and each refusal is deterministic.
"""

from __future__ import annotations

import datetime as dt
import uuid
from itertools import pairwise

import pytest

from agents.graph import queue as q
from triad_core.config import settings
from triad_core.governance import (
    CommentRequired,
    EvidenceBundle,
    GovernanceService,
    NotAuthorised,
    NothingPending,
    PolicyDenied,
    SelfApprovalRefused,
)
from triad_core.models import User
from triad_core.policy import PolicyEngine, Verdict


def _policy() -> PolicyEngine:
    return PolicyEngine(settings().governance_policy_path)


def _evidence() -> EvidenceBundle:
    return EvidenceBundle(
        action_type="model_promotion",
        target_ref="churn_classifier:8",
        metrics_vs_champion={"pr_auc_delta": 0.02, "lower_95": 0.01},
        segment_checks=[{"segment": "country=IN", "recall": 0.61}],
        calibration={"ece": 0.02},
        drift_summary={"max_psi": 0.04},
        model_card_uri="file:///data/triad/reports/card.md",
    )


# --- the policy engine on its own ---------------------------------------------


def test_policy_loads_and_reports_its_version() -> None:
    p = _policy()
    assert p.valid, p.load_error
    assert p.version == "1.0"
    assert p.defaults()["max_llm_calls_per_run"] == 60


def test_forbidden_actions_are_denied() -> None:
    """ATC-02 / EXP-10: an agent can never move an alias, delete data or widen policy."""
    p = _policy()
    for action in ("registry.set_alias", "data.delete", "policy.change",
                   "dependency.add", "external_message.send"):
        decision = p.check(action)
        assert decision.verdict is Verdict.DENY, action
        assert decision.rule_ids


def test_unknown_actions_are_denied_by_omission() -> None:
    decision = _policy().check("something.invented")
    assert decision.verdict is Verdict.DENY
    assert "policy.unknown_action" in decision.rule_ids


def test_supervised_actions_require_approval_with_named_approvers() -> None:
    decision = _policy().check("model.promote")
    assert decision.verdict is Verdict.REQUIRE_APPROVAL
    assert "ml_engineer" in decision.approvers
    assert decision.separation_of_duties is True
    assert "metrics_vs_champion" in decision.required_evidence


def test_bounded_action_within_limits_is_allowed_and_beyond_them_escalates() -> None:
    p = _policy()
    ok = p.check("cleaning.execute", context={"rows_affected_pct": 3.0, "column_drop": False})
    assert ok.verdict is Verdict.ALLOW

    over = p.check("cleaning.execute", context={"rows_affected_pct": 8.0})
    assert over.verdict is Verdict.REQUIRE_APPROVAL
    assert "rows_affected_pct 8.0 > 5.0" in over.reason

    drop = p.check("cleaning.execute", context={"rows_affected_pct": 1.0, "column_drop": True})
    assert drop.verdict is Verdict.REQUIRE_APPROVAL


def test_spark_setting_outside_the_allowlist_is_refused() -> None:
    p = _policy()
    ok = p.check("spark.config_change",
                 context={"settings": {"spark.sql.shuffle.partitions": "400"}})
    assert ok.verdict is Verdict.ALLOW

    bad = p.check("spark.config_change",
                  context={"settings": {"spark.sql.ansi.enabled": "false"}})
    assert bad.verdict is Verdict.DENY
    assert "not on the allowlist" in bad.reason


def test_an_invalid_policy_file_denies_every_gated_action(tmp_path) -> None:
    """SYS-05 / R-14: fail closed."""
    missing = PolicyEngine(tmp_path / "nope.yaml")
    assert missing.valid is False
    assert missing.check("model.promote").verdict is Verdict.DENY

    broken = tmp_path / "broken.yaml"
    broken.write_text("policy_version: '2'\nactions:\n  model.promote:\n    autonomy: whatever\n",
                      encoding="utf-8")
    invalid = PolicyEngine(broken)
    assert invalid.valid is False
    assert "invalid autonomy" in (invalid.load_error or "")
    assert invalid.check("cleaning.execute").verdict is Verdict.DENY

    no_approvers = tmp_path / "no_approvers.yaml"
    no_approvers.write_text(
        "policy_version: '1'\nactions:\n  model.promote:\n    autonomy: supervised\n",
        encoding="utf-8",
    )
    assert PolicyEngine(no_approvers).valid is False


def test_rollback_is_pre_approved_only_after_a_failed_smoke_test() -> None:
    p = _policy()
    allowed = p.check("model.rollback_to_previous",
                      context={"post_deploy_smoke_failed": True})
    assert allowed.verdict is Verdict.ALLOW

    refused = p.check("model.rollback_to_previous",
                      context={"post_deploy_smoke_failed": False})
    assert refused.verdict is Verdict.DENY


# --- the approval workflow (needs the database) --------------------------------


@pytest.mark.postgres
class TestApprovalWorkflow:
    @pytest.fixture(autouse=True)
    def _db(self, postgres_available, db_session):
        if not postgres_available:
            pytest.skip("postgres service is not reachable")
        self.session = db_session
        self.gov = GovernanceService(db_session, _policy())
        self.run = q.create_run(
            db_session, pipeline_name="churn_v1", trigger_type="manual",
            triggered_by="tester", idempotency_key=f"gov-{uuid.uuid4()}",
        )

    def _user(self, role: str, email: str | None = None) -> User:
        u = User(
            email=email or f"{role}-{uuid.uuid4().hex[:8]}@triad.test",
            display_name=role, role=role, is_active=True,
        )
        self.session.add(u)
        self.session.flush()
        return u

    def _request(self, requested_by: str):
        return self.gov.request_approval(
            self.run,
            action="model.promote",
            target_ref="churn_classifier:8",
            requested_by=requested_by,
            evidence=_evidence(),
            evidence_uri="file:///data/triad/reports/evidence.json",
        )

    def test_requesting_creates_a_pending_approval_with_an_expiry(self) -> None:
        approval = self._request("agent:evaluation_agent")
        assert approval.status == "pending"
        assert approval.action_type == "model_promotion"
        assert approval.expires_at > q.utcnow()

    def test_incomplete_evidence_is_refused(self) -> None:
        """AT-08: an approver must see everything on one page, so the bundle is checked."""
        thin = EvidenceBundle(action_type="model_promotion", target_ref="churn_classifier:8")
        with pytest.raises(Exception, match="evidence bundle is incomplete"):
            self.gov.request_approval(
                self.run, action="model.promote", target_ref="churn_classifier:8",
                requested_by="agent:x", evidence=thin, evidence_uri="file:///x",
            )

    def test_a_forbidden_action_cannot_even_be_requested(self) -> None:
        with pytest.raises(PolicyDenied):
            self.gov.request_approval(
                self.run, action="data.delete", target_ref="raw/transactions",
                requested_by="agent:x", evidence=_evidence(), evidence_uri="file:///x",
            )

    def test_approval_by_an_eligible_engineer_succeeds(self) -> None:
        approval = self._request("agent:evaluation_agent")
        engineer = self._user("ml_engineer")
        decided = self.gov.decide(
            approval.id, approver=engineer, approved=True, comment="metrics reviewed, promote"
        )
        assert decided.status == "approved"
        assert decided.decided_by == engineer.id
        assert decided.decision_note

    def test_self_approval_is_refused(self) -> None:
        """ATC-17: the requester cannot approve their own promotion."""
        requester = self._user("ml_engineer")
        approval = self._request(str(requester.id))
        with pytest.raises(SelfApprovalRefused) as exc:
            self.gov.decide(approval.id, approver=requester, approved=True, comment="looks fine")
        assert exc.value.status == 403
        self.session.refresh(approval)
        assert approval.status == "pending", "a refused self-approval must change nothing"

    def test_a_different_engineer_may_approve_the_same_request(self) -> None:
        requester = self._user("ml_engineer")
        other = self._user("ml_engineer")
        approval = self._request(str(requester.id))
        decided = self.gov.decide(approval.id, approver=other, approved=True,
                                  comment="independent review done")
        assert decided.status == "approved"

    def test_wrong_role_is_refused(self) -> None:
        approval = self._request("agent:x")
        analyst = self._user("business_analyst")
        with pytest.raises(NotAuthorised) as exc:
            self.gov.decide(approval.id, approver=analyst, approved=True, comment="fine by me")
        assert exc.value.status == 403

    def test_a_decision_without_a_comment_is_refused(self) -> None:
        approval = self._request("agent:x")
        engineer = self._user("ml_engineer")
        with pytest.raises(CommentRequired) as exc:
            self.gov.decide(approval.id, approver=engineer, approved=True, comment="   ")
        assert exc.value.status == 422

    def test_rejection_is_recorded_and_the_run_keeps_its_champion(self) -> None:
        """AT-13 / ATC-11."""
        approval = self._request("agent:x")
        engineer = self._user("ml_engineer")
        decided = self.gov.decide(approval.id, approver=engineer, approved=False,
                                  comment="segment gap too large for this quarter")
        assert decided.status == "rejected"
        assert "segment gap" in decided.decision_note

    def test_deciding_twice_is_refused(self) -> None:
        approval = self._request("agent:x")
        engineer = self._user("ml_engineer")
        self.gov.decide(approval.id, approver=engineer, approved=True, comment="ok")
        with pytest.raises(NothingPending):
            self.gov.decide(approval.id, approver=engineer, approved=False, comment="changed mind")

    def test_an_expired_request_counts_as_rejected(self) -> None:
        approval = self._request("agent:x")
        approval.expires_at = q.utcnow() - dt.timedelta(hours=1)
        self.session.flush()

        assert self.gov.expire_due() == 1
        self.session.refresh(approval)
        assert approval.status == "expired"

        engineer = self._user("ml_engineer")
        with pytest.raises(NothingPending):
            self.gov.decide(approval.id, approver=engineer, approved=True, comment="too late")

    def test_every_governance_step_is_audited_and_the_chain_holds(self) -> None:
        from sqlalchemy import select

        from triad_core.audit import verify_chain
        from triad_core.models import AuditLog

        requester = self._user("ml_engineer")
        approval = self._request(str(requester.id))
        with pytest.raises(SelfApprovalRefused):
            self.gov.decide(approval.id, approver=requester, approved=True, comment="mine")
        other = self._user("ml_engineer")
        self.gov.decide(approval.id, approver=other, approved=True, comment="reviewed")

        actions = [
            a.action for a in self.session.execute(
                select(AuditLog).where(AuditLog.run_id == self.run.id)
            ).scalars().all()
        ]
        assert "approval.requested" in actions
        assert "approval.self_approval_refused" in actions
        assert "approval.approved" in actions

        assert verify_chain(self.session).ok

    def test_tampering_with_an_audit_entry_breaks_the_chain(self) -> None:
        """TC-078: editing an entry must fail verification.

        Since migration c7d3e9a15b42 an ordinary UPDATE of `audit_logs` is refused by a trigger,
        so the edit can no longer be simulated by simply making it - which is the point of the
        trigger. The attacker this test now models is the one the trigger cannot stop: a
        superuser who switches triggers off for the session first. `session_replication_role =
        replica` is how that is done in PostgreSQL, and it is local to this transaction, which
        the fixture rolls back. What is asserted is unchanged: the edit is detected.
        """
        from sqlalchemy import select, text

        from triad_core.audit import verify_chain
        from triad_core.models import AuditLog

        self._request("agent:x")
        entry = self.session.execute(
            select(AuditLog).where(AuditLog.run_id == self.run.id).order_by(AuditLog.id.desc())
        ).scalars().first()
        assert entry is not None

        self.session.execute(text("SET LOCAL session_replication_role = replica"))
        entry.details = {**entry.details, "tampered": True}
        self.session.flush()
        self.session.execute(text("SET LOCAL session_replication_role = origin"))

        result = verify_chain(self.session)
        assert result.ok is False
        assert result.broken_at_id == entry.id
        assert "entry_hash" in (result.reason or "")


    def test_an_audit_entry_cannot_be_edited_without_disabling_triggers(self) -> None:
        """The first line of defence, before the chain has to detect anything."""
        from sqlalchemy import select
        from sqlalchemy.exc import DatabaseError

        from triad_core.models import AuditLog

        self._request("agent:y")
        entry = self.session.execute(
            select(AuditLog).where(AuditLog.run_id == self.run.id).order_by(AuditLog.id.desc())
        ).scalars().first()
        assert entry is not None

        nested = self.session.begin_nested()
        entry.details = {**entry.details, "tampered": True}
        with pytest.raises(DatabaseError, match="append-only"):
            self.session.flush()
        nested.rollback()


class TestAuditChainUnderConcurrency:
    """The defect this class exists for was real: two writers forked the chain.

    `verify_chain` then reported "prev_hash does not match", which reads as tampering. A
    tamper-evidence mechanism that cries wolf under ordinary load is worse than none, because
    the real alarm is the one that gets ignored.

    These tests commit. They have to: a fork only happens when two *open* transactions read
    the same last entry, so nothing can be proved inside a single rolled-back one.
    """

    def _append(self, run_id, actor: str, ready, go):
        from triad_core import audit
        from triad_core.db import session_factory

        session = session_factory()()
        try:
            # Read the chain head, then wait for the other thread to have done the same.
            # Under the old code both threads then chained from the identical entry.
            audit.record(
                session, actor_type="system", actor_id=actor,
                action="test.concurrent_append", resource_type="test",
                resource_id=actor, run_id=run_id, details={"actor": actor},
            )
            ready.set()
            go.wait(timeout=10)
            session.commit()
        finally:
            session.close()

    def test_two_writers_on_one_chain_do_not_fork_it(self, db_session) -> None:
        import threading

        from sqlalchemy import func, select

        from agents.graph import queue as q
        from triad_core import audit
        from triad_core.db import session_scope
        from triad_core.models import AuditLog

        # A committed run of its own, so the appended entries belong to a chain no other
        # test or run touches - under a pipeline name of its own too: as `churn_v1` it joined
        # that pipeline's run history on every test run, as a cancelled run nobody started.
        with session_scope() as setup:
            run = q.create_run(
                setup, pipeline_name="test_audit_race", trigger_type="manual",
                triggered_by="concurrency-test",
                idempotency_key=f"audit-race-{uuid.uuid4()}",
            )
            run_id = run.id
            # This run is committed on purpose and cannot be deleted afterwards, because its
            # audit entries are append-only. It is left in a terminal state so it is not
            # mistaken for pending work: leaving it in RECEIVED once made the API refuse every
            # new run of this pipeline.
            q.set_state(setup, run, "CANCELLED", actor="concurrency-test")

        first_ready, second_ready = threading.Event(), threading.Event()
        go = threading.Event()
        threads = [
            threading.Thread(target=self._append, args=(run_id, "writer-a", first_ready, go)),
            threading.Thread(target=self._append, args=(run_id, "writer-b", second_ready, go)),
        ]
        for thread in threads:
            thread.start()
        # Release both only once at least one has taken the lock and written.
        first_ready.wait(timeout=15)
        second_ready.wait(timeout=15)
        go.set()
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive(), "an append blocked for ever instead of serialising"

        with session_scope() as check:
            rows = check.execute(
                select(AuditLog).where(AuditLog.run_id == run_id).order_by(AuditLog.id)
            ).scalars().all()
            assert len(rows) >= 2
            prev_hashes = [r.prev_hash for r in rows]
            assert len(set(prev_hashes)) == len(prev_hashes), (
                "two entries share a prev_hash: the chain forked"
            )
            assert audit.verify_chain(check).ok

            # GENESIS is the head of every chain, so sharing *it* is correct. Any other
            # repeated prev_hash means two entries claim the same predecessor.
            forked = check.execute(
                select(AuditLog.prev_hash, func.count())
                .where(AuditLog.prev_hash != audit.GENESIS_HASH)
                .group_by(AuditLog.prev_hash).having(func.count() > 1)
            ).all()
            assert forked == [], f"forked links remain: {forked}"

    def test_entries_are_chained_per_run(self, db_session) -> None:
        """Interleaving two runs must produce two chains, not one interleaved mess."""
        from agents.graph import queue as q
        from triad_core import audit

        runs = [
            q.create_run(db_session, pipeline_name="churn_v1", trigger_type="manual",
                         triggered_by="t", idempotency_key=f"chain-{i}-{uuid.uuid4()}")
            for i in range(2)
        ]
        entries = []
        for _ in range(3):
            for run in runs:
                entries.append(audit.record(
                    db_session, actor_type="system", actor_id="t", action="test.interleaved",
                    resource_type="test", resource_id="x", run_id=run.id,
                ))

        from sqlalchemy import select

        from triad_core.models import AuditLog

        for run in runs:
            # `create_run` already wrote this run's first entry, so the chain starts there.
            chain = db_session.execute(
                select(AuditLog).where(AuditLog.run_id == run.id).order_by(AuditLog.id)
            ).scalars().all()
            assert chain[0].prev_hash == audit.GENESIS_HASH, "each run starts its own chain"
            for earlier, later in pairwise(chain):
                assert later.prev_hash == earlier.entry_hash, (
                    "an interleaved write from the other run broke this chain"
                )
            assert [e.id for e in entries if e.run_id == run.id]
        assert audit.verify_chain(db_session).ok


def test_the_system_chain_is_separate_from_every_run(db_session) -> None:
    from triad_core import audit

    assert audit.chain_scope(None) == audit.SYSTEM_SCOPE
    assert audit.chain_scope(uuid.uuid4()) != audit.SYSTEM_SCOPE
