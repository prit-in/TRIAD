"""The generic approval queue (PRD section 25.2).

`/models/{id}/approve` is the promotion-shaped door onto the same mechanism; this is the
one that handles every other governed action - destructive cleaning, schema changes,
definition changes, policy overrides and quota increases - without the API needing to know
what any of them mean. The policy file decides who may decide what.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from sqlalchemy import select

from backend.app import problems
from backend.app.deps import CurrentUser, DbSession
from backend.app.schemas import AlertView, ApprovalView, DecisionRequest
from backend.app.services import enqueue_publish
from triad_core.config import settings
from triad_core.governance import (
    CommentRequired,
    GovernanceService,
    NotAuthorised,
    NothingPending,
    PolicyDenied,
    SelfApprovalRefused,
)
from triad_core.models import Alert, Approval
from triad_core.policy import PolicyEngine

router = APIRouter(tags=["approvals"])

VALID_STATUSES = ("pending", "approved", "rejected", "expired")


@router.get("/approvals", response_model=list[ApprovalView], summary="Approval requests by status")
def list_approvals(
    session: DbSession,
    user: CurrentUser,
    status: str = Query(default="pending"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ApprovalView]:
    if status not in VALID_STATUSES:
        raise problems.bad_request(
            f"status must be one of {', '.join(VALID_STATUSES)}."
        )
    service = GovernanceService(session, PolicyEngine(settings().governance_policy_path))
    # Expiry is a state change, not a display rule: a request past its deadline counts as
    # rejected, so it is settled here rather than rendered as still pending.
    service.expire_due()

    rows = session.execute(
        select(Approval)
        .where(Approval.status == status)
        .order_by(Approval.requested_at.desc())
        .limit(limit)
    ).scalars().all()
    return [ApprovalView.model_validate(r) for r in rows]


@router.get("/approvals/{approval_id}", response_model=ApprovalView, summary="One request")
def get_approval(approval_id: uuid.UUID, session: DbSession, user: CurrentUser) -> ApprovalView:
    approval = session.get(Approval, approval_id)
    if approval is None:
        raise problems.not_found("Approval", str(approval_id))
    return ApprovalView.model_validate(approval)


@router.post(
    "/approvals/{approval_id}/decision",
    response_model=ApprovalView,
    summary="Decide any approval type",
)
def decide(
    approval_id: uuid.UUID,
    payload: DecisionRequest,
    session: DbSession,
    user: CurrentUser,
) -> ApprovalView:
    """The role check lives in the policy, not in a decorator here.

    Which roles may decide depends on the action type, so pinning a role list to this
    endpoint would either be wrong for some action or duplicate the policy file. The service
    raises `NotAuthorised` with the roles the policy actually names.
    """
    service = GovernanceService(session, PolicyEngine(settings().governance_policy_path))
    try:
        decided = service.decide(
            approval_id, approver=user, approved=payload.approved, comment=payload.comment
        )
    except SelfApprovalRefused as exc:
        raise problems.forbidden(str(exc), problem_type="self-approval") from exc
    except NotAuthorised as exc:
        raise problems.forbidden(str(exc), problem_type="role") from exc
    except CommentRequired as exc:
        raise problems.unprocessable(str(exc)) from exc
    except NothingPending as exc:
        raise problems.conflict("Nothing pending", str(exc),
                                problem_type="nothing-pending") from exc
    except PolicyDenied as exc:
        raise problems.forbidden(str(exc), problem_type="policy-denied") from exc

    # An approved promotion still has to be carried out, and a rejected one still has to
    # archive its shadow scores and say so in a report. Both are the orchestrator's work.
    enqueue_publish(session, decided)
    return ApprovalView.model_validate(decided)


@router.get("/alerts", response_model=list[AlertView], summary="Open alerts, newest first")
def list_alerts(
    session: DbSession,
    user: CurrentUser,
    status: str = Query(default="open", pattern="^(open|acknowledged|closed)$"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[AlertView]:
    rows = session.execute(
        select(Alert)
        .where(Alert.status == status)
        .order_by(Alert.created_at.desc())
        .limit(limit)
    ).scalars().all()
    return [AlertView.model_validate(r) for r in rows]


@router.post("/alerts/{alert_id}/acknowledge", response_model=AlertView, summary="Acknowledge")
def acknowledge(alert_id: uuid.UUID, session: DbSession, user: CurrentUser) -> AlertView:
    from triad_core import audit

    alert = session.get(Alert, alert_id)
    if alert is None:
        raise problems.not_found("Alert", str(alert_id))
    if alert.status != "open":
        raise problems.conflict(
            "Alert is not open", f"Alert {alert_id} is already {alert.status}."
        )
    alert.status = "acknowledged"
    alert.acknowledged_by = user.id
    session.flush()
    audit.record(
        session, actor_type="user", actor_id=str(user.id), action="alert.acknowledged",
        resource_type="alert", resource_id=str(alert.id), run_id=alert.run_id,
        outcome="acknowledged", details={"alert_type": alert.alert_type},
    )
    return AlertView.model_validate(alert)
