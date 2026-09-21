"""Predictions, drift reports, generated reports and the audit trail (PRD section 25.2).

Two access rules are enforced here rather than in the frontend, because a rule a client can
skip is not a rule:

  * a Viewer receives aggregates only, never customer rows;
  * every export is audited before the file is produced, with the filter that produced it,
    so "who pulled this list" is always answerable.
"""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from backend.app import problems
from backend.app.deps import CurrentUser, DbSession, require_roles
from backend.app.schemas import (
    AuditEntryView,
    ChainStatus,
    DriftReportView,
    PredictionAggregate,
    PredictionView,
    ReportView,
)
from backend.app.security import (
    ADMIN,
    BUSINESS_ANALYST,
    DATA_SCIENTIST,
    ML_ENGINEER,
    VIEWER,
)
from triad_core import audit
from triad_core.models import AuditLog, DriftReport, Prediction, Report
from triad_core.storage import storage

router = APIRouter(tags=["insights"])


# --- the case-study experiments (PRD section 33) --------------------------------


@router.get("/experiments", summary="The ten case-study experiments and their latest results")
def list_experiments(user: CurrentUser) -> list[dict]:
    """Read from `experiments/<EXP-n>/latest.json`, which the harness writes.

    These are results of the *case study*, not rows of the `experiments` table - that one
    records a run's modelling experiment and is reached through the run. The two share a word
    and nothing else.

    Served as the harness wrote them, including `status` and `limitations`. An experiment that
    could not run the workload the PRD specifies says so in its own result, and flattening that
    into a pass/fail here would throw away the only part a reader needs.
    """
    from experiments.harness import load_results

    results = load_results()
    known = {r.get("exp_id") for r in results}
    # Every experiment the PRD defines appears, so one that has never been run is visibly
    # absent rather than silently missing from the list.
    for exp_id in [f"EXP-{n}" for n in range(1, 11)]:
        if exp_id not in known:
            results.append({
                "exp_id": exp_id,
                "status": "not_run",
                "title": "",
                "hypothesis_held": None,
                "conclusion": "No result has been recorded for this experiment yet.",
                "limitations": [],
                "trials": [],
            })
    return sorted(results, key=lambda r: int(str(r["exp_id"]).split("-")[1]))

ROW_ROLES = (BUSINESS_ANALYST, DATA_SCIENTIST, ML_ENGINEER)


def _prediction_query(
    model_version_id: uuid.UUID | None,
    score_date: dt.date | None,
    risk_band: str | None,
    prediction_set: str,
    min_probability: float | None,
):
    stmt = select(Prediction).where(Prediction.prediction_set == prediction_set)
    if model_version_id:
        stmt = stmt.where(Prediction.model_version_id == model_version_id)
    if score_date:
        stmt = stmt.where(Prediction.score_date == score_date)
    if risk_band:
        stmt = stmt.where(Prediction.risk_band == risk_band)
    if min_probability is not None:
        stmt = stmt.where(Prediction.churn_probability >= min_probability)
    return stmt


@router.get(
    "/predictions",
    response_model=list[PredictionView] | PredictionAggregate,
    summary="Scores, filtered; Viewers receive aggregates only",
)
def list_predictions(
    session: DbSession,
    user: CurrentUser,
    model_version_id: uuid.UUID | None = Query(default=None),
    score_date: dt.date | None = Query(default=None),
    risk_band: str | None = Query(default=None, pattern="^(high|medium|low)$"),
    prediction_set: str = Query(default="official", pattern="^(official|shadow)$"),
    min_probability: float | None = Query(default=None, ge=0.0, le=1.0),
    limit: int = Query(default=100, ge=1, le=1000),
):
    stmt = _prediction_query(model_version_id, score_date, risk_band,
                             prediction_set, min_probability)

    if user.role == VIEWER:
        rows = session.execute(stmt).scalars().all()
        bands: dict[str, int] = {}
        for row in rows:
            bands[row.risk_band] = bands.get(row.risk_band, 0) + 1
        mean = (
            sum(r.churn_probability for r in rows) / len(rows) if rows else None
        )
        return PredictionAggregate(
            rows=len(rows), by_risk_band=bands, mean_probability=mean,
            score_dates=sorted({r.score_date for r in rows}),
        )

    if user.role not in {*ROW_ROLES, ADMIN}:
        raise problems.forbidden(
            f"Role {user.role} may not read customer-level scores.", problem_type="role"
        )
    rows = session.execute(
        stmt.order_by(Prediction.churn_probability.desc()).limit(limit)
    ).scalars().all()
    return [PredictionView.model_validate(r) for r in rows]


