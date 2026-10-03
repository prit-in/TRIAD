"""M16 Monitor: the deterministic service behind drift reports and retraining triggers.

PRD section 11 gives Monitor no agency of its own: it measures, records and raises alerts.
Deciding to retrain is `agents/retraining.py`; acting on that decision is the orchestrator's,
and promoting anything still needs a person. Nothing here calls an LLM.

It also owns the small piece of catalogue bookkeeping the rest of the platform depends on:
MLflow holds the artifact, but `models` / `model_versions` in PostgreSQL are what drift
reports, predictions and approvals point at, so a registration has to land in both.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from agents.retraining import MonitoringState, RetrainingDecision, RetrainingPolicy
from python_ml.triad_ml.hygiene import catalog_version
from triad_core import audit
from triad_core.governance import GovernanceService
from triad_core.ids import utcnow
from triad_core.models import DriftReport, Model, ModelVersion, PipelineRun
from triad_core.storage import Storage, storage

if TYPE_CHECKING:  # pragma: no cover
    import numpy as np
    import pandas as pd
    from sqlalchemy.orm import Session

    from python_ml.triad_ml.drift import DriftResult, Reference


@dataclass
class DriftOutcome:
    """What a scoring run's drift check produced, with the row it was written to."""

    result: DriftResult
    report_id: uuid.UUID
    reference_uri: str
    current_uri: str
    alert_id: uuid.UUID | None = None

    @property
    def flagged(self) -> bool:
        return bool(self.result.flagged_features) or bool(
            self.result.score and self.result.score.flagged
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": str(self.report_id),
            "reference_uri": self.reference_uri,
            "current_uri": self.current_uri,
            "flagged": self.flagged,
            "alert_id": str(self.alert_id) if self.alert_id else None,
            **self.result.to_dict(),
        }


