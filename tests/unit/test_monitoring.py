"""M16 Monitor: catalogue rows, stored references, drift reports and trigger records.

These tests use the real PostgreSQL schema (rolled back afterwards), because the point of the
service is that a drift report is a row someone can query later, not a log line.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from agents.graph import queue as q
from agents.monitoring import MonitoringService
from agents.retraining import MonitoringState
from triad_core.config import settings
from triad_core.governance import GovernanceService
from triad_core.ids import utcnow
from triad_core.models import Alert, AuditLog, DriftReport, Model, ModelVersion
from triad_core.policy import PolicyEngine

pytestmark = pytest.mark.postgres

RNG = np.random.default_rng(7)
FEATURES = ["recency_days", "orders_365d", "country"]


def _frame(n: int, *, shift: float = 0.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "recency_days": RNG.normal(60 + shift, 15, size=n),
            "orders_365d": RNG.poisson(4, size=n).astype(float),
            "country": RNG.choice(["UK", "FR"], size=n, p=[0.7, 0.3]),
        }
    )


@pytest.fixture()
def run(db_session):
    return q.create_run(
        db_session, pipeline_name="churn_v1", trigger_type="manual",
        triggered_by="test", idempotency_key=f"monitor-{utcnow().timestamp()}",
    )


@pytest.fixture()
def monitor(db_session, storage):
    governance = GovernanceService(
        db_session, PolicyEngine(settings().governance_policy_path)
    )
    return MonitoringService(db_session, store=storage, governance=governance)


@pytest.fixture()
def version(monitor, run):
    model = monitor.model("churn_classifier_test", target_definition="no order in 90 days")
    return monitor.register_model_version(
        model, run=run, algorithm="xgboost", mlflow_version="3",
        params={"max_depth": np.int64(6)}, feature_set_version="fsv=1", threshold=0.42,
        governance_status="challenger",
    )


# --- catalogue ----------------------------------------------------------------


def test_a_model_is_created_once_and_then_reused(monitor) -> None:
    first = monitor.model("reused_model", target_definition="d")
    second = monitor.model("reused_model", target_definition="d")
    assert first.id == second.id


def test_a_version_row_mirrors_the_mlflow_registration(db_session, version) -> None:
    stored = db_session.get(ModelVersion, version.id)
    assert stored.mlflow_version == "3"
    assert stored.governance_status == "challenger"
    assert stored.threshold == pytest.approx(0.42)


def test_numpy_params_are_stored_as_json(db_session, version) -> None:
    """Optuna and sklearn hand back numpy scalars; JSONB will not take them raw."""
    db_session.flush()
    assert db_session.get(ModelVersion, version.id).params_json == {"max_depth": 6}


def test_a_version_is_recorded_even_when_mlflow_was_unreachable(monitor, run) -> None:
    model = monitor.model("offline_model", target_definition="d")
    version = monitor.register_model_version(
        model, run=run, algorithm="logreg", mlflow_version=None,
    )
    assert version.mlflow_version is None      # visibly missing, not invented
    assert version.governance_status == "candidate"


def test_promotion_moves_the_champion_pointer(db_session, monitor, version) -> None:
    monitor.set_status(version, "champion")
    model = db_session.get(Model, version.model_id)
    assert model.champion_version_id == version.id
    assert version.promoted_at is not None


def test_registering_a_version_writes_an_audit_entry(db_session, version) -> None:
    entries = db_session.execute(
        select(AuditLog).where(AuditLog.resource_id == str(version.id))
    ).scalars().all()
    assert any(e.action == "model_version.recorded" for e in entries)


# --- reference and drift ------------------------------------------------------


def test_a_reference_round_trips_through_storage(monitor, version) -> None:
    train = _frame(2000)
    uri = monitor.capture_reference(version, train, FEATURES,
                                    scores=RNG.beta(2, 8, size=2000),
                                    high_risk_threshold=0.66)
    reference = monitor.load_reference(uri)
    assert set(reference.features) == set(FEATURES)
    assert reference.n_rows == 2000
    assert reference.high_risk_share is not None


def test_a_stable_scoring_run_stores_a_report_and_raises_no_alert(
    db_session, monitor, version, run
) -> None:
    uri = monitor.capture_reference(version, _frame(2000), FEATURES)
    outcome = monitor.report_drift(
        version, reference_uri=uri, current=_frame(2000),
        current_ref="file:///data/triad/predictions/test", run=run,
    )
    assert not outcome.flagged
    assert outcome.alert_id is None
    stored = db_session.get(DriftReport, outcome.report_id)
    assert stored.flagged_features == []
    assert stored.reference_ref == uri


def test_a_drifted_scoring_run_raises_an_alert_with_its_evidence(
    db_session, monitor, version, run
) -> None:
    uri = monitor.capture_reference(version, _frame(2000), FEATURES)
    outcome = monitor.report_drift(
        version, reference_uri=uri, current=_frame(2000, shift=45.0),
        current_ref="file:///data/triad/predictions/test", run=run,
    )
    assert outcome.flagged and "recency_days" in outcome.result.flagged_features
    alert = db_session.get(Alert, outcome.alert_id)
    assert alert.alert_type == "drift_detected"
    assert alert.evidence["drift_report_id"] == str(outcome.report_id)


def test_the_drift_report_keeps_the_numbers_not_only_the_verdict(
    db_session, monitor, version, run
) -> None:
    uri = monitor.capture_reference(version, _frame(2000), FEATURES)
    outcome = monitor.report_drift(
        version, reference_uri=uri, current=_frame(2000, shift=45.0),
        current_ref="file:///x", run=run,
    )
    stored = db_session.get(DriftReport, outcome.report_id)
    psi_values = {f["feature"]: f["psi"] for f in stored.psi_json["features"]}
    assert psi_values["recency_days"] > 0.25
    assert "orders_365d" in psi_values          # measured even though it did not move


def test_drift_is_recorded_in_the_audit_trail(db_session, monitor, version, run) -> None:
    uri = monitor.capture_reference(version, _frame(1000), FEATURES)
    outcome = monitor.report_drift(version, reference_uri=uri, current=_frame(1000),
                                   current_ref="file:///x", run=run)
    entry = db_session.execute(
        select(AuditLog).where(AuditLog.resource_id == str(outcome.report_id))
    ).scalar_one()
    assert entry.action == "drift.computed" and entry.outcome == "stable"


# --- retraining ---------------------------------------------------------------


def test_a_quiet_evaluation_is_still_recorded(db_session, monitor, run) -> None:
    decision = monitor.evaluate_retraining(MonitoringState(), run=run)
    assert not decision.should_retrain
    entry = db_session.execute(
        select(AuditLog).where(AuditLog.action == "retraining.evaluated",
                               AuditLog.run_id == run.id)
    ).scalar_one()
    assert entry.outcome == "quiet"


def test_a_fired_trigger_alerts_and_records_the_rule(db_session, monitor, run) -> None:
    state = MonitoringState(
        last_trained_at=utcnow() - timedelta(days=40),
        labelled_rows_at_training=10000, labelled_rows_now=13000,
    )
    decision = monitor.evaluate_retraining(state, run=run)
    assert decision.should_retrain and {"RT-5", "RT-6"} <= set(decision.rule_ids)
    alert = db_session.execute(
        select(Alert).where(Alert.run_id == run.id)
    ).scalar_one()
    assert alert.alert_type == "retraining_triggered"
    assert alert.evidence["fired"]


def test_a_parked_trigger_alerts_at_info_and_says_why(db_session, monitor, run) -> None:
    state = MonitoringState(
        last_trained_at=utcnow() - timedelta(days=1),
        labelled_rows_at_training=10000, labelled_rows_now=13000,
    )
    decision = monitor.evaluate_retraining(state, run=run)
    assert not decision.should_retrain and decision.blocked_by == "cooldown"
    alert = db_session.execute(select(Alert).where(Alert.run_id == run.id)).scalar_one()
    assert alert.alert_type == "retraining_parked" and alert.severity == "info"
    assert "cooldown" in alert.message


def test_thresholds_come_from_the_pipeline_config(db_session, storage) -> None:
    from triad_core.config import load_yaml

    config = load_yaml(settings().pipeline_config_path)
    service = MonitoringService(db_session, store=storage, config=config)
    assert service.policy.config["cooldown_days"] == config["retraining"]["cooldown_days"]
    assert service.policy.enabled_rules == set(config["retraining"]["triggers"])


# --- predictions and reports in the catalogue ---------------------------------


def _prediction_set(n: int = 5):
    from python_ml.triad_ml.predict import OFFICIAL, PredictionSet

    frame = pd.DataFrame(
        {
            "customer_ref": [f"C-{i:04d}" for i in range(n)],
            "churn_probability": np.linspace(0.05, 0.95, n),
            "risk_band": ["low", "low", "medium", "high", "high"][:n],
            "score_date": ["2026-04-01"] * n,
            "feature_set_version": ["fsv=1"] * n,
            "top_reasons": [[{"feature": "recency_days", "value": 0.3}] for _ in range(n)],
        }
    )
    return PredictionSet(
        frame=frame, model_name="churn_classifier", model_version="1",
        feature_set_version="fsv=1", score_date="2026-04-01", set_type=OFFICIAL,
    )


def test_predictions_are_stored_with_their_provenance(db_session, monitor, version, run) -> None:
    from triad_core.models import Prediction

    written = monitor.record_predictions(version, _prediction_set(), run=run)
    assert written == 5
    rows = db_session.execute(
        select(Prediction).where(Prediction.model_version_id == version.id)
    ).scalars().all()
    assert {r.feature_set_version for r in rows} == {"fsv=1"}
    assert {r.prediction_set for r in rows} == {"official"}
    assert all(r.run_id == run.id for r in rows)


def test_customer_references_are_pseudonymous_by_default(db_session, monitor, version, run) -> None:
    from triad_core.models import Prediction

    monitor.record_predictions(version, _prediction_set(), run=run)
    refs = db_session.execute(
        select(Prediction.customer_ref).where(Prediction.model_version_id == version.id)
    ).scalars().all()
    assert not any(r.startswith("C-") for r in refs), "raw identifiers must not be stored"
    assert len(set(refs)) == 5, "the mapping must stay one-to-one, or rows collapse"


def test_the_same_identifier_always_maps_to_the_same_reference(monitor, version, run) -> None:
    """Without this a customer cannot be followed across scoring runs."""
    from agents.llm.redaction import mask_customer_ref

    assert mask_customer_ref("C-0001") == mask_customer_ref("C-0001")
    assert mask_customer_ref("C-0001") != mask_customer_ref("C-0002")


def test_an_empty_prediction_set_writes_nothing(monitor, version, run) -> None:
    assert monitor.record_predictions(version, pd.DataFrame(), run=run) == 0


def test_a_report_that_passed_its_claim_check_is_published(db_session, monitor, run) -> None:
    row = monitor.record_report(
        run, report_type="business", storage_uri="file:///data/triad/reports/r.html",
        claim_check={"passed": True, "facts_verified": 4},
    )
    assert row.status == "published" and row.claim_check_status == "passed"


def test_a_report_that_failed_its_claim_check_is_held(db_session, monitor, run) -> None:
    """An unverified number must not reach a reader as a published report."""
    row = monitor.record_report(
        run, report_type="business", storage_uri="file:///data/triad/reports/r.html",
        claim_check={"passed": False, "unverified": ["42"]},
    )
    assert row.status == "draft" and row.claim_check_status == "failed"
    entry = db_session.execute(
        select(AuditLog).where(AuditLog.resource_id == str(row.id))
    ).scalar_one()
    assert entry.action == "report.held"
