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
to a copy of the dataset version") and then put through the **real** ingest, profiling and
cleaning stages - the same `DatasetRegistry.register_version`, `ChurnPipeline.stage_profile`
and `stage_scala_job` a production run uses, running the same generated Scala on Spark in the
sandbox.

Each condition runs as far as its claim needs. A renamed column is decided at ingest, where
the contract refuses it and proposes a mapping (PRD 20.2); prices and row accounting are
decided when cleaning commits; and what a missing optional table disables shows only at the
feature stage, so the baseline and that condition also run the feature job. Training would
add half an hour per condition and no evidence: the effect of a disabled feature group on
model quality is EXP-5's territory.

A proposed mapping needs a person, and an experiment has none, so the harness plays the Data
Engineer: a user made for the purpose, with that role, approves the request through the same
`GovernanceService.decide` the API calls, and says so in its comment. Everything before that
decision - the refusal, the proposal, the request, and the refusal to apply a pending mapping -
is the platform's own behaviour. Time to recovery is measured from the decision, because how
long a person takes to decide is not something a harness can measure.

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

# The conditions that also run the feature stage: the missing table, and the baseline it is
# compared with.
FEATURE_CONDITIONS = ("baseline_clean", "dropped_optional_table")


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
    session: Any, store: Any, tables: dict[str, Path], tag: str, run: Any
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Register every generated table under a per-condition name, and report what was refused.

    Three things here are deliberate.

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

    **The condition's run is passed in**, so an approval requested for a proposed mapping is
    filed against it, where the governance measures below look for it.
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
        result = registry.register_version(dataset_name, files[0], actor="exp04", run=run)
        if result.version_id:
            registered[name] = result.storage_uri or ""
            continue
        blocking = [
            v.to_dict() for v in (
                result.contract_result.blocking if result.contract_result else []
            )
        ]
        refusals.append({
            "table": name, "reason": result.reason, "blocking": blocking,
            "proposed_mapping": result.proposed_mapping,
            "approval_id": str(result.approval_id) if result.approval_id else None,
        })
    return registered, refusals


def trial_has(trial: Any, name: str) -> bool:
    """Whether a measurement was recorded at all, and truthy. `Trial.get` raises if absent."""
    return any(m.name == name and m.value for m in trial.measurements)


def _value(trial: Any, name: str) -> Any:
    """A measurement's value, or None when it was not recorded."""
    return next((m.value for m in trial.measurements if m.name == name), None)


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


def _decide_and_apply(
    trial: Any, session: Any, store: Any, approval_id: str, dataset_name: str
) -> tuple[str | None, float]:
    """Check what a pending mapping must not do, then decide it as the Data Engineer.

    Returns the storage URI of the mapped version, if one registered, and the clock at the
    decision, from which the time to recovery is measured.
    """
    from dataeng.ingest import DatasetRegistry, IngestError
    from triad_core.config import settings
    from triad_core.governance import GovernanceService
    from triad_core.models import Approval, User
    from triad_core.policy import PolicyEngine

    registry = DatasetRegistry(session, store)
    approval = session.get(Approval, uuid.UUID(approval_id))
    trial.record("approval_action", approval.action_type)
    trial.record("approval_status_before_decision", approval.status)
    # Nothing may exist downstream of a pending mapping: no version, so nothing to profile.
    trial.record("registered_before_decision",
                 registry.latest_version(registry.get(dataset_name)) is not None)
    try:
        registry.apply_approved_mapping(approval, actor="exp04")
        trial.record("applied_while_pending", True)
    except IngestError as exc:
        trial.record("applied_while_pending", False, note=str(exc)[:200])

    person = User(
        email=f"exp04-data-engineer-{uuid.uuid4().hex[:8]}@triad.invalid",
        display_name="EXP-4 harness as Data Engineer", role="data_engineer",
        password_hash="!experiment-only-no-login",
    )
    session.add(person)
    session.flush()
    decided_at = time.perf_counter()
    GovernanceService(session, PolicyEngine(settings().governance_policy_path)).decide(
        approval.id, approver=person, approved=True,
        comment="EXP-4 harness, playing the Data Engineer: the proposed mapping is accepted "
                "so that recovery can be measured",
    )
    applied = registry.apply_approved_mapping(approval, actor=str(person.id))
    trial.record("mapping_applied_after_approval", bool(applied and applied.created))
    return (applied.storage_uri if applied and applied.version_id else None), decided_at


