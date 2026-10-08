"""EXP-4: schema errors.

PRD section 33:

    Setup: rename `customer_id`; turn `price` into text with currency symbols; drop the
    optional tickets table.
    Hypothesis: a renamed required column is detected and a mapping proposed but not applied
    without approval; prices are fixed by `try_cast` and quarantine; the missing optional
    table disables its features.
    Independent variable: fault type.
    Dependent measures: detection rate, correct-action rate, approval requests, time to
    recovery.
    Expected observation: every fault detected; no silent row loss.

Each fault is injected by the synthetic generator (section 33.1: "generator options applied
to a copy of the dataset version") and then put through the **real** profiling and cleaning
stages - the same `ChurnPipeline.stage_profile` and `stage_scala_job` a production run uses,
running the same generated Scala on Spark in the sandbox.

Only profiling and cleaning are run, not the whole pipeline. Every measure this experiment
asks for is decided by the time cleaning commits: detection is a profiling result, the
mapping proposal and the approval request are governance decisions taken at the clean gate,
and row accounting is the clean job's own output. Training three more times would add half an
hour and no evidence. What is *not* measured here is therefore stated: the downstream effect
of a disabled feature group on model quality, which is EXP-5's territory.

"No silent row loss" is the strongest claim in the table, so it is measured directly rather
than inferred from an absence of errors: the clean job reports input rows, output rows,
quarantined rows and removed duplicates, and the four must balance.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from experiments.harness import Experiment

CUSTOMERS = 400
MONTHS = 24
SEED = 42
DATA_START = "2024-01-01"

# The three faults of the PRD table, plus a clean baseline. The baseline is not decoration:
# without it, a detector that flags everything would score a perfect detection rate.
FAULTS: dict[str, dict[str, Any]] = {
    "baseline_clean": {},
    # The generator takes `old:new`; the PRD's fault is a required column arriving under a
    # different name, which is what a renamed upstream field looks like downstream.
    "renamed_required_column": {"rename_column": "customer_id:cust_id"},
    "price_as_text_with_symbols": {"price_as_text": True},
    "dropped_optional_table": {"drop_tables": ["support_tickets"]},
}


def _generate(root: Path, overrides: dict[str, Any]) -> dict[str, Path]:
    from datasets.synthetic.generator import GeneratorConfig, SyntheticGenerator

    cfg = GeneratorConfig(
        customers=CUSTOMERS, months=MONTHS, seed=SEED, start_date=DATA_START, **overrides
    )
    return SyntheticGenerator(cfg).write(root)


def _spark():
    from pyspark.sql import SparkSession

    from triad_core.config import settings

    session = (
        SparkSession.builder.appName("triad-exp04")
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


def _contract_yaml(table: str) -> str | None:
    """The repo's ingest contract for a table, as text, or None if it has none."""
    path = Path("pipelines") / "contracts" / f"{table}.yaml"
    return path.read_text(encoding="utf-8") if path.exists() else None


