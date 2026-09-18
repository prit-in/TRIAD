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


def enqueue_publish(session: Session, approval: Approval) -> AgentTask | None:
    """Queue the post-approval half of the run, once, for a decided promotion.

    Returns None when there is nothing to queue: the approval is still pending, it is not a
    promotion, or the work is already queued. Queuing twice would run the publish phase twice
    and move the champion alias twice.
    """
    if approval.action_type != "model_promotion":
        return None
    if approval.status not in ("approved", "rejected"):
        return None

    run = session.get(PipelineRun, approval.run_id)
    if run is None:
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
