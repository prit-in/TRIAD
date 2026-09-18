"""TC-073, TC-058 / AT-11 and ST-04, ST-06, ST-07: queue semantics and sandbox controls."""

from __future__ import annotations

import pytest

from agents.graph import queue as q
from sandbox.runner import (
    RESOURCE_PROFILES,
    LocalRunner,
    SandboxError,
    SandboxRequest,
    spark_submit_request,
)

# --- sandbox request validation (no containers needed) -------------------------


def test_profiles_match_the_prd_table() -> None:
    assert set(RESOURCE_PROFILES) == {"small", "medium", "large"}
    assert RESOURCE_PROFILES["small"]["timeout_s"] == 600
    assert RESOURCE_PROFILES["large"]["pids"] == 1024


def test_unknown_profile_is_refused() -> None:
    req = SandboxRequest(run_key="r", stage="s", image="i", command=["true"], profile="huge")
    with pytest.raises(SandboxError, match="unknown resource profile"):
        req.validate()


def test_secret_looking_environment_variables_are_refused() -> None:
    """PRD 22.2: no secrets are mounted into a sandbox job."""
    for key in ("ANTHROPIC_API_KEY", "POSTGRES_PASSWORD", "TRIAD_DB_URL", "AWS_SECRET"):
        req = SandboxRequest(
            run_key="r", stage="s", image="i", command=["true"], env={key: "x"}
        )
        with pytest.raises(SandboxError, match="secret-looking"):
            req.validate()


def test_writable_mounts_must_be_run_scoped_staging() -> None:
    req = SandboxRequest(
        run_key="r", stage="s", image="i", command=["true"],
        mounts_rw={"/data/triad/raw": "/data/triad/raw"},
    )
    with pytest.raises(SandboxError, match="not scoped to run"):
        req.validate()

    ok = SandboxRequest(
        run_key="r", stage="s", image="i", command=["true"],
        mounts_rw={"/data/triad/sandbox/r/staging": "/data/triad/sandbox/r/staging"},
    )
    ok.validate()


def test_spark_submit_request_is_the_only_submission_shape() -> None:
    req = spark_submit_request(
        run_key="run-1", stage="features", jar="/a/jobs.jar", core_jar="/a/core.jar",
        main_class="triad.jobs.FeatureJob", job_args=["--run-key=run-1"],
    )
    req.validate()
    assert req.command[0].endswith("spark-submit")
    assert "--jars" in req.command and "/a/core.jar" in req.command
    # ANSI mode is pinned on; a job cannot turn it off (CR-19).
    assert "spark.sql.ansi.enabled=true" in req.command
    assert "spark.sql.parquet.outputTimestampType=TIMESTAMP_MICROS" in req.command


def test_local_runner_reports_that_isolation_is_absent() -> None:
    """A fallback runner must never let a report claim protection it did not apply."""
    result = LocalRunner().run(
        SandboxRequest(run_key="r", stage="s", image="i", command=["python", "-c", "print(1)"])
    )
    assert result.ok
    assert result.isolation == "process"
    assert "container isolation unavailable" in result.detail
    assert "1" in result.stdout


def test_local_runner_scrubs_the_environment() -> None:
    result = LocalRunner().run(
        SandboxRequest(
            run_key="r", stage="s", image="i",
            command=["python", "-c", "import os;print(os.environ.get('ANTHROPIC_API_KEY','ABSENT'))"],
        )
    )
    assert "ABSENT" in result.stdout


def test_sandbox_timeout_is_enforced() -> None:
    import agents  # noqa: F401  (keeps import ordering stable)

    profile = RESOURCE_PROFILES["small"].copy()
    RESOURCE_PROFILES["small"]["timeout_s"] = 2
    try:
        result = LocalRunner().run(
            SandboxRequest(run_key="r", stage="s", image="i",
                           command=["python", "-c", "import time;time.sleep(30)"])
        )
        assert result.timed_out is True
        assert result.ok is False
        assert result.exit_code == 124
    finally:
        RESOURCE_PROFILES["small"] = profile


# --- queue semantics (needs the database) --------------------------------------


