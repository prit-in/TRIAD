"""FR-074: "Data, schedule, drift and manual triggers - each trigger type starts a run in tests."

Until agents/scheduler.py only the manual trigger ever started a run. RT-1..RT-9 were evaluated
once, straight after training, where the cooldown blocks every decision by construction; the
monitor's own evaluation path for a deployed champion had no caller; and `watch_folder` was run
by nothing. Each test here starts a run through `Scheduler.tick`, the call the worker makes.
(The manual trigger is covered by tests/unit/test_api.py through POST /pipelines/run.)
"""

from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from agents.scheduler import Scheduler, trigger_type_for
from python_ml.triad_ml.hygiene import catalog_version
from triad_core.config import load_yaml, settings
from triad_core.ids import utcnow


@pytest.fixture()
def config() -> dict:
    return load_yaml(settings().pipeline_config_path)


def _champion(session, *, trained_days_ago: int, pipeline: str):
    """A model whose champion was trained `trained_days_ago` days ago, by a run of `pipeline`."""
    from triad_core.models import Model, ModelVersion, PipelineRun

    run = PipelineRun(run_key=f"run-trig-{uuid.uuid4().hex[:10]}", pipeline_name=pipeline,
                      state="PUBLISHED", trigger_type="manual", mode="full",
                      input_versions={"transactions": "file:///data/triad/raw/transactions/v=1"})
    name = f"trig-{uuid.uuid4().hex[:8]}"
    model = Model(name=name, mlflow_registered_name=name, target_definition="t")
    session.add_all([run, model])
    session.flush()
    version = ModelVersion(model_id=model.id, run_id=run.id, mlflow_version="1",
                           algorithm="logreg", params_json={}, governance_status="champion",
                           feature_set_version=catalog_version())
    session.add(version)
    session.flush()
    version.created_at = utcnow() - dt.timedelta(days=trained_days_ago)
    model.champion_version_id = version.id
    session.flush()
    return run, version


def _runs_started_from(session, source) -> list:
    from triad_core.models import PipelineRun

    return list(session.execute(
        select(PipelineRun).where(PipelineRun.parent_run_id == source.id)
    ).scalars())


def test_trigger_types_name_their_cause() -> None:
    assert trigger_type_for(["RT-2"]) == "drift"
    assert trigger_type_for(["RT-6", "RT-3"]) == "drift"
    assert trigger_type_for(["RT-5"]) == "data"
    assert trigger_type_for(["RT-6"]) == "schedule"
    assert trigger_type_for(["RT-7"]) == "retrain"


def test_the_schedule_trigger_starts_a_run(db_session, config) -> None:
    """RT-6: thirty days since training, nothing else wrong."""
    source, _ = _champion(db_session, trained_days_ago=40,
                         pipeline=f"churn-sched-{uuid.uuid4().hex[:6]}")

    Scheduler(config).tick(db_session)

    started = _runs_started_from(db_session, source)
    assert [r.trigger_type for r in started] == ["schedule"]
    assert started[0].state == "RECEIVED", "queued for the worker, not run in the tick"


def test_the_drift_trigger_starts_a_run(db_session, config) -> None:
    """RT-2: a feature's PSI over the threshold, past the cooldown, before the schedule."""
    from triad_core.models import DriftReport

    source, version = _champion(db_session, trained_days_ago=10,
                                pipeline=f"churn-drift-{uuid.uuid4().hex[:6]}")
    db_session.add(DriftReport(
        model_version_id=version.id, reference_ref="ref", current_ref="cur",
        psi_json={"features": [{"feature": "recency_days", "psi": 0.42, "flagged": True,
                                "kind": "numeric", "test": "ks"}],
                  "thresholds": {"psi_feature_threshold": 0.25}, "n_rows": 500},
        flagged_features=["recency_days"],
    ))
    db_session.flush()

    Scheduler(config).tick(db_session)

    assert [r.trigger_type for r in _runs_started_from(db_session, source)] == ["drift"]


