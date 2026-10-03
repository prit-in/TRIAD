"""M07 task queue on PostgreSQL (CR-09, PRD section 12.5, FR-073).

Workers claim tasks with `FOR UPDATE SKIP LOCKED`, hold a lease with a deadline, and a reaper
re-queues tasks whose lease expired — which is what makes an orchestrator crash survivable
without a second broker (AT-11).

Queue changes commit in the same transaction as run state, so the two can never disagree.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from triad_core import audit
from triad_core.ids import run_key as make_run_key
from triad_core.ids import utcnow
from triad_core.models import AgentTask, Event, PipelineRun

CLAIM_SQL = text(
    """
    UPDATE agent_tasks
    SET status = 'leased',
        lease_owner = :owner,
        lease_expires_at = now() + make_interval(secs => :lease_seconds),
        started_at = COALESCE(started_at, now())
    WHERE id = (
      SELECT id
      FROM agent_tasks
      WHERE status = 'queued'
        -- the cast is required: PostgreSQL cannot infer the type of a bare NULL parameter
        AND (CAST(:stage AS text) IS NULL OR stage = CAST(:stage AS text))
      ORDER BY priority DESC, created_at
      FOR UPDATE SKIP LOCKED
      LIMIT 1
    )
    RETURNING id;
    """
)


@dataclass
class ClaimedTask:
    id: uuid.UUID
    run_id: uuid.UUID
    stage: str
    agent_name: str
    task_type: str
    attempt: int
    max_attempts: int
    input_refs: dict[str, Any]

    @classmethod
    def from_row(cls, task: AgentTask) -> ClaimedTask:
        return cls(
            id=task.id,
            run_id=task.run_id,
            stage=task.stage,
            agent_name=task.agent_name,
            task_type=task.task_type,
            attempt=task.attempt,
            max_attempts=task.max_attempts,
            input_refs=dict(task.input_refs or {}),
        )


RUN_KEY_ATTEMPTS = 10


class QueueError(Exception):
    pass


def _next_run_sequence(session: Session, now: dt.datetime) -> int:
    """Next free number for today, read from the highest key rather than a row count.

    Counting rows is wrong twice: two creators racing both read the same count, and a run
    that was rolled back frees a number that a later run then collides with. The unique
    constraint on `run_key` is the real guard; this only proposes the next candidate.
    """
    prefix = f"run-{now.strftime('%Y-%m-%d')}-"
    keys = session.execute(
        select(PipelineRun.run_key).where(PipelineRun.run_key.like(f"{prefix}%"))
    ).scalars().all()
    highest = 0
    for key in keys:
        suffix = key[len(prefix):]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return highest + 1


def create_run(
    session: Session,
    *,
    pipeline_name: str,
    trigger_type: str,
    triggered_by: str,
    config: dict[str, Any] | None = None,
    input_versions: dict[str, Any] | None = None,
    budget: dict[str, Any] | None = None,
    mode: str = "full",
    idempotency_key: str | None = None,
    policy_version: str | None = None,
) -> PipelineRun:
    """Create a run, or return the existing one for a repeated idempotency key."""
    from triad_core.config import config_hash

    if idempotency_key:
        existing = session.execute(
            select(PipelineRun).where(PipelineRun.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    now = utcnow()
    run: PipelineRun | None = None
    for _ in range(RUN_KEY_ATTEMPTS):
        candidate = PipelineRun(
            run_key=make_run_key(now, _next_run_sequence(session, now)),
            pipeline_name=pipeline_name,
            trigger_type=trigger_type,
            triggered_by=triggered_by,
            state="RECEIVED",
            config_json=config or {},
            config_hash=config_hash(config or {}),
            input_versions=input_versions or {},
            budget_json=budget or {},
            mode=mode,
            idempotency_key=idempotency_key,
            policy_version=policy_version,
        )
        savepoint = session.begin_nested()
        session.add(candidate)
        try:
            session.flush()
        except IntegrityError:
            # Another creator took this key between the read and the insert. The unique
            # constraint is what actually serialises them; this just picks the next number.
            # Rolling the savepoint back already discards the pending instance, so there is
            # nothing left to expunge.
            savepoint.rollback()
            # Two unique constraints can raise here, and they need opposite answers. A run-key
            # collision is retried with the next number, which is what this loop is for. An
            # `idempotency_key` collision means a concurrent request created the run this call
            # was asked for - so the answer is that run, not a retry. Retrying regenerated the
            # run key and hit the same key again on every attempt, then raised QueueError,
            # which reached the client as a 500: two clicks on "Start run" broke the guarantee
            # the Idempotency-Key header exists to give. The pre-check above cannot close this
            # window; only the constraint serialises the two requests.
            if idempotency_key:
                concurrent = session.execute(
                    select(PipelineRun).where(
                        PipelineRun.idempotency_key == idempotency_key
                    )
                ).scalar_one_or_none()
                if concurrent is not None:
                    return concurrent
            continue
        savepoint.commit()
        run = candidate
        break

    if run is None:
        raise QueueError(
            f"could not allocate a run key after {RUN_KEY_ATTEMPTS} attempts; "
            "another process is creating runs faster than this one can number them"
        )
    audit.record(
        session,
        actor_type="user" if triggered_by != "system" else "system",
        actor_id=triggered_by,
        action="run.created",
        resource_type="pipeline_run",
        resource_id=str(run.id),
        run_id=run.id,
        details={"run_key": run.run_key, "pipeline": pipeline_name,
                 "trigger": trigger_type, "mode": mode},
    )
    session.add(
        Event(event_type="run.created", run_id=run.id,
              payload={"run_key": run.run_key, "pipeline": pipeline_name})
    )
    return run


def enqueue(
    session: Session,
    run: PipelineRun,
    *,
    stage: str,
    agent_name: str,
    task_type: str,
    input_refs: dict[str, Any] | None = None,
    priority: int = 100,
    max_attempts: int = 3,
) -> AgentTask:
    task = AgentTask(
        run_id=run.id,
        stage=stage,
        agent_name=agent_name,
        task_type=task_type,
        status="queued",
        priority=priority,
        max_attempts=max_attempts,
        input_refs=input_refs or {},
    )
    session.add(task)
    session.flush()
    return task


def claim(
    session: Session, *, owner: str, lease_seconds: int = 300, stage: str | None = None
) -> ClaimedTask | None:
    """Claim the highest-priority queued task. Concurrent workers never get the same row."""
    row = session.execute(
        CLAIM_SQL, {"owner": owner, "lease_seconds": lease_seconds, "stage": stage}
    ).first()
    if row is None:
        return None
    # The claim is raw SQL, so the identity map still holds the pre-claim row. Without this
    # expiry a later ORM write of the same value looks like "no change" and is never emitted.
    session.expire_all()
    task = session.get(AgentTask, row[0])
    return ClaimedTask.from_row(task) if task else None


def heartbeat(session: Session, task_id: uuid.UUID, *, owner: str, lease_seconds: int = 300) -> bool:
    """Extend a lease. Returns False if the lease was lost (the reaper got there first)."""
    result = session.execute(
        update(AgentTask)
        .where(
            AgentTask.id == task_id,
            AgentTask.lease_owner == owner,
            AgentTask.status.in_(("leased", "running")),
        )
        .values(lease_expires_at=utcnow() + dt.timedelta(seconds=lease_seconds))
    )
    return result.rowcount > 0


def mark_running(session: Session, task_id: uuid.UUID) -> None:
    session.execute(
        update(AgentTask).where(AgentTask.id == task_id).values(status="running")
    )
    session.expire_all()


def complete(
    session: Session, task_id: uuid.UUID, *, output_refs: dict[str, Any] | None = None
) -> None:
    session.execute(
        update(AgentTask)
        .where(AgentTask.id == task_id)
        .values(
            status="succeeded",
            finished_at=utcnow(),
            output_refs=output_refs or {},
            lease_owner=None,
            lease_expires_at=None,
        )
    )
    session.expire_all()


def fail(
    session: Session,
    task_id: uuid.UUID,
    *,
    error_class: str,
    fingerprint: str,
    escalate: bool = False,
    requeue: bool = False,
) -> None:
    """Record a failure. `requeue` puts the task back for another attempt within budget."""
    task = session.get(AgentTask, task_id)
    if task is None:
        return
    task.error_class = error_class
    task.error_fingerprint = fingerprint
    task.lease_owner = None
    task.lease_expires_at = None
    if escalate:
        task.status = "escalated"
        task.finished_at = utcnow()
    elif requeue and task.attempt + 1 < task.max_attempts:
        task.attempt += 1
        task.status = "queued"
    else:
        task.status = "failed"
        task.finished_at = utcnow()
    session.flush()

    # A task that will never run again takes its run with it. This only updated the task, so
    # a run whose phase raised stayed in whatever state it had reached - RECEIVED, for one run
    # observed - with no error, refused by `resume` (only ESCALATED resumes) and never closing
    # its event stream.
    if task.status in ("failed", "escalated"):
        run = session.get(PipelineRun, task.run_id)
        if run is not None and run.state not in TERMINAL_RUN_STATES | {"ESCALATED"}:
            set_state(
                session, run, "ESCALATED" if task.status == "escalated" else "FAILED",
                error_summary=f"task {task.task_type} {task.status}: {error_class} "
                              f"(fingerprint {fingerprint[:12]})",
            )


# States a run cannot leave, and the ones a run waits in for a person rather than a task.
TERMINAL_RUN_STATES = frozenset(
    {"COMPLETED", "ABORTED", "PUBLISHED", "REJECTED_INPUT", "CANCELLED", "FAILED"}
)
AWAITING_A_PERSON = frozenset({"APPROVAL_REQUIRED", "ESCALATED"})
LIVE_TASK_STATUSES = ("queued", "leased", "running")


def reap_stranded_runs(
    session: Session,
    *,
    now: dt.datetime | None = None,
    default_ceiling_minutes: float = 240.0,
    grace_minutes: float = 60.0,
) -> int:
    """Fail runs no task can ever advance, once they are past their own wall-clock ceiling.

    A run is stranded when it is not finished, is not waiting for a person, has no queued,
    leased or running task, and started longer ago than its budget allows any run to take.
    The ceiling is the run's own `max_wall_clock_minutes`, so a run executing in-process
    without a task - `scripts/run_demo.py` does that - is never touched while it could still
    legitimately be running. Thirteen runs from 2026-09-18 had sat in RECEIVED ever since.
    """
    now = now or utcnow()
    candidates = session.execute(
        select(PipelineRun).where(
            PipelineRun.state.not_in(TERMINAL_RUN_STATES | AWAITING_A_PERSON)
        )
    ).scalars().all()
    reaped = 0
    for run in candidates:
        limits = (run.budget_json or {}).get("limits", {})
        ceiling = float(limits.get("max_wall_clock_minutes") or default_ceiling_minutes)
        if run.started_at is None or now - run.started_at < dt.timedelta(
            minutes=ceiling + grace_minutes
        ):
            continue
        live = session.execute(
            select(AgentTask.id).where(
                AgentTask.run_id == run.id, AgentTask.status.in_(LIVE_TASK_STATUSES)
            ).limit(1)
        ).first()
        if live is not None:
            continue
        set_state(
            session, run, "FAILED", actor="reaper",
            error_summary=f"stranded: no live task and past its {ceiling:.0f}-minute ceiling "
                          f"(last state {run.state})",
        )
        reaped += 1
    return reaped


def reap_expired(session: Session, *, limit: int = 100) -> int:
    """Re-queue tasks whose lease expired; a crashed worker must not strand a run (AT-11)."""
    rows = session.execute(
        select(AgentTask)
        .where(
            AgentTask.status.in_(("leased", "running")),
            AgentTask.lease_expires_at.is_not(None),
            AgentTask.lease_expires_at < utcnow(),
        )
        .limit(limit)
    ).scalars().all()
    for task in rows:
        task.status = "queued"
        task.lease_owner = None
        task.lease_expires_at = None
        audit.record(
            session,
            actor_type="system",
            actor_id="reaper",
            action="task.lease_expired",
            resource_type="agent_task",
            resource_id=str(task.id),
            run_id=task.run_id,
            outcome="requeued",
            details={"stage": task.stage, "attempt": task.attempt},
        )
    session.flush()
    return len(rows)


def set_state(
    session: Session, run: PipelineRun, state: str, *, stage: str | None = None,
    error_summary: str | None = None, actor: str = "orchestrator",
) -> None:
    """Record a state transition and its audit entry in the same transaction (FR-073).

    This flushes; the caller's commit makes the transition and its audit entry visible
    together. `ChurnPipeline._advance` commits at every transition - before it did, this
    docstring claimed a commit that nothing performed.
    """
    previous = run.state
    run.state = state
    if stage is not None:
        run.current_stage = stage
    if error_summary is not None:
        run.error_summary = error_summary
    if state in TERMINAL_RUN_STATES:
        run.finished_at = utcnow()
    session.flush()

    audit.record(
        session,
        actor_type="system",
        actor_id=actor,
        action="run.state_changed",
        resource_type="pipeline_run",
        resource_id=str(run.id),
        run_id=run.id,
        details={"from": previous, "to": state, "stage": stage},
    )
    session.add(
        Event(
            event_type="run.stage.completed",
            run_id=run.id,
            payload={"run_key": run.run_key, "from": previous, "to": state, "stage": stage},
        )
    )
    # LISTEN/NOTIFY lets the API stream state changes without polling (PRD 12.1).
    session.execute(
        text("SELECT pg_notify('triad_events', :payload)"),
        {"payload": f'{{"run_key":"{run.run_key}","state":"{state}"}}'},
    )


def pending_tasks(session: Session, run_id: uuid.UUID) -> int:
    return len(
        session.execute(
            select(AgentTask).where(
                AgentTask.run_id == run_id,
                AgentTask.status.in_(("queued", "leased", "running")),
            )
        ).scalars().all()
    )