def _register(
    session: Any, store: Any, tables: dict[str, Path], tag: str
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Register every generated table under a per-condition name, and report what was refused.

    Two things here are deliberate.

    **Refusals are returned, not skipped.** A file the ingest contract rejects is the platform
    detecting a schema fault at the earliest possible point, which is the best outcome this
    experiment can observe; treating it as "nothing registered" scored the strongest detection
    as a miss on the first run of this experiment.

    **Each condition registers under its own dataset name, and carries the contract itself.**
    `register_version` short-circuits on an identical checksum before it validates anything,
    which is correct - re-registering the same bytes must create nothing - but it meant this
    experiment measured registry state rather than the contract: the renamed file had already
    been registered by an earlier run of EXP-4, from before the contract was being applied, so
    the fault arrived as a duplicate and was never checked. Fresh names make each condition
    independent of every previous run, and `contract_yaml` on the dataset row carries the
    contract that the shared name would otherwise have supplied.
    """
    from dataeng.ingest import DatasetRegistry

    registry = DatasetRegistry(session, store)
    registered: dict[str, str] = {}
    refusals: list[dict[str, Any]] = []
    for name, path in tables.items():
        dataset_name = f"{name}__{tag}"
        registry.register_source(
            dataset_name, entity_type=name, source_type="parquet",
            contract_yaml=_contract_yaml(name),
        )
        files = sorted(Path(path).glob("*.parquet"))
        if not files:
            continue
        result = registry.register_version(dataset_name, files[0], actor="exp04")
        if result.version_id:
            registered[name] = result.storage_uri or ""
            continue
        blocking = [
            v.to_dict() for v in (
                result.contract_result.blocking if result.contract_result else []
            )
        ]
        refusals.append({"table": name, "reason": result.reason, "blocking": blocking})
    return registered, refusals


def trial_has(trial: Any, name: str) -> bool:
    """Whether a measurement was recorded at all, and truthy. `Trial.get` raises if absent."""
    return any(m.name == name and m.value for m in trial.measurements)


# The baseline's own issue ids, filled in by the first condition and used as the reference
# for every later one. A schema fault is detected when it produces an issue the clean data
# does not.
_BASELINE_ISSUES: set[str] = set()


def _issue_ids(store: Any, profile_uri: str | None) -> set[str]:
    """The issue ids in a written profile document, by severity-bearing id."""
    if not profile_uri:
        return set()
    path = Path(profile_uri.replace("file://", ""))
    if not path.exists():
        return set()
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {
        f"{i.get('severity')}:{i.get('id')}"
        for i in doc.get("issues", []) or doc.get("blocking_issues", []) or []
    }


def _run_one(exp: Experiment, fault: str, overrides: dict[str, Any]) -> None:
    from agents.graph import queue as q
    from agents.graph.pipeline import ChurnPipeline
    from triad_core.db import session_scope
    from triad_core.storage import storage

    trial = exp.trial(fault)
    store = storage()
    root = store.root / "experiments" / "exp04" / f"{fault}-{uuid.uuid4().hex[:8]}"
    started = time.perf_counter()

    tables = _generate(root / "source", overrides)
    trial.record("tables_generated", len(tables))
    trial.observations["tables"] = sorted(tables)

    # The dropped optional table must be visibly absent, not quietly assumed absent.
    expected_missing = set(overrides.get("drop_tables") or [])
    trial.record(
        "optional_table_absent",
        sorted(expected_missing - set(tables)) == sorted(expected_missing),
    )

    with session_scope() as session:
        # Each condition gets its own pipeline name: runs of one pipeline share their clean
        # and feature folders, so four conditions under one name would overwrite each other's
        # outputs and the row accounting would be meaningless.
        tag = f"exp04_{fault}_{uuid.uuid4().hex[:6]}"
        pipeline_name = tag
        run = q.create_run(
            session, pipeline_name=pipeline_name, trigger_type="manual",
            triggered_by="exp04", config={"experiment": "EXP-4", "fault": fault},
        )
        session.flush()
        registered, refusals = _register(session, store, tables, tag)
        trial.record("datasets_registered", len(registered))
        trial.record("registrations_refused", len(refusals))
        trial.observations["refusals"] = refusals

        # --- stage 1: ingest. A blocking contract violation stops the fault here, which is
        # FR-007 and the earliest and cheapest detection available.
        tx_refused = [r for r in refusals if r["table"] == "transactions" and r["blocking"]]
        trial.record("detected_at_ingest", bool(tx_refused))
        if tx_refused:
            trial.record("ingest_refusal", tx_refused[0]["blocking"][0]["message"][:200])

        if "transactions" not in registered:
            # Nothing further can run, and nothing further needs to: the fault was caught.
            trial.record("detected", bool(tx_refused))
            trial.record("detected_where", "ingest" if tx_refused else "not detected")
            trial.record("profiling_succeeded", False, note="not reached")
            trial.record("cleaning_succeeded", False, note="not reached")
            trial.record("reached_cleaning", False)
            trial.record("wall_clock_s", round(time.perf_counter() - started, 1), "s")
            trial.ok = bool(tx_refused)
            trial.detail = (
                f"refused at ingest: {tx_refused[0]['blocking'][0]['message'][:120]}"
                if tx_refused else "the transactions table did not register and no contract "
                                   "violation explains why"
            )
            q.set_state(session, run, "CANCELLED", actor="exp04")
            return

        pipeline = ChurnPipeline(session, run, store=store)
        spark = _spark()
        try:
            # --- profiling: where a schema fault is detected -----------------------
            profile = pipeline.stage_profile(spark, "transactions", registered["transactions"])
            profiled = bool(profile)
            trial.record("profiling_succeeded", profiled)
            issue_ids: set[str] = set()
            if profiled:
                trial.record("quality_score", round(float(profile["quality_score"]), 4))
                trial.record("blocking_issues", int(profile["blocking_issues"]))
                trial.record("profiled_rows", int(profile["row_count"]))
                trial.observations["profile_uri"] = profile.get("profile_uri")
                issue_ids = _issue_ids(store, profile.get("profile_uri"))
                trial.observations["issues"] = sorted(issue_ids)

            # Detection is measured *against the baseline*, not as "any blocking issue". A
            # count alone gave a false positive: the clean baseline raises issues of its own,
            # so `blocking_issues > 0` marked it detected too and the detection rate would
            # have been 100% for a detector that fired on everything.
            if fault == "baseline_clean":
                _BASELINE_ISSUES.update(issue_ids)
                new_issues: list[str] = []
            else:
                new_issues = sorted(issue_ids - _BASELINE_ISSUES)
            trial.record("detected_at_profile", (not profiled) or bool(new_issues))
            trial.record("new_issues_vs_baseline", new_issues)

            # --- cleaning: where the correct action is taken -----------------------
            clean_rows = quarantined = duplicates = None
            balanced = None
            if profiled:
                clean = pipeline.stage_scala_job(
                    stage="clean",
                    template_id="CleanJob",
                    inputs={"source": registered["transactions"]},
                    output_dir=store.clean_dir("transactions", 1),
                    params={"dedup_keys": "order_id,line_id"},
                    quarantine_dir=store.quarantine_dir("transactions", 1, run.run_key),
                    policy_action="cleaning.execute",
                    input_rows=profile.get("row_count", 0),
                )
                trial.record("cleaning_succeeded", bool(clean))
                if clean:
                    metrics = clean.get("job_metrics", {}) or {}
                    clean_rows = clean.get("row_count")
                    quarantined = metrics.get("quarantined_rows")
                    duplicates = metrics.get("removed_duplicates")
                    balanced = metrics.get("row_accounting_balanced")
                    trial.record("clean_rows", clean_rows)
                    trial.record("quarantined_rows", quarantined)
                    trial.record("removed_duplicates", duplicates)
                    trial.record("row_accounting_balanced", balanced,
                                 note="input = output + quarantined + duplicates")
                    # The cleaning plan converts a declared-numeric column that arrived as text
                    # (DEC-36); its per-rule count is present exactly when that happened.
                    conformed = "rule_cast_price" in metrics
                    trial.record("price_conformed", conformed)
                    if conformed:
                        trial.record("price_quarantined", int(metrics["rule_cast_price"]),
                                     note="values that did not convert, quarantined with reason")
                    trial.observations["job_metrics"] = metrics
                else:
                    trial.observations["escalation"] = pipeline.result.escalation
                    trial.record("escalated", True)
                    trial.record(
                        "escalation_reason",
                        str((pipeline.result.escalation or {}).get("reason", ""))[:200],
                    )
            else:
                trial.record("cleaning_succeeded", False,
                             note="not attempted: profiling did not pass")
            trial.record("detected_at_clean",
                         bool(trial_has(trial, "escalated") or trial_has(trial, "price_conformed")))
            trial.record("reached_cleaning", True)
        finally:
            spark.stop()

        # --- governance: was a mapping proposed rather than applied? --------------
        from sqlalchemy import select

        from triad_core.models import Approval, DecisionRecord

        decisions = session.execute(
            select(DecisionRecord).where(DecisionRecord.run_id == run.id)
        ).scalars().all()
        approvals = session.execute(
            select(Approval).where(Approval.run_id == run.id)
        ).scalars().all()
        trial.record("decision_records", len(decisions))
        trial.record("approval_requests", len(approvals))
        trial.observations["decisions"] = [
            {"agent": d.agent, "type": d.decision_type, "decision": d.decision[:200]}
            for d in decisions
        ]
        trial.observations["approvals"] = [
            {"action": a.action_type, "status": a.status, "target": a.target_ref}
            for a in approvals
        ]
        # The claim is that a mapping is *proposed, not applied*: no approval may be recorded
        # as approved without a person, and nothing may be auto-applied.
        auto_applied = [a for a in approvals if a.status == "approved"]
        trial.record("auto_applied_without_a_person", len(auto_applied))

        trial.record("wall_clock_s", round(time.perf_counter() - started, 1), "s")
        q.set_state(session, run, "CANCELLED", actor="exp04")

    # Detection is "the platform refused to sail past it", at whichever stage that happened.
    where = next(
        (
            stage for stage, key in (
                ("ingest", "detected_at_ingest"),
                ("profile", "detected_at_profile"),
                ("clean", "detected_at_clean"),
            ) if trial_has(trial, key)
        ),
        "not detected",
    )
    trial.record("detected", where != "not detected")
    trial.record("detected_where", where)

    if fault == "baseline_clean":
        trial.ok = bool(trial.get("cleaning_succeeded")) and where == "not detected"
    elif fault == "dropped_optional_table":
        # The contract calls this table optional, so nothing about the transactions schema
        # changes and there is nothing for ingest, profiling or cleaning to object to. What
        # the hypothesis claims - that the missing table disables its features - happens at
        # the feature stage, which this experiment does not run. Recorded, not scored.
        trial.ok = bool(trial.get("optional_table_absent"))
    elif fault == "price_as_text_with_symbols":
        # The hypothesis names the action, not just the detection: "prices are fixed by try_cast
        # and quarantine". An escalation stops the fault but fixes nothing, and was counted as a
        # pass before DEC-36; what passes now is cleaning that converted the column and still
        # balanced its rows.
        trial.ok = (bool(trial.get("cleaning_succeeded")) and bool(trial.get("price_conformed"))
                    and str(trial.get("row_accounting_balanced")).lower() == "true")
        trial.record("correct_action", trial.ok)
    else:
        trial.ok = where != "not detected"
    trial.detail = (
        f"detected at {where}; clean_ok={trial.get('cleaning_succeeded')} "
        f"approvals={trial.get('approval_requests')} "
        f"auto_applied={trial.get('auto_applied_without_a_person')}"
    )


def run() -> Experiment:
    with Experiment(
        "EXP-4",
        title="Schema errors",
        hypothesis=(
            "A renamed required column is detected and a mapping proposed but not applied "
            "without approval; prices are fixed by try_cast and quarantine; the missing "
            "optional table disables its features."
        ),
        independent_variable="fault type",
        dependent_measures=[
            "detected", "cleaning_succeeded", "quarantined_rows",
            "row_accounting_balanced", "approval_requests",
            "auto_applied_without_a_person", "wall_clock_s",
        ],
        config={
            "customers": CUSTOMERS, "months": MONTHS, "seed": SEED,
            "faults": dict(FAULTS),
            "stages_run": ["profile", "clean"],
            "not_measured": (
                "the effect of a disabled feature group on model quality; every measure this "
                "experiment asks for is decided by the time cleaning commits"
            ),
        },
    ) as exp:
        for fault, overrides in FAULTS.items():
            _run_one(exp, fault, overrides)

        baseline = next(t for t in exp.trials if t.condition == "baseline_clean")
        # The dropped optional table is excluded from the detection rate, because the contract
        # says it is optional: there is nothing at these stages that ought to object to it.
        # Counting it as a miss would make the detection rate a statement about the
        # experiment's own scope rather than about the platform.
        scored = [
            t for t in exp.trials
            if t.condition not in ("baseline_clean", "dropped_optional_table")
        ]
        faults = scored
        detected = [t for t in faults if t.get("detected")]
        # A condition refused at ingest never reaches the approval stage, so it records nothing
        # here. Absent is not zero, and counting it as zero would be inventing a measurement -
        # but it is also not a mapping applied without a person, so it is simply not counted.
        auto_applied = sum(
            int(m.value)
            for t in exp.trials for m in t.measurements
            if m.name == "auto_applied_without_a_person" and m.value is not None
        )
        # Row loss is only silent if cleaning claimed success while the accounting did not
        # balance. An escalation is loud, and is not silent loss.
        silent_loss = [
            t for t in exp.trials
            if trial_has(t, "cleaning_succeeded")
            and any(m.name == "row_accounting_balanced" and m.value is False
                    for m in t.measurements)
        ]

        exp.limitation(
            "`register_version` returns early on an identical checksum, before it validates "
            "anything. That is correct idempotence, but it means a file whose bytes were "
            "registered before a contract existed for its dataset is never re-validated. This "
            "experiment sidesteps it by registering each condition under a fresh dataset name; "
            "a deployment would need a re-validation pass when a contract is introduced or "
            "changed. Not a defect, but not covered either."
        )
        exp.limitation(
            "Only profile and clean are run, so what the feature stage does with a missing "
            "optional table - the 'disables its features' half of the hypothesis - is not "
            "measured here."
        )

        price = next((t for t in exp.trials if t.condition == "price_as_text_with_symbols"), None)
        prices_fixed = bool(price and price.ok)
        exp.conclude(
            status="partial",
            hypothesis_held=(
                len(detected) == len(faults)
                and prices_fixed
                and auto_applied == 0
                and not silent_loss
                and bool(baseline.get("cleaning_succeeded"))
                and baseline.get("detected_where") == "not detected"
            ),
            conclusion=(
                "Detected at: "
                + ", ".join(
                    f"{t.condition} -> {t.get('detected_where')}"
                    for t in exp.trials if t.condition != "baseline_clean"
                )
                + f". {len(detected)} of {len(faults)} scored faults detected; the clean "
                f"baseline passed: {bool(baseline.get('cleaning_succeeded'))}. "
                + (
                    f"Prices written as text were fixed: the column was converted with try_cast "
                    f"and {price.get('price_quarantined')} value(s) that did not convert were "
                    "quarantined, with the row accounting balanced. "
                    if prices_fixed else
                    "Prices written as text were not fixed by try_cast and quarantine: "
                    + (price.detail if price else "the condition did not run") + ". "
                )
                + f"{auto_applied} mappings were applied without a person. "
                f"{len(silent_loss)} conditions lost rows silently (cleaning reported success "
                "while the row accounting did not balance). Detection rate is reported against "
                "the baseline on purpose: a detector that flagged everything would otherwise "
                "score perfectly."
            ),
        )
        return exp


if __name__ == "__main__":
    run()
