"""Models, versions and the promotion decisions taken on them (PRD section 25.2).

Approve, reject and rollback are thin wrappers over `GovernanceService` and `ModelRegistry`.
They are deliberately thin: every refusal - wrong role, self-approval, nothing pending,
missing comment - is decided by the governance service, so the API cannot accidentally hold a
more permissive opinion than the policy does. Each refusal maps to one status code here and
nowhere else.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select

from backend.app import problems
from backend.app.deps import CurrentUser, DbSession, require_roles
from backend.app.schemas import (
    ApprovalView,
    DecisionRequest,
    ModelDetail,
    ModelSummary,
    ModelVersionView,
    RollbackRequest,
)
from backend.app.security import ML_ENGINEER
from triad_core import audit
from triad_core.config import settings
from triad_core.governance import (
    CommentRequired,
    GovernanceService,
    NotAuthorised,
    NothingPending,
    PolicyDenied,
    SelfApprovalRefused,
)
from triad_core.models import Approval, Metric, Model, ModelVersion
from triad_core.policy import PolicyEngine

router = APIRouter(tags=["models"])


def _governance(session: DbSession) -> GovernanceService:
    return GovernanceService(session, PolicyEngine(settings().governance_policy_path))


def _metrics_for(session: DbSession, version: ModelVersion) -> dict[str, float]:
    """Metrics are recorded against the run that produced the version (PRD 24.1)."""
    if version.run_id is None:
        return {}
    rows = session.execute(
        select(Metric).where(Metric.ref_id == str(version.run_id), Metric.split == "test")
    ).scalars().all()
    return {r.metric_name: r.metric_value for r in rows}


def _version_view(session: DbSession, version: ModelVersion) -> ModelVersionView:
    return ModelVersionView(
        id=version.id, algorithm=version.algorithm, mlflow_version=version.mlflow_version,
        feature_set_version=version.feature_set_version,
        governance_status=version.governance_status, threshold=version.threshold,
        created_at=version.created_at, promoted_at=version.promoted_at,
        metrics=_metrics_for(session, version),
    )


def _versions(session: DbSession, model: Model) -> list[ModelVersion]:
    return list(session.execute(
        select(ModelVersion)
        .where(ModelVersion.model_id == model.id)
        .order_by(ModelVersion.created_at.desc())
    ).scalars().all())


def _summary(session: DbSession, model: Model) -> ModelSummary:
    versions = _versions(session, model)
    champion = next((v for v in versions if v.governance_status == "champion"), None)
    challenger = next((v for v in versions if v.governance_status == "challenger"), None)
    return ModelSummary(
        id=model.id, name=model.name, task_type=model.task_type,
        target_definition=model.target_definition,
        champion=_version_view(session, champion) if champion else None,
        challenger=_version_view(session, challenger) if challenger else None,
    )


def _find_model(session: DbSession, model_ref: str) -> Model:
    model = session.execute(
        select(Model).where(Model.name == model_ref)
    ).scalar_one_or_none()
    if model is None:
        try:
            model = session.get(Model, uuid.UUID(model_ref))
        except ValueError:
            model = None
    if model is None:
        raise problems.not_found("Model", model_ref)
    return model


# --- read ----------------------------------------------------------------------


@router.get("/models", response_model=list[ModelSummary], summary="Models with their aliases")
def list_models(session: DbSession, user: CurrentUser) -> list[ModelSummary]:
    models = session.execute(select(Model).order_by(Model.name)).scalars().all()
    return [_summary(session, m) for m in models]


@router.get("/models/{model_ref}", response_model=ModelDetail, summary="Versions and metrics")
def get_model(model_ref: str, session: DbSession, user: CurrentUser) -> ModelDetail:
    model = _find_model(session, model_ref)
    versions = _versions(session, model)
    card = next((v.model_card_uri for v in versions if v.model_card_uri), None)
    return ModelDetail(
        **_summary(session, model).model_dump(),
        versions=[_version_view(session, v) for v in versions],
        model_card_uri=card,
    )


# --- decisions -----------------------------------------------------------------


def _pending_promotion(session: DbSession, model: Model) -> Approval:
    """The pending promotion for this model, found through its versions' target refs."""
    version_refs = {
        f"{model.name}:{v.mlflow_version}" for v in _versions(session, model) if v.mlflow_version
    }
    pending = session.execute(
        select(Approval)
        .where(Approval.action_type == "model_promotion", Approval.status == "pending")
        .order_by(Approval.requested_at.desc())
    ).scalars().all()
    for approval in pending:
        if approval.target_ref in version_refs or approval.target_ref.startswith(f"{model.name}:"):
            return approval
    raise problems.conflict(
        "Nothing pending",
        f"There is no pending promotion for {model.name}.",
        problem_type="nothing-pending",
    )


