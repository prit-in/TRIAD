"""Configuration P for the experiments: TRIAD's governed loop, through `churn.prepare`.

EXP-1 and EXP-2 both measure P, and they have to measure the same thing: one way of registering
the inputs, one pipeline call, one way of reading stage and gate times off the platform's own
records. `run_p` returns plain values, so it can run in a process of its own (`run_isolated`)
and the memory it reports is P's, as B2's and B3's are theirs.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

SEED = 42
# The full run is gate 9 - the job doing its work on the whole input. Every other gate is
# validation the baselines do not perform, which is the overhead the hypotheses ask about.
FULL_RUN_GATE = 9


def _spark(app: str) -> Any:
    from pyspark.sql import SparkSession

    from triad_core.config import settings

    session = (
        SparkSession.builder.appName(app)
        .master(settings().spark_master_url)
        .config("spark.sql.ansi.enabled", "true")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.parquet.outputTimestampType", "TIMESTAMP_MICROS")
        .config("spark.sql.shuffle.partitions", "8")
        .config("spark.ui.enabled", "false")
        .config("spark.driver.memory", "1g")
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    return session


def _round(value: Any, places: int = 4) -> Any:
    try:
        return round(float(value), places)
    except (TypeError, ValueError):
        return value


def gate_seconds(artifacts: list[Any]) -> dict[int, float]:
    """Seconds per gate number, summed over every generated job of the run."""
    totals: dict[int, float] = {}
    for artifact in artifacts:
        for gate in (artifact.gate_results or {}).get("gates", []):
            number = gate.get("gate")
            if isinstance(number, int):
                totals[number] = totals.get(number, 0.0) + float(gate.get("duration_ms") or 0) / 1000
    return {k: round(v, 2) for k, v in sorted(totals.items())}


def register_tables(
    session: Any, store: Any, source: Path, pipeline_name: str, *, actor: str
) -> dict[str, str]:
    """Register each table folder under `source` as `<table>__<pipeline_name>`, under its
    contract where there is one, and return `{table: storage_uri}` of what registered.

    Called again with newer files, it registers them as new versions of the same datasets -
    data arriving, as EXP-7 needs it.
    """
    from dataeng.ingest import DatasetRegistry

    registry = DatasetRegistry(session, store)
    registered: dict[str, str] = {}
    for table in sorted(p.name for p in source.iterdir() if p.is_dir()):
        name = f"{table}__{pipeline_name}"
        contract = Path("pipelines") / "contracts" / f"{table}.yaml"
        registry.register_source(
            name, entity_type=table, source_type="parquet",
            contract_yaml=contract.read_text(encoding="utf-8") if contract.exists() else None,
        )
        files = sorted((source / table).glob("*.parquet"))
        if not files:
            continue
        result = registry.register_version(name, files[0], actor=actor)
        if result.version_id:
            registered[table] = result.storage_uri or ""
    return registered


def run_p(source: str | Path, *, prefix: str, experiment: str, seed: int = SEED) -> dict[str, Any]:
    """Register `source`'s tables, run `churn.prepare` on them, and read back what it did.

    The run is cancelled afterwards (which withdraws any approval it asked for): an experiment
    measures the run up to the decision, and nobody is going to make it.
    """
    from sqlalchemy import select

    from agents.graph import churn
    from agents.graph import queue as q
    from experiments.baselines import jvm_peak_rss_mb, peak_rss_mb
    from triad_core.db import session_scope
    from triad_core.models import (
        AuditLog,
        CodeArtifact,
        DecisionRecord,
        Execution,
        LlmInvocation,
        PipelineRun,
    )
    from triad_core.storage import storage

    source = Path(source)
    store = storage()
    started = time.perf_counter()

    with session_scope() as session:
        pipeline_name = f"{prefix}_p_{uuid.uuid4().hex[:6]}"
        run = q.create_run(
            session, pipeline_name=pipeline_name, trigger_type="manual",
            triggered_by=prefix, config={"experiment": experiment, "configuration": "P"},
        )
        session.flush()

        t0 = time.perf_counter()
        registered = register_tables(session, store, source, pipeline_name, actor=prefix)
        ingest_s = round(time.perf_counter() - t0, 2)
        run_id = run.id

    # Committed before the pipeline starts. `audit.record` takes a per-chain
    # `pg_advisory_xact_lock` and the ingest writes above land in the *system* chain, so one
    # transaction spanning the whole run holds that lock for its duration and every
    # `auth.login` blocks behind it (observed in EXP-1: idle for 377 s with a login queued).
    out: dict[str, Any] = {"pipeline_name": pipeline_name, "ingest_s": ingest_s}
    with session_scope() as session:
        run = session.get(PipelineRun, run_id)
        if run is None:
            raise RuntimeError(f"run {run_id} vanished between transactions")
        out["run_key"] = run.run_key

        spark = _spark(f"triad-{prefix}-p")
        try:
            prepared = churn.prepare(
                session, run, inputs=registered, spark=spark, store=store,
                seed=seed, requested_by=prefix,
            )
            out["jvm_peak_rss_mb"] = jvm_peak_rss_mb(spark)
        finally:
            spark.stop()

        out["wall_clock_s"] = round(time.perf_counter() - started, 2)
        out["peak_rss_mb"] = peak_rss_mb()
        out["prepare_ok"] = prepared.ok
        out["prepare_state"] = prepared.state
        out["reason"] = prepared.reason

        executions = session.execute(
            select(Execution).where(Execution.run_id == run.id)
        ).scalars().all()
        stages = prepared.details.get("stages")
        out["per_stage_s"] = (
            {s.get("stage"): round(float(s.get("duration_ms") or 0) / 1000.0, 2) for s in stages}
            if isinstance(stages, list) else
            {f"{e.engine}:{e.id.hex[:6]}": round(float(e.duration_ms or 0) / 1000.0, 2)
             for e in executions}
        )
        gates = gate_seconds(session.execute(
            select(CodeArtifact).where(CodeArtifact.run_id == run.id)
        ).scalars().all())
        out["gate_s"] = gates
        out["validation_gates_s"] = round(sum(v for k, v in gates.items() if k != FULL_RUN_GATE), 2)
        out["full_runs_s"] = gates.get(FULL_RUN_GATE, 0.0)
        out["llm_calls"] = len(session.execute(
            select(LlmInvocation).where(LlmInvocation.run_id == run.id)).scalars().all())
        out["decision_records"] = len(session.execute(
            select(DecisionRecord).where(DecisionRecord.run_id == run.id)).scalars().all())
        out["audit_entries"] = len(session.execute(
            select(AuditLog).where(AuditLog.run_id == run.id)).scalars().all())
        out["spark_jobs"] = len(executions)

        # P counts as having run end to end when it reached selection - every stage executed
        # and the candidate was judged - whatever the acceptance policy then decided. An
        # escalation is the policy stopping for a person, the outcome the platform exists to
        # produce when a rule fails (DEC-34); since DEC-27 such a candidate is still scored
        # once on the test cutoff, so its test figures exist.
        state = prepared.details or {}
        selection = state.get("selection") or {}
        out["reached_selection"] = bool(state.get("split"))
        if out["reached_selection"]:
            escalated = not prepared.ok
            verdict = (selection.get("verdicts") or {}).get(selection.get("selected_family")) or {}
            # `SelectionResult.to_dict` writes `selected_family` and `test`, not the attribute
            # names `selected` and `test_metrics`; reading those returned None for both.
            test_values = (selection.get("test") or {}).get("values", {}) or {}
            # The approval gate is the only manual step the hypotheses allow, so it is counted
            # apart from anything else a person had to do. An escalation is a decision for a
            # person too: two approvals if the candidate may be promoted anyway (PRD section 30).
            approvals = (
                1 if selection.get("outcome") == "PROPOSE_PROMOTION"
                else (2 if escalated and selection.get("override_eligible") else 0)
            )
            out.update({
                "escalated": escalated,
                "escalation_reason": prepared.reason[:300] if escalated else "",
                "failed_rules": selection.get("failed_rules") or [],
                "valid_rows": (verdict.get("evidence") or {}).get("ece_n_rows"),
                "selected_family": selection.get("selected_family"),
                "test_pr_auc": _round(test_values.get("pr_auc")),
                "test_roc_auc": _round(test_values.get("roc_auc")),
                "test_brier": _round(test_values.get("brier")),
                "selection_outcome": selection.get("outcome"),
                "approvals_required": approvals,
                "person_decision_required": escalated or approvals > 0,
                "human_actions_excluding_approval": 0,
                "shared": {k: state.get(k) for k in (
                    "features_uri", "labels_uri", "split", "feature_columns",
                    "numeric_columns", "categorical_columns", "cutoffs")},
            })
        q.set_state(session, run, "CANCELLED", actor=prefix)
    return out


def record_p(trial: Any, p: dict[str, Any]) -> None:
    """Write `run_p`'s values onto a trial, under the names EXP-1 has always used."""
    trial.record("wall_clock_s", p.get("wall_clock_s"), "s")
    trial.record("ingest_s", p.get("ingest_s"), "s")
    trial.record("prepare_ok", p.get("prepare_ok"))
    trial.record("prepare_state", p.get("prepare_state"))
    trial.record("validation_gates_s", p.get("validation_gates_s"), "s",
                 note="gates 1 to 8 summed over the run's generated jobs: compile, fixture "
                      "tests, dry runs, contract and storage checks")
    trial.record("full_runs_s", p.get("full_runs_s"), "s",
                 note="gate 9: the jobs doing their work on the whole input")
    trial.record("peak_rss_mb", p.get("peak_rss_mb"), "MB",
                 note="P's Python process: the pipeline's control and the modelling. The "
                      "in-process Spark JVM is the next figure; the gates' compiler and the "
                      "sandboxed jobs run as processes and containers of their own")
    trial.record("jvm_peak_rss_mb", p.get("jvm_peak_rss_mb"), "MB",
                 note="the in-process Spark session's JVM (profile and cutoffs)")
    trial.observations["per_stage_s"] = p.get("per_stage_s") or {}
    trial.observations["gate_s"] = p.get("gate_s") or {}
    trial.observations["run_key"] = p.get("run_key")
    trial.record("llm_calls", p.get("llm_calls"),
                 note="0 because no provider is configured (ADR-0003), not because the loop "
                      "made none")
    trial.record("decision_records", p.get("decision_records"))
    trial.record("audit_entries", p.get("audit_entries"))
    trial.record("spark_jobs", p.get("spark_jobs"))

    if not p.get("reached_selection"):
        trial.record("test_pr_auc", None, note="the pipeline did not reach selection")
        trial.ok = False
        trial.detail = f"stopped at {p.get('prepare_state')}: {str(p.get('reason'))[:150]}"
        return
    trial.record("escalated", p["escalated"])
    if p["escalated"]:
        trial.record("escalation_reason", p["escalation_reason"])
        trial.record("failed_rules", p["failed_rules"])
    trial.record("valid_rows", p["valid_rows"],
                 note="the split AP-5's calibration error is measured on")
    for name in ("selected_family", "test_pr_auc", "test_roc_auc", "test_brier",
                 "selection_outcome", "person_decision_required",
                 "human_actions_excluding_approval", "approvals_required"):
        trial.record(name, p[name])
    trial.ok = True
    trial.detail = (
        f"ran end to end in {p['wall_clock_s']:.0f}s over {p['spark_jobs']} Spark jobs "
        f"({p['validation_gates_s']:.0f}s of it in validation gates); "
        f"{p['selected_family']} at test PR-AUC {p['test_pr_auc']}; "
        + (f"the acceptance policy escalated it ({p['escalation_reason'][:120]}), "
           "so a person decides what happens next; "
           if p["escalated"] else f"{p['approvals_required']} approval(s) required; ")
        + "no other manual step"
    )
