"""What the API does *after* a decision, beyond recording it.

A decision is not the end of the story: an approved promotion still has to be carried out, and
a rejected one still has to archive its shadow scores and publish a report saying what was
decided and why. Both are work for the orchestrator, so the API queues a task rather than
doing it in the request - the same rule that keeps `POST /pipelines/run` from executing a
pipeline inside an HTTP call.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.graph import queue as q
from triad_core.models import AgentTask, Approval, PipelineRun

PUBLISH_STAGE = "publish"
PUBLISH_TASK = "publish"
RUN_TASK = "run"


def enqueue_publish(
    session: Session, approval: Approval, *, decision_status: str | None = None
) -> AgentTask | None:
    """Queue the post-approval half of the run, once, for a decided promotion.

    Returns None when there is nothing to queue: the approval is still pending, it is not a
    promotion, or the work is already queued. Queuing twice would run the publish phase twice
    and move the champion alias twice.

    `decision_status` is the status of the whole decision the approval belongs to
    (`GovernanceService.group_status`). A promotion despite a failed check is a group of
    shares, one per required role: it is queued only when every share has approved, and as
    soon as any is rejected the run returns to ESCALATED - a refused override leaves the
    candidate unpromoted and nothing to publish.
    """
    override = approval.policy_action == "model.promote_with_failed_check"
    if approval.action_type != "model_promotion" and not override:
        return None
    status = decision_status or approval.status

    run = session.get(PipelineRun, approval.run_id)
    if run is None:
        return None
    if override and status == "rejected":
        if run.state == "APPROVAL_REQUIRED":
            q.set_state(session, run, "ESCALATED", stage="approval", actor="governance",
                        error_summary="the promotion despite a failed check was rejected")
        return None
    if status not in (("approved",) if override else ("approved", "rejected")):
        return None

    existing = session.execute(
        select(AgentTask).where(
            AgentTask.run_id == run.id,
            AgentTask.task_type == PUBLISH_TASK,
            AgentTask.status.in_(("queued", "leased", "running", "succeeded")),
        )
    ).scalars().first()
    if existing is not None:
        return None

    return q.enqueue(
        session, run,
        stage=PUBLISH_STAGE,
        agent_name="orchestrator",
        task_type=PUBLISH_TASK,
        input_refs={"approval_id": str(approval.id), "decision": approval.status},
        priority=200,   # a decided run is ahead of a new one: someone is waiting on it
    )


def enqueue_resume(session: Session, approval: Approval) -> AgentTask | None:
    """Resume a run that paused for a destructive cleaning plan, once that plan is decided.

    Approved: the run's phase is queued again with the inputs it recorded, and its cleaning
    stage finds the approval and commits (PRD US-06). Rejected: the run is escalated, its
    output never committed and its quarantine kept. Anything else: None.
    """
    if approval.action_type != "destructive_cleaning" or approval.status == "pending":
        return None
    run = session.get(PipelineRun, approval.run_id)
    if run is None or run.state != "APPROVAL_REQUIRED":
        return None
    if approval.status != "approved":
        q.set_state(session, run, "ESCALATED", stage="clean", actor="governance",
                    error_summary="the cleaning plan was rejected")
        return None
    return q.enqueue(
        session, run, stage="pipeline", agent_name="orchestrator", task_type=RUN_TASK,
        input_refs=dict(run.input_versions or {}), priority=200,
    )