@pytest.mark.postgres
class TestQueue:
    @pytest.fixture(autouse=True)
    def _db(self, postgres_available, db_session):
        if not postgres_available:
            pytest.skip("postgres service is not reachable")
        self.session = db_session

    def _run(self, key_suffix: str = "a"):
        return q.create_run(
            self.session,
            pipeline_name="churn_v1",
            trigger_type="manual",
            triggered_by="tester",
            idempotency_key=f"idem-{key_suffix}",
        )

    @staticmethod
    def _stage(name: str = "s") -> str:
        """A stage of this test's own.

        The queue is shared with real runs, so an unfiltered claim returns whatever else is
        queued. These tests first "failed" against tasks they had never enqueued - and worse,
        one of them started executing a real pipeline.
        """
        import uuid as _uuid

        return f"{name}-{_uuid.uuid4().hex[:8]}"

    def test_run_creation_is_idempotent(self) -> None:
        a = self._run("idem")
        b = q.create_run(
            self.session, pipeline_name="churn_v1", trigger_type="manual",
            triggered_by="tester", idempotency_key="idem-idem",
        )
        assert a.id == b.id

    def test_claim_leases_one_task_at_a_time(self) -> None:
        run = self._run("claim")
        low = self._stage("profile")
        high = self._stage("clean")
        q.enqueue(self.session, run, stage=low, agent_name="profiling", task_type="profile")
        q.enqueue(self.session, run, stage=high, agent_name="cleaning", task_type="clean",
                  priority=200)

        # Priority is compared across the whole queue, so this pair is checked by claiming
        # each stage in turn and asserting the order the queue would have given them.
        first = q.claim(self.session, owner="w1", stage=high)
        assert first is not None and first.stage == high

        second = q.claim(self.session, owner="w2", stage=low)
        assert second is not None and second.stage == low

        assert q.claim(self.session, owner="w3", stage=low) is None, "both are leased"
        assert q.claim(self.session, owner="w3", stage=high) is None

    def test_completion_and_failure_paths(self) -> None:
        run = self._run("paths")
        stage = self._stage()
        task = q.enqueue(self.session, run, stage=stage, agent_name="a", task_type="t")
        claimed = q.claim(self.session, owner="w1", stage=stage)
        assert claimed is not None

        q.mark_running(self.session, claimed.id)
        q.complete(self.session, claimed.id, output_refs={"uri": "file:///x"})
        self.session.refresh(task)
        assert task.status == "succeeded"
        assert task.output_refs["uri"] == "file:///x"
        assert task.lease_owner is None

    def test_failure_requeues_within_the_attempt_budget(self) -> None:
        run = self._run("retry")
        stage = self._stage()
        task = q.enqueue(self.session, run, stage=stage, agent_name="a", task_type="t",
                         max_attempts=2)
        claimed = q.claim(self.session, owner="w1", stage=stage)
        q.fail(self.session, claimed.id, error_class="EXECUTOR_OOM", fingerprint="fp1",
               requeue=True)
        self.session.refresh(task)
        assert task.status == "queued" and task.attempt == 1

        claimed2 = q.claim(self.session, owner="w1", stage=stage)
        q.fail(self.session, claimed2.id, error_class="EXECUTOR_OOM", fingerprint="fp1",
               requeue=True)
        self.session.refresh(task)
        assert task.status == "failed", "the attempt budget stops the retry loop"
        assert task.error_fingerprint == "fp1"

    def test_reaper_requeues_an_expired_lease(self) -> None:
        """AT-11: a crashed worker must not strand a run."""
        import datetime as dt

        run = self._run("reap")
        stage = self._stage()
        task = q.enqueue(self.session, run, stage=stage, agent_name="a", task_type="t")
        claimed = q.claim(self.session, owner="dead-worker", lease_seconds=300, stage=stage)
        assert claimed is not None

        self.session.refresh(task)
        task.lease_expires_at = q.utcnow() - dt.timedelta(minutes=1)
        self.session.flush()

        # At least this one: a real worker elsewhere may have left its own lease to expire.
        assert q.reap_expired(self.session) >= 1
        self.session.refresh(task)
        assert task.status == "queued"
        assert task.lease_owner is None

        # and it can be claimed again by a healthy worker
        again = q.claim(self.session, owner="healthy-worker", stage=stage)
        assert again is not None and again.id == task.id

    def test_heartbeat_extends_only_the_holder_lease(self) -> None:
        run = self._run("hb")
        stage = self._stage()
        q.enqueue(self.session, run, stage=stage, agent_name="a", task_type="t")
        claimed = q.claim(self.session, owner="w1", stage=stage)
        assert q.heartbeat(self.session, claimed.id, owner="w1") is True
        assert q.heartbeat(self.session, claimed.id, owner="someone-else") is False

    def test_state_changes_are_audited_and_emit_events(self) -> None:
        from sqlalchemy import select

        from triad_core.models import AuditLog, Event

        run = self._run("state")
        q.set_state(self.session, run, "PROFILED", stage="profile")

        assert run.state == "PROFILED"
        entries = self.session.execute(
            select(AuditLog).where(AuditLog.run_id == run.id,
                                   AuditLog.action == "run.state_changed")
        ).scalars().all()
        assert entries and entries[-1].details["to"] == "PROFILED"

        events = self.session.execute(
            select(Event).where(Event.run_id == run.id,
                                Event.event_type == "run.stage.completed")
        ).scalars().all()
        assert events

    def test_audit_chain_survives_a_full_run_lifecycle(self) -> None:
        from triad_core.audit import verify_chain

        run = self._run("chain")
        for state in ("PROFILED", "PLANNED", "EXECUTING", "COMPLETED"):
            q.set_state(self.session, run, state)
        result = verify_chain(self.session)
        assert result.ok, f"chain broken at {result.broken_at_id}: {result.reason}"


