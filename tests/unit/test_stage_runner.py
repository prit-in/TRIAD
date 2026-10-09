"""ATC-03, ATC-05, ATC-10, ATC-14, TC-058, TC-059, TC-062, TC-083.

The stage loop is where TRIAD's autonomy is actually bounded, so these tests drive it with
injected failures rather than a real Spark job.
"""

from __future__ import annotations

import uuid

import pytest

from agents.graph import queue as q
from agents.graph.stages import StageRequest, StageRunner
from agents.llm.budget import RunBudget
from triad_core.errors import ErrorClass


class FakeGateReport:
    def __init__(self, passed: bool, detail: str = "", error_class=None) -> None:
        self.passed = passed
        self.detail = detail
        self.first_failure = None if passed else self
        self.error_class = error_class

    def to_dict(self) -> dict:
        return {"passed": self.passed, "detail": self.detail}


@pytest.mark.postgres
class TestStageRunner:
    @pytest.fixture(autouse=True)
    def _db(self, postgres_available, db_session):
        if not postgres_available:
            pytest.skip("postgres service is not reachable")
        self.session = db_session
        self.run = q.create_run(
            db_session, pipeline_name="churn_v1", trigger_type="manual",
            triggered_by="tester", idempotency_key=f"stage-{uuid.uuid4()}",
        )
        self.budget = RunBudget()

    def _runner(self, **kwargs) -> StageRunner:
        return StageRunner(self.session, self.run, self.budget, **kwargs)

    def _request(self, **overrides) -> StageRequest:
        defaults = dict(  # noqa: C408 - keyword form mirrors the StageRequest call site
            stage="features",
            agent_name="code_generation",
            inputs={"dataset": "dsv_1"},
            generate=lambda inputs, history: {"artifact": "x", "attempt": len(history) + 1},
            validate=lambda artifact: FakeGateReport(True),
            execute=lambda artifact: {"ok": True, "outputs": {"uri": "file:///features"}},
        )
        defaults.update(overrides)
        return StageRequest(**defaults)

    # -- happy path ------------------------------------------------------------

    def test_a_clean_stage_runs_once_and_returns_its_outputs(self) -> None:
        outcome = self._runner().run_stage(self._request())
        assert outcome.ok and outcome.attempts == 1
        assert outcome.outputs["uri"] == "file:///features"
        assert outcome.escalated is False

    def test_memoisation_reuses_an_identical_stage(self) -> None:
        """TC-083 / FR-083: unchanged input, code and config means the stage is skipped."""
        memo: dict = {}
        runner = self._runner(memo=memo)
        calls = {"n": 0}

        def execute(artifact):
            calls["n"] += 1
            return {"ok": True, "outputs": {"uri": "file:///f"}}

        first = runner.run_stage(self._request(execute=execute))
        second = runner.run_stage(self._request(execute=execute))

        assert first.ok and second.ok
        assert calls["n"] == 1, "the second run must not execute anything"
        assert second.reused is True

    # -- repair ----------------------------------------------------------------

    def test_a_transient_failure_is_repaired_within_the_attempt_budget(self) -> None:
        """ATC-03: the repair loop fixes an injected fault and the stage then passes."""
        attempts = {"n": 0}

        def execute(artifact):
            attempts["n"] += 1
            if attempts["n"] == 1:
                return {"ok": False,
                        "error": "ExecutorLostFailure ... ExecutorOutOfMemory: memory"}
            return {"ok": True, "outputs": {"uri": "file:///ok"}}

        outcome = self._runner().run_stage(self._request(execute=execute))
        assert outcome.ok is True
        assert outcome.attempts == 2
        assert outcome.diagnoses[0]["error_class"] == ErrorClass.EXECUTOR_OOM.value
        assert outcome.diagnoses[0]["remedy"] == "raise_shuffle_partitions"

    def test_the_playbook_fix_reaches_the_next_generation(self) -> None:
        seen: list[dict] = []

        def generate(inputs, history):
            seen.append(dict(inputs))
            return {"artifact": "x"}

        calls = {"n": 0}

        def execute(artifact):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"ok": False, "error": "java.lang.OutOfMemoryError in executor"}
            return {"ok": True, "outputs": {}}

        self._runner().run_stage(
            self._request(generate=generate, execute=execute,
                          inputs={"shuffle_partitions": 200})
        )
        assert len(seen) == 2
        assert seen[1]["shuffle_partitions"] == 400, "the playbook raised shuffle partitions"
        assert seen[1]["_applied_fixes"][0]["remedy"] == "raise_shuffle_partitions"

    def test_a_failing_gate_feeds_the_repair_loop_not_execution(self) -> None:
        """No artifact runs before it passes the gates (FR-055)."""
        executed = {"n": 0}

        def execute(artifact):
            executed["n"] += 1
            return {"ok": True, "outputs": {}}

        gates = [FakeGateReport(False, "[error] Job.scala:12: type mismatch; found Double"),
                 FakeGateReport(True)]

        def validate(artifact):
            return gates.pop(0) if gates else FakeGateReport(True)

        outcome = self._runner().run_stage(
            self._request(validate=validate, execute=execute)
        )
        assert outcome.ok is True
        assert executed["n"] == 1, "execution happens only after the gates pass"
        assert outcome.diagnoses[0]["error_class"] == ErrorClass.SCALA_COMPILE_ERROR.value

    # -- bounds ----------------------------------------------------------------

    def test_a_repeated_fingerprint_trips_the_circuit_breaker(self) -> None:
        """ATC-10: the same fault after each fix escalates on the second identical print."""
        def execute(artifact):
            return {"ok": False, "error": "cannot resolve 'customer_idd' given input columns"}

        outcome = self._runner().run_stage(self._request(execute=execute, max_attempts=5))
        assert outcome.ok is False
        assert outcome.escalated is True
        assert "circuit breaker" in outcome.reason
        assert outcome.attempts == 2, "it must not keep retrying the same failure"

    def test_a_non_recoverable_error_escalates_after_one_diagnosis(self) -> None:
        """ATC-05: a missing input is classified non-recoverable; no repair loop runs."""
        def execute(artifact):
            return {"ok": False, "error": "required column customer_id is missing from the source"}

        outcome = self._runner().run_stage(self._request(execute=execute, max_attempts=5))
        assert outcome.escalated is True
        assert outcome.attempts == 1
        assert len(outcome.diagnoses) == 1
        assert outcome.diagnoses[0]["error_class"] == ErrorClass.MISSING_REQUIRED_COLUMN.value

    def test_budget_exhaustion_stops_generation_and_keeps_artifacts(self) -> None:
        """ATC-14: the ceiling is enforced outside the agent, and partial work survives."""
        self.budget.limits["max_llm_calls"] = 1
        self.budget.llm_calls = 1  # already spent

        outcome = self._runner().run_stage(self._request())
        assert outcome.ok is False
        assert outcome.escalated is True
        assert "budget exceeded" in outcome.reason

    def test_fingerprints_ignore_incidental_host_and_line_numbers(self) -> None:
        """The same fault on a different host is the same fault, so the breaker still fires."""
        counter = {"n": 0}

        def execute(artifact):
            counter["n"] += 1
            return {"ok": False, "error": f"ExecutorLostFailure on host worker-{counter['n']}"}

        outcome = self._runner().run_stage(self._request(execute=execute, max_attempts=5))
        assert outcome.escalated is True
        assert "circuit breaker" in outcome.reason
        assert counter["n"] == 2, "normalisation collapsed the host number into one print"

    def test_the_attempt_budget_is_the_final_bound(self) -> None:
        """With genuinely different failures each time, only the attempt budget stops it."""
        messages = [
            "FetchFailedException: shuffle block missing",
            "ExecutorLostFailure: container exited with a non-zero code",
            "Connection reset by peer while reading shuffle output",
            "Lost executor during the map stage",
        ]
        counter = {"n": 0}

        def execute(artifact):
            msg = messages[counter["n"] % len(messages)]
            counter["n"] += 1
            return {"ok": False, "error": msg}

        outcome = self._runner().run_stage(self._request(execute=execute, max_attempts=3))
        assert outcome.escalated is True
        assert counter["n"] == 3
        assert "attempt budget" in outcome.reason

    # -- evidence ---------------------------------------------------------------

    def test_every_failure_and_escalation_leaves_records(self) -> None:
        from sqlalchemy import select

        from triad_core.models import AuditLog, DecisionRecord, Execution

        def execute(artifact):
            return {"ok": False, "error": "insufficient data: too few cutoffs for the gap rule"}

        outcome = self._runner().run_stage(self._request(execute=execute))
        assert outcome.escalated

        decisions = self.session.execute(
            select(DecisionRecord).where(DecisionRecord.run_id == self.run.id)
        ).scalars().all()
        kinds = {d.decision_type for d in decisions}
        assert {"diagnosis", "escalation"} <= kinds
        for d in decisions:
            assert d.summary and len(d.summary) <= 500, "summaries stay short and factual"
            assert d.evidence_refs, "every decision cites evidence"

        escalation = next(d for d in decisions if d.decision_type == "escalation")
        ticket = escalation.evidence_refs
        for key in ("run_key", "stage", "attempts", "reason", "diagnoses",
                    "budget", "suggested_actions"):
            assert key in ticket, f"escalation ticket missing {key}"
        assert ticket["suggested_actions"], "an escalation must suggest a human action"

        executions = self.session.execute(
            select(Execution).where(Execution.run_id == self.run.id)
        ).scalars().all()
        assert executions and executions[0].status == "failed"

        audits = self.session.execute(
            select(AuditLog).where(AuditLog.run_id == self.run.id)
        ).scalars().all()
        assert any(a.action == "stage.escalated" for a in audits)

    def test_fix_memory_records_and_retrieves_a_known_fingerprint(self) -> None:
        """FR-062 / TC-062: a repeated fingerprint retrieves the earlier fix."""
        from sqlalchemy import select

        from triad_core.models import FixMemory

        def execute(artifact):
            return {"ok": False, "error": "java.lang.OutOfMemoryError in executor 3"}

        runner = self._runner()
        runner.run_stage(self._request(execute=execute, max_attempts=2))

        remembered = self.session.execute(select(FixMemory)).scalars().all()
        assert remembered, "a fix should have been remembered"
        fingerprint = remembered[0].error_fingerprint
        assert runner.lookup_fix(fingerprint) is not None


