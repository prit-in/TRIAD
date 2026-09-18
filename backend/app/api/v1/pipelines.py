"""Pipeline runs: start, inspect, follow, cancel, resume (PRD section 25.2).

Starting a run creates the run record and its first task; it does not execute anything in the
request. Execution belongs to the orchestrator worker, which claims the task from the queue,
so an HTTP timeout can never abandon a half-run pipeline.

Everything read here comes from the database the pipeline actually wrote to. There is no
synthesised progress and no placeholder row: a run with no stages yet returns an empty list.
"""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from agents.graph import queue as q
from backend.app import problems
from backend.app.deps import CurrentUser, DbSession, require_roles
from backend.app.pagination import clamp_limit, decode_cursor, page
from backend.app.schemas import (
    ArtifactView,
    CancelResponse,
    DecisionView,
    LogLine,
    Page,
    PendingApprovalView,
    ResumeRequest,
    RunDetail,
    RunSummary,
    StageView,
    StartRunRequest,
    StartRunResponse,
)
from backend.app.security import DATA_ENGINEER, DATA_SCIENTIST, ML_ENGINEER
from triad_core import audit
from triad_core.config import config_hash
from triad_core.db import session_scope
from triad_core.models import (
    AgentTask,
    Approval,
    CodeArtifact,
    DecisionRecord,
    Event,
    Execution,
    PipelineRun,
)

router = APIRouter(tags=["pipelines"])

STARTER_ROLES = (DATA_ENGINEER, DATA_SCIENTIST, ML_ENGINEER)
FINISHED_STATES = {"COMPLETED", "PUBLISHED", "ABORTED", "CANCELLED", "REJECTED_INPUT"}


def _find_run(session: DbSession, run_ref: str) -> PipelineRun:
    """Accept the readable run key or the internal UUID; the PRD's examples use the key."""
    run = session.execute(
        select(PipelineRun).where(PipelineRun.run_key == run_ref)
    ).scalar_one_or_none()
    if run is None:
        try:
            run = session.get(PipelineRun, uuid.UUID(run_ref))
        except ValueError:
            run = None
    if run is None:
        raise problems.not_found("Run", run_ref)
    return run


def _stages(session: DbSession, run: PipelineRun) -> list[StageView]:
    tasks = session.execute(
        select(AgentTask).where(AgentTask.run_id == run.id).order_by(AgentTask.created_at)
    ).scalars().all()
    out: list[StageView] = []
    for task in tasks:
        duration = None
        if task.started_at and task.finished_at:
            duration = round((task.finished_at - task.started_at).total_seconds(), 1)
        out.append(StageView(name=task.stage, status=task.status,
                             attempts=task.attempt, duration_s=duration))
    return out


def _pending_approval(session: DbSession, run: PipelineRun) -> PendingApprovalView | None:
    approval = session.execute(
        select(Approval)
        .where(Approval.run_id == run.id, Approval.status == "pending")
        .order_by(Approval.requested_at.desc())
    ).scalars().first()
    if approval is None:
        return None
    return PendingApprovalView(
        id=approval.id, action_type=approval.action_type,
        target_ref=approval.target_ref, expires_at=approval.expires_at,
    )


def _summary(run: PipelineRun) -> RunSummary:
    return RunSummary(
        run_id=run.run_key, pipeline=run.pipeline_name, state=run.state,
        current_stage=run.current_stage, trigger_type=run.trigger_type,
        triggered_by=run.triggered_by, started_at=run.started_at,
        finished_at=run.finished_at, error_summary=run.error_summary,
    )


# --- start ---------------------------------------------------------------------