def _decide(
    session: DbSession, user: CurrentUser, model_ref: str, approved: bool, comment: str
) -> ApprovalView:
    model = _find_model(session, model_ref)
    approval = _pending_promotion(session, model)
    try:
        decided = _governance(session).decide(
            approval.id, approver=user, approved=approved, comment=comment
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
    return ApprovalView.model_validate(decided)


@router.post(
    "/models/{model_ref}/approve",
    response_model=ApprovalView,
    summary="Approve a pending promotion",
    dependencies=[Depends(require_roles(ML_ENGINEER))],
)
def approve(
    model_ref: str, payload: DecisionRequest, session: DbSession, user: CurrentUser
) -> ApprovalView:
    return _decide(session, user, model_ref, True, payload.comment)


@router.post(
    "/models/{model_ref}/reject",
    response_model=ApprovalView,
    summary="Reject a pending promotion",
    dependencies=[Depends(require_roles(ML_ENGINEER))],
)
def reject(
    model_ref: str, payload: DecisionRequest, session: DbSession, user: CurrentUser
) -> ApprovalView:
    return _decide(session, user, model_ref, False, payload.comment)


@router.post(
    "/models/{model_ref}/rollback",
    response_model=ModelDetail,
    summary="Move the champion alias back to a recorded version",
    dependencies=[Depends(require_roles(ML_ENGINEER))],
)
def rollback(
    model_ref: str, payload: RollbackRequest, session: DbSession, user: CurrentUser
) -> ModelDetail:
    """Rollback needs no fresh approval: it is the one pre-approved action (PRD 19.5, 30.2).

    It only ever moves the alias to a version this platform recorded, so it cannot be used to
    deploy something that was never approved in the first place.
    """
    model = _find_model(session, model_ref)
    target = next(
        (v for v in _versions(session, model) if v.mlflow_version == payload.previous_version),
        None,
    )
    if target is None:
        raise problems.conflict(
            "Unknown version",
            f"{model.name} has no recorded version {payload.previous_version}; "
            "rollback only restores versions this platform registered.",
            problem_type="unknown-version",
        )

    from python_ml.triad_ml.registry import ModelRegistry

    try:
        result = ModelRegistry(settings().mlflow_tracking_uri).rollback(
            model.mlflow_registered_name,
            previous_version=payload.previous_version,
            reason=payload.reason,
        )
    except Exception as exc:
        raise problems.conflict(
            "Registry refused the rollback", str(exc), problem_type="registry-error"
        ) from exc

    for version in _versions(session, model):
        if version.governance_status == "champion":
            version.governance_status = "archived"
    target.governance_status = "champion"
    model.champion_version_id = target.id
    session.flush()

    audit.record(
        session, actor_type="user", actor_id=str(user.id), action="model.rolled_back",
        resource_type="model_version", resource_id=str(target.id),
        run_id=target.run_id, outcome="rolled_back",
        details={"reason": payload.reason, **result},
    )
    return get_model(model_ref, session, user)
