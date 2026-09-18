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
    def _db(self, postgres_available, db_session):  # noqa: ANN001
        if not postgres_available:
            pytest.skip("postgres service is not reachable")
        self.session = db_session

    def _run(self, key_suffix: str = "a"):  # noqa: ANN202
        return q.create_run(
            self.session,
            pipeline_name="churn_v1",
            trigger_type="manual",
            triggered_by="tester",
            idempotency_key=f"idem-{key_suffix}",
        )

    def test_run_creation_is_idempotent(self) -> None:
        a = self._run("idem")
        b = q.create_run(
            self.session, pipeline_name="churn_v1", trigger_type="manual",
            triggered_by="tester", idempotency_key="idem-idem",
        )
        assert a.id == b.id

    def test_claim_leases_one_task_at_a_time(self) -> None:
        run = self._run("claim")
        q.enqueue(self.session, run, stage="profile", agent_name="profiling", task_type="profile")
        q.enqueue(self.session, run, stage="clean", agent_name="cleaning", task_type="clean",
                  priority=200)

        first = q.claim(self.session, owner="w1")
        assert first is not None
        assert first.stage == "clean", "higher priority must be claimed first"

        second = q.claim(self.session, owner="w2")
        assert second is not None and second.stage == "profile"

        assert q.claim(self.session, owner="w3") is None, "queue is empty"

    def test_completion_and_failure_paths(self) -> None:
        run = self._run("paths")
        task = q.enqueue(self.session, run, stage="s", agent_name="a", task_type="t")
        claimed = q.claim(self.session, owner="w1")
        assert claimed is not None

        q.mark_running(self.session, claimed.id)
        q.complete(self.session, claimed.id, output_refs={"uri": "file:///x"})
        self.session.refresh(task)
        assert task.status == "succeeded"
        assert task.output_refs["uri"] == "file:///x"
        assert task.lease_owner is None

    def test_failure_requeues_within_the_attempt_budget(self) -> None:
        run = self._run("retry")
        task = q.enqueue(self.session, run, stage="s", agent_name="a", task_type="t",
                         max_attempts=2)
        claimed = q.claim(self.session, owner="w1")
        q.fail(self.session, claimed.id, error_class="EXECUTOR_OOM", fingerprint="fp1",
               requeue=True)
        self.session.refresh(task)
        assert task.status == "queued" and task.attempt == 1

        claimed2 = q.claim(self.session, owner="w1")
        q.fail(self.session, claimed2.id, error_class="EXECUTOR_OOM", fingerprint="fp1",
               requeue=True)
        self.session.refresh(task)
        assert task.status == "failed", "the attempt budget stops the retry loop"
        assert task.error_fingerprint == "fp1"

    def test_reaper_requeues_an_expired_lease(self) -> None:
        """AT-11: a crashed worker must not strand a run."""
        import datetime as dt

        run = self._run("reap")
        task = q.enqueue(self.session, run, stage="s", agent_name="a", task_type="t")
        claimed = q.claim(self.session, owner="dead-worker", lease_seconds=300)
        assert claimed is not None

        self.session.refresh(task)
        task.lease_expires_at = q.utcnow() - dt.timedelta(minutes=1)
        self.session.flush()

        assert q.reap_expired(self.session) == 1
        self.session.refresh(task)
        assert task.status == "queued"
        assert task.lease_owner is None

        # and it can be claimed again by a healthy worker
        again = q.claim(self.session, owner="healthy-worker")
        assert again is not None and again.id == task.id

    def test_heartbeat_extends_only_the_holder_lease(self) -> None:
        run = self._run("hb")
        q.enqueue(self.session, run, stage="s", agent_name="a", task_type="t")
        claimed = q.claim(self.session, owner="w1")
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
