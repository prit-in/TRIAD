"""Models, versions and the promotion decisions taken on them (PRD section 25.2).

Approve, reject and rollback are thin wrappers over `GovernanceService` and `ModelRegistry`.
They are deliberately thin: every refusal - wrong role, self-approval, nothing pending,
missing comment - is decided by the governance service, so the API cannot accidentally hold a
more permissive opinion than the policy does. Each refusal maps to one status code here and
nowhere else.
"""

from __future__ import annotations

import json
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
from backend.app.services import enqueue_publish
from triad_core import audit
from triad_core.config import settings
from triad_core.governance import (
    CommentRequired,
    DuplicateApprover,
    GovernanceService,
    NotAuthorised,
    NothingPending,
    PolicyDenied,
    SelfApprovalRefused,
)
from triad_core.models import Alert, Approval, Metric, Model, ModelVersion, PipelineRun
from triad_core.policy import PolicyEngine
from triad_core.storage import storage

router = APIRouter(tags=["models"])


def _governance(session: DbSession) -> GovernanceService:
    return GovernanceService(session, PolicyEngine(settings().governance_policy_path))


def _metrics_for(session: DbSession, version: ModelVersion) -> dict[str, float]:
    """The version's headline test metrics: overall only, never a segment.

    Metrics are recorded against the run that produced the version (PRD 24.1). Since the run
    also records per-segment rows for AP-6 - same `metric_name`, same `split`, a non-null
    `segment` - this query has to exclude them. Without the filter the dictionary comprehension
    below kept whichever row the database returned last, so a model whose overall test PR-AUC
    was 0.7801 was reported at 0.8568, the value for `loyalty_tier=bronze`, and the
    segment-only keys `insufficient_data` and `positives` appeared beside the real metrics.
    Per-segment numbers are served by the evaluation endpoint, which labels them.
    """
    if version.run_id is None:
        return {}
    rows = session.execute(
        select(Metric).where(
            Metric.ref_id == str(version.run_id),
            Metric.scope == "run",
            Metric.split == "test",
            Metric.segment.is_(None),
        )
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
    # The pointer is the authority (docs/database-schema.md: "the only place an alias lives in
    # this database"). Reading "the newest version with status champion" reported a stale or
    # withdrawn version whenever the statuses disagreed with it, and they did.
    champion = next((v for v in versions if v.id == model.champion_version_id), None) or next(
        (v for v in versions if v.governance_status == "champion"), None
    )
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


@router.get(
    "/models/{model_ref}/versions/{version_id}/evaluation",
    summary="Curves, intervals, segment metrics and feature importance for one version",
)
def get_evaluation(
    model_ref: str, version_id: uuid.UUID, session: DbSession, user: CurrentUser
) -> dict:
    """The evidence behind the charts (FR-065, FR-066, FR-067).

    Served from the document the run wrote, not recomputed: a ROC or reliability curve needs
    the labels and the scores together, and neither survives the run - the predictions table
    has no label column. Recomputing here would mean either inventing the points or shipping
    customer-level labels to a browser, and the first is forbidden and the second is worse.

    The path is derived from the run key rather than read from a column, so a version trained
    before this document existed returns 404 rather than a null pointer to nothing.
    """
    model = _find_model(session, model_ref)
    version = session.get(ModelVersion, version_id)
    if version is None or version.model_id != model.id:
        raise problems.not_found(f"Version of {model.name}", str(version_id))
    if version.run_id is None:
        raise problems.not_found("Run behind model version", str(version_id))
    run = session.get(PipelineRun, version.run_id)
    if run is None:
        raise problems.not_found("Run", str(version.run_id))

    store = storage()
    path = store.reports_dir(run.run_key) / "evaluation.json"
    if not path.exists():
        raise problems.Problem(
            status=404,
            title="No evaluation document for this version",
            detail=(
                f"Run {run.run_key} wrote no evaluation document. A version trained before "
                "these documents existed does not have one, and nothing here reconstructs it: "
                "the curves need the labels and the scores together and neither survives the "
                "run."
            ),
            problem_type="evaluation-absent",
        )
    return json.loads(path.read_text(encoding="utf-8"))


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
    except DuplicateApprover as exc:
        raise problems.forbidden(str(exc), problem_type="duplicate-approver") from exc

    enqueue_publish(session, decided,
                    decision_status=_governance(session).group_status(decided))
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

    Pre-approved because it restores what was approved before: PRD 30 limits it to the
    "previous recorded champion only". It used to accept any version this platform had
    recorded, which included every challenger that was never approved - so "roll back to v17"
    deployed an unapproved model with no approval at all. A version qualifies only if it was
    promoted once (`promoted_at`), and only the most recent such version other than the
    current champion. Like the automatic rollback, it raises an alert (PRD 19.5).
    """
    model = _find_model(session, model_ref)
    versions = _versions(session, model)
    target = next((v for v in versions if v.mlflow_version == payload.previous_version), None)
    if target is None:
        raise problems.conflict(
            "Unknown version",
            f"{model.name} has no recorded version {payload.previous_version}; "
            "rollback only restores versions this platform registered.",
            problem_type="unknown-version",
        )
    former = sorted((v for v in versions
                     if v.promoted_at is not None and v.governance_status != "champion"),
                    key=lambda v: v.promoted_at, reverse=True)
    previous = former[0] if former else None
    if previous is None or target.id != previous.id:
        raise problems.conflict(
            "Not the previous champion",
            "Rollback restores only the previous recorded champion"
            + (f", which for {model.name} is v{previous.mlflow_version}" if previous else
               f"; {model.name} has none")
            + f". v{payload.previous_version} was "
            + ("never promoted" if target.promoted_at is None else "not the most recent")
            + "; deploying it needs an approval (PRD 30).",
            problem_type="not-previous-champion",
        )
    replaced = next((v for v in versions if v.governance_status == "champion"), None)

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

    # Through the same status change publish and the automatic rollback use, so the restored
    # version's `promoted_at` is refreshed: left at its first promotion, the next rollback's
    # "previous champion" was computed from a stale order and named the wrong version.
    from agents.monitoring import MonitoringService

    MonitoringService(session).set_status(target, "champion", promoted_by=user.id)

    audit.record(
        session, actor_type="user", actor_id=str(user.id), action="model.rolled_back",
        resource_type="model_version", resource_id=str(target.id),
        run_id=target.run_id, outcome="rolled_back",
        details={"reason": payload.reason, **result},
    )
    session.add(Alert(
        alert_type="model_rolled_back", severity="warning", model_id=model.id,
        run_id=target.run_id,
        message=(f"{model.name} champion rolled back to v{target.mlflow_version}"
                 + (f" from v{replaced.mlflow_version}" if replaced else "")
                 + f" by a person: {payload.reason}"),
        evidence={"rolled_back_by": str(user.id), "reason": payload.reason,
                  "from_version": replaced.mlflow_version if replaced else None,
                  "to_version": target.mlflow_version},
    ))
    session.flush()
    return get_model(model_ref, session, user)
