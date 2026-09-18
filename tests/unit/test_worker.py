"""The orchestrator worker (PRD 12.5, FR-073, AT-11).

The queue mechanics are tested elsewhere; what matters here is what the worker does with a
claimed task. Every test is about an outcome being *recorded correctly*, because a worker that
quietly marks failed work as done is worse than one that crashes.

These use the real queue against PostgreSQL. The pipeline itself is substituted, so the tests
stay about the worker rather than about Spark.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from agents.graph import churn
from agents.graph import queue as q
from agents.worker import PUBLISH_TASK, RUN_TASK, UnknownTaskType, Worker
from triad_core.models import AgentTask

pytestmark = pytest.mark.postgres


@pytest.fixture()
def run(db_session):
    return q.create_run(
        db_session, pipeline_name="churn_v1", trigger_type="manual",
        triggered_by="tester", idempotency_key=f"worker-{uuid.uuid4()}",
    )


class StubWorker(Worker):
    """A worker whose only difference is that the pipeline is a function under test control.

    It also claims only its own stage. The database is shared with real runs, so an
    unfiltered claim picks up whatever else is queued - which is how these tests first
    "failed" against tasks they had never enqueued.
    """

    def __init__(self, session, result=None, raises=None, stage=None, **kwargs):
        super().__init__(name=f"test-worker-{uuid.uuid4().hex[:6]}", **kwargs)
        self._session = session
        self._result = result
        self._raises = raises
        self.stage = stage
        self.dispatched: list[q.ClaimedTask] = []

    # The real worker opens its own sessions; the tests need it to use the rolled-back one.
    def _claim(self):
        return q.claim(self._session, owner=self.name, lease_seconds=self.lease_seconds,
                       stage=self.stage)

    def _dispatch(self, task):
        self.dispatched.append(task)
        if self._raises is not None:
            raise self._raises
        if self._result is not None:
            return self._result
        return super()._dispatch(task)

    def handle(self, task):
        import contextlib

        # One session for the whole test, instead of the worker's session-per-step.
        @contextlib.contextmanager
        def scope():
            yield self._session

        import agents.worker as module

        original = module.session_scope
        module.session_scope = scope
        try:
            return super().handle(task)
        finally:
            module.session_scope = original


def _stage() -> str:
    """A stage name of this test's own, so its worker cannot claim anyone else's work."""
    return f"test-{uuid.uuid4().hex[:8]}"


def _enqueue(session, run, task_type=RUN_TASK, stage=None, **kwargs):
    return q.enqueue(session, run, stage=stage or _stage(), agent_name="orchestrator",
                     task_type=task_type, **kwargs)


def _task(session, task_id):
    session.expire_all()
    return session.get(AgentTask, task_id)


# --- outcomes ------------------------------------------------------------------


def test_a_successful_phase_completes_the_task(db_session, run) -> None:
    stage = _stage()
    task = _enqueue(db_session, run, stage=stage)
    worker = StubWorker(
        db_session, stage=stage,
        result=churn.PhaseResult(True, "APPROVAL_REQUIRED", "", {"approval_id": "apr-1"}),
    )
    worker.run_forever(max_tasks=1)

    stored = _task(db_session, task.id)
    assert stored.status == "succeeded"
    assert stored.output_refs["state"] == "APPROVAL_REQUIRED"
    assert stored.output_refs["approval_id"] == "apr-1"
    assert stored.finished_at is not None


def test_an_escalating_phase_escalates_the_task_rather_than_retrying(db_session, run) -> None:
    stage = _stage()
    task = _enqueue(db_session, run, stage=stage)
    worker = StubWorker(
        db_session, stage=stage,
        result=churn.PhaseResult(False, "ESCALATED", "contract check failed: output is empty"),
    )
    worker.run_forever(max_tasks=1)

    stored = _task(db_session, task.id)
    assert stored.status == "escalated"
    assert stored.error_class == "CONTRACT_VIOLATION"
    assert stored.error_fingerprint


def test_the_same_failure_always_gets_the_same_fingerprint(db_session, run) -> None:
    """The circuit breaker counts fingerprints, so noise in one would disable it."""
    reason = "contract check failed: output is empty"
    prints = []
    for _ in range(2):
        stage = _stage()
        task = _enqueue(db_session, run, stage=stage)
        StubWorker(db_session, stage=stage,
                   result=churn.PhaseResult(False, "ESCALATED", reason),
                   ).run_forever(max_tasks=1)
        prints.append(_task(db_session, task.id).error_fingerprint)
    assert prints[0] == prints[1]


def test_an_unknown_task_type_escalates_and_is_never_marked_done(db_session, run) -> None:
    stage = _stage()
    task = _enqueue(db_session, run, task_type="something_nobody_implemented", stage=stage)
    # No stub result: the real dispatch runs and refuses.
    worker = StubWorker(db_session, stage=stage)
    worker.run_forever(max_tasks=1)

    stored = _task(db_session, task.id)
    assert stored.status == "escalated"
    assert stored.attempt == 0, "a task nobody can run must not burn its retries"


def test_an_exception_is_recorded_rather_than_escaping(db_session, run) -> None:
    stage = _stage()
    task = _enqueue(db_session, run, stage=stage)
    worker = StubWorker(db_session, stage=stage, raises=RuntimeError("the sandbox went away"))
    worker.run_forever(max_tasks=1)     # must not raise

    stored = _task(db_session, task.id)
    assert stored.status in ("failed", "queued")
    assert stored.error_fingerprint


def test_a_recoverable_failure_is_requeued_within_its_attempt_budget(db_session, run) -> None:
    stage = _stage()
    task = _enqueue(db_session, run, stage=stage, max_attempts=3)
    worker = StubWorker(
        db_session, stage=stage,
        raises=TimeoutError("connection timed out talking to the database"),
    )
    worker.run_forever(max_tasks=1)

    stored = _task(db_session, task.id)
    assert stored.status == "queued" and stored.attempt == 1


def test_dispatch_routes_by_task_type(db_session, run) -> None:
    stage = _stage()
    for task_type in (RUN_TASK, PUBLISH_TASK):
        _enqueue(db_session, run, task_type=task_type, stage=stage)
    worker = StubWorker(db_session, stage=stage, result=churn.PhaseResult(True, "PUBLISHED"))
    worker.run_forever(max_tasks=2)
    assert {t.task_type for t in worker.dispatched} == {RUN_TASK, PUBLISH_TASK}


def test_the_real_dispatcher_refuses_an_unknown_type() -> None:
    worker = Worker(name="dispatch-check")
    task = q.ClaimedTask(id=uuid.uuid4(), run_id=uuid.uuid4(), stage="s",
                         agent_name="a", task_type="nonsense", attempt=0,
                         max_attempts=3, input_refs={})
    with pytest.raises(UnknownTaskType, match="no handler"):
        worker._dispatch(task)


# --- leases --------------------------------------------------------------------


def test_a_claimed_task_is_leased_to_one_worker(db_session, run) -> None:
    stage = _stage()
    _enqueue(db_session, run, stage=stage)
    first = q.claim(db_session, owner="worker-a", lease_seconds=60, stage=stage)
    second = q.claim(db_session, owner="worker-b", lease_seconds=60, stage=stage)
    assert first is not None and second is None


def test_a_lost_lease_stops_the_worker_recording_a_completion(db_session, run) -> None:
    """The reaper already gave the task to someone else; overwriting their work is worse."""
    stage = _stage()
    task = _enqueue(db_session, run, stage=stage)
    worker = StubWorker(db_session, stage=stage, result=churn.PhaseResult(True, "PUBLISHED"))

    import agents.worker as module

    class LostBeat(module.Heartbeat):
        def start(self):
            self.lost = True
            return self

        def stop(self) -> None:
            return None

    original = module.Heartbeat
    module.Heartbeat = LostBeat
    try:
        worker.run_forever(max_tasks=1)
    finally:
        module.Heartbeat = original

    assert _task(db_session, task.id).status != "succeeded"


def test_the_reaper_requeues_a_task_whose_lease_expired(db_session, run) -> None:
    stage = _stage()
    task = _enqueue(db_session, run, stage=stage)
    claimed = q.claim(db_session, owner="worker-that-died", lease_seconds=60, stage=stage)
    assert claimed is not None

    stored = _task(db_session, task.id)
    stored.lease_expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    db_session.flush()

    assert q.reap_expired(db_session) == 1
    assert _task(db_session, task.id).status == "queued"


def test_an_idle_worker_stops_cleanly(db_session) -> None:
    worker = StubWorker(db_session, poll_interval=0.01)
    worker.stopping.set()
    assert worker.run_forever(max_tasks=1) == 0


# --- what the API queues -------------------------------------------------------


def test_the_api_queues_a_task_type_the_worker_understands(db_session) -> None:
    """A mismatch here would queue work that escalates instead of running."""
    from backend.app.api.v1.pipelines import start_run

    assert RUN_TASK == "run"
    assert "task_type=\"run\"" in __import__("inspect").getsource(start_run)


def test_a_decided_promotion_queues_exactly_one_publish_task(db_session, run) -> None:
    from backend.app.services import enqueue_publish
    from triad_core.models import Approval

    approval = Approval(
        run_id=run.id, action_type="model_promotion", target_ref="churn_classifier:1",
        requested_by="someone", evidence_uri="file:///evidence.json", status="approved",
        decision_note="reviewed", expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=1),
        decided_at=dt.datetime.now(dt.UTC),
    )
    db_session.add(approval)
    db_session.flush()

    assert enqueue_publish(db_session, approval) is not None
    assert enqueue_publish(db_session, approval) is None, "queuing twice would promote twice"

    tasks = db_session.execute(
        select(AgentTask).where(AgentTask.run_id == run.id,
                                AgentTask.task_type == PUBLISH_TASK)
    ).scalars().all()
    assert len(tasks) == 1 and tasks[0].status == "queued"


def test_a_pending_approval_queues_nothing(db_session, run) -> None:
    from backend.app.services import enqueue_publish
    from triad_core.models import Approval

    approval = Approval(
        run_id=run.id, action_type="model_promotion", target_ref="churn_classifier:1",
        requested_by="someone", evidence_uri="file:///evidence.json", status="pending",
        expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=1),
    )
    db_session.add(approval)
    db_session.flush()
    assert enqueue_publish(db_session, approval) is None


def test_a_non_promotion_approval_queues_nothing(db_session, run) -> None:
    from backend.app.services import enqueue_publish
    from triad_core.models import Approval

    approval = Approval(
        run_id=run.id, action_type="schema_change", target_ref="transactions",
        requested_by="someone", evidence_uri="file:///evidence.json", status="approved",
        decision_note="ok", expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=1),
        decided_at=dt.datetime.now(dt.UTC),
    )
    db_session.add(approval)
    db_session.flush()
    assert enqueue_publish(db_session, approval) is None
