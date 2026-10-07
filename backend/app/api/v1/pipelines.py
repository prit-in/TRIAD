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
from sqlalchemy import select, tuple_
from sse_starlette.sse import EventSourceResponse

from agents.graph import queue as q
from backend.app import problems
from backend.app.deps import CurrentUser, DbSession, require_roles
from backend.app.pagination import clamp_limit, decode_cursor, page
from backend.app.schemas import (
    ApprovalView,
    ArtifactView,
    CancelResponse,
    DecisionView,
    ExecutionView,
    LogLine,
    OverrideRequest,
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
FINISHED_STATES = {"COMPLETED", "PUBLISHED", "ABORTED", "CANCELLED", "REJECTED_INPUT", "FAILED"}


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


def _stage_label(execution: Execution) -> str:
    """What stage an execution belongs to, for the stage list and the log filter alike.

    Migration `b1f2a3c4d5e6` added `executions.stage` so the stage would stop being inferred.
    The stage list used it; the log endpoint did not - it labelled every execution line with
    `execution.engine` and then filtered `?stage=` against that, so the declared filter could
    never select an execution line. Asking for `?stage=profiling` returned nothing while the
    stage list showed `profiling`. Rows written before the column existed have none, and the
    engine is what is actually known about those, labelled as the engine rather than guessed at.
    """
    return execution.stage or f"({execution.engine})"


def _stages(session: DbSession, run: PipelineRun) -> list[StageView]:
    """The stages of a run, from its queued tasks - or from what actually executed.

    A run driven through the API has `agent_tasks`; one driven directly, as
    `scripts/run_demo.py` does, has none, and the timeline used to come back empty for every
    such run even though four stages had executed and recorded durations. The executions are
    the record of what really ran, so they are the fallback rather than an empty list.

    The two sources are not merged. A run has one shape or the other, and interleaving a task's
    attempt count with an execution's duration would present a timeline that never happened.
    """
    tasks = session.execute(
        select(AgentTask).where(AgentTask.run_id == run.id).order_by(AgentTask.created_at)
    ).scalars().all()
    if tasks:
        out: list[StageView] = []
        for task in tasks:
            duration = None
            if task.started_at and task.finished_at:
                duration = round((task.finished_at - task.started_at).total_seconds(), 1)
            out.append(StageView(name=task.stage, status=task.status,
                                 attempts=task.attempt, duration_s=duration))
        return out

    executions = session.execute(
        select(Execution).where(Execution.run_id == run.id).order_by(Execution.started_at)
    ).scalars().all()
    views: list[StageView] = []
    for execution in executions:
        duration = None
        if execution.duration_ms is not None:
            duration = round(execution.duration_ms / 1000.0, 1)
        elif execution.started_at and execution.finished_at:
            duration = round((execution.finished_at - execution.started_at).total_seconds(), 1)
        views.append(
            StageView(
                name=_stage_label(execution),
                status=execution.status,
                # An execution is one attempt by definition: the repair loop creates a new row
                # per attempt, so counting them as 1 each is the truth, not a placeholder.
                attempts=1,
                duration_s=duration,
            )
        )
    return views


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


def _active_run(session: DbSession, pipeline: str) -> PipelineRun | None:
    """The run, if any, that genuinely still holds this pipeline's shared outputs.

    "Active" is defined by **live work**, not by the state column. A run whose state is not
    final but which has no queued, leased or running task is going nowhere: nothing will ever
    move it on. Reading the state alone meant one abandoned row - a crashed process, or a test
    that committed a run and walked away - refused every future run of that pipeline until a
    person cancelled it by hand. That happened.
    """
    return session.execute(
        select(PipelineRun)
        .join(AgentTask, AgentTask.run_id == PipelineRun.id)
        .where(
            PipelineRun.pipeline_name == pipeline,
            PipelineRun.state.notin_(tuple(FINISHED_STATES | {"ESCALATED", "FAILED"})),
            AgentTask.status.in_(("queued", "leased", "running")),
        )
        .order_by(PipelineRun.started_at.desc())
        .limit(1)
    ).scalars().first()


def _resolve_inputs(session: DbSession, inputs: dict[str, str]) -> dict[str, str]:
    """Turn dataset names into the storage URI of their latest version.

    A caller may pass either a `file://` URI or a registered dataset name. Names are resolved
    here, at request time, so "that dataset has no versions yet" is answered immediately
    rather than becoming a worker failure ten minutes later.
    """
    from triad_core.models import Dataset, DatasetVersion

    resolved: dict[str, str] = {}
    for logical, reference in inputs.items():
        if reference.startswith(("file://", "s3://", "abfss://")):
            resolved[logical] = reference
            continue

        dataset = session.execute(
            select(Dataset).where(Dataset.name == reference)
        ).scalar_one_or_none()
        if dataset is None:
            raise problems.unprocessable(
                f"input {logical!r}: no dataset named {reference!r} is registered."
            )
        version = session.execute(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == dataset.id)
            .order_by(DatasetVersion.version_no.desc())
        ).scalars().first()
        if version is None:
            raise problems.unprocessable(
                f"input {logical!r}: dataset {reference!r} has no registered version."
            )
        resolved[logical] = version.storage_uri
    return resolved


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
    resolved_inputs = _resolve_inputs(session, payload.inputs)


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

    # Only now, after the idempotency check: a retry of a request that already started a run
    # must get that run back, not "the pipeline is busy" - the busy one *is* its own run.
    active = _active_run(session, payload.pipeline)
    if active is not None:
        raise problems.conflict(
            "A run of this pipeline is already active",
            f"{active.run_key} is {active.state} with work still queued. Runs of "
            f"{payload.pipeline} share their clean, feature and label folders, so they are "
            "run one at a time; cancel it or wait.",
            problem_type="pipeline-busy",
        )

    run = q.create_run(
        session,
        pipeline_name=payload.pipeline,
        trigger_type="manual",
        triggered_by=str(user.id),
        config={"inputs": payload.inputs, "overrides": payload.config_overrides,
                "pipeline": payload.pipeline, "mode": payload.mode},
        input_versions=resolved_inputs,
        budget=payload.budget,
        mode=payload.mode,
        idempotency_key=idempotency_key,
    )
    run.config_hash = body_hash
    # One task for the run's preparation phase. The worker claims it and drives the stages;
    # the run then pauses at the approval gate and a second task carries out the decision.
    q.enqueue(
        session, run, stage="pipeline", agent_name="orchestrator",
        task_type="run", input_refs=resolved_inputs,
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
        # Row-wise comparison on the same pair the ORDER BY uses. Seeking on `started_at`
        # alone skipped every row sharing a timestamp with the previous page's last row, and
        # `now()` gives every row inserted in one transaction the same timestamp.
        stmt = stmt.where(
            tuple_(PipelineRun.started_at, PipelineRun.id) < tuple_(*before)
        )

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
        override=_override_offer(run),
    )