class MonitoringService:
    def __init__(
        self,
        session: Session,
        *,
        store: Storage | None = None,
        config: dict[str, Any] | None = None,
        governance: GovernanceService | None = None,
    ) -> None:
        self.session = session
        self.storage = store or storage()
        self.config = config or {}
        self.governance = governance
        self.policy = RetrainingPolicy(self.config.get("retraining"))

    # -- catalogue -------------------------------------------------------------
    def model(self, name: str, *, target_definition: str, mlflow_name: str | None = None) -> Model:
        existing = self.session.execute(
            select(Model).where(Model.name == name)
        ).scalar_one_or_none()
        if existing:
            return existing
        row = Model(
            name=name,
            mlflow_registered_name=mlflow_name or name,
            task_type="binary",
            target_definition=target_definition,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def register_model_version(
        self,
        model: Model,
        *,
        run: PipelineRun | None,
        algorithm: str,
        mlflow_version: str | None,
        params: dict[str, Any] | None = None,
        feature_set_version: str | None = None,
        training_data_ref: str | None = None,
        threshold: float | None = None,
        governance_status: str = "candidate",
        model_card_uri: str | None = None,
        artifact_sha256: str | None = None,
    ) -> ModelVersion:
        """Mirror an MLflow registration into the catalogue.

        `mlflow_version` may be None when MLflow was unreachable: the catalogue row is still
        written, so the run is traceable, and the missing link is visible rather than implied.
        """
        version = ModelVersion(
            model_id=model.id,
            run_id=run.id if run else None,
            mlflow_version=str(mlflow_version) if mlflow_version is not None else None,
            algorithm=algorithm,
            params_json=jsonable(params or {}),
            feature_set_version=feature_set_version,
            training_data_ref=training_data_ref,
            threshold=float(threshold) if threshold is not None else None,
            governance_status=governance_status,
            model_card_uri=model_card_uri,
            artifact_sha256=artifact_sha256,
        )
        self.session.add(version)
        self.session.flush()
        audit.record(
            self.session,
            actor_type="system",
            actor_id="monitoring",
            action="model_version.recorded",
            resource_type="model_version",
            resource_id=str(version.id),
            run_id=run.id if run else None,
            outcome=governance_status,
            details={"model": model.name, "algorithm": algorithm,
                     "mlflow_version": version.mlflow_version},
        )
        return version

    def set_status(self, version: ModelVersion, status: str, *, promoted_by: uuid.UUID | None = None):
        """Follow an alias move in the catalogue; MLflow remains the source of truth."""
        from triad_core.ids import utcnow

        previous = version.governance_status
        version.governance_status = status
        superseded: list[ModelVersion] = []
        if status == "champion":
            version.promoted_at = utcnow()
            version.promoted_by = promoted_by
            model = self.session.get(Model, version.model_id)
            if model is not None:
                model.champion_version_id = version.id
            # One champion per model. This set the new one and left the old one marked
            # `champion` too, so four versions of churn_classifier carried the status at once,
            # and after a smoke-test rollback the withdrawn version still did. The API's
            # rollback endpoint archived the others; this path, which publish uses, did not.
            superseded = list(self.session.execute(
                select(ModelVersion).where(
                    ModelVersion.model_id == version.model_id,
                    ModelVersion.governance_status == "champion",
                    ModelVersion.id != version.id,
                )
            ).scalars())
            for old in superseded:
                old.governance_status = "archived"
        self.session.flush()
        for old in superseded:
            audit.record(
                self.session,
                actor_type="system",
                actor_id="monitoring",
                action="model_version.status_changed",
                resource_type="model_version",
                resource_id=str(old.id),
                run_id=old.run_id,
                outcome="archived",
                details={"from": "champion", "to": "archived",
                         "superseded_by": str(version.id)},
            )
        audit.record(
            self.session,
            actor_type="system",
            actor_id="monitoring",
            action="model_version.status_changed",
            resource_type="model_version",
            resource_id=str(version.id),
            run_id=version.run_id,
            outcome=status,
            details={"from": previous, "to": status},
        )
        return version

    def record_predictions(
        self,
        version: ModelVersion,
        predictions: Any,
        *,
        run: PipelineRun | None = None,
        pseudonymise: bool = True,
    ) -> int:
        """Store a scored set so it can be queried, filtered and exported later.

        Customer references are pseudonymous by default (PRD 25.1): the catalogue keeps a
        stable one-way reference, and the raw identifier stays in the feature store where
        access is already controlled. `pseudonymise=False` exists for a run that is scoring
        an internal test fixture, not as a convenience.
        """
        import datetime as dt

        from agents.llm.redaction import mask_customer_ref
        from triad_core.models import Prediction

        frame = getattr(predictions, "frame", predictions)
        if frame is None or not len(frame):
            return 0

        set_type = getattr(predictions, "set_type", "official")
        feature_set_version = getattr(predictions, "feature_set_version", None)
        rows = []
        for record in frame.to_dict("records"):
            reference = str(record["customer_ref"])
            score_date = record["score_date"]
            if isinstance(score_date, str):
                score_date = dt.date.fromisoformat(score_date)
            rows.append(
                Prediction(
                    model_version_id=version.id,
                    prediction_set=set_type,
                    customer_ref=mask_customer_ref(reference) if pseudonymise else reference,
                    score_date=score_date,
                    churn_probability=float(record["churn_probability"]),
                    risk_band=str(record["risk_band"]),
                    top_reasons=record.get("top_reasons") or [],
                    feature_set_version=record.get("feature_set_version")
                    or feature_set_version,
                    run_id=run.id if run else None,
                )
            )
        self.session.add_all(rows)
        self.session.flush()
        audit.record(
            self.session,
            actor_type="system",
            actor_id="monitoring",
            action="predictions.published",
            resource_type="model_version",
            resource_id=str(version.id),
            run_id=run.id if run else None,
            outcome=set_type,
            details={"rows": len(rows), "pseudonymous": pseudonymise},
        )
        return len(rows)

    def record_report(
        self,
        run: PipelineRun,
        *,
        report_type: str,
        storage_uri: str,
        claim_check: dict[str, Any] | None = None,
        fmt: str = "html",
    ) -> Any:
        """Register a produced report, with the claim check that let it be published."""
        from triad_core.models import Report

        claim = claim_check or {}
        passed = bool(claim.get("passed", False))
        row = Report(
            run_id=run.id,
            report_type=report_type,
            storage_uri=storage_uri,
            format=fmt,
            status="published" if passed else "draft",
            claim_check_status="passed" if passed else "failed",
            claim_check_json=claim,
        )
        self.session.add(row)
        self.session.flush()
        audit.record(
            self.session,
            actor_type="system",
            actor_id="monitoring",
            action="report.published" if passed else "report.held",
            resource_type="report",
            resource_id=str(row.id),
            run_id=run.id,
            outcome="published" if passed else "held",
            details={"report_type": report_type, "claim_check": claim.get("passed")},
        )
        return row

    # -- reference and drift ---------------------------------------------------
    def capture_reference(
        self,
        version: ModelVersion,
        frame: pd.DataFrame,
        features: list[str],
        *,
        scores: np.ndarray | None = None,
        high_risk_threshold: float | None = None,
    ) -> str:
        """Freeze the training distribution next to the model version and return its URI."""
        from python_ml.triad_ml.drift import Reference

        reference = Reference.from_frame(
            frame, features, scores=scores, high_risk_threshold=high_risk_threshold
        )
        path = self.storage.write_json(
            self._reference_path(version), reference.to_dict()
        )
        return self.storage.uri(path)

    def load_reference(self, uri: str) -> Reference:
        import json

        from python_ml.triad_ml.drift import Reference

        return Reference.from_dict(json.loads(self.storage.read_text(self.storage.path(uri))))

    def report_drift(
        self,
        version: ModelVersion,
        *,
        reference_uri: str,
        current: pd.DataFrame,
        current_ref: str,
        scores: np.ndarray | None = None,
        high_risk_threshold: float | None = None,
        top_features: list[str] | None = None,
        run: PipelineRun | None = None,
        reference_sample: pd.DataFrame | None = None,
    ) -> DriftOutcome:
        """Compute drift for one scoring run, store it and alert if anything is flagged."""
        from python_ml.triad_ml.drift import compute_drift

        reference = self.load_reference(reference_uri)
        result = compute_drift(
            reference, current,
            scores=scores, high_risk_threshold=high_risk_threshold,
            top_features=top_features, thresholds=self.config.get("retraining"),
            reference_sample=reference_sample,
        )
        row = DriftReport(
            model_version_id=version.id,
            reference_ref=reference_uri,
            current_ref=current_ref,
            psi_json=result.to_dict(),
            flagged_features=result.flagged_features,
        )
        self.session.add(row)
        self.session.flush()

        outcome = DriftOutcome(result=result, report_id=row.id,
                               reference_uri=reference_uri, current_uri=current_ref)
        if outcome.flagged and self.governance is not None:
            reasons = list(result.flagged_features)
            if result.score and result.score.flagged:
                reasons.append(f"scores: {result.score.reason}")
            alert = self.governance.alert(
                alert_type="drift_detected",
                severity="warning",
                run_id=run.id if run else None,
                message="drift detected: " + ", ".join(reasons),
                evidence={"drift_report_id": str(row.id), **result.to_dict()},
            )
            outcome.alert_id = alert.id

        audit.record(
            self.session,
            actor_type="system",
            actor_id="monitoring",
            action="drift.computed",
            resource_type="drift_report",
            resource_id=str(row.id),
            run_id=run.id if run else None,
            outcome="flagged" if outcome.flagged else "stable",
            details={"flagged_features": result.flagged_features,
                     "max_psi": round(result.max_psi, 6)},
        )
        return outcome

    # -- retraining ------------------------------------------------------------
    def evaluate_retraining(
        self, state: MonitoringState, *, run: PipelineRun | None = None
    ) -> RetrainingDecision:
        """Run RT-1..RT-9 and record the verdict, whether or not anything fired."""
        decision = self.policy.evaluate(state)
        audit.record(
            self.session,
            actor_type="system",
            actor_id="monitoring",
            action="retraining.evaluated",
            resource_type="pipeline_run",
            resource_id=str(run.id) if run else "none",
            run_id=run.id if run else None,
            outcome="retrain" if decision.should_retrain else (decision.blocked_by or "quiet"),
            details={"fired": decision.rule_ids, "reason": decision.reason[:500]},
        )
        if decision.fired and self.governance is not None:
            severity = "warning" if decision.should_retrain else "info"
            self.governance.alert(
                alert_type="retraining_triggered" if decision.should_retrain
                else "retraining_parked",
                severity=severity,
                run_id=run.id if run else None,
                message=(
                    f"{', '.join(decision.rule_ids)}: {decision.reason}"
                    if decision.should_retrain
                    else f"{', '.join(decision.rule_ids)} fired but "
                         f"{decision.blocked_by}: {decision.reason}"
                ),
                evidence=decision.to_dict(),
            )
        return decision

    # -- acting on a trigger (FR-074) ------------------------------------------
    def monitoring_state(self, version: ModelVersion) -> MonitoringState:
        """Build the trigger inputs from what the platform has actually recorded.

        Anything not recorded stays `None`, so the rule that needs it reports "not evaluated"
        rather than passing by default. Matured performance in particular is genuinely absent
        here: churn labels mature 90 days after scoring, so RT-1 and RT-4 have nothing to
        look at until then, and saying so is the honest answer.
        """
        from python_ml.triad_ml.drift import DriftResult, FeatureDrift, ScoreDrift

        latest = self.session.execute(
            select(DriftReport)
            .where(DriftReport.model_version_id == version.id)
            .order_by(DriftReport.computed_at.desc())
        ).scalars().first()

        drift = None
        if latest is not None:
            payload = latest.psi_json or {}
            score = payload.get("score")
            drift = DriftResult(
                features=[
                    FeatureDrift(
                        feature=f["feature"], kind=f.get("kind", "numeric"),
                        psi=float(f.get("psi") or 0.0), test=f.get("test", "none"),
                        flagged=bool(f.get("flagged")), note=f.get("note", ""),
                    )
                    for f in payload.get("features", [])
                ],
                score=ScoreDrift(
                    psi=float(score.get("psi") or 0.0),
                    reference_high_risk_share=score.get("reference_high_risk_share"),
                    current_high_risk_share=score.get("current_high_risk_share"),
                    share_change=score.get("share_change"),
                    flagged=bool(score.get("flagged")),
                    reason=score.get("reason", ""),
                ) if score else None,
                thresholds=payload.get("thresholds", {}),
                n_rows=int(payload.get("n_rows") or 0),
                computed_at=payload.get("computed_at", ""),
            )

        return MonitoringState(
            drift=drift,
            last_trained_at=version.created_at,
            # The catalogue's own version. This read a config key nothing sets and defaulted to
            # "fsv=1", so a catalogue change could never make RT-7 fire against a champion.
            feature_catalog_version=self.config.get("feature_set_version") or catalog_version(),
            model_feature_catalog_version=version.feature_set_version,
        )

    def start_retraining(
        self,
        decision: RetrainingDecision,
        *,
        source: PipelineRun,
        pipeline: str | None = None,
        trigger_type: str = "retrain",
    ) -> PipelineRun | None:
        """Queue a retraining run for a decision that fired and passed the validity gate.

        Returns None when nothing should start. Two guards, and the second is the important
        one: the cooldown is measured from the last *training*, which does not move when a
        retrain keeps the champion, so without a guard on recent **attempts** a fired trigger
        would start a new run every time the monitor looked.
        """
        from agents.graph import queue as q

        if not decision.should_retrain:
            return None

        name = pipeline or source.pipeline_name
        cooldown = dt.timedelta(days=float(self.policy.config["cooldown_days"]))
        since = utcnow() - cooldown

        # Every retraining run has a parent, whatever its trigger type says caused it; a manual
        # run or a data-arrival run does not. Keyed on the type alone, a `drift` or `schedule`
        # retrain would not count as an attempt and the next tick would start another.
        recent = self.session.execute(
            select(PipelineRun)
            .where(
                PipelineRun.pipeline_name == name,
                PipelineRun.parent_run_id.is_not(None),
                PipelineRun.trigger_type.in_(("retrain", "drift", "schedule", "data")),
                PipelineRun.started_at >= since,
            )
            .order_by(PipelineRun.started_at.desc())
        ).scalars().first()
        if recent is not None:
            audit.record(
                self.session, actor_type="system", actor_id="monitoring",
                action="retraining.not_started", resource_type="pipeline_run",
                resource_id=str(recent.id), run_id=source.id, outcome="already_attempted",
                details={"reason": f"{recent.run_key} was already started for this trigger "
                                   f"within the {self.policy.config['cooldown_days']}-day "
                                   "cooldown", "fired": decision.rule_ids},
            )
            return None

        run = q.create_run(
            self.session,
            pipeline_name=name,
            trigger_type=trigger_type,
            triggered_by="system",
            config={"retraining": decision.to_dict(), "parent_run": source.run_key},
            input_versions=dict(source.input_versions or {}),
            budget=dict(source.budget_json or {}),
        )
        run.parent_run_id = source.id
        q.enqueue(
            self.session, run, stage="pipeline", agent_name="orchestrator",
            task_type="run", input_refs=dict(source.input_versions or {}),
        )
        audit.record(
            self.session, actor_type="system", actor_id="monitoring",
            action="retraining.started", resource_type="pipeline_run",
            resource_id=str(run.id), run_id=run.id, outcome="queued",
            details={"fired": decision.rule_ids, "because": decision.reason[:500],
                     "parent_run": source.run_key},
        )
        if self.governance is not None:
            self.governance.alert(
                alert_type="retraining_started", severity="info", run_id=run.id,
                message=f"{run.run_key} was queued by {', '.join(decision.rule_ids)}",
                evidence=decision.to_dict(),
            )
        return run

    # -- helpers ---------------------------------------------------------------
    def _reference_path(self, version: ModelVersion) -> Path:
        return (
            self.storage.root / "monitoring" / "references" / f"mv={version.id}"
            / "reference.json"
        )


def jsonable(payload: dict[str, Any]) -> dict[str, Any]:
    """Coerce a payload for a JSON column: numpy scalars and tuples turn up from Optuna,
    sklearn and the evaluation code, and `json.dumps` refuses both."""
    import numpy as np

    def convert(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): convert(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [convert(v) for v in value]
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    return {str(k): convert(v) for k, v in payload.items()}