@router.post(
    "/predictions/export",
    status_code=202,
    summary="Audited CSV export",
    dependencies=[Depends(require_roles(BUSINESS_ANALYST))],
)
def export_predictions(
    session: DbSession,
    user: CurrentUser,
    model_version_id: uuid.UUID | None = Query(default=None),
    score_date: dt.date | None = Query(default=None),
    risk_band: str | None = Query(default=None, pattern="^(high|medium|low)$"),
    prediction_set: str = Query(default="official", pattern="^(official|shadow)$"),
) -> dict:
    """Write the CSV, then hand back a link. The audit entry is written first, on purpose."""
    import csv

    filters = {"model_version_id": str(model_version_id) if model_version_id else None,
               "score_date": score_date.isoformat() if score_date else None,
               "risk_band": risk_band, "prediction_set": prediction_set}
    rows = session.execute(
        _prediction_query(model_version_id, score_date, risk_band, prediction_set, None)
        .order_by(Prediction.churn_probability.desc())
    ).scalars().all()

    audit.record(
        session, actor_type="user", actor_id=str(user.id), action="predictions.exported",
        resource_type="prediction_set", resource_id=prediction_set, outcome="exported",
        details={"filters": filters, "rows": len(rows)},
    )

    store = storage()
    target = store.root / "exports" / f"predictions-{uuid.uuid4().hex[:12]}.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["customer_ref", "score_date", "churn_probability",
                         "risk_band", "prediction_set", "feature_set_version"])
        for row in rows:
            writer.writerow([row.customer_ref, row.score_date, row.churn_probability,
                             row.risk_band, row.prediction_set, row.feature_set_version])

    return {"rows": len(rows), "filters": filters,
            "download": f"/api/v1/exports/{target.name}"}


@router.get("/exports/{name}", summary="Download a previously requested export",
            dependencies=[Depends(require_roles(BUSINESS_ANALYST))])
def download_export(name: str) -> FileResponse:
    if "/" in name or "\\" in name or ".." in name:
        raise problems.bad_request("That is not an export name this API issued.")
    path = storage().root / "exports" / name
    if not path.is_file():
        raise problems.not_found("Export", name)
    return FileResponse(path, media_type="text/csv", filename=name)


