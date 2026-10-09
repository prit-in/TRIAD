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

**"Key nulls are quarantined" is measured, not inferred.** The nulls the generator put into
`customer_id` are counted in the registered file and compared with what the cleaning job's
`null_customer` rule quarantined: all of them, and only them, is the claim.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from experiments.harness import Experiment

# 2 000, not the 400 of the first run. At 400 customers the validation split had 324 rows,
# where a perfectly calibrated model exceeds AP-5's ECE limit 40-46% of the time
# (`experiments/calibration_noise_floor.py`); at about 1 600 rows that share is about 0%, so
# the calibration verdict describes the model rather than the sample. Chosen from the noise
# floor, which involves no model and no test data - not from any result of this experiment.
CUSTOMERS = 2_000
MONTHS = 24
SEED = 42
DATA_START = "2024-01-01"

# The PRD's rates. The key-null rate is held at 1% throughout, because the hypothesis about
# key nulls is about *what happens to them*, not about how many there are; varying both at once
# would confound the two halves of the claim.
TREND_CONDITIONS: dict[str, dict[str, Any]] = {
    "nulls_0pct_baseline": {"null_rate_non_key": 0.0, "null_rate_key": 0.0},
    "nulls_5pct_nonkey": {"null_rate_non_key": 0.05, "null_rate_key": 0.01},
    "nulls_20pct_nonkey": {"null_rate_non_key": 0.20, "null_rate_key": 0.01},
    "nulls_50pct_nonkey": {"null_rate_non_key": 0.50, "null_rate_key": 0.01},
}
# One condition the PRD's expected observation implies and its setup never reaches: approval
# "when more than 5% of rows are affected". A 1% key-null rate quarantines about 1% of rows,
# so no PRD condition comes near the limit; a 10% rate does. It stops at the cleaning gate by
# design, costs one cleaning job rather than a training run, and is not part of the PR-AUC
# trend. Chosen from the policy's limit before running, not from any result.
OVER_LIMIT = "nulls_key_10pct_over_limit"
CONDITIONS: dict[str, dict[str, Any]] = {
    **TREND_CONDITIONS,
    OVER_LIMIT: {"null_rate_non_key": 0.0, "null_rate_key": 0.10},
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


def _value(trial: Any, name: str) -> Any:
    """A measurement's value, or None when it was not recorded."""
    return next((m.value for m in trial.measurements if m.name == name), None)


def _run_one(exp: Experiment, name: str, overrides: dict[str, Any]) -> None:
    import pyarrow.parquet as pq

    from agents.graph import churn
    from agents.graph import queue as q
    from dataeng.ingest import DatasetRegistry
    from datasets.synthetic.generator import GeneratorConfig, SyntheticGenerator
    from triad_core.db import session_scope
    from triad_core.models import PipelineRun
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
            if table == "transactions":
                # Counted in the file that is registered, so the comparison with the quarantine
                # is like for like.
                key_nulls = pq.read_table(files[0], columns=["customer_id"]) \
                    .column("customer_id").null_count
                trial.record("key_nulls_injected", int(key_nulls),
                             note="null customer_id values in the registered file")
            result = registry.register_version(table, files[0], actor="exp05")
            if result.version_id:
                registered[table] = result.storage_uri or ""
        trial.record("datasets_registered", len(registered))
        run_id = run.id

    # Committed before the pipeline starts, and not for tidiness. `audit.record` takes a
    # per-chain `pg_advisory_xact_lock`, and the ingest writes above land in the *system*
    # chain because `register_source` and `register_version` carry no run id. Holding one
    # transaction across the whole run therefore holds that lock for its entire duration, and
    # every `auth.login` - which writes to the same chain - blocks behind it. EXP-1 was
    # observed doing exactly that: one transaction idle for 377s with a login queued behind it.
    with session_scope() as session:
        run = session.get(PipelineRun, run_id)
        if run is None:
            raise RuntimeError(f"run {run_id} vanished between transactions")

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
        if not prepared.ok or prepared.state == "APPROVAL_REQUIRED":
            trial.record("stop_reason", prepared.reason[:300])
            trial.observations["details"] = prepared.details

        # An escalated run carries its selection too: since DEC-27 a candidate that failed a
        # calibration or segment rule is still scored once on the test cutoff, so its own test
        # PR-AUC exists and is read below, beside the policy's verdict on it.
        state = prepared.details or {}
        selection = state.get("selection") or {}
        # `to_dict` writes `selected_family`, `test` and `valid` - not the attribute names.
        chosen_family = selection.get("selected_family")

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
            by_key = int(clean_metrics.get("rule_null_customer") or 0)
            trial.record("quarantined_null_customer", by_key,
                         note="rows the null_customer rule quarantined (NO_CUSTOMER_KEY)")
            trial.record("key_nulls_all_quarantined", by_key == _value(trial, "key_nulls_injected"))
        trial.observations["quarantine_by_rule"] = {
            k.removeprefix("rule_"): v for k, v in clean_metrics.items() if k.startswith("rule_")
        }

        # --- the model: PR-AUC and calibration ----------------------------------
        # The selected candidate's own figures, scored once on the sealed test cutoff, whether
        # or not the acceptance policy then accepted it; the policy's verdict is recorded beside
        # them. Absent only when selection stopped before scoring (a floor or leakage rule), and
        # then absent is what gets recorded - never the last good model's.
        test_metrics = (selection.get("test") or {}).get("values", {}) or {}
        # The winner's validation metrics are not in the serialised selection - `rejected`
        # holds the families that lost. The test split is what the hypothesis is about, so the
        # validation figures are simply not recorded rather than reconstructed from a guess.
        trial.record("selected_family", chosen_family)
        trial.record("test_pr_auc", _round(test_metrics.get("pr_auc")))
        trial.record("test_roc_auc", _round(test_metrics.get("roc_auc")))
        trial.record("test_ece", _round(test_metrics.get("ece")))
        trial.record("churn_rate", _round(state.get("cutoffs", {}).get("churn_rate")))
        trial.record("selection_outcome", selection.get("outcome"))
        trial.record("failed_rules", selection.get("failed_rules") or [])
        trial.record("valid_rows",
                     (((selection.get("verdicts") or {}).get(chosen_family) or {})
                      .get("evidence") or {}).get("ece_n_rows"),
                     note="the split AP-5's ECE is measured on")
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
        trial.record("cleaning_approval_requests",
                     sum(1 for a in approvals if a.action_type == "destructive_cleaning"))
        trial.record(
            "cleaning_policy_decisions",
            [d.decision[:120] for d in decisions
             if d.decision_type == "policy" and "cleaning" in d.decision],
        )
        trial.observations["approvals"] = [
            {"action": a.action_type, "status": a.status, "target": a.target_ref}
            for a in approvals
        ]

        trial.record("wall_clock_s", round(time.perf_counter() - started, 1), "s")
        # Cancelling withdraws a still-pending request, so the queue is left as it was found.
        q.set_state(session, run, "CANCELLED", actor="exp05")

    stopped = _value(trial, "stop_reason") or ""
    if name == OVER_LIMIT:
        # The claim for this condition is the pause itself: over the limit, the cleaning plan
        # waits for a person, with its quarantine kept and nothing committed.
        trial.ok = (_value(trial, "prepare_state") == "APPROVAL_REQUIRED"
                    and (_value(trial, "cleaning_approval_requests") or 0) >= 1
                    and (_value(trial, "rows_affected_pct") or 0) > 5.0)
    else:
        # A condition succeeded as a measurement when it produced the model's test figures,
        # which an escalated run now does; whether the policy accepted the model is
        # `prepare_ok`. `prepare_ok` alone no longer counts: a run paused at the cleaning gate
        # also succeeds as a phase, and produces no model.
        trial.ok = isinstance(_value(trial, "test_pr_auc"), (int, float))
    trial.detail = (
        f"quarantined {trial.get('quarantined_rows')} of {trial.get('input_rows')} rows "
        f"({_value(trial, 'rows_affected_pct')}%); key nulls "
        f"{_value(trial, 'key_nulls_injected')}, quarantined by the key rule "
        f"{_value(trial, 'quarantined_null_customer')}; test PR-AUC "
        f"{trial.get('test_pr_auc')}, ECE {trial.get('test_ece')}; "
        f"cleaning approvals {_value(trial, 'cleaning_approval_requests')}"
        + (f"; the run stopped: {stopped}" if stopped else "")
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
            "declines gradually with the null rate; a cleaning plan affecting more than 5% of "
            "rows waits for approval."
        ),
        independent_variable="null rate, column role",
        dependent_measures=[
            "key_nulls_injected", "quarantined_null_customer", "quarantined_rows",
            "rows_affected_pct", "row_accounting_balanced", "test_pr_auc", "test_ece",
            "cleaning_approval_requests",
        ],
        config={
            "customers": CUSTOMERS, "months": MONTHS, "seed": SEED,
            "conditions": dict(CONDITIONS),
            "trend_conditions": list(TREND_CONDITIONS),
            "over_limit_condition": OVER_LIMIT,
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

        trend = [t for t in exp.trials if t.condition in TREND_CONDITIONS]
        over = next(t for t in exp.trials if t.condition == OVER_LIMIT)
        ok = [t for t in trend if t.ok]
        if not ok:
            exp.conclude(
                status="failed",
                hypothesis_held=None,
                conclusion="no condition completed the pipeline, so nothing was measured: "
                           + "; ".join(f"{t.condition}: {t.detail}" for t in exp.trials),
            )
            return exp

        # Is the decline monotone in the null rate? Reported as measured, not asserted.
        pr_aucs = [(t.condition, _value(t, "test_pr_auc")) for t in ok]
        numeric = [(n, v) for n, v in pr_aucs if isinstance(v, (int, float))]
        # `all()` over an empty or single-element sequence is True, so a run where only the
        # baseline produced a PR-AUC previously reported "monotone decline: True" from one
        # data point - and the hypothesis as held. A trend needs at least two points.
        monotone = (
            all(numeric[i][1] >= numeric[i + 1][1] - 1e-9 for i in range(len(numeric) - 1))
            if len(numeric) >= 2
            else None
        )
        balanced = all(_value(t, "row_accounting_balanced") in (True, "true") for t in ok)
        # Every injected key null quarantined by the key rule, and nothing else by it.
        keys_quarantined = all(_value(t, "key_nulls_all_quarantined") is True
                               for t in exp.trials
                               if _value(t, "key_nulls_all_quarantined") is not None)
        # Under the limit, no cleaning approval; over it, a request and a paused run.
        quiet_under_limit = all((_value(t, "cleaning_approval_requests") or 0) == 0 for t in ok)

        if len(ok) < len(TREND_CONDITIONS):
            exp.limitation(
                f"{len(TREND_CONDITIONS) - len(ok)} of {len(TREND_CONDITIONS)} conditions did "
                "not complete the pipeline; their measurements are absent rather than "
                "estimated: " + "; ".join(f"{t.condition}: {t.detail}"
                                          for t in trend if not t.ok)
            )
        exp.limitation(
            "The word 'imputed' in the PRD hypothesis is not measured as such: cleaning does "
            "not impute, the model pipeline does. What is measured is that non-key nulls do "
            "not cost rows."
        )
        exp.limitation(
            f"{OVER_LIMIT} is not one of the PRD's conditions. It was added, from the policy's "
            "5% limit and before any run, because no PRD condition reaches that limit, so the "
            "expected observation about approval could not otherwise be observed."
        )

        complete = len(ok) == len(TREND_CONDITIONS) and over.ok
        exp.conclude(
            status="completed" if complete else "partial",
            # `None` when the decline could not be assessed at all: no verdict is the honest
            # answer when three of four conditions never produced a model.
            hypothesis_held=(
                (monotone and balanced and keys_quarantined and quiet_under_limit and over.ok)
                if monotone is not None else None
            ),
            conclusion=(
                "PR-AUC on the sealed test split by null rate: "
                + ", ".join(f"{n}={v}" for n, v in pr_aucs)
                + (
                    f". Monotone decline: {monotone}. "
                    if monotone is not None
                    else ". Monotone decline: not assessable - fewer than two conditions "
                         "produced a test PR-AUC. "
                )
                + "Calibration error: "
                + ", ".join(f"{t.condition}={_value(t, 'test_ece')}" for t in ok)
                + ". Key nulls quarantined by the key rule, of those injected: "
                + ", ".join(f"{t.condition} {_value(t, 'quarantined_null_customer')}/"
                            f"{_value(t, 'key_nulls_injected')}" for t in exp.trials)
                + f" (all of them in every condition: {keys_quarantined}). Rows affected: "
                + ", ".join(f"{t.condition} {_value(t, 'rows_affected_pct')}%"
                            for t in exp.trials)
                + f". Row accounting balanced in every completed condition: {balanced}, so no "
                "rows were lost silently at any null rate. Policy outcome: "
                + ", ".join(f"{t.condition} {_value(t, 'selection_outcome')}"
                            + (f" ({', '.join(_value(t, 'failed_rules'))})"
                               if _value(t, 'failed_rules') else "")
                            for t in ok)
                + ". Cleaning approvals under the 5% limit: "
                + ", ".join(f"{t.condition}={_value(t, 'cleaning_approval_requests')}"
                            for t in ok)
                + f". Over it ({OVER_LIMIT}, {_value(over, 'rows_affected_pct')}% of rows): "
                + (
                    f"{_value(over, 'cleaning_approval_requests')} approval requested and the "
                    "run paused in APPROVAL_REQUIRED with its output uncommitted."
                    if over.ok else f"not paused as claimed: {over.detail}."
                )
            ),
        )
        return exp


if __name__ == "__main__":
    run()