def test_a_trigger_inside_the_cooldown_starts_nothing(db_session, config) -> None:
    """The validity gate still applies: a champion trained two days ago is not retrained."""
    source, _ = _champion(db_session, trained_days_ago=2,
                          pipeline=f"churn-cool-{uuid.uuid4().hex[:6]}")
    Scheduler(config).tick(db_session)
    assert _runs_started_from(db_session, source) == []


def test_a_second_tick_does_not_start_a_second_run(db_session, config) -> None:
    """The guard on recent attempts: one fired trigger is one run, not one per tick."""
    source, _ = _champion(db_session, trained_days_ago=40,
                          pipeline=f"churn-twice-{uuid.uuid4().hex[:6]}")
    scheduler = Scheduler(config)
    scheduler.tick(db_session)
    scheduler.tick(db_session)
    assert len(_runs_started_from(db_session, source)) == 1


def test_a_retrain_takes_the_latest_data_and_a_fresh_budget(db_session, config) -> None:
    """Found live on 2026-10-03: the first scheduled retrain copied its source run's inputs -
    none, for the early demo runs - and was rejected for want of transactions; and it copied
    the source's budget, usage included, so it started with the parent's minutes spent."""
    from sqlalchemy import func

    from triad_core.models import Dataset, DatasetVersion

    source, _ = _champion(db_session, trained_days_ago=40,
                          pipeline=f"churn-latest-{uuid.uuid4().hex[:6]}")
    source.input_versions = {}
    source.budget_json = {"limits": {"max_wall_clock_minutes": 90}, "elapsed_minutes": 15.5,
                          "llm_calls": 7, "repair_attempts": {"P5": 3}}
    dataset = db_session.execute(
        select(Dataset).where(Dataset.name == "transactions")
    ).scalar_one_or_none()
    if dataset is None:
        dataset = Dataset(name="transactions", entity_type="transaction", source_type="parquet")
        db_session.add(dataset)
        db_session.flush()
    newest = 1 + (db_session.execute(
        select(func.max(DatasetVersion.version_no)).where(DatasetVersion.dataset_id == dataset.id)
    ).scalar() or 0)
    uri = f"file:///data/triad/raw/transactions/v={newest}/part-00000.parquet"
    db_session.add(DatasetVersion(dataset_id=dataset.id, version_no=newest, storage_uri=uri,
                                  checksum_sha256="0" * 64, size_bytes=1))
    db_session.flush()

    Scheduler(config).tick(db_session)

    [run] = _runs_started_from(db_session, source)
    assert run.input_versions["transactions"] == uri
    assert run.budget_json == {"limits": {"max_wall_clock_minutes": 90}}


@pytest.mark.slow
def test_the_data_trigger_starts_a_run(db_session, config, tmp_path) -> None:
    """New data landing in a watched folder registers a version and starts a run on it."""
    from dataeng.ingest import DatasetRegistry
    from datasets.synthetic.generator import GeneratorConfig, SyntheticGenerator
    from triad_core.models import PipelineRun
    from triad_core.storage import Storage

    store = Storage(tmp_path / "data")
    pipeline = f"churn-data-{uuid.uuid4().hex[:6]}"
    tables = SyntheticGenerator(
        GeneratorConfig(customers=30, months=6, seed=5, start_date="2025-01-01")
    ).write(tmp_path / "generated")

    landing = store.root / "landing" / "transactions"
    landing.mkdir(parents=True)
    source = sorted(Path(tables["transactions"]).glob("*.parquet"))[0]
    (landing / source.name).write_bytes(source.read_bytes())

    DatasetRegistry(db_session, store).register_source(
        "transactions", entity_type="transaction", source_type="parquet"
    )
    db_session.flush()

    cfg = {**config, "pipeline": pipeline,
           "triggers": {"watch": [{"dataset": "transactions",
                                   "folder": "landing/transactions", "pattern": "*.parquet"}]}}
    outcome = Scheduler(cfg, store=store).tick(db_session)

    assert outcome.files_registered >= 1 or outcome.data_runs_queued, outcome
    runs = list(db_session.execute(
        select(PipelineRun).where(PipelineRun.pipeline_name == pipeline)
    ).scalars())
    assert [r.trigger_type for r in runs] == ["data"]
    assert runs[0].input_versions.get("transactions", "").startswith("file://")
    assert tables, "the generator wrote its tables"

    # The same file at the next tick is not an arrival, even with no run active.
    from agents.graph import queue as q

    q.set_state(db_session, runs[0], "CANCELLED")
    again = Scheduler(cfg, store=store).tick(db_session)
    assert again.files_registered == 0 and not again.data_runs_queued, again


