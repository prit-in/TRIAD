"""What starts a run besides a person: schedule, drift and data-arrival triggers (FR-074).

FR-074 is "Data, schedule, drift and manual triggers - each trigger type starts a run in tests."
Until this module only the manual one did. The retraining rules RT-1..RT-9 were evaluated in one
place, the publish phase, immediately after training - where the 7-day cooldown blocks every
decision by construction, so a fired trigger could never start anything; and
`MonitoringService.monitoring_state`, which builds the state for evaluating the *deployed*
champion, had no caller outside its own test. `watch_folder` likewise existed and nothing ran it.

The worker calls `Scheduler.tick` on a timer. A tick does two things:

  1. For every model with a champion, evaluate RT-1..RT-9 against the champion's latest drift
     report and age, and queue a retraining run if a rule fires and the validity gate allows it.
     The run's `trigger_type` says why: `drift` (RT-1..RT-4), `data` (RT-5), `schedule` (RT-6),
     `retrain` otherwise.
  2. For every configured landing folder, register the files that arrived and, if any did,
     start a run on the latest version of every input - `trigger_type = "data"`.

Both go through the same queue as a manual run, so nothing a trigger starts skips a gate, and
nothing it produces is promoted without a person.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from triad_core.ids import utcnow

log = logging.getLogger("triad.scheduler")

DRIFT_RULES = frozenset({"RT-1", "RT-2", "RT-3", "RT-4"})
ACTIVE_STATES_EXCLUDED = frozenset(
    {"COMPLETED", "ABORTED", "PUBLISHED", "REJECTED_INPUT", "CANCELLED", "FAILED",
     "APPROVAL_REQUIRED", "ESCALATED"}
)


def trigger_type_for(rule_ids: list[str]) -> str:
    """Name a retraining run by its cause, so the four FR-074 trigger types are distinguishable."""
    fired = set(rule_ids)
    if fired & DRIFT_RULES:
        return "drift"
    if "RT-5" in fired:
        return "data"
    if "RT-6" in fired:
        return "schedule"
    return "retrain"


@dataclass
class TickResult:
    evaluated_models: int = 0
    retraining_queued: list[str] = field(default_factory=list)
    data_runs_queued: list[str] = field(default_factory=list)
    files_registered: int = 0
    notes: list[str] = field(default_factory=list)


class Scheduler:
    def __init__(self, config: dict[str, Any], *, store: Any = None) -> None:
        self.config = config
        self.triggers = config.get("triggers", {}) or {}
        self.store = store

    # -- the tick ------------------------------------------------------------------------------
    def tick(self, session: Session, *, now: dt.datetime | None = None) -> TickResult:
        result = TickResult()
        self._evaluate_champions(session, result)
        self._watch_landing_folders(session, result, now=now or utcnow())
        return result

    # -- 1. schedule and drift -------------------------------------------------------------------
    def _evaluate_champions(self, session: Session, result: TickResult) -> None:
        from agents.monitoring import MonitoringService
        from triad_core.models import Model, ModelVersion, PipelineRun

        monitor = MonitoringService(session, store=self.store, config=self.config)
        models = session.execute(
            select(Model).where(Model.champion_version_id.is_not(None))
        ).scalars().all()
        for model in models:
            champion = session.get(ModelVersion, model.champion_version_id)
            if champion is None or champion.run_id is None:
                continue
            source = session.get(PipelineRun, champion.run_id)
            if source is None:
                continue
            result.evaluated_models += 1
            decision = monitor.evaluate_retraining(monitor.monitoring_state(champion), run=source)
            if not decision.should_retrain:
                continue
            queued = monitor.start_retraining(
                decision, source=source, trigger_type=trigger_type_for(decision.rule_ids)
            )
            if queued is not None:
                result.retraining_queued.append(queued.run_key)
                log.info("queued %s for %s (%s)", queued.run_key, model.name,
                         ", ".join(decision.rule_ids))

    # -- 2. data arrival ---------------------------------------------------------------------------
    def _watch_landing_folders(self, session: Session, result: TickResult, *, now: dt.datetime) -> None:
        from dataeng.ingest import DatasetRegistry, watch_folder
        from triad_core.storage import storage

        watches = self.triggers.get("watch") or []
        if not watches:
            return
        store = self.store or storage()
        registry = DatasetRegistry(session, store)
        arrived = False
        for watch in watches:
            dataset = watch["dataset"]
            folder = Path(watch["folder"])
            folder = folder if folder.is_absolute() else store.root / folder
            if not folder.is_dir() or registry.get(dataset) is None:
                continue
            outcome = watch_folder(registry, dataset, folder, pattern=watch.get("pattern", "*"))
            registered = [r for r in outcome.registered if getattr(r, "version_id", None)]
            result.files_registered += len(registered)
            arrived = arrived or bool(registered)
        if arrived:
            run = self._start_data_run(session, store=store)
            if run is not None:
                result.data_runs_queued.append(run.run_key)
            else:
                result.notes.append("new data registered; a run is already active, so none started")

    def _start_data_run(self, session: Session, *, store: Any) -> Any:
        from agents.graph import queue as q
        from triad_core import audit
        from triad_core.models import Dataset, DatasetVersion, PipelineRun

        pipeline = self.config.get("pipeline", "churn_v1")
        # One run of a pipeline at a time (DEC-18): data arriving mid-run waits for the next tick.
        active = session.execute(
            select(PipelineRun.id).where(
                PipelineRun.pipeline_name == pipeline,
                PipelineRun.state.not_in(ACTIVE_STATES_EXCLUDED),
            ).limit(1)
        ).first()
        if active is not None:
            return None

        inputs: dict[str, str] = {}
        for name in (self.config.get("inputs") or {}):
            dataset = session.execute(select(Dataset).where(Dataset.name == name)).scalars().first()
            if dataset is None:
                continue
            version = session.execute(
                select(DatasetVersion)
                .where(DatasetVersion.dataset_id == dataset.id)
                .order_by(DatasetVersion.version_no.desc())
            ).scalars().first()
            if version is not None and version.storage_uri:
                inputs[name] = version.storage_uri
        if "transactions" not in inputs:
            return None

        run = q.create_run(
            session, pipeline_name=pipeline, trigger_type="data", triggered_by="scheduler",
            config={"trigger": "data arrival"}, input_versions=inputs,
        )
        q.enqueue(session, run, stage="pipeline", agent_name="orchestrator",
                  task_type="run", input_refs=inputs)
        audit.record(
            session, actor_type="system", actor_id="scheduler", action="run.triggered",
            resource_type="pipeline_run", resource_id=str(run.id), run_id=run.id,
            outcome="queued", details={"trigger": "data", "inputs": sorted(inputs)},
        )
        return run