@router.post(
    "/pipelines/run",
    response_model=StartRunResponse,
    status_code=202,
    summary="Start a run",
    dependencies=[Depends(require_roles(*STARTER_ROLES))],
)
def start_run(
    payload: StartRunRequest,
    response: Response,
    session: DbSession,
    user: CurrentUser,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> StartRunResponse:
    body_hash = config_hash({"pipeline": payload.pipeline, "inputs": payload.inputs,
                             "mode": payload.mode, "overrides": payload.config_overrides})

    if idempotency_key:
        existing = session.execute(
            select(PipelineRun).where(PipelineRun.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if existing is not None:
            # Same key, same body: hand back the run that already exists. Same key, different
            # body: the client has a bug, and silently returning the old run would hide it.
            if existing.config_hash != body_hash:
                raise problems.conflict(
                    "Idempotency key reused with a different body",
                    f"Key {idempotency_key} already started run {existing.run_key} with "
                    "different parameters.",
                    problem_type="idempotency-conflict",
                )
            response.status_code = 200
            response.headers["Location"] = f"/api/v1/pipelines/{existing.run_key}"
            return _start_response(existing)

    run = q.create_run(
        session,
        pipeline_name=payload.pipeline,
        trigger_type="manual",
        triggered_by=str(user.id),
        config={"inputs": payload.inputs, "overrides": payload.config_overrides,
                "pipeline": payload.pipeline, "mode": payload.mode},
        input_versions=payload.inputs,
        budget=payload.budget,
        mode=payload.mode,
        idempotency_key=idempotency_key,
    )
    run.config_hash = body_hash
    q.enqueue(
        session, run, stage="profile", agent_name="data_profiling",
        task_type="stage", input_refs=payload.inputs,
    )
    audit.record(
        session, actor_type="user", actor_id=str(user.id), action="run.requested",
        resource_type="pipeline_run", resource_id=str(run.id), run_id=run.id,
        outcome="accepted",
        details={"pipeline": payload.pipeline, "mode": payload.mode,
                 "inputs": list(payload.inputs)},
    )
    response.headers["Location"] = f"/api/v1/pipelines/{run.run_key}"
    return _start_response(run)


def _start_response(run: PipelineRun) -> StartRunResponse:
    return StartRunResponse(
        run_id=run.run_key,
        state=run.state,
        links={
            "self": f"/api/v1/pipelines/{run.run_key}",
            "events": f"/api/v1/pipelines/{run.run_key}/events",
        },
    )


# --- read ----------------------------------------------------------------------


@router.get("/pipelines", response_model=Page, summary="List runs, newest first")
def list_runs(
    session: DbSession,
    user: CurrentUser,
    state: str | None = Query(default=None),
    pipeline: str | None = Query(default=None),
    limit: int = Query(default=50),
    cursor: str | None = Query(default=None),
) -> Page:
    size = clamp_limit(limit)
    stmt = select(PipelineRun).order_by(PipelineRun.started_at.desc(), PipelineRun.id.desc())
    if state:
        stmt = stmt.where(PipelineRun.state == state)
    if pipeline:
        stmt = stmt.where(PipelineRun.pipeline_name == pipeline)
    before = decode_cursor(cursor)
    if before:
        stmt = stmt.where(PipelineRun.started_at < before)

    rows = session.execute(stmt.limit(size + 1)).scalars().all()
    built = page(rows, size, "started_at")
    return Page(items=[_summary(r) for r in built["items"]],
                next_cursor=built["next_cursor"], limit=size)


@router.get("/pipelines/{run_ref}", response_model=RunDetail, summary="Run state and stages")
def get_run(run_ref: str, session: DbSession, user: CurrentUser) -> RunDetail:
    run = _find_run(session, run_ref)
    return RunDetail(
        **_summary(run).model_dump(),
        mode=run.mode,
        config_hash=run.config_hash,
        stages=_stages(session, run),
        budget_used={"llm_calls": run.llm_calls_used, "llm_cost_usd": run.llm_cost_used},
        budget_limits=run.budget_json or {},
        pending_approval=_pending_approval(session, run),
    )


@router.get(
    "/pipelines/{run_ref}/decisions",
    response_model=list[DecisionView],
    summary="Agent decision records",
    dependencies=[Depends(require_roles(*STARTER_ROLES))],
)
def run_decisions(run_ref: str, session: DbSession) -> list[DecisionView]:
    run = _find_run(session, run_ref)
    rows = session.execute(
        select(DecisionRecord)
        .where(DecisionRecord.run_id == run.id)
        .order_by(DecisionRecord.created_at)
    ).scalars().all()
    return [DecisionView.model_validate(r) for r in rows]


@router.get(
    "/pipelines/{run_ref}/artifacts",
    response_model=list[ArtifactView],
    summary="Generated code artifacts and their gate results",
    dependencies=[Depends(require_roles(*STARTER_ROLES))],
)
def run_artifacts(run_ref: str, session: DbSession) -> list[ArtifactView]:
    run = _find_run(session, run_ref)
    rows = session.execute(
        select(CodeArtifact)
        .where(CodeArtifact.run_id == run.id)
        .order_by(CodeArtifact.created_at)
    ).scalars().all()
    return [ArtifactView.model_validate(r) for r in rows]


@router.get(
    "/pipelines/{run_ref}/logs",
    response_model=list[LogLine],
    summary="The run's recorded timeline, redacted",
    dependencies=[Depends(require_roles(*STARTER_ROLES))],
)
def run_logs(
    run_ref: str,
    session: DbSession,
    stage: str | None = Query(default=None),
    level: str | None = Query(default=None, pattern="^(info|warning|error)$"),
) -> list[LogLine]:
    """The timeline the platform recorded, not a raw stdout capture.

    Raw job output stays in the sandbox and is referenced by `executions.log_uri`; it is not
    served here because it is the one place customer values can appear verbatim. What this
    returns is what the platform itself recorded, passed through the same redaction the LLM
    adapter uses, so an identifier cannot leak through a log line.
    """
    from agents.llm.redaction import mask

    run = _find_run(session, run_ref)
    lines: list[LogLine] = []

    for task in session.execute(
        select(AgentTask).where(AgentTask.run_id == run.id).order_by(AgentTask.created_at)
    ).scalars():
        severity = "error" if task.status in ("failed", "escalated") else "info"
        message = f"task {task.task_type} on stage {task.stage} is {task.status}"
        if task.error_class:
            message += f" ({task.error_class}, fingerprint {task.error_fingerprint})"
        lines.append(LogLine(at=task.created_at, stage=task.stage,
                             level=severity, message=message))

    for execution in session.execute(
        select(Execution).where(Execution.run_id == run.id).order_by(Execution.started_at)
    ).scalars():
        severity = "error" if execution.exit_code not in (0, None) else "info"
        masked, _ = mask(execution.command or "")
        lines.append(LogLine(
            at=execution.started_at, stage=execution.engine, level=severity,
            message=f"{execution.engine} exit={execution.exit_code} "
                    f"duration_ms={execution.duration_ms} :: {masked[:500]}",
        ))

    lines.sort(key=lambda line: (line.at is None, line.at))
    if stage:
        lines = [line for line in lines if line.stage == stage]
    if level:
        lines = [line for line in lines if line.level == level]
    return lines


@router.get("/pipelines/{run_ref}/events", summary="Live progress (server-sent events)")
async def run_events(run_ref: str, request: Request, user: CurrentUser) -> EventSourceResponse:
    """Stream the run's own `events` rows as they are written.

    The stream ends when the run reaches a final state, so a browser tab left open does not
    hold a connection for ever. Each event is the row the pipeline wrote in the same
    transaction as the state change, so the stream cannot show progress that was rolled back.
    """
    with session_scope() as session:
        run = _find_run(session, run_ref)
        run_id, run_key, state = run.id, run.run_key, run.state
    last_seen = 0

    async def stream():
        nonlocal last_seen, state
        while True:
            if await request.is_disconnected():
                return
            with session_scope() as session:
                rows = session.execute(
                    select(Event)
                    .where(Event.run_id == run_id, Event.id > last_seen)
                    .order_by(Event.id)
                    .limit(100)
                ).scalars().all()
                payloads = [
                    {"id": e.id, "event": e.event_type, "at": e.created_at.isoformat(),
                     "payload": e.payload}
                    for e in rows
                ]
                if rows:
                    last_seen = rows[-1].id
                current = session.get(PipelineRun, run_id)
                state = current.state if current else state

            for item in payloads:
                yield {"event": item["event"], "id": str(item["id"]),
                       "data": json.dumps(item)}

            if state in FINISHED_STATES:
                yield {"event": "run.finished",
                       "data": json.dumps({"run_id": run_key, "state": state})}
                return
            await asyncio.sleep(1.0)

    return EventSourceResponse(stream())


# --- control -------------------------------------------------------------------


@router.post(
    "/pipelines/{run_ref}/cancel",
    response_model=CancelResponse,
    status_code=202,
    summary="Cancel a run",
)
def cancel_run(run_ref: str, session: DbSession, user: CurrentUser) -> CancelResponse:
    run = _find_run(session, run_ref)
    if run.state in FINISHED_STATES:
        raise problems.conflict(
            "Run already finished",
            f"Run {run.run_key} is {run.state} and cannot be cancelled.",
        )
    # Only the person who started it, or an admin, may stop it.
    if run.triggered_by != str(user.id) and user.role != "admin":
        raise problems.forbidden(
            "Only the user who started this run, or an admin, may cancel it.",
            problem_type="not-run-owner",
        )

    cancelled = 0
    for task in session.execute(
        select(AgentTask).where(
            AgentTask.run_id == run.id,
            AgentTask.status.in_(("queued", "leased", "running")),
        )
    ).scalars():
        task.status = "cancelled"
        cancelled += 1
    q.set_state(session, run, "CANCELLED", actor=str(user.id))
    audit.record(
        session, actor_type="user", actor_id=str(user.id), action="run.cancelled",
        resource_type="pipeline_run", resource_id=str(run.id), run_id=run.id,
        outcome="cancelled", details={"tasks_cancelled": cancelled},
    )
    return CancelResponse(run_id=run.run_key, state=run.state)


@router.post(
    "/pipelines/{run_ref}/resume",
    response_model=CancelResponse,
    status_code=202,
    summary="Resume an escalated run with a fix note",
    dependencies=[Depends(require_roles(DATA_ENGINEER, ML_ENGINEER))],
)
def resume_run(
    run_ref: str, payload: ResumeRequest, session: DbSession, user: CurrentUser
) -> CancelResponse:
    run = _find_run(session, run_ref)
    if run.state != "ESCALATED":
        raise problems.conflict(
            "Run is not escalated",
            f"Run {run.run_key} is {run.state}; only an ESCALATED run can be resumed.",
        )

    stage = run.current_stage or "profile"
    q.enqueue(session, run, stage=stage, agent_name="code_generation",
              task_type="stage", input_refs={"resumed_from": run.state})
    q.set_state(session, run, "RETRYING", stage=stage, actor=str(user.id))
    audit.record(
        session, actor_type="user", actor_id=str(user.id), action="run.resumed",
        resource_type="pipeline_run", resource_id=str(run.id), run_id=run.id,
        outcome="resumed", details={"stage": stage, "fix_note": payload.fix_note[:500]},
    )
    return CancelResponse(run_id=run.run_key, state=run.state)