def _override_offer(run: PipelineRun) -> dict | None:
    """What an escalated run's candidate failed, if a person may promote it anyway (PRD 30) -
    and, once it has been, what it was promoted despite. A published run kept showing its
    escalation reason as an error, with nothing to say two approvers had accepted it."""
    if run.state not in ("ESCALATED", "APPROVAL_REQUIRED", "DEPLOYED", "PUBLISHED", "COMPLETED"):
        return None
    from agents.graph.churn import read_state
    from triad_core.storage import storage

    try:
        offer = (read_state(storage(), run).get("override") or {})
    except (FileNotFoundError, ValueError):
        return None
    return offer if offer.get("eligible") else None


@router.post(
    "/pipelines/{run_ref}/override",
    response_model=list[ApprovalView],
    status_code=202,
    summary="Ask two approvers to promote despite a failed check (PRD 30)",
    dependencies=[Depends(require_roles(*STARTER_ROLES))],
)
def request_override(
    run_ref: str, payload: OverrideRequest, session: DbSession, user: CurrentUser
) -> list[ApprovalView]:
    """PRD section 30: "Promotion despite a failed check - two approvers, ML Engineer and
    Admin - written justification." Only a candidate that failed a calibration or segment
    rule, and would otherwise have been proposed, is eligible; the requester may approve
    neither share, and nor may whoever started the run."""
    from agents.graph.churn import read_state
    from triad_core.config import settings
    from triad_core.governance import (
        EvidenceBundle,
        GovernanceError,
        GovernanceService,
        PolicyDenied,
    )
    from triad_core.policy import PolicyEngine
    from triad_core.storage import storage

    run = _find_run(session, run_ref)
    if run.state != "ESCALATED":
        raise problems.conflict(
            "Run is not escalated",
            f"Run {run.run_key} is {run.state}; only an ESCALATED run's candidate can be "
            "promoted despite a failed check.",
        )
    offer = _override_offer(run)
    if offer is None:
        raise problems.conflict(
            "Nothing to override",
            f"Run {run.run_key} escalated without a candidate that may be promoted: only a "
            "failed calibration or segment rule, on a candidate that beats the champion, can "
            "be overridden.",
            problem_type="not-overridable",
        )
    already = session.execute(
        select(Approval).where(
            Approval.run_id == run.id,
            Approval.policy_action == "model.promote_with_failed_check",
            Approval.status.in_(("pending", "approved")),
        )
    ).scalars().first()
    if already is not None:
        raise problems.conflict("Already requested",
                                "A promotion despite a failed check is already pending for "
                                "this run.", problem_type="already-requested")

    store = storage()
    state = read_state(store, run)
    selection = state.get("selection") or {}
    target = f"{state.get('model_name')}:{state.get('mlflow_version') or 'unregistered'}"
    # PRD 30.5: what the two approvers decide on, on one page. The bundle used to say only
    # "AP-6 failed": the gaps that failed it, the segment-aware threshold review (PRD 19.4) and
    # the calibration were all in the run's evaluation and none of it reached the approvers.
    measured = selection.get("evidence") or {}
    review = measured.get("segment_threshold_review") or {}
    failed = offer.get("failed_rules", [])
    evidence = EvidenceBundle(
        action_type="policy_override",
        target_ref=target,
        metrics_vs_champion=selection.get("champion_delta")
        or {"note": "no champion yet: first model"},
        segment_checks=[
            {"rule": rule, "result": "failed",
             **({"recall_gaps": measured.get("segment_recall_gaps"), "threshold_review": review}
                if rule == "AP-6" else {})}
            for rule in failed
        ],
        calibration={key: measured[key] for key in (
            "calibration", "ece_after_calibration", "brier_skill_after_calibration",
            "calibration_remedies_tried") if key in measured},
        model_card_uri=str(store.reports_dir(run.run_key) / "model_card.md"),
        decision_records=[{"outcome": "ESCALATE", "reason": offer.get("reason"),
                           "failed_rules": failed}],
        risk_notes=[note for note in (offer.get("reason"), review.get("note")) if note],
        override_note=payload.note.strip(),
    )
    evidence_path = store.write_json(
        store.reports_dir(run.run_key) / "override_evidence.json", evidence.to_dict()
    )
    service = GovernanceService(session, PolicyEngine(settings().governance_policy_path))
    try:
        shares = service.request_approvals(
            run, action="model.promote_with_failed_check", target_ref=target,
            requested_by=str(user.id), evidence=evidence,
            evidence_uri=store.uri(evidence_path),
        )
    except PolicyDenied as exc:
        raise problems.forbidden(str(exc), problem_type="policy-denied") from exc
    except GovernanceError as exc:
        raise problems.unprocessable(str(exc)) from exc

    q.set_state(session, run, "APPROVAL_REQUIRED", stage="approval", actor=str(user.id))
    audit.record(
        session, actor_type="user", actor_id=str(user.id), action="override.requested",
        resource_type="pipeline_run", resource_id=str(run.id), run_id=run.id,
        outcome="requested",
        details={"failed_rules": offer.get("failed_rules"), "note": payload.note.strip()[:500],
                 "approvals": [str(a.id) for a in shares]},
    )
    return [ApprovalView.model_validate(a) for a in shares]


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
    "/pipelines/{run_ref}/executions",
    response_model=list[ExecutionView],
    summary="Executions and their per-stage Spark metrics (FR-036)",
    dependencies=[Depends(require_roles(*STARTER_ROLES))],
)
def run_executions(run_ref: str, session: DbSession) -> list[ExecutionView]:
    run = _find_run(session, run_ref)
    rows = session.execute(
        select(Execution).where(Execution.run_id == run.id).order_by(Execution.started_at)
    ).scalars().all()
    return [
        ExecutionView(
            id=e.id, stage=_stage_label(e), engine=e.engine, status=e.status,
            exit_code=e.exit_code, duration_ms=e.duration_ms, started_at=e.started_at,
            finished_at=e.finished_at, spark_app_id=e.spark_app_id,
            spark=(e.metrics_json or {}).get("spark"),
        )
        for e in rows
    ]


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
            at=execution.started_at, stage=_stage_label(execution), level=severity,
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

    # Re-queue the phase the run was in, with the inputs it was given, as a task type the
    # worker actually handles. Queuing anything else would escalate again immediately.
    stage = run.current_stage or "pipeline"
    task_type = "publish" if stage == "publish" else "run"
    q.enqueue(
        session, run, stage=stage, agent_name="orchestrator", task_type=task_type,
        input_refs={**(run.input_versions or {}), "resumed_from": run.state},
    )
    q.set_state(session, run, "RETRYING", stage=stage, actor=str(user.id))
    audit.record(
        session, actor_type="user", actor_id=str(user.id), action="run.resumed",
        resource_type="pipeline_run", resource_id=str(run.id), run_id=run.id,
        outcome="resumed", details={"stage": stage, "fix_note": payload.fix_note[:500]},
    )
    return CancelResponse(run_id=run.run_key, state=run.state)