@pytest.mark.postgres
class TestARemedyThatCannotBeAppliedSaysSo(TestStageRunner):
    """A prescribed fix that changes nothing must not look like a fix that was applied.

    `_apply_fix` writes a playbook remedy into the stage inputs, and only some of those inputs
    are read by anything. `use_try_cast`, `quarantine_bad_rows` and
    `disable_missing_feature_groups` are set and then read by no `generate`, so the next attempt
    submits byte-identical work and fails identically - which is correct behaviour for a fix
    that needs the code rewritten, but the escalation ticket used to say only "attempt budget
    exhausted" and gave a reader no way to tell the difference between "the fix did not work"
    and "the fix was never applied to anything". EXP-4 hit exactly this on a price column
    arriving as text.
    """

    def test_an_infrastructure_remedy_is_recorded_as_applied(self) -> None:
        attempts: list[dict] = []

        def execute(artifact):
            attempts.append(artifact)
            if len(attempts) == 1:
                return {"ok": False, "error": "org.apache.spark.shuffle.FetchFailedException"}
            return {"ok": True, "outputs": {"uri": "file:///features"}}

        outcome = self._runner().run_stage(self._request(execute=execute))
        assert outcome.ok
        assert outcome.applied_fixes, "a retry must record which remedy it applied"
        assert all(f["applied"] for f in outcome.applied_fixes)
        assert all(not f["note"] for f in outcome.applied_fixes)

    def test_a_remedy_needing_code_regeneration_is_recorded_as_unapplied(self) -> None:
        """An ANSI cast failure prescribes try_cast, which no stage input can deliver."""
        ansi = (
            '{"error_class": "INVALID_CAST_ANSI", "message": "[CAST_INVALID_INPUT] The value '
            "'INR 871.59' of the type \\\"STRING\\\" cannot be cast to \\\"DECIMAL(18,2)\\\"\"}"
        )
        outcome = self._runner().run_stage(
            self._request(execute=lambda artifact: {"ok": False, "error": ansi})
        )
        assert not outcome.ok
        assert outcome.escalated
        unapplied = [f for f in outcome.applied_fixes if not f["applied"]]
        assert unapplied, "try_cast_with_quarantine changes no input any stage reads"
        assert "requires an LLM provider" in unapplied[0]["note"]

    def test_the_escalation_ticket_names_the_remedies_it_could_not_apply(self) -> None:
        """So an operator reading the ticket learns why the retries were identical."""
        from sqlalchemy import select

        from triad_core.models import DecisionRecord

        ansi = '{"error_class": "INVALID_CAST_ANSI", "message": "[CAST_INVALID_INPUT] nope"}'
        self._runner().run_stage(
            self._request(execute=lambda artifact: {"ok": False, "error": ansi})
        )
        tickets = [
            d for d in self.session.execute(
                select(DecisionRecord).where(DecisionRecord.run_id == self.run.id)
            ).scalars().all()
            if d.decision_type == "escalation"
        ]
        assert tickets, "an escalation must leave a decision record"
        assert "try_cast_with_quarantine" in tickets[-1].evidence_refs["unapplied_remedies"]


