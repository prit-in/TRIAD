"""EXP-5: missing values.

PRD section 33:

    Setup: random nulls at 5%, 20% and 50% in non-key columns and 1% in key columns.
    Hypothesis: key nulls are quarantined, others imputed; PR-AUC declines gradually with the
    null rate.
    Independent variable: null rate, column role.
    Dependent measures: quarantine counts, PR-AUC, calibration error.
    Expected observation: steady degradation; approval requested when more than 5% of rows are
    affected.

This one runs the **whole** first phase of the pipeline per condition - profile, clean,
features, labels, hand-off, train, select - because three of the four measures live at
different ends of it: the quarantine counts come from the Scala cleaning job, PR-AUC and the
calibration error come from the trained model, and the approval request is a governance
decision taken at the cleaning gate. Running only part of the pipeline would mean inventing
one of them.

Tuning stays enabled, exactly as production has it. It is part of the treatment, held constant
across conditions; the independent variable is the null rate. Switching it off for the
experiment would measure a pipeline nobody runs.

**A note on what "imputed" means here, because the hypothesis uses the word loosely.** TRIAD
does not impute in the cleaning stage; nulls in non-key columns survive into the feature
tables and are handled inside the model pipeline by the imputer in `candidates.preprocessing`.
So the measurable claim is not "the cleaner filled them in" - it is that non-key nulls do not
cause rows to be quarantined, and that the model still trains and degrades gracefully. That is
what is measured.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from experiments.harness import Experiment

CUSTOMERS = 400
MONTHS = 24
SEED = 42
DATA_START = "2024-01-01"

# The PRD's rates. The key-null rate is held at 1% throughout, because the hypothesis about
# key nulls is about *what happens to them*, not about how many there are; varying both at once
# would confound the two halves of the claim.
CONDITIONS: dict[str, dict[str, Any]] = {
    "nulls_0pct_baseline": {"null_rate_non_key": 0.0, "null_rate_key": 0.0},
    "nulls_5pct_nonkey": {"null_rate_non_key": 0.05, "null_rate_key": 0.01},
    "nulls_20pct_nonkey": {"null_rate_non_key": 0.20, "null_rate_key": 0.01},
    "nulls_50pct_nonkey": {"null_rate_non_key": 0.50, "null_rate_key": 0.01},
}


def _spark():
    from pyspark.sql import SparkSession

    from triad_core.config import settings

    session = (
        SparkSession.builder.appName("triad-exp05")
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


def _run_one(exp: Experiment, name: str, overrides: dict[str, Any]) -> None:
    from agents.graph import churn
    from agents.graph import queue as q
    from dataeng.ingest import DatasetRegistry
    from datasets.synthetic.generator import GeneratorConfig, SyntheticGenerator
    from triad_core.db import session_scope
    from triad_core.storage import storage

    trial = exp.trial(name)
    store = storage()
    root = store.root / "experiments" / "exp05" / f"{name}-{uuid.uuid4().hex[:8]}"
    started = time.perf_counter()

    cfg = GeneratorConfig(
        customers=CUSTOMERS, months=MONTHS, seed=SEED, start_date=DATA_START, **overrides
    )
    tables = SyntheticGenerator(cfg).write(root / "source")
    trial.record("null_rate_non_key", overrides["null_rate_non_key"])
    trial.record("null_rate_key", overrides["null_rate_key"])

    with session_scope() as session:
        # Its own pipeline name: conditions share the clean/feature/label folders otherwise, and
        # each would overwrite the last one's outputs mid-flight.
        pipeline_name = f"exp05_{name}_{uuid.uuid4().hex[:6]}"
        run = q.create_run(
            session, pipeline_name=pipeline_name, trigger_type="manual",
            triggered_by="exp05", config={"experiment": "EXP-5", "condition": name},
        )
        session.flush()

        registry = DatasetRegistry(session, store)
        registered: dict[str, str] = {}
        for table, path in tables.items():
            registry.register_source(table, entity_type=table, source_type="parquet")
            files = sorted(Path(path).glob("*.parquet"))
            if not files:
                continue
            result = registry.register_version(table, files[0], actor="exp05")
            if result.version_id:
                registered[table] = result.storage_uri or ""
        trial.record("datasets_registered", len(registered))

        spark = _spark()
        try:
            prepared = churn.prepare(
                session, run, inputs=registered, spark=spark, store=store,
                seed=SEED, requested_by="exp05",
            )
        finally:
            spark.stop()

        trial.record("prepare_ok", prepared.ok)
        trial.record("prepare_state", prepared.state)
        if not prepared.ok:
            trial.record("stop_reason", prepared.reason[:300])
            trial.observations["details"] = prepared.details
            trial.ok = False
            trial.detail = f"the pipeline stopped at {prepared.state}: {prepared.reason[:150]}"
            trial.record("wall_clock_s", round(time.perf_counter() - started, 1), "s")
            q.set_state(session, run, "CANCELLED", actor="exp05")
            return

        state = prepared.details
        selection = state.get("selection") or {}
        chosen = selection.get("selected") or {}

        # --- the cleaning stage: quarantine counts ------------------------------
        # Read from the stage records the pipeline itself wrote, not recomputed here.
        from sqlalchemy import select as sa_select

        from triad_core.models import Execution

        executions = session.execute(
            sa_select(Execution).where(Execution.run_id == run.id)
        ).scalars().all()
        trial.observations["executions"] = [
            {"engine": e.engine, "status": e.status, "metrics": e.metrics_json}
            for e in executions
        ]
        clean_metrics: dict[str, Any] = {}
        for e in executions:
            metrics = e.metrics_json or {}
            if "quarantined_rows" in metrics or "row_accounting_balanced" in metrics:
                clean_metrics = metrics
                break
        trial.record("input_rows", clean_metrics.get("input_rows"))
        trial.record("quarantined_rows", clean_metrics.get("quarantined_rows"))
        trial.record("removed_duplicates", clean_metrics.get("removed_duplicates"))
        trial.record("row_accounting_balanced", clean_metrics.get("row_accounting_balanced"))
        if clean_metrics.get("input_rows"):
            affected = (
                int(clean_metrics.get("quarantined_rows") or 0)
                + int(clean_metrics.get("removed_duplicates") or 0)
            )
            trial.record(
                "rows_affected_pct",
                round(100.0 * affected / int(clean_metrics["input_rows"]), 4), "%",
                note="what the cleaning.execute policy limit of 5% is compared against",
            )
        trial.observations["quarantine_by_rule"] = clean_metrics.get("quarantined_by_rule")

        # --- the model: PR-AUC and calibration ----------------------------------
        test_metrics = selection.get("test_metrics", {}).get("values", {}) or {}
        valid_metrics = (chosen.get("valid_metrics") or {}).get("values", {}) or {}
        trial.record("selected_family", chosen.get("family"))
        trial.record("valid_pr_auc", _round(valid_metrics.get("pr_auc")))
        trial.record("test_pr_auc", _round(test_metrics.get("pr_auc")))
        trial.record("test_roc_auc", _round(test_metrics.get("roc_auc")))
        trial.record("valid_ece", _round(valid_metrics.get("ece")),
                     note="expected calibration error after calibration (AP-5)")
        trial.record("test_ece", _round(test_metrics.get("ece")))
        trial.record("churn_rate", _round(state.get("cutoffs", {}).get("churn_rate")))
        trial.record("selection_outcome", selection.get("outcome"))
        trial.record("quality_score", _round(state.get("quality_score")))
        trial.observations["selection"] = selection

        # --- governance: was approval asked for? --------------------------------
        from triad_core.models import Approval, DecisionRecord

        approvals = session.execute(
            sa_select(Approval).where(Approval.run_id == run.id)
        ).scalars().all()
        decisions = session.execute(
            sa_select(DecisionRecord).where(DecisionRecord.run_id == run.id)
        ).scalars().all()
        trial.record("approval_requests", len(approvals))
        trial.record(
            "cleaning_policy_decisions",
            [d.decision[:120] for d in decisions
             if d.decision_type == "policy" and "cleaning" in d.decision],
        )
        trial.observations["approvals"] = [
            {"action": a.action_type, "status": a.status} for a in approvals
        ]

        trial.record("wall_clock_s", round(time.perf_counter() - started, 1), "s")
        q.set_state(session, run, "CANCELLED", actor="exp05")

    trial.ok = bool(trial.get("prepare_ok"))
    trial.detail = (
        f"quarantined {trial.get('quarantined_rows')} of {trial.get('input_rows')} rows; "
        f"test PR-AUC {trial.get('test_pr_auc')}, ECE {trial.get('test_ece')}; "
        f"approvals {trial.get('approval_requests')}"
    )


def _round(value: Any, places: int = 4) -> Any:
    """Round a number, leave anything else - including None - exactly as it is.

    A missing measurement must stay missing rather than becoming 0.0.
    """
    try:
        return round(float(value), places)
    except (TypeError, ValueError):
        return value


def run() -> Experiment:
    with Experiment(
        "EXP-5",
        title="Missing values",
        hypothesis=(
            "Key nulls are quarantined, others are handled without losing rows; PR-AUC "
            "declines gradually with the null rate."
        ),
        independent_variable="null rate, column role",
        dependent_measures=[
            "quarantined_rows", "rows_affected_pct", "row_accounting_balanced",
            "test_pr_auc", "test_ece", "approval_requests",
        ],
        config={
            "customers": CUSTOMERS, "months": MONTHS, "seed": SEED,
            "conditions": dict(CONDITIONS),
            "pipeline_phase": "churn.prepare (profile -> clean -> features -> labels -> "
                              "train -> select)",
            "tuning": "left enabled, as production has it; held constant across conditions",
            "imputation_note": (
                "TRIAD does not impute during cleaning. Non-key nulls reach the feature tables "
                "and are handled by the imputer inside the model pipeline, so the measurable "
                "claim is that they do not cause rows to be quarantined."
            ),
        },
    ) as exp:
        for name, overrides in CONDITIONS.items():
            _run_one(exp, name, overrides)

        ok = [t for t in exp.trials if t.get("prepare_ok")]
        if not ok:
            exp.conclude(
                status="failed",
                hypothesis_held=None,
                conclusion="no condition completed the pipeline, so nothing was measured: "
                           + "; ".join(f"{t.condition}: {t.detail}" for t in exp.trials),
            )
            return exp

        # Is the decline monotone in the null rate? Reported as measured, not asserted.
        ordered = [t for t in CONDITIONS if any(x.condition == t for x in ok)]
        pr_aucs = [
            (t, next(x for x in ok if x.condition == t).get("test_pr_auc")) for t in ordered
        ]
        numeric = [(n, v) for n, v in pr_aucs if isinstance(v, (int, float))]
        monotone = all(
            numeric[i][1] >= numeric[i + 1][1] - 1e-9 for i in range(len(numeric) - 1)
        )
        balanced = all(t.get("row_accounting_balanced") in (True, "true") for t in ok)
        quarantine_rises = None
        if len(numeric) >= 2:
            first = next(x for x in ok if x.condition == ordered[0])
            last = next(x for x in ok if x.condition == ordered[-1])
            quarantine_rises = (
                int(last.get("quarantined_rows") or 0) >= int(first.get("quarantined_rows") or 0)
            )

        if len(ok) < len(CONDITIONS):
            exp.limitation(
                f"{len(CONDITIONS) - len(ok)} of {len(CONDITIONS)} conditions did not complete "
                "the pipeline; their measurements are absent rather than estimated: "
                + "; ".join(
                    f"{t.condition}: {t.detail}" for t in exp.trials if not t.get("prepare_ok")
                )
            )
        exp.limitation(
            "The word 'imputed' in the PRD hypothesis is not measured as such: cleaning does "
            "not impute, the model pipeline does. What is measured is that non-key nulls do "
            "not cost rows."
        )

        exp.conclude(
            status="completed" if len(ok) == len(CONDITIONS) else "partial",
            hypothesis_held=monotone and balanced,
            conclusion=(
                "PR-AUC on the sealed test split by null rate: "
                + ", ".join(f"{n}={v}" for n, v in pr_aucs)
                + f". Monotone decline: {monotone}. "
                + "Calibration error: "
                + ", ".join(
                    f"{t.condition}={t.get('test_ece')}" for t in ok
                )
                + ". Quarantine: "
                + ", ".join(
                    f"{t.condition} {t.get('quarantined_rows')}/{t.get('input_rows')} rows "
                    f"({t.get('rows_affected_pct')}%)" for t in ok
                )
                + f". Row accounting balanced in every completed condition: {balanced}, so no "
                "rows were lost silently at any null rate. Quarantine rises with the key-null "
                f"rate: {quarantine_rises}. "
                "Approval requests: "
                + ", ".join(f"{t.condition}={t.get('approval_requests')}" for t in ok)
                + ". The 5% limit in the cleaning.execute policy is compared against "
                "rows_affected_pct, recorded above for each condition."
            ),
        )
        return exp


if __name__ == "__main__":
    run()