def _ticket_features() -> list[str]:
    """The catalogue's features that cannot exist without the support tickets table."""
    import yaml

    catalog = yaml.safe_load(
        Path("pipelines/feature_catalog/churn_features.yaml").read_text(encoding="utf-8")
    )
    return [
        f["name"] for f in catalog["features"]
        if "support_tickets" in (f.get("unavailable_without") or [])
    ]


def _run_features(
    trial: Any, pipeline: Any, spark: Any, store: Any, clean: dict[str, Any],
    registered: dict[str, str],
) -> None:
    """The feature stage exactly as a production run calls it, and what it left out."""
    import pyarrow.dataset as ds

    from agents.graph.churn import FEATURE_SOURCES, cutoff_window

    window = cutoff_window(spark, clean["uri"])
    inputs = {"transactions": clean["uri"]}
    inputs.update({s: registered[s] for s in FEATURE_SOURCES if registered.get(s)})
    trial.observations["feature_inputs"] = sorted(inputs)
    features = pipeline.stage_scala_job(
        stage="features",
        template_id="FeatureJob",
        inputs=inputs,
        output_dir=store.features_dir("churn_features", 1),
        params={"max_window_days": 365, **window},
        partition_by=["snapshot_date"],
        required_columns=["customer_id", "snapshot_date", "recency_days"],
        input_rows=clean.get("row_count", 0),
    )
    trial.record("features_succeeded", bool(features))
    if not features:
        trial.observations["feature_escalation"] = pipeline.result.escalation
        return
    groups = {
        key.removeprefix("feature_group_"): value
        for key, value in (features.get("job_metrics") or {}).items()
        if key.startswith("feature_group_")
    }
    trial.observations["feature_groups"] = groups
    trial.record("customer_service_group", groups.get("customer_service", "not reported"))
    columns = set(ds.dataset(
        store.path(features["uri"]), format="parquet", partitioning="hive"
    ).schema.names)
    expected = _ticket_features()
    trial.record("feature_columns", len(columns))
    trial.record("ticket_features_in_catalogue", len(expected))
    trial.record("ticket_features_present", sorted(columns & set(expected)))