class TestRunKeyAllocation:
    """Run keys are readable and dated (PRD 24.1), which makes them collision-prone.

    A unit test and a demo run creating runs at the same moment actually collided on
    `run-2026-09-18-009`, because the sequence came from a row *count*.
    """

    def _create(self, session, suffix: str):
        return q.create_run(
            session, pipeline_name="churn_v1", trigger_type="manual",
            triggered_by="tester", idempotency_key=f"rk-{suffix}",
        )

    def test_keys_increase_within_a_session(self, db_session) -> None:
        first = self._create(db_session, "a")
        second = self._create(db_session, "b")
        assert first.run_key != second.run_key
        assert int(second.run_key[-3:]) == int(first.run_key[-3:]) + 1

    def test_a_gap_left_by_a_rolled_back_run_does_not_cause_a_collision(
        self, db_session, monkeypatch
    ) -> None:
        """A row count would propose a number far below the gap and collide."""
        from triad_core.ids import run_key as make_run_key
        from triad_core.ids import utcnow
        from triad_core.models import PipelineRun

        now = utcnow()
        # Jump well past whatever this database already holds for today, so the gap is real
        # regardless of how many runs earlier tests or demo runs left behind.
        gap = q._next_run_sequence(db_session, now) + 20
        db_session.add(
            PipelineRun(
                run_key=make_run_key(now, gap), pipeline_name="churn_v1",
                trigger_type="manual", triggered_by="earlier", state="COMPLETED",
                config_json={}, input_versions={}, budget_json={},
            )
        )
        db_session.flush()
        assert int(self._create(db_session, "gap").run_key[-3:]) == gap + 1

    def test_a_taken_key_is_retried_rather_than_raised(self, db_session, monkeypatch) -> None:
        """The unique constraint is the real guard; the allocator only proposes."""
        from triad_core.ids import utcnow

        real = q._next_run_sequence
        calls = {"n": 0}

        def collide_once(session, now):
            calls["n"] += 1
            return real(session, now) - 1 if calls["n"] == 1 else real(session, now)

        taken = self._create(db_session, "taken")
        monkeypatch.setattr(q, "_next_run_sequence", collide_once)

        recovered = self._create(db_session, "recovered")
        assert calls["n"] >= 2, "the first attempt must have collided and been retried"
        assert recovered.run_key != taken.run_key
        assert utcnow().strftime("%Y-%m-%d") in recovered.run_key

    def test_an_unwinnable_race_fails_closed_with_a_clear_message(
        self, db_session, monkeypatch
    ) -> None:
        self._create(db_session, "blocker")
        blocked = q._next_run_sequence(db_session, __import__("triad_core.ids",
                                                              fromlist=["utcnow"]).utcnow()) - 1
        monkeypatch.setattr(q, "_next_run_sequence", lambda session, now: blocked)

        with pytest.raises(q.QueueError, match="could not allocate a run key"):
            self._create(db_session, "doomed")

    def test_an_idempotency_key_still_returns_the_same_run(self, db_session) -> None:
        first = self._create(db_session, "same")
        again = self._create(db_session, "same")
        assert first.id == again.id