def test_a_retrain_trains_the_model_its_parent_named(db_session, config) -> None:
    """A run that names its own model (EXP-7) is retrained under that name, not the platform's:
    without it the retrain would register as, and be compared with, the platform's champion."""
    source, _ = _champion(db_session, trained_days_ago=40,
                          pipeline=f"churn-named-{uuid.uuid4().hex[:6]}")
    source.config_json = {"model_name": "churn_classifier_named"}
    db_session.flush()

    Scheduler(config).tick(db_session)

    [started] = _runs_started_from(db_session, source)
    assert started.config_json["model_name"] == "churn_classifier_named"


def test_one_champion_is_evaluated_as_the_tick_evaluates_it(db_session, config) -> None:
    from triad_core.models import Model

    source, version = _champion(db_session, trained_days_ago=40,
                                pipeline=f"churn-one-{uuid.uuid4().hex[:6]}")
    model = db_session.get(Model, version.model_id)

    decision, queued = Scheduler(config).evaluate_champion(db_session, model)

    assert decision.should_retrain and queued is not None
    assert queued.parent_run_id == source.id

    model.champion_version_id = None
    assert Scheduler(config).evaluate_champion(db_session, model) is None, "no champion"


def test_a_retired_model_is_no_longer_watched(db_session, config) -> None:
    """Retiring clears the champion, so the tick passes the model by - and it is audited."""
    from agents.monitoring import MonitoringService
    from triad_core.models import AuditLog, Model

    source, version = _champion(db_session, trained_days_ago=40,
                                pipeline=f"churn-retired-{uuid.uuid4().hex[:6]}")
    model = db_session.get(Model, version.model_id)

    stood_down = MonitoringService(db_session).retire(model, actor="tester",
                                                      reason="experiment finished")
    Scheduler(config).tick(db_session)

    assert stood_down is version and version.governance_status == "archived"
    assert model.champion_version_id is None
    assert _runs_started_from(db_session, source) == []
    assert db_session.execute(select(AuditLog).where(
        AuditLog.action == "model.retired", AuditLog.resource_id == str(model.id))).first()


def test_a_retrain_reads_the_newest_version_of_the_dataset_its_parent_read(db_session,
                                                                        config) -> None:
    """Not the dataset that shares the input's name: an EXP-7 champion read datasets carrying
    the experiment's tag, and its retrain was given whatever another run had last registered
    as `transactions` (DEC-48)."""
    from sqlalchemy import func

    from triad_core.models import Dataset, DatasetVersion

    def add_version(name: str) -> str:
        dataset = db_session.execute(
            select(Dataset).where(Dataset.name == name)).scalar_one_or_none()
        if dataset is None:
            dataset = Dataset(name=name, entity_type="transaction", source_type="parquet")
            db_session.add(dataset)
            db_session.flush()
        number = 1 + (db_session.execute(
            select(func.max(DatasetVersion.version_no))
            .where(DatasetVersion.dataset_id == dataset.id)).scalar() or 0)
        uri = f"file:///data/triad/raw/{name}/v={number}/part-00000.parquet"
        db_session.add(DatasetVersion(dataset_id=dataset.id, version_no=number, storage_uri=uri,
                                      checksum_sha256=uuid.uuid4().hex * 2, size_bytes=1))
        db_session.flush()
        return uri

    tagged = f"transactions__trig{uuid.uuid4().hex[:6]}"
    read = add_version(tagged)
    arrived = add_version(tagged)
    elsewhere = add_version("transactions")
    source, _ = _champion(db_session, trained_days_ago=40,
                          pipeline=f"churn-own-{uuid.uuid4().hex[:6]}")
    source.input_versions = {"transactions": read}
    db_session.flush()

    Scheduler(config).tick(db_session)

    [run] = _runs_started_from(db_session, source)
    assert run.input_versions["transactions"] == arrived != elsewhere
