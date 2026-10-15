"""Predictions, drift reports, generated reports and the audit trail (PRD section 25.2).

Two access rules are enforced here rather than in the frontend, because a rule a client can
skip is not a rule:

  * a Viewer receives aggregates only, never customer rows;
  * every export is audited before the file is produced, with the filter that produced it,
    so "who pulled this list" is always answerable.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from backend.app import problems
from backend.app.deps import CurrentUser, DbSession, require_roles
from backend.app.schemas import (
    AuditActionCount,
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


@router.get("/experiments/{exp_id}/runs", summary="Every recorded run of one experiment")
def experiment_runs(exp_id: str, user: CurrentUser) -> list[dict]:
    """Newest first, including runs that were interrupted or superseded and why (their notes).

    `exp_id` names a folder, so only the ten the PRD defines are accepted.
    """
    from experiments.harness import load_history

    if exp_id not in {f"EXP-{n}" for n in range(1, 11)}:
        raise problems.not_found("Experiment", exp_id)
    return load_history(exp_id)

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
        # Aggregated by the database, not in Python. This branch used to execute the row query
        # with no LIMIT and build every matching Prediction as an ORM object just to count risk
        # bands and average one column - and `predictions` holds one row per customer per score
        # date per prediction set, so the cost grows with the table while `limit`, which the
        # signature accepts and validates, was silently ignored here. Ignoring it is right for
        # an aggregate; loading the table to compute one is not.
        #
        # Counting and summing rather than using avg() keeps the mean identical to what the
        # Python loop produced, over the same rows.
        filters = stmt.whereclause
        banded = session.execute(
            select(
                Prediction.risk_band,
                func.count().label("n"),
                func.sum(Prediction.churn_probability).label("total"),
            )
            .where(filters)
            .group_by(Prediction.risk_band)
        ).all()
        total_rows = sum(int(row.n) for row in banded)
        probability_total = sum(float(row.total or 0.0) for row in banded)
        score_dates = session.execute(
            select(Prediction.score_date)
            .where(filters)
            .distinct()
            .order_by(Prediction.score_date)
        ).scalars().all()
        return PredictionAggregate(
            rows=total_rows,
            by_risk_band={row.risk_band: int(row.n) for row in banded},
            mean_probability=(probability_total / total_rows) if total_rows else None,
            score_dates=list(score_dates),
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
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    """Write the CSV, then hand back a link. The audit entry is written first, on purpose.

    With an `Idempotency-Key` (PRD 25.1) a retried request gets the export it already made -
    one file and one audit entry - and the same key with other filters is refused, as run
    creation refuses it (DEC-53). The key is scoped to the user who sent it.
    """
    import csv

    filters = {"model_version_id": str(model_version_id) if model_version_id else None,
               "score_date": score_date.isoformat() if score_date else None,
               "risk_band": risk_band, "prediction_set": prediction_set}
    folder = storage().root / "exports"
    receipt = None
    if idempotency_key:
        digest = hashlib.sha256(f"{user.id}:{idempotency_key}".encode()).hexdigest()[:16]
        target = folder / f"predictions-{digest}.csv"
        # Beside the exports, in a folder no download name can reach (names carry no "/").
        receipt = folder / "receipts" / f"{digest}.json"
        if receipt.is_file():
            earlier = json.loads(receipt.read_text(encoding="utf-8"))
            if earlier["filters"] != filters:
                raise problems.conflict(
                    "Idempotency key reused with a different body",
                    f"Key {idempotency_key} already exported predictions with other filters.",
                    problem_type="idempotency-conflict",
                )
            return {**earlier, "download": f"/api/v1/exports/{target.name}"}
    else:
        target = folder / f"predictions-{uuid.uuid4().hex[:12]}.csv"
    rows = session.execute(
        _prediction_query(model_version_id, score_date, risk_band, prediction_set, None)
        .order_by(Prediction.churn_probability.desc())
    ).scalars().all()

    audit.record(
        session, actor_type="user", actor_id=str(user.id), action="predictions.exported",
        resource_type="prediction_set", resource_id=prediction_set, outcome="exported",
        details={"filters": filters, "rows": len(rows)},
    )

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["customer_ref", "score_date", "churn_probability",
                         "risk_band", "prediction_set", "feature_set_version"])
        for row in rows:
            writer.writerow([row.customer_ref, row.score_date, row.churn_probability,
                             row.risk_band, row.prediction_set, row.feature_set_version])
    # Written last: a request that failed before this point left nothing a retry would reuse.
    # ponytail: two simultaneous first requests with one key both export; a lock if that matters.
    if receipt is not None:
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps({"rows": len(rows), "filters": filters}),
                           encoding="utf-8")

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
    until: dt.datetime | None = Query(default=None),
    before_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AuditEntryView]:
    """Newest first. `before_id` pages backwards; `since` and `until` bound a period.

    The trail used to be reachable only from the newest end: `since` is a lower bound on a
    newest-first query, so asking for "on or after 2026-09-18" returned the same 200 rows as
    asking for nothing, and the 763 entries from 18 and 19 September could not be read.
    """
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if before_id is not None:
        stmt = stmt.where(AuditLog.id < before_id)
    if until:
        stmt = stmt.where(AuditLog.ts < until)
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
    "/audit-logs/actions",
    response_model=list[AuditActionCount],
    summary="Every action the trail holds, with how many entries each",
    dependencies=[Depends(require_roles(ADMIN))],
)
def audit_actions(session: DbSession) -> list[AuditActionCount]:
    """What the action filter offers. Built from the newest page alone, it offered 17 of the 30
    actions the trail held - including none of the six `approval.self_approval_refused`
    entries, the refusals an auditor most wants to find."""
    rows = session.execute(
        select(AuditLog.action, func.count()).group_by(AuditLog.action).order_by(AuditLog.action)
    ).all()
    return [AuditActionCount(action=action, count=int(count)) for action, count in rows]


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
    # Runs that ended on their own. A cancelled run was withdrawn - every experiment cancels its
    # runs once it has read them - and an escalated one waits for a person; counted as failures
    # they put this tile at 8% on a platform whose runs mostly did what they were asked.
    finished = session.execute(
        select(PipelineRun.state).where(
            PipelineRun.state.in_(("COMPLETED", "PUBLISHED", "FAILED", "ABORTED"))
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