@router.get("/drift", response_model=list[DriftReportView], summary="Drift reports, newest first")
def list_drift(
    session: DbSession,
    user: CurrentUser,
    model_version_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[DriftReportView]:
    stmt = select(DriftReport).order_by(DriftReport.computed_at.desc()).limit(limit)
    if model_version_id:
        stmt = stmt.where(DriftReport.model_version_id == model_version_id)
    out: list[DriftReportView] = []
    for row in session.execute(stmt).scalars():
        payload = row.psi_json or {}
        score = payload.get("score") or {}
        out.append(DriftReportView(
            id=row.id, model_version_id=row.model_version_id,
            reference_ref=row.reference_ref, current_ref=row.current_ref,
            flagged_features=row.flagged_features or [],
            max_psi=payload.get("max_psi"),
            score_flagged=score.get("flagged"),
            computed_at=row.computed_at,
        ))
    return out


@router.get("/drift/{report_id}", summary="One drift report, with every measured index")
def get_drift(report_id: uuid.UUID, session: DbSession, user: CurrentUser) -> dict:
    row = session.get(DriftReport, report_id)
    if row is None:
        raise problems.not_found("Drift report", str(report_id))
    return {
        "id": str(row.id),
        "model_version_id": str(row.model_version_id),
        "reference_ref": row.reference_ref,
        "current_ref": row.current_ref,
        "computed_at": row.computed_at.isoformat() if row.computed_at else None,
        **(row.psi_json or {}),
    }


@router.get("/reports", response_model=list[ReportView], summary="Generated reports")
def list_reports(
    session: DbSession,
    user: CurrentUser,
    run_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ReportView]:
    stmt = select(Report).order_by(Report.created_at.desc()).limit(limit)
    if run_id:
        stmt = stmt.where(Report.run_id == run_id)
    return [ReportView.model_validate(r) for r in session.execute(stmt).scalars()]


@router.get("/reports/{report_id}", summary="Report metadata, or the file itself")
def get_report(
    report_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
    file: bool = Query(default=False, description="return the rendered document"),
):
    row = session.get(Report, report_id)
    if row is None:
        raise problems.not_found("Report", str(report_id))
    if not file:
        return ReportView.model_validate(row)

    path = storage().path(row.storage_uri)
    if not path.is_file():
        raise problems.not_found("Report document", row.storage_uri)
    media = "text/html" if row.format == "html" else "application/pdf"
    return FileResponse(path, media_type=media, filename=path.name)


@router.get(
    "/audit-logs",
    response_model=list[AuditEntryView],
    summary="Search the audit trail",
    dependencies=[Depends(require_roles(ADMIN))],
)
def search_audit(
    session: DbSession,
    action: str | None = Query(default=None),
    actor_id: str | None = Query(default=None),
    run_id: uuid.UUID | None = Query(default=None),
    since: dt.datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AuditEntryView]:
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if run_id:
        stmt = stmt.where(AuditLog.run_id == run_id)
    if since:
        stmt = stmt.where(AuditLog.ts >= since)
    return [AuditEntryView.model_validate(r) for r in session.execute(stmt).scalars()]


@router.get(
    "/audit-logs/verify",
    response_model=ChainStatus,
    summary="Recompute the hash chain",
    dependencies=[Depends(require_roles(ADMIN))],
)
def verify_audit_chain(session: DbSession) -> ChainStatus:
    """Recomputes every entry. A tampered or reordered row fails here and names the entry."""
    result = audit.verify_chain(session)
    return ChainStatus(
        ok=result.ok, entries_checked=result.entries_checked,
        chains_checked=result.chains_checked,
        broken_at=result.broken_at_id, broken_scope=result.broken_scope,
        detail=result.reason,
    )


@router.get("/stats", summary="Dashboard tiles, computed from the database")
def dashboard_stats(session: DbSession, user: CurrentUser) -> dict:
    """Every tile on the dashboard, each one a query rather than a stored counter."""
    from triad_core.models import Alert, Approval, PipelineRun

    today = dt.datetime.now(dt.UTC).date()
    runs_today = session.execute(
        select(func.count()).select_from(PipelineRun)
        .where(func.date(PipelineRun.started_at) == today)
    ).scalar_one()
    finished = session.execute(
        select(PipelineRun.state).where(
            PipelineRun.state.in_(
                ("COMPLETED", "PUBLISHED", "FAILED", "ABORTED", "ESCALATED", "CANCELLED")
            )
        )
    ).scalars().all()
    succeeded = sum(1 for s in finished if s in ("COMPLETED", "PUBLISHED"))
    return {
        "runs_today": runs_today,
        "runs_finished": len(finished),
        "success_rate": round(succeeded / len(finished), 4) if finished else None,
        "open_alerts": session.execute(
            select(func.count()).select_from(Alert).where(Alert.status == "open")
        ).scalar_one(),
        "pending_approvals": session.execute(
            select(func.count()).select_from(Approval).where(Approval.status == "pending")
        ).scalar_one(),
    }
