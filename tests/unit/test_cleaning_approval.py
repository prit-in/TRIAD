"""PRD US-06: a cleaning plan over its limit pauses for a Data Engineer, and resumes once decided.

Until 10-10-2026 such a plan escalated the run and filed nothing: the approval queue never saw
it, so no Data Engineer could approve it, and EXP-5's "approval requested when more than 5% of
rows are affected" could only ever measure zero.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from agents.graph import queue as q
from agents.graph.state import check_transition
from triad_core.config import settings
from triad_core.governance import GovernanceService
from triad_core.models import Approval, User
from triad_core.policy import PolicyEngine

OVER = {"job_metrics": {"input_rows": 100, "quarantined_rows": 10, "removed_duplicates": 0,
                        "rule_null_customer": "10"}}


def test_a_paused_run_restarts_its_phase_once_approved() -> None:
    check_transition("APPROVAL_REQUIRED", "PROFILED")


@pytest.mark.postgres
class TestCleaningOverItsLimit:
    @pytest.fixture(autouse=True)
    def _db(self, postgres_available, db_session, storage, tmp_path: Path):
        if not postgres_available:
            pytest.skip("postgres service is not reachable")
        from agents.graph.pipeline import ChurnPipeline

        self.session = db_session
        self.quarantine = tmp_path / "quarantine"
        self.quarantine.mkdir()
        self.run = q.create_run(db_session, pipeline_name=f"clean_{uuid.uuid4().hex[:8]}",
                                trigger_type="manual", triggered_by="tester")
        self.pipeline = ChurnPipeline(db_session, self.run, store=storage)

    def _check(self, outputs: dict = OVER):
        return self.pipeline._check_stage_policy("cleaning.execute", "clean", outputs,
                                                 self.quarantine)

    def _decide(self, approval_id: str, *, approved: bool) -> Approval:
        person = User(email=f"de-{uuid.uuid4().hex[:8]}@triad.test", display_name="de",
                      role="data_engineer", is_active=True)
        self.session.add(person)
        self.session.flush()
        return GovernanceService(self.session, PolicyEngine(settings().governance_policy_path)) \
            .decide(uuid.UUID(approval_id), approver=person, approved=approved,
                    comment="the quarantined rows were checked")

    def _paused(self) -> str:
        held = self._check()
        q.set_state(self.session, self.run, "PROFILED")
        q.set_state(self.session, self.run, "APPROVAL_REQUIRED")
        self.run.input_versions = {"transactions": "file:///data/transactions.parquet"}
        return held["approval_id"]

    def test_the_plan_waits_for_a_person_and_commits_once_approved(self) -> None:
        held = self._check()
        assert held["verdict"] == "require_approval"
        approval = self.session.get(Approval, uuid.UUID(held["approval_id"]))
        assert (approval.action_type, approval.status) == ("destructive_cleaning", "pending")
        assert self._check()["approval_id"] == held["approval_id"], "one request, not two"
        self._decide(held["approval_id"], approved=True)
        assert self._check() is None, "the resumed stage finds the approval and commits"

    def test_under_the_limit_nothing_is_asked(self) -> None:
        under = {"job_metrics": {"input_rows": 100, "quarantined_rows": 1,
                                 "removed_duplicates": 0}}
        assert self._check(under) is None
        assert not self.session.execute(
            select(Approval).where(Approval.run_id == self.run.id)
        ).scalars().all()

    def test_an_approval_requeues_the_run_with_its_recorded_inputs(self) -> None:
        from backend.app.services import enqueue_resume

        approval = self._decide(self._paused(), approved=True)
        task = enqueue_resume(self.session, approval)
        assert task is not None and task.task_type == "run"
        assert task.input_refs == {"transactions": "file:///data/transactions.parquet"}

    def test_a_rejection_escalates_the_run_and_queues_nothing(self) -> None:
        from backend.app.services import enqueue_resume

        approval = self._decide(self._paused(), approved=False)
        assert enqueue_resume(self.session, approval) is None
        assert self.run.state == "ESCALATED"
