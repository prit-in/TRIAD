"""Regression tests for the defects the October 2026 audit confirmed, each against the live path.

Each test names the defect and the evidence it was confirmed on. None of them asserts on a
copy of the logic: they call the function the pipeline calls.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import delete, select, text

# --- #5: per-customer reasons ----------------------------------------------------------------


def _fitted(calibrated: bool, family: str = "logreg"):
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.frozen import FrozenEstimator
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    rng = np.random.default_rng(0)
    n = 300
    x = pd.DataFrame({"recency_days": rng.gamma(3, 20, n), "frequency_90d": rng.poisson(3, n)})
    y = ((x.recency_days > 60) ^ (rng.random(n) < 0.1)).astype(int)
    clf = (LogisticRegression(max_iter=500) if family == "logreg"
           else RandomForestClassifier(n_estimators=30, random_state=0))
    pipe = Pipeline([
        ("prep", ColumnTransformer([("num", StandardScaler(), list(x.columns))])),
        ("clf", clf),
    ]).fit(x, y)
    if calibrated:
        pipe = CalibratedClassifierCV(FrozenEstimator(pipe), method="sigmoid").fit(x, y)
    frame = x.assign(customer_id=[f"C{i}" for i in range(n)])
    return pipe, frame, list(x.columns)


@pytest.mark.parametrize("calibrated", [False, True])
@pytest.mark.parametrize("family", ["logreg", "random_forest"])
def test_every_scored_customer_gets_reasons(calibrated: bool, family: str) -> None:
    """`top_reasons` was empty on all 3 625 predictions: the champion was a calibrated wrapper
    `explain` could not see into, and the linear branch returned global coefficients only."""
    from python_ml.triad_ml.predict import explain

    model, frame, cols = _fitted(calibrated, family)
    out = explain(model, frame, cols, sample=len(frame))
    if family == "random_forest" and out.method.startswith("permutation"):
        pytest.skip("shap is not installed here; the tree branch needs it")
    assert len(out.per_customer) == len(frame), out.method
    assert all(reasons for reasons in out.per_customer.values())
    first = next(iter(out.per_customer.values()))[0]
    assert first["direction"] in {"raises", "lowers"}
    if calibrated:
        assert "uncalibrated" in out.method, "the method must say what was explained"


def test_linear_reasons_are_an_exact_decomposition_of_the_log_odds() -> None:
    """Coefficient x standardised input sums, with the intercept, to the model's own logit."""
    from python_ml.triad_ml.predict import explain

    model, frame, cols = _fitted(calibrated=False)
    out = explain(model, frame, cols, sample=len(frame), top_k=len(cols))
    clf = model.named_steps["clf"]
    xt = model.named_steps["prep"].transform(frame[cols])
    logit = clf.decision_function(model.named_steps["prep"].transform(frame[cols]))
    for row in range(5):
        ref = frame.customer_id.iloc[row]
        total = sum(r["contribution"] for r in out.per_customer[ref]) + clf.intercept_[0]
        assert total == pytest.approx(float(logit[row]), abs=1e-9), (ref, xt[row])


# --- #2/#6: one champion per model -----------------------------------------------------------


def test_promoting_a_version_archives_the_previous_champion(db_session) -> None:
    """Four versions of churn_classifier carried `champion` at once."""
    from agents.monitoring import MonitoringService
    from triad_core.models import Model, ModelVersion

    service = MonitoringService(db_session)
    name = f"m-{uuid.uuid4().hex[:6]}"
    model = Model(name=name, mlflow_registered_name=name, target_definition="t")
    db_session.add(model)
    db_session.flush()
    versions = []
    for i in range(3):
        v = ModelVersion(model_id=model.id, mlflow_version=str(i + 1), algorithm="logreg",
                         params_json={}, governance_status="challenger")
        db_session.add(v)
        versions.append(v)
    db_session.flush()

    for v in versions:
        service.set_status(v, "champion")

    statuses = {
        r.mlflow_version: r.governance_status
        for r in db_session.execute(
            select(ModelVersion).where(ModelVersion.model_id == model.id)
        ).scalars()
    }
    assert statuses == {"1": "archived", "2": "archived", "3": "champion"}
    assert model.champion_version_id == versions[-1].id

    # A rollback is the same move in the other direction, and leaves one champion too.
    service.set_status(versions[0], "champion")
    champions = [v for v in versions if v.governance_status == "champion"]
    assert champions == [versions[0]]


# --- #3/#16: model integrity -----------------------------------------------------------------


@pytest.mark.slow
def test_a_tampered_model_is_refused_and_an_untampered_one_loads(tmp_path) -> None:
    """ST-08. `load` claimed to verify a hash; none was recorded and nothing was checked."""
    import mlflow

    from python_ml.triad_ml.registry import ModelIntegrityError, ModelRegistry

    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    registry = ModelRegistry(f"sqlite:///{tmp_path / 'mlflow.db'}", experiment="integrity")
    model, frame, cols = _fitted(calibrated=False)
    registered = registry.log_candidate(
        model=model, name="integrity", params={}, metrics={}, register_as="integrity_model",
    )
    assert registered is not None and registered.artifact_sha256
    assert len(registered.artifact_sha256) == 64

    loaded = registry.load_version("integrity_model", registered.version,
                                   expected_sha256=registered.artifact_sha256)
    assert loaded.predict_proba(frame[cols]).shape == (len(frame), 2)

    with pytest.raises(ModelIntegrityError, match="does not match"):
        registry.load_version("integrity_model", registered.version, expected_sha256="0" * 64)