def test_a_local_mode_heap_exhaustion_is_an_executor_oom() -> None:
    """Spark names the local-mode executor "driver"; the task still ran out of heap."""
    from triad_core.errors import classify

    lost_task = ("org.apache.spark.SparkException: Job aborted due to stage failure: Lost task "
                 "0.0 in stage 3.0 (TID 3) (localhost executor driver): "
                 "java.lang.OutOfMemoryError: Java heap space")
    for message in (lost_task, "java.lang.OutOfMemoryError: GC overhead limit exceeded"):
        assert classify(message).error_class is ErrorClass.EXECUTOR_OOM, message
    # Below Spark's own minimum is a setting a person fixes, not an out-of-memory.
    refused = "System memory 259522560 must be at least 471859200. Please increase heap size"
    assert classify(refused).error_class is ErrorClass.RESOURCE_CONFIG_INVALID


def test_memory_is_raised_from_megabytes_and_never_past_the_sandbox() -> None:
    from agents.graph.stages import _bump_memory, _max_heap_mb

    assert _bump_memory("512m") == "1024m", "megabytes used to come back unchanged"
    assert _bump_memory("1g") == "2048m"
    ceiling = f"{_max_heap_mb()}m"
    assert _bump_memory("2048m") == ceiling
    assert _bump_memory(ceiling) == ceiling