def _mapped_without_approval(session: Any, tag: str) -> int:
    """Versions of this condition's datasets whose mapping names no approval."""
    from sqlalchemy import select

    from triad_core.models import Dataset, DatasetVersion

    versions = session.execute(
        select(DatasetVersion)
        .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
        .where(Dataset.name.endswith(f"__{tag}", autoescape=True))
    ).scalars().all()
    return sum(
        1 for v in versions
        if (v.schema_json or {}).get("mapped_from")
        and not v.schema_json["mapped_from"].get("approval_id")
    )


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

    # The dropped optional table must be visibly absent, not quietly assumed absent. Recorded
    # only where a table was dropped: for every other condition the check is vacuously true,
    # and the first runs reported it as a result for the baseline.
    expected_missing = set(overrides.get("drop_tables") or [])
    if expected_missing:
        trial.record("optional_table_absent", expected_missing.isdisjoint(tables))

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
        registered, refusals = _register(session, store, tables, tag, run)
        trial.record("datasets_registered", len(registered))
        trial.record("registrations_refused", len(refusals))
        trial.observations["refusals"] = refusals

        # --- stage 1: ingest. A blocking contract violation stops the fault here, which is
        # FR-007 and the earliest and cheapest detection available; a likely rename is
        # proposed as a mapping and waits for a person (PRD 20.2).
        tx_refused = [r for r in refusals if r["table"] == "transactions" and r["blocking"]]
        trial.record("detected_at_ingest", bool(tx_refused))
        decided_at: float | None = None
        if tx_refused:
            refusal = tx_refused[0]
            trial.record("ingest_refusal", refusal["blocking"][0]["message"][:200])
            trial.record("mapping_proposed", bool(refusal["proposed_mapping"]))
            trial.observations["proposed_mapping"] = refusal["proposed_mapping"]
            if refusal["approval_id"]:
                uri, decided_at = _decide_and_apply(
                    trial, session, store, refusal["approval_id"], f"transactions__{tag}"
                )
                if uri:
                    registered["transactions"] = uri

        if "transactions" not in registered:
            # Nothing further can run, and nothing further needs to: the fault was caught.
            trial.record("detected", bool(tx_refused))
            trial.record("detected_where", "ingest" if tx_refused else "not detected")
            trial.record("profiling_succeeded", False, note="not reached")
            trial.record("cleaning_succeeded", False, note="not reached")
            trial.record("reached_cleaning", False)
            trial.record("wall_clock_s", round(time.perf_counter() - started, 1), "s")
            trial.ok = False
            trial.detail = (
                f"refused at ingest and not recovered: "
                f"{tx_refused[0]['blocking'][0]['message'][:120]}"
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
            clean = None
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
                    if decided_at is not None:
                        trial.record(
                            "time_to_recovery_s", round(time.perf_counter() - decided_at, 1),
                            "s", note="from the approval to cleaned, balanced output; the "
                                      "time a person takes to decide is not included",
                        )
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

            # --- features: what a missing optional table disables -------------------
            if clean and fault in FEATURE_CONDITIONS:
                _run_features(trial, pipeline, spark, store, clean, registered)
                trial.record("disabled_at_features",
                             _value(trial, "customer_service_group") == "unavailable")
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
            {"action": a.action_type, "status": a.status, "target": a.target_ref,
             "decided_by_a_person": a.decided_by is not None}
            for a in approvals
        ]
        # The claim is that a mapping is *proposed, not applied*. Applied without a person
        # would show as a version whose mapping names no approval, or as an approval marked
        # approved with nobody recorded as deciding it. The harness's own approval names its
        # decider, so it is not counted - it is the person the claim requires.
        unattended = [a for a in approvals if a.status == "approved" and a.decided_by is None]
        trial.record("auto_applied_without_a_person",
                     _mapped_without_approval(session, tag) + len(unattended))

        trial.record("wall_clock_s", round(time.perf_counter() - started, 1), "s")
        q.set_state(session, run, "CANCELLED", actor="exp04")

    # Detection is "the platform refused to sail past it", at whichever stage that happened.
    where = next(
        (
            stage for stage, key in (
                ("ingest", "detected_at_ingest"),
                ("profile", "detected_at_profile"),
                ("clean", "detected_at_clean"),
                ("features", "disabled_at_features"),
            ) if trial_has(trial, key)
        ),
        "not detected",
    )
    trial.record("detected", where != "not detected")
    trial.record("detected_where", where)

    balanced_ok = str(_value(trial, "row_accounting_balanced")).lower() == "true"
    if fault == "baseline_clean":
        trial.ok = (bool(trial.get("cleaning_succeeded")) and where == "not detected"
                    and trial_has(trial, "features_succeeded"))
    elif fault == "renamed_required_column":
        # The whole of ATC-06 and PRD 20.2: refused, proposed, held while pending, and only
        # then applied - after which the data must clean like any other.
        trial.ok = (where == "ingest" and trial_has(trial, "mapping_proposed")
                    and _value(trial, "approval_status_before_decision") == "pending"
                    and not trial_has(trial, "registered_before_decision")
                    and not trial_has(trial, "applied_while_pending")
                    and trial_has(trial, "mapping_applied_after_approval")
                    and trial_has(trial, "cleaning_succeeded") and balanced_ok)
        trial.record("correct_action", trial.ok)
    elif fault == "dropped_optional_table":
        trial.ok = (trial_has(trial, "optional_table_absent")
                    and trial_has(trial, "features_succeeded")
                    and _value(trial, "customer_service_group") == "unavailable"
                    and not trial_has(trial, "ticket_features_present"))
        trial.record("correct_action", trial.ok)
    else:
        # The hypothesis names the action, not just the detection: "prices are fixed by try_cast
        # and quarantine". An escalation stops the fault but fixes nothing, and was counted as a
        # pass before DEC-36; what passes now is cleaning that converted the column and still
        # balanced its rows.
        trial.ok = (trial_has(trial, "cleaning_succeeded") and trial_has(trial, "price_conformed")
                    and balanced_ok)
        trial.record("correct_action", trial.ok)
    trial.detail = (
        f"detected at {where}; clean_ok={_value(trial, 'cleaning_succeeded')} "
        f"approvals={_value(trial, 'approval_requests')} "
        f"auto_applied={_value(trial, 'auto_applied_without_a_person')}"
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
            "detected", "correct_action", "mapping_proposed", "approval_requests",
            "auto_applied_without_a_person", "time_to_recovery_s", "cleaning_succeeded",
            "quarantined_rows", "row_accounting_balanced", "customer_service_group",
            "wall_clock_s",
        ],
        config={
            "customers": CUSTOMERS, "months": MONTHS, "seed": SEED,
            "faults": dict(FAULTS),
            "stages_run": ["ingest", "profile", "clean"],
            "feature_stage_for": list(FEATURE_CONDITIONS),
            "approver": "the harness, as a user with the data_engineer role",
            "not_measured": (
                "the effect of a disabled feature group on model quality (EXP-5), and the time "
                "a person takes to decide an approval"
            ),
        },
    ) as exp:
        for fault, overrides in FAULTS.items():
            _run_one(exp, fault, overrides)

        baseline = next(t for t in exp.trials if t.condition == "baseline_clean")
        faults = [t for t in exp.trials if t.condition != "baseline_clean"]
        detected = [t for t in faults if _value(t, "detected")]
        correct = [t for t in faults if t.ok]
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
            "The proposed mapping was approved by the harness acting as a Data Engineer, so "
            "the time to recovery covers the platform's work after the decision and not the "
            "time a person would take to make it."
        )

        renamed = next(t for t in exp.trials if t.condition == "renamed_required_column")
        price = next(t for t in exp.trials if t.condition == "price_as_text_with_symbols")
        dropped = next(t for t in exp.trials if t.condition == "dropped_optional_table")
        mapping = renamed.observations.get("proposed_mapping") or {}
        mapping_text = ", ".join(f"{src} -> {dst}" for src, dst in mapping.items()) or "none"
        exp.conclude(
            status="completed",
            hypothesis_held=(
                len(correct) == len(faults) == len(detected)
                and auto_applied == 0
                and not silent_loss
                and baseline.ok
                and _value(baseline, "customer_service_group") == "available"
            ),
            conclusion=(
                "Detected at: "
                + ", ".join(f"{t.condition} -> {_value(t, 'detected_where')}" for t in faults)
                + f". {len(detected)} of {len(faults)} faults detected and {len(correct)} "
                f"handled correctly; the clean baseline passed: {baseline.ok}. "
                + (
                    f"The renamed column was refused at ingest with the mapping {mapping_text} "
                    f"proposed and a {_value(renamed, 'approval_action')} approval requested; "
                    "while it was pending nothing was registered and applying it was refused, "
                    "and once approved the mapped copy registered and cleaned with balanced "
                    f"rows {_value(renamed, 'time_to_recovery_s')} s after the decision. "
                    if renamed.ok else f"The renamed column was not handled: {renamed.detail}. "
                )
                + (
                    f"Prices written as text were fixed: the column was converted with try_cast "
                    f"and {_value(price, 'price_quarantined')} value(s) that did not convert were "
                    "quarantined, with the row accounting balanced. "
                    if price.ok else
                    f"Prices written as text were not fixed by try_cast and quarantine: "
                    f"{price.detail}. "
                )
                + (
                    "Without the support tickets table the feature job marked the customer "
                    "service group unavailable and produced none of its "
                    f"{_value(dropped, 'ticket_features_in_catalogue')} features, which the "
                    "baseline produced. "
                    if dropped.ok else
                    f"The missing optional table did not disable its features as claimed: "
                    f"{dropped.detail}. "
                )
                + f"{auto_applied} mappings were applied without a person. "
                f"{len(silent_loss)} conditions lost rows silently (cleaning reported success "
                "while the row accounting did not balance). Detection is measured against the "
                "baseline on purpose: a detector that flagged everything would otherwise score "
                "perfectly."
            ),
        )
        return exp


if __name__ == "__main__":
    run()