@pytest.mark.slow
def test_a_model_with_no_recorded_hash_is_not_loaded_on_trust(tmp_path) -> None:
    import mlflow

    from python_ml.triad_ml.registry import ModelIntegrityError, ModelRegistry

    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    registry = ModelRegistry(f"sqlite:///{tmp_path / 'mlflow.db'}", experiment="no-hash")
    model, _, _ = _fitted(calibrated=False)
    registered = registry.log_candidate(
        model=model, name="nohash", params={}, metrics={}, register_as="nohash_model",
    )
    registry.client.delete_model_version_tag("nohash_model", registered.version, "artifact_sha256")
    with pytest.raises(ModelIntegrityError, match="no recorded artifact hash"):
        registry.load_version("nohash_model", registered.version)


# --- #7/#8: the worker's transactions ----------------------------------------------------------


def _run(session, *, state: str = "RECEIVED", started_hours_ago: float = 0.0):
    import datetime as dt

    from triad_core.ids import utcnow
    from triad_core.models import PipelineRun

    run = PipelineRun(
        run_key=f"run-audit-{uuid.uuid4().hex[:10]}", pipeline_name="churn",
        state=state, trigger_type="manual", mode="full",
        budget_json={"limits": {"max_wall_clock_minutes": 240}},
    )
    session.add(run)
    session.flush()
    if started_hours_ago:
        run.started_at = utcnow() - dt.timedelta(hours=started_hours_ago)
        session.flush()
    return run


def test_a_transition_is_visible_to_another_connection_at_once(postgres_available) -> None:
    """#7: the worker ran a whole phase in one transaction, so nothing was visible until it ended."""
    if not postgres_available:
        pytest.skip("postgres service is not reachable")
    from agents.graph.pipeline import ChurnPipeline
    from triad_core.db import session_scope
    from triad_core.models import AuditLog, PipelineRun

    with session_scope() as writer:
        run = _run(writer)
        run_id = run.id
        stub = SimpleNamespace(session=writer, run=run, result=SimpleNamespace(state=None))
        ChurnPipeline._advance(stub, "PROFILED", stage="profile")

        # Read from a separate connection while the writer's session is still open.
        with session_scope() as reader:
            assert reader.get(PipelineRun, run_id).state == "PROFILED"

        # Clean up what `set_state` wrote: the event, the audit entry (an archival, so it has to
        # opt in past the append-only trigger) and the run itself.
        from triad_core.models import Event

        writer.execute(delete(Event).where(Event.run_id == run_id))
        writer.execute(text("SET LOCAL triad.allow_audit_archival = 'on'"))
        writer.execute(delete(AuditLog).where(AuditLog.run_id == run_id))
        writer.execute(delete(PipelineRun).where(PipelineRun.id == run_id))


def test_a_task_that_fails_for_good_fails_its_run(db_session) -> None:
    """#8: run-2026-09-18-011 had a failed task and still read RECEIVED, with no error."""
    from agents.graph import queue as q
    from triad_core.models import AgentTask

    run = _run(db_session)
    task = AgentTask(run_id=run.id, task_type="run", stage="pipeline", agent_name="orchestrator",
                     status="running", attempt=0, max_attempts=1)
    db_session.add(task)
    db_session.flush()

    q.fail(db_session, task.id, error_class="UNKNOWN", fingerprint="f" * 32, requeue=False)

    assert task.status == "failed"
    assert run.state == "FAILED"
    assert "UNKNOWN" in (run.error_summary or "")
    assert run.finished_at is not None, "FAILED is terminal, so it gets a finish time"


def test_a_requeued_task_leaves_its_run_alone(db_session) -> None:
    from agents.graph import queue as q
    from triad_core.models import AgentTask

    run = _run(db_session, state="PROFILED")
    task = AgentTask(run_id=run.id, task_type="run", stage="pipeline", agent_name="orchestrator",
                     status="running", attempt=0, max_attempts=3)
    db_session.add(task)
    db_session.flush()
    q.fail(db_session, task.id, error_class="EXECUTOR_LOST", fingerprint="e" * 32, requeue=True)
    assert task.status == "queued"
    assert run.state == "PROFILED"


def test_a_stranded_run_is_failed_only_after_its_own_ceiling(db_session) -> None:
    """Thirteen runs sat in RECEIVED from 2026-09-18 onward with nothing able to advance them."""
    from agents.graph import queue as q
    from triad_core.models import AgentTask

    stranded = _run(db_session, started_hours_ago=6)       # past 240 min + 60 min grace
    young = _run(db_session, started_hours_ago=1)          # could still be running in-process
    waiting = _run(db_session, state="APPROVAL_REQUIRED", started_hours_ago=48)
    busy = _run(db_session, state="TRAINING", started_hours_ago=6)
    db_session.add(AgentTask(run_id=busy.id, task_type="run", stage="pipeline",
                             agent_name="orchestrator", status="leased", attempt=0,
                             max_attempts=1))
    db_session.flush()

    q.reap_stranded_runs(db_session)

    assert stranded.state == "FAILED" and "stranded" in stranded.error_summary
    assert young.state == "RECEIVED"
    assert waiting.state == "APPROVAL_REQUIRED", "a run waiting for a person is not stranded"
    assert busy.state == "TRAINING", "a run with a live task is not stranded"
