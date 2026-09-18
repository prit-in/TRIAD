"""M16 Monitor: the deterministic service behind drift reports and retraining triggers.

PRD section 11 gives Monitor no agency of its own: it measures, records and raises alerts.
Deciding to retrain is `agents/retraining.py`; acting on that decision is the orchestrator's,
and promoting anything still needs a person. Nothing here calls an LLM.

It also owns the small piece of catalogue bookkeeping the rest of the platform depends on:
MLflow holds the artifact, but `models` / `model_versions` in PostgreSQL are what drift
reports, predictions and approvals point at, so a registration has to land in both.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from agents.retraining import MonitoringState, RetrainingDecision, RetrainingPolicy
from triad_core import audit
from triad_core.governance import GovernanceService
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
            params_json=_jsonable(params or {}),
            feature_set_version=feature_set_version,
            training_data_ref=training_data_ref,
            threshold=float(threshold) if threshold is not None else None,
            governance_status=governance_status,
            model_card_uri=model_card_uri,
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
        if status == "champion":
            version.promoted_at = utcnow()
            version.promoted_by = promoted_by
            model = self.session.get(Model, version.model_id)
            if model is not None:
                model.champion_version_id = version.id
        self.session.flush()
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

    # -- helpers ---------------------------------------------------------------
    def _reference_path(self, version: ModelVersion) -> Path:
        return (
            self.storage.root / "monitoring" / "references" / f"mv={version.id}"
            / "reference.json"
        )


def _jsonable(payload: dict[str, Any]) -> dict[str, Any]:
    """Params come from Optuna and sklearn, so numpy scalars and tuples turn up."""
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
