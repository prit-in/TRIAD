"""The churn run as two phases, either side of the human approval gate (PRD section 39).

`prepare` takes registered inputs through profiling, cleaning, features, labels, the hand-off
to Python, training and selection, and stops by *requesting* an approval. `publish` runs after
a person has decided, and promotes, smoke-tests, scores, measures drift and reports.

They are two functions rather than one because a run genuinely pauses in between, possibly for
days. Nothing may be carried between them in memory: `publish` reconstructs everything it
needs from what `prepare` wrote down, which is also what makes the same run reproducible and
what lets a worker crash between the two without losing the run.

Both the demonstration script and the queue worker call these, so there is one implementation
of the sequence and one place where its order is decided.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from agents.graph.pipeline import ChurnPipeline
from agents.monitoring import MonitoringService, jsonable
from triad_core.config import settings
from triad_core.ids import utcnow
from triad_core.models import Experiment, ModelVersion, PipelineRun
from triad_core.storage import Storage, storage

Logger = Callable[[str, str, bool | None], None]

STATE_FILE = "run_state.json"
MODEL_NAME = "churn_classifier"

# Customer attributes the segment checks group by (PRD 26.1: country, tenure, value tier).
# Joined at the hand-off for evaluation only and excluded from the feature columns below, so
# the model never trains on them. A segment check asks whether a model built from *behaviour*
# treats groups differently; a model handed `country` as an input answers a different question.
SEGMENT_ATTRIBUTES = ("country", "loyalty_tier", "acquisition_channel")


def _noop(step: str, message: str, ok: bool | None = None) -> None:
    return None


@dataclass
class PhaseResult:
    ok: bool
    state: str
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "state": self.state, "reason": self.reason,
                "details": self.details}


# --- durable state between the two phases --------------------------------------


def state_path(store: Storage, run: PipelineRun):
    return store.reports_dir(run.run_key) / STATE_FILE


def write_state(store: Storage, run: PipelineRun, payload: dict[str, Any]) -> str:
    return store.uri(store.write_json(state_path(store, run), payload))


def read_state(store: Storage, run: PipelineRun) -> dict[str, Any]:
    path = state_path(store, run)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing: the preparation phase of {run.run_key} did not finish, "
            "so there is nothing to publish."
        )
    return json.loads(store.read_text(path))


# --- cutoffs, derived from the data rather than assumed -------------------------


def cutoff_window(
    spark: Any, clean_uri: str, *, max_window_days: int = 365, horizon_days: int = 90,
    date_column: str = "order_date",
) -> dict[str, str]:
    """Month-start cutoffs the data can actually support.

    A cutoff needs `max_window_days` of history behind it and `horizon_days` of data ahead of
    it, or its features are computed over a short window and its label can never mature. Both
    edges are read from the data, because a window assumed from a parameter is the kind of
    thing that silently produces an empty label set.
    """
    from pyspark.sql import functions as F

    frame = spark.read.parquet(clean_uri.replace("file://", ""))
    bounds = frame.select(
        F.min(F.to_date(F.col(date_column))).alias("first"),
        F.max(F.to_date(F.col(date_column))).alias("last"),
    ).head()
    if bounds is None or bounds["first"] is None:
        raise ValueError(f"{clean_uri} has no usable {date_column} values")

    data_start, data_end = bounds["first"], bounds["last"]
    first = _month_start_on_or_after(data_start + dt.timedelta(days=max_window_days))
    last = (data_end - dt.timedelta(days=horizon_days)).replace(day=1)
    if last < first:
        raise ValueError(
            f"the data spans {data_start} to {data_end}, which leaves no cutoff with "
            f"{max_window_days} days of history and {horizon_days} days of horizon"
        )
    return {"first_cutoff": first.isoformat(), "last_cutoff": last.isoformat(),
            "data_end": data_end.isoformat()}


def _lock_pipeline(session: Session, pipeline_name: str) -> None:
    """Hold this pipeline's run lock until the transaction ends.

    A second worker starting the same pipeline waits here instead of overwriting the first
    one's intermediate outputs. With a single worker nothing ever waits.
    """
    import zlib

    from sqlalchemy import text

    try:
        dialect = session.get_bind().dialect.name
    except Exception:
        return
    if dialect != "postgresql":
        return
    session.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, :key)"),
        {"namespace": 0x54524144, "key": zlib.crc32(pipeline_name.encode()) & 0x7FFFFFFF},
    )


def _champion_scores(split: Any, feature_cols: list[str]) -> tuple[Any, str]:
    """The deployed champion's probabilities on this run's test cutoff.

    Three outcomes, kept distinct on purpose:

      * `(scores, "")` - there is a champion and it scored the test set;
      * `(None, "no champion yet: ...")` - genuinely the first model, so AP-8 has nothing to
        compare against and the run proceeds;
      * `(None, "could not ...")` - there *is* a champion and it could not be scored. That is
        not the same as having none, so the caller escalates rather than promoting a
        challenger that was never actually compared.
    """
    from python_ml.triad_ml.registry import ModelRegistry

    try:
        registry = ModelRegistry(settings().mlflow_tracking_uri)
        current = registry.get_alias(MODEL_NAME, "champion")
    except Exception as exc:
        return None, f"could not reach the model registry to find the champion: {exc}"

    if current is None:
        return None, "no champion yet: this challenger is compared against the floor only"

    try:
        champion = registry.load(MODEL_NAME, "champion")
        return champion.predict_proba(split.test[feature_cols])[:, 1], (
            f"champion v{current.version} scored on the same test cutoff"
        )
    except Exception as exc:
        return None, (
            f"could not score champion v{current.version} on this test set ({exc}); "
            "AP-8 cannot be evaluated, so the promotion is not proposed"
        )


def _month_start_on_or_after(day: dt.date) -> dt.date:
    if day.day == 1:
        return day
    total = day.year * 12 + day.month   # already +1 month, since month is 1-based
    return dt.date(total // 12, total % 12 + 1, 1)


# --- phase one: prepare, train, select, request approval ------------------------


def prepare(
    session: Session,
    run: PipelineRun,
    *,
    inputs: dict[str, str],
    spark: Any,
    store: Storage | None = None,
    seed: int = 42,
    cutoffs: dict[str, str] | None = None,
    log: Logger | None = None,
    requested_by: str | None = None,
    replay_of: PipelineRun | None = None,
) -> PhaseResult:
    """Everything up to and including the approval request. Leaves the run paused.

    `replay_of` puts the run in replay mode (ATC-18): the generated Scala of that completed run
    is read back from storage and used verbatim rather than generated afresh, so the replay
    reproduces what ran even if a template has changed since.
    """
    from python_ml.triad_ml.candidates import shortlist, split_feature_types
    from python_ml.triad_ml.registry import ModelCard, write_model_card
    from python_ml.triad_ml.splits import apply_split, plan_split
    from python_ml.triad_ml.train import select_model, train_all
    from triad_core.governance import EvidenceBundle
    from triad_core.parquet_io import estimate_in_memory_bytes, read_parquet

    say = log or _noop
    store = store or storage()
    pipeline = ChurnPipeline(session, run, store=store, replay_of=replay_of)

    # Two runs of the same pipeline write the same clean/feature/label folders, and
    # `storage.commit` supersedes what is there. Run them at once and the second silently
    # replaces the first's inputs mid-flight - which is exactly how a concurrent run's feature
    # job failed. Serialising them is a real constraint of this layout, so it is enforced
    # rather than left to chance; lifting it means versioning those outputs per run.
    _lock_pipeline(session, run.pipeline_name)

    if "transactions" not in inputs:
        return PhaseResult(False, "REJECTED_INPUT",
                           "the churn pipeline needs a registered `transactions` input")

    # NFR-REP-01: record the exact input versions this run read. The column existed and nothing
    # wrote to it, which meant a replay had no way to find the bytes the original run used - it
    # would have had to guess at "the current version", and a replay that reads different data
    # proves nothing about determinism.
    run.input_versions = dict(inputs)
    session.flush()

    # -- profile ----------------------------------------------------------------
    profile_out = pipeline.stage_profile(spark, "transactions", inputs["transactions"])
    if not profile_out:
        pipeline.finish("ESCALATED", ok=False, reason="profiling failed")
        return PhaseResult(False, "ESCALATED", "profiling failed")
    say("profile", f"quality {profile_out['quality_score']:.3f}, "
                   f"{profile_out['row_count']} rows, "
                   f"{profile_out['blocking_issues']} blocking issues", True)
    pipeline._advance("PROFILED", stage="profile")

    # -- clean ------------------------------------------------------------------
    clean_out = pipeline.stage_scala_job(
        stage="clean",
        template_id="CleanJob",
        inputs={"source": inputs["transactions"]},
        output_dir=store.clean_dir("transactions", 1),
        params={"dedup_keys": "order_id,line_id"},
        quarantine_dir=store.quarantine_dir("transactions", 1, run.run_key),
        policy_action="cleaning.execute",
        input_rows=profile_out.get("row_count", 0),
    )
    if not clean_out:
        pipeline.finish("ESCALATED", ok=False, reason="cleaning failed")
        return PhaseResult(False, "ESCALATED", "cleaning failed",
                           {"escalation": pipeline.result.escalation})
    accounting = clean_out.get("job_metrics", {})
    say("clean", f"clean rows {clean_out.get('row_count')}, "
                 f"quarantined {accounting.get('quarantined_rows', '?')}, "
                 f"duplicates removed {accounting.get('removed_duplicates', '?')}, "
                 f"accounting balanced {accounting.get('row_accounting_balanced', '?')}", True)
    pipeline._advance("PLANNED", stage="plan")

    # -- cutoffs ----------------------------------------------------------------
    window = cutoffs or cutoff_window(spark, clean_out["uri"])
    say("plan", f"cutoffs {window['first_cutoff']} to {window['last_cutoff']}; "
                f"data ends {window['data_end']}", True)

    # -- features and labels ----------------------------------------------------
    features_out = pipeline.stage_scala_job(
        stage="features",
        template_id="FeatureJob",
        inputs={"transactions": clean_out["uri"], "products": inputs.get("products", "")},
        output_dir=store.features_dir("churn_features", 1),
        params={"max_window_days": 365, **window},
        partition_by=["snapshot_date"],
        required_columns=["customer_id", "snapshot_date", "recency_days"],
        input_rows=clean_out.get("row_count", 0),
    )
    if not features_out:
        pipeline.finish("ESCALATED", ok=False, reason="feature job failed")
        return PhaseResult(False, "ESCALATED", "feature job failed",
                           {"escalation": pipeline.result.escalation})
    say("features", f"{features_out.get('row_count')} customer-snapshot rows", True)

    labels_out = pipeline.stage_scala_job(
        stage="labels",
        template_id="LabelJob",
        inputs={"transactions": clean_out["uri"]},
        output_dir=store.labels_dir("churned_90d", 1),
        params={"max_window_days": 365, "horizon_days": 90, **window},
        required_columns=["customer_id", "snapshot_date", "churned_90d"],
        input_rows=clean_out.get("row_count", 0),
    )
    if not labels_out:
        pipeline.finish("ESCALATED", ok=False, reason="label job failed")
        return PhaseResult(False, "ESCALATED", "label job failed",
                           {"escalation": pipeline.result.escalation})
    say("labels", f"{labels_out.get('row_count')} labelled rows", True)
    pipeline._advance("FEATURES_READY", stage="features")

    # -- hand off behind the size guard ----------------------------------------
    estimate = estimate_in_memory_bytes(store.path(features_out["uri"]))
    route = pipeline._route("handoff", kind="handoff", estimated_memory_bytes=estimate)
    say("handoff", f"{route['rule_id']} -> {route['target']} "
                   f"({estimate / 1e6:.1f} MB estimated)", True)

    dataset = read_parquet(store.path(features_out["uri"])).merge(
        read_parquet(store.path(labels_out["uri"])),
        on=["customer_id", "snapshot_date"], how="inner",
    )
    say("handoff", f"joined {len(dataset)} labelled rows", True)

    # Segment attributes, for evaluation only. Without them the fairness gate was structurally
    # vacuous: `segment_cols` was `["country"] if "country" in dataset.columns else None`, the
    # feature job emits 23 numeric columns and no customer attributes, so every run passed None
    # and AP-6 - the segment recall gap - has never compared anything in any run.
    segment_columns: list[str] = []
    if "customers" in inputs:
        try:
            attributes = read_parquet(store.path(inputs["customers"]))
            wanted = [c for c in SEGMENT_ATTRIBUTES if c in attributes.columns]
            if wanted:
                dataset = dataset.merge(
                    attributes[["customer_id", *wanted]].drop_duplicates("customer_id"),
                    on="customer_id", how="left",
                )
                segment_columns = wanted
        except Exception as exc:   # a missing attribute table must not fail the run
            say("segments", f"customer attributes unavailable: {type(exc).__name__}: {exc}",
                False)
    say("segments",
        f"segment attributes joined for evaluation: {segment_columns or 'none available'}",
        bool(segment_columns))

    if dataset.empty:
        pipeline.finish("ESCALATED", ok=False,
                        reason="features and labels produced no joined rows")
        return PhaseResult(False, "ESCALATED",
                           "features and labels produced no joined rows")

    # -- train and select -------------------------------------------------------
    pipeline._advance("TRAINING", stage="train")
    spec = plan_split(sorted(dataset.snapshot_date.unique()), horizon_days=90)
    split = apply_split(dataset, spec)
    # The segment attributes stay in the frames - `segment_metrics` groups by them - and the
    # split carries the fact that they are not features, so every training call excludes them.
    split.evaluation_only_cols = tuple(segment_columns)
    say("split", f"train {len(split.train)} / valid {len(split.valid)} / "
                 f"test {len(split.test)}; churn rate "
                 f"{split.class_balance.get('valid', float('nan')):.3f}", True)

    models = train_all(
        shortlist(n_rows=len(split.train), n_features=len(dataset.columns)), split, seed=seed
    )
    for model in models:
        say("train", f"{model.family:<14} "
                     + ("failed" if model.failed else f"valid PR-AUC {model.valid_pr_auc:.4f}"),
            not model.failed)
    pipeline._advance("EVALUATED", stage="evaluate")

    # AP-8 is a comparison against the champion on the *same* test cutoff. Without the
    # champion's scores every run would silently behave as if it were the first one, and the
    # rule that a challenger must beat what is deployed would never actually apply.
    num_cols, cat_cols = split_feature_types(
        split.train, exclude=split.evaluation_only_cols
    )
    feature_cols = num_cols + cat_cols
    champion_scores, champion_note = _champion_scores(split, feature_cols)
    if champion_note:
        say("compare", champion_note, champion_scores is not None)
    if champion_scores is None and champion_note.startswith("could not"):
        pipeline.finish("ESCALATED", ok=False, reason=champion_note)
        return PhaseResult(False, "ESCALATED", champion_note)

    selection = select_model(
        models, split, pipeline.config["acceptance_policy"],
        champion_scores=champion_scores,
        segment_cols=segment_columns or None,
        tuning=pipeline.config.get("tuning"),
        seed=seed,
    )
    for record in selection.evidence.get("tuning", []):
        if record.get("skipped"):
            say("tune", record["skipped"], True)
            continue
        say("tune", f"{record['family']}: {record['trials_run']} of "
                    f"{record['trials_requested']} trials in {record['seconds']}s -> "
                    + (f"{record['best_value']:.4f} (was {record['baseline_value']:.4f})"
                       if record["improved"]
                       else "no improvement on the screened model")
                    + (f"; {record['stopped_because']}" if record["stopped_because"] else ""),
            True)
    say("select", f"{selection.outcome}: {selection.reason}", selection.outcome != "ESCALATE")

    # The acceptance decision is recorded whatever it was, and before the escalation branch.
    # It used to be written only on the paths that continued, so a run stopped by the
    # acceptance policy left the database holding a one-line reason and nothing else: no rule
    # ids, no measured calibration error, no record of which remedies were tried. EXP-5 made
    # that plain - three of its four conditions escalated on MISCALIBRATED and not one of the
    # three recorded the number it escalated on, which is the number a person resuming the run
    # needs first. FR-060 asks for a decision record per stage; an escalation is the stage where
    # it matters most.
    pipeline.runner.record_decision(
        agent="model_selection",
        decision_type="acceptance",
        decision=f"{selection.outcome}: {selection.reason}",
        summary=f"{selection.selected.family if selection.selected else 'no candidate'} "
                f"judged against the acceptance policy",
        # A JSON column, and the acceptance evidence carries numpy scalars.
        evidence=jsonable(selection.evidence),
        policy_rules=selection.rules_applied,
        outcome="escalated" if selection.outcome == "ESCALATE" else "applied",
    )
    # Validation metrics too, for the same reason: they are what the verdict was computed from.
    if selection.selected is not None:
        for name, value in (selection.selected.valid_metrics.values or {}).items():
            pipeline._record_metric(
                name, value, split="valid",
                ci=selection.selected.valid_metrics.intervals.get(name),
            )

    if selection.outcome == "ESCALATE":
        pipeline.finish("ESCALATED", ok=False, reason=selection.reason)
        return PhaseResult(False, "ESCALATED", selection.reason,
                           {"selection": selection.to_dict()})

    pipeline._advance("SELECTED", stage="select")
    # `intervals` carries the bootstrap 95% bounds. They used to stop here: `_record_metric`
    # has taken a `ci=` argument since it was written and no caller ever passed one, so
    # `metrics.ci_low` and `metrics.ci_high` were columns nothing filled - and FR-066's
    # "comparison with confidence intervals" had no intervals to show.
    for name, value in (selection.test_metrics.values or {}).items():
        pipeline._record_metric(
            name, value, split="test", ci=selection.test_metrics.intervals.get(name)
        )
    for name, value in (selection.selected.valid_metrics.values or {}).items():
        pipeline._record_metric(
            name, value, split="valid",
            ci=selection.selected.valid_metrics.intervals.get(name),
        )

    threshold = selection.selected.valid_metrics.get("threshold", 0.5)

    # Per-segment metrics: the `segment` column existed and nothing wrote to it, so AP-6 was
    # enforced on a number nobody could see afterwards.
    segment_metrics_out: list[dict[str, Any]] = []
    try:
        from python_ml.triad_ml.evaluate import segment_metrics as _segment_metrics
        from python_ml.triad_ml.train import LABEL as _LABEL

        test_scores = selection.selected.pipeline.predict_proba(
            split.test[num_cols + cat_cols]
        )[:, 1]
        available = [c for c in segment_columns if c in split.test.columns]
        for metrics in _segment_metrics(
            split.test, split.test[_LABEL].to_numpy(), test_scores,
            segment_cols=available, threshold=threshold,
        ):
            segment_metrics_out.append(metrics.to_dict())
            for name, value in (metrics.values or {}).items():
                pipeline._record_metric(name, value, split="test", segment=metrics.segment)
    except Exception as exc:   # a missing segment column must not fail the run
        say("segments", f"per-segment metrics not recorded: {type(exc).__name__}: {exc}", False)

    # -- register the challenger ------------------------------------------------
    say("segments", f"{len(segment_metrics_out)} segment metric set(s) recorded", True)
    mlflow_version: str | None = None
    mlflow_experiment_id: str | None = None
    registry_error = ""
    try:
        from python_ml.triad_ml.registry import ModelRegistry

        registry = ModelRegistry(settings().mlflow_tracking_uri)
        mlflow_experiment_id = registry.experiment_id
        registered = registry.log_candidate(
            model=selection.selected.pipeline,
            name=f"{run.run_key}-{selection.selected.family}",
            params={"family": selection.selected.family, "seed": seed,
                    **selection.selected.candidate.params},
            metrics=dict(selection.test_metrics.values or {}),
            input_example=split.valid[num_cols + cat_cols].head(2),
            tags={"run_key": run.run_key, "feature_set_version": "fsv=1",
                  "split": json.dumps(spec.to_dict())},
            register_as=MODEL_NAME,
        )
        mlflow_version = registered.version if registered else None
        if mlflow_version:
            registry.set_challenger(MODEL_NAME, mlflow_version)
            say("registry", f"registered {MODEL_NAME} v{mlflow_version} as challenger", True)
    except Exception as exc:
        registry_error = str(exc)
        say("registry", f"could not register the challenger: {exc}", False)

    # FR-065/FR-066/FR-067: the evaluation document the charts are drawn from.
    #
    # Everything in here can only be computed while `y_true` and `y_score` are both in hand,
    # and neither survives the run: the predictions table has no label column, so nothing
    # downstream can reconstruct a ROC or a reliability diagram. Curves, intervals, per-segment
    # metrics and feature importance were all computed and then dropped on the floor, which
    # left "render charts from stored data only" impossible to satisfy honestly.
    #
    # The path is derived from the run key rather than stored in a column, so no migration is
    # needed and an older model version simply has no document rather than a null pointer.
    evaluation = {
        "run_key": run.run_key,
        "model_name": MODEL_NAME,
        "selected_family": selection.selected.family,
        "threshold": threshold,
        "generated_at": utcnow().isoformat(),
        "test": selection.test_metrics.to_dict(),
        "valid": selection.selected.valid_metrics.to_dict(),
        "segments": segment_metrics_out,
        "champion_delta": selection.champion_delta,
        # Every family that was screened, so the selection can be read rather than trusted.
        "candidates": [
            {
                "family": m.family,
                "valid_pr_auc": m.valid_metrics.get("pr_auc"),
                "valid_roc_auc": m.valid_metrics.get("roc_auc"),
                "selected": m.family == selection.selected.family,
            }
            for m in models
        ],
        "rejected": selection.rejected,
        "acceptance": {k: v.to_dict() for k, v in (selection.verdicts or {}).items()},
        "rules_applied": selection.rules_applied,
        "feature_importance": None,   # filled below when it can be computed
    }
    try:
        from python_ml.triad_ml.predict import explain

        explanations = explain(
            selection.selected.pipeline, split.valid, num_cols + cat_cols, seed=seed
        )
        evaluation["feature_importance"] = {
            "method": explanations.method,
            "note": explanations.note,
            "global": explanations.global_importance,
        }
    except Exception as exc:   # importance is evidence, not a gate: record why it is absent
        evaluation["feature_importance"] = {
            "method": "not computed",
            "note": f"{type(exc).__name__}: {exc}",
            "global": [],
        }
    evaluation_path = store.write_json(
        store.reports_dir(run.run_key) / "evaluation.json", evaluation
    )
    say("evaluation", f"curves, intervals, {len(segment_metrics_out)} segment set(s) and "
                      f"importance written to {evaluation_path.name}", True)

    # PRD 17.10: the model card. `ModelCard` and `write_model_card` were written, complete with
    # a markdown renderer, and never called by anything - while `model_versions.model_card_uri`
    # was set to `reports/<run_key>/model_card.md` and the governance policy names `model_card`
    # as required evidence for `model.promote`. An approver following that link got nothing.
    # Everything the card needs is already in hand at this point, which is why it is written
    # here rather than at the approval, where the split and the candidates would have to be
    # reconstructed.
    card = ModelCard(
        model_name=MODEL_NAME,
        version=str(mlflow_version or "unregistered"),
        algorithm=selection.selected.family,
        intended_use=(
            "Ranks customers by their probability of placing no order in the next "
            f"{pipeline.config['label']['horizon_days']} days, so a retention list can be "
            "ordered. It is an association, not a cause: nothing here shows that changing a "
            "feature changes the outcome."
        ),
        limits=[
            "Not for pricing, credit, employment, insurance or housing decisions.",
            "A risk band is a bucket of a probability, not a statement about a person.",
            "Trained and evaluated on synthetic data (ADR-0002); the metrics describe this "
            "pipeline, not real retail churn.",
        ],
        churn_definition=pipeline.config["label"]["definition"].strip(),
        horizon_days=int(pipeline.config["label"]["horizon_days"]),
        training_cutoffs=list(spec.train_cutoffs),
        feature_set_version="fsv=1",
        candidate_comparison=[
            {
                "family": c["family"],
                # Rounded for a document a person reads; the unrounded value is in
                # evaluation.json, which is what anything recomputing should use.
                "valid_pr_auc": (
                    round(c["valid_pr_auc"], 4) if c["valid_pr_auc"] is not None else None
                ),
                "outcome": "selected" if c["selected"] else "screened out",
            }
            for c in evaluation["candidates"]
        ],
        metrics=jsonable(selection.test_metrics.to_dict().get("values", {})),
        calibration=jsonable(
            {
                "method": selection.selected.calibration_method,
                "ece": selection.selected.valid_metrics.get("ece"),
                "brier_skill": selection.selected.valid_metrics.get("brier_skill"),
                "threshold": threshold,
            }
        ),
        segment_results=jsonable({"segments": segment_metrics_out})["segments"],
        top_drivers=(evaluation["feature_importance"] or {}).get("global", [])[:10],
        driver_method=(evaluation["feature_importance"] or {}).get("method", "not computed"),
        known_limitations=[
            f"Test split is the single cutoff {spec.test_cutoff}; one cutoff is one season.",
            "Segments with too few positives are not reported rather than reported thinly.",
            (evaluation["feature_importance"] or {}).get("note", ""),
        ],
    )
    card_path = write_model_card(card, store.reports_dir(run.run_key) / "model_card.md")
    say("model card", f"written to {card_path.name} for version "
                      f"{mlflow_version or 'unregistered'}", True)

    # FR-077: the modelling experiment this run's candidates belong to, recorded in the
    # database and not only on the tracking server. Written whether or not MLflow was
    # reachable - the split spec and objective are what make a comparison reproducible, and
    # they are known here regardless. `mlflow_experiment_id` is null when it was not.
    session.add(
        Experiment(
            run_id=run.id,
            mlflow_experiment_id=mlflow_experiment_id,
            name=f"{run.run_key}-churn-selection",
            objective_metric="pr_auc",
            split_spec_json=spec.to_dict(),
            feature_set_version="fsv=1",
        )
    )
    session.flush()

    monitor = MonitoringService(session, store=store, config=pipeline.config,
                                governance=pipeline.governance)
    catalogue_model = monitor.model(
        MODEL_NAME, target_definition=pipeline.config["label"]["definition"].strip()
    )
    model_version = monitor.register_model_version(
        catalogue_model, run=run, algorithm=selection.selected.family,
        mlflow_version=mlflow_version, params=selection.selected.candidate.params,
        feature_set_version="fsv=1", training_data_ref=features_out["uri"],
        threshold=threshold,
        governance_status="challenger" if mlflow_version else "candidate",
    )

    # The reference is the training feature distribution plus the score distribution the model
    # produced on the *validation* split. Scores on the training rows would be optimistic and
    # would make every later run look like drift.
    reference_uri = monitor.capture_reference(
        model_version, split.train, num_cols + cat_cols,
        scores=selection.selected.pipeline.predict_proba(split.valid[num_cols + cat_cols])[:, 1],
        high_risk_threshold=pipeline.config["risk_bands"]["high"],
    )
    say("monitor", f"catalogue version {model_version.id} recorded; "
                   f"reference captured from {len(split.train)} training rows", True)

    state = {
        "run_key": run.run_key,
        "model_name": MODEL_NAME,
        "model_version_id": str(model_version.id),
        "mlflow_version": mlflow_version,
        "registry_error": registry_error,
        "selection_outcome": selection.outcome,
        "features_uri": features_out["uri"],
        "labels_uri": labels_out["uri"],
        "clean_uri": clean_out["uri"],
        "reference_uri": reference_uri,
        "split": spec.to_dict(),
        "feature_columns": feature_cols,
        "numeric_columns": num_cols,
        "categorical_columns": cat_cols,
        "threshold": threshold,
        "selection": selection.to_dict(),
        "quality_score": profile_out.get("quality_score"),
        "cutoffs": window,
        "seed": seed,
    }

    # Only a proposal asks a person to decide. KEEP_CHAMPION already *is* the decision, and
    # asking someone to approve a promotion the acceptance policy just refused would invite
    # them to overrule it without saying so - which is how a model that did not win gets
    # deployed anyway.
    if selection.outcome != "PROPOSE_PROMOTION":
        state["approval_id"] = None
        write_state(store, run, state)
        say("select", f"{selection.outcome}: no promotion is proposed, so no approval is "
                      "requested; the challenger is kept for shadow scoring", True)
        pipeline._persist_budget()
        pipeline._advance("SELECTED", stage="select")
        return PhaseResult(True, "SELECTED", selection.reason, state)

    # -- request approval -------------------------------------------------------
    evidence = EvidenceBundle(
        action_type="model_promotion",
        target_ref=f"{MODEL_NAME}:{mlflow_version or 'unregistered'}",
        metrics_vs_champion=selection.champion_delta or
        {"note": "no champion yet: first model"},
        segment_checks=[{"rule": "AP-6", "result": "checked"}],
        calibration={"method": selection.selected.calibration_method,
                     "ece": selection.selected.valid_metrics.get("ece")},
        drift_summary={"note": "first deployment: no reference distribution yet"},
        model_card_uri=str(store.reports_dir(run.run_key) / "model_card.md"),
        decision_records=[{"outcome": selection.outcome, "rules": selection.rules_applied}],
    )
    approval_id = pipeline.request_promotion(
        model_ref=evidence.target_ref, evidence=evidence,
        requested_by=requested_by or "agent:evaluation_agent",
    )
    say("approval", f"requested {approval_id}; run paused in APPROVAL_REQUIRED", True)

    state["approval_id"] = approval_id
    write_state(store, run, state)
    pipeline._persist_budget()
    return PhaseResult(True, "APPROVAL_REQUIRED", "awaiting a decision", state)


# --- phase two: publish, after a person has decided -----------------------------


def publish(
    session: Session,
    run: PipelineRun,
    *,
    store: Storage | None = None,
    log: Logger | None = None,
    force_smoke_failure: bool = False,
) -> PhaseResult:
    """Promote if approved, smoke-test, score, measure drift, report.

    Everything is rebuilt from what `prepare` wrote down, so this runs correctly in a
    different process, on a different day, after the approval finally arrives.
    """
    from sqlalchemy import select

    from python_ml.triad_ml.drift import top_raw_features
    from python_ml.triad_ml.predict import OFFICIAL, SHADOW, explain, score
    from python_ml.triad_ml.registry import ModelRegistry, smoke_test_scores
    from python_ml.triad_ml.reports import FactSheet, build_report
    from python_ml.triad_ml.splits import SplitSpec, apply_split
    from triad_core.models import Approval
    from triad_core.parquet_io import read_parquet, write_parquet

    say = log or _noop
    store = store or storage()
    pipeline = ChurnPipeline(session, run, store=store)
    state = read_state(store, run)

    approval = session.execute(
        select(Approval).where(Approval.run_id == run.id,
                               Approval.action_type == "model_promotion")
        .order_by(Approval.requested_at.desc())
    ).scalars().first()
    proposed = state.get("selection_outcome") == "PROPOSE_PROMOTION"
    if approval is None and proposed:
        return PhaseResult(False, run.state, "no promotion request exists for this run")
    if approval is not None and approval.status == "pending":
        return PhaseResult(False, "APPROVAL_REQUIRED",
                           "the promotion has not been decided yet")
    if not proposed:
        say("deploy", f"{state.get('selection_outcome')}: the champion is unchanged and this "
                      "challenger is scored as shadow", True)

    model_version = session.get(ModelVersion, state["model_version_id"])
    if model_version is None:
        return PhaseResult(False, "ESCALATED",
                           f"catalogue version {state['model_version_id']} is missing")

    monitor = MonitoringService(session, store=store, config=pipeline.config,
                                governance=pipeline.governance)
    mlflow_version = state.get("mlflow_version")
    feature_cols = state["feature_columns"]

    # -- rebuild the data exactly as it was split ------------------------------
    dataset = read_parquet(store.path(state["features_uri"])).merge(
        read_parquet(store.path(state["labels_uri"])),
        on=["customer_id", "snapshot_date"], how="inner",
    )
    split = apply_split(dataset, SplitSpec(**state["split"]))
    say("rebuild", f"restored {len(split.test)} test rows from stored features and labels",
        True)

    registry = ModelRegistry(settings().mlflow_tracking_uri)
    promoted: dict[str, Any] | None = None

    approved = approval is not None and approval.status == "approved"
    if approved and mlflow_version:
        promoted = registry.promote(
            state["model_name"], mlflow_version,
            approval_id=str(approval.id), approved_by=str(approval.decided_by),  # type: ignore[union-attr]
        )
        say("deploy", f"champion -> v{mlflow_version} "
                      f"(previous {promoted['previous_champion']})", True)
        monitor.set_status(model_version, "champion",
                           promoted_by=approval.decided_by if approval else None)
        pipeline._advance("DEPLOYED", stage="deploy")

        champion = registry.load(state["model_name"], "champion")
        sample = split.test.head(200)
        scores = champion.predict_proba(sample[feature_cols])[:, 1]
        if force_smoke_failure:
            scores = scores * 0 + 5.0
        smoke = smoke_test_scores(scores)
        say("smoke", "passed" if smoke["passed"] else f"failed: {smoke['problems']}",
            smoke["passed"])

        if not smoke["passed"]:
            previous = promoted["previous_champion"]
            if previous:
                registry.rollback(state["model_name"], previous_version=previous,
                                  reason="post-deployment smoke test failed")
                say("rollback", f"champion restored to v{previous}", True)
            else:
                say("rollback", "no previous champion to restore; alerting instead", False)
            pipeline.governance.alert(
                alert_type="smoke_test_failed", severity="critical", run_id=run.id,
                message="post-deployment smoke test failed; champion rolled back",
                evidence=smoke,
            )
            pipeline.finish("ESCALATED", ok=False,
                            reason="smoke test failed after promotion")
            return PhaseResult(False, "ESCALATED", "smoke test failed after promotion",
                               {"smoke": smoke})
    elif approved:
        say("deploy", "approved but not promoted: the challenger was never registered", False)
        pipeline.governance.alert(
            alert_type="promotion_blocked", severity="critical", run_id=run.id,
            message="promotion was approved but the challenger is not in the registry",
            evidence={"approval_id": str(approval.id),
                      "registry_error": state.get("registry_error", "")},
        )
    elif approval is not None:
        say("deploy", "rejected: the champion is unchanged and shadow scores archived", True)

    # -- score ------------------------------------------------------------------
    model = registry.load(state["model_name"], "champion") if promoted else None
    if model is None:
        # Nothing was promoted, so the scores are explicitly shadow. Loading the challenger
        # by version keeps them traceable to the artifact that produced them.
        model = (
            registry.mlflow.sklearn.load_model(
                f"models:/{state['model_name']}/{mlflow_version}"
            )
            if mlflow_version
            else None
        )
    if model is None:
        # Say *why* there is no artifact. The cause is almost always the registration
        # failure recorded five stages earlier, and an operator reading only "no model
        # artifact" has to go and find it.
        reason = "no model artifact is available to score with"
        if state.get("registry_error"):
            reason += f"; the challenger was never registered: {state['registry_error']}"
        pipeline.finish("ESCALATED", ok=False, reason=reason)
        return PhaseResult(False, "ESCALATED", reason)

    set_type = OFFICIAL if promoted else SHADOW
    explanations = explain(model, split.test, feature_cols)
    predictions = score(
        model, split.test, feature_cols=feature_cols,
        model_name=state["model_name"], model_version=str(mlflow_version or "unregistered"),
        feature_set_version="fsv=1", set_type=set_type, threshold=state["threshold"],
        reasons=dict(explanations.per_customer),
    )
    summary = predictions.summary()
    say("predict", f"{summary['rows']} {set_type} scores, "
                   f"{summary['high_risk_count']} high risk", True)

    pred_dir = store.predictions_dir(state["model_name"], str(mlflow_version or "0"),
                                     summary["score_date"], set_type)
    write_parquet(predictions.frame.drop(columns=["top_reasons"]), pred_dir)
    stored = monitor.record_predictions(model_version, predictions, run=run)
    say("predict", f"{stored} rows recorded in the catalogue as {set_type}", True)

    # -- drift and retraining triggers -----------------------------------------
    drift = monitor.report_drift(
        model_version, reference_uri=state["reference_uri"], current=split.test,
        current_ref=store.uri(pred_dir),
        scores=predictions.frame["churn_probability"].to_numpy(),
        high_risk_threshold=pipeline.config["risk_bands"]["high"],
        top_features=top_raw_features(explanations.global_importance, feature_cols),
        run=run, reference_sample=split.train,
    )
    ranked = sorted((f for f in drift.result.features if f.psi == f.psi),
                    key=lambda f: -f.psi)[:3]
    say("drift", "highest PSI: " + ", ".join(f"{f.feature} {f.psi:.3f}" for f in ranked), True)
    if drift.result.score:
        say("drift", f"score PSI {drift.result.score.psi:.3f}; "
                     + (f"FLAGGED - {drift.result.score.reason}"
                        if drift.result.score.flagged else "score distribution stable"))

    from agents.retraining import MonitoringState

    retrain = monitor.evaluate_retraining(
        MonitoringState(
            drift=drift.result,
            labelled_rows_at_training=len(split.train),
            labelled_rows_now=len(dataset),
            last_trained_at=run.started_at,
            feature_catalog_version="fsv=1",
            model_feature_catalog_version="fsv=1",
            data_quality_ok=(state.get("quality_score") or 0.0)
            >= pipeline.config["quality_gate"]["min_quality_score"],
            data_quality_detail=f"quality score {state.get('quality_score')}",
        ),
        run=run,
    )
    say("retrain", f"{', '.join(retrain.rule_ids) or 'no trigger'} -> "
                   f"{'retrain' if retrain.should_retrain else (retrain.blocked_by or 'wait')}",
        True)

    # A trigger that fires has to start something, or RT-1..RT-9 are a report rather than a
    # control (FR-074, PO-06). The queued run goes through the same gates as any other, so
    # nothing is promoted without a person.
    queued = monitor.start_retraining(retrain, source=run)
    if queued is not None:
        say("retrain", f"queued {queued.run_key} to retrain", True)

    # -- report -----------------------------------------------------------------
    sheet = FactSheet()
    sheet.add("customers_scored", summary["rows"], definition="rows in the prediction set")
    sheet.add("high_risk_count", summary["high_risk_count"],
              definition="customers in the high-risk band", provenance="Model output")
    test_pr_auc = ((state["selection"].get("test") or {}).get("values") or {}).get("pr_auc")
    if test_pr_auc is not None:
        sheet.add("test_pr_auc", round(test_pr_auc, 4),
                  definition="PR-AUC on the held-out test cutoff", provenance="Model output")
    sheet.add("valid_churn_rate", round(split.class_balance.get("valid", 0.0), 4),
              definition="observed churn rate at the validation cutoff")

    report = build_report(
        title=f"Churn report for {run.run_key}", run_key=run.run_key,
        report_type="business",
        sections=[
            {"heading": "Churn risk this scoring run", "provenance": "AI interpretation",
             "text": "{{fact:customers_scored}} customers were scored. "
                     "{{fact:high_risk_count}} of them are in the high-risk band. "
                     "The observed churn rate at the validation cutoff was "
                     "{{fact:valid_churn_rate}}."},
            {"heading": "Model confidence", "provenance": "Model output",
             "text": ("The selected model scored {{fact:test_pr_auc}} PR-AUC on the test "
                      "cutoff, which was used once."
                      if test_pr_auc is not None
                      # The claim checker refuses a digit with no fact behind it, so the
                      # sentence changes rather than quoting a number that was not measured.
                      else "The test metric was not recorded for this run.")},
            {"heading": "Main associations", "provenance": "Model output",
             "text": "The features most associated with a high score are: "
                     + ", ".join(d["feature"] for d in explanations.global_importance[:5])
                     + f". Method: {explanations.method}."},
        ],
        sheet=sheet,
    )
    path = report.write(store.reports_dir(run.run_key) / "business_report.html")
    store.write_json(store.reports_dir(run.run_key) / "fact_sheet.json", sheet.to_dict())
    report_row = monitor.record_report(
        run, report_type="business", storage_uri=store.uri(path),
        claim_check=report.claim.to_dict(),
    )
    say("report", f"claim check {'passed' if report.claim.passed else 'FAILED'}; "
                  f"{report.claim.facts_verified} facts verified; registered as "
                  f"{report_row.id}", report.claim.passed)

    if approved and not promoted:
        pipeline.finish("ESCALATED", ok=False,
                        reason="promotion approved but the challenger was never registered")
        return PhaseResult(False, "ESCALATED",
                           "promotion approved but the challenger was never registered")

    pipeline.finish("PUBLISHED", ok=True, reason="")
    return PhaseResult(True, "PUBLISHED", "", {
        "predictions": summary,
        "drift_report_id": str(drift.report_id),
        "report_id": str(report_row.id),
        "retraining": retrain.to_dict(),
        "promoted": promoted,
    })
