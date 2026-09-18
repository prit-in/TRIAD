"""One governed end-to-end run (PRD section 55 demonstration scenario).

    generate data -> register -> profile -> clean (Scala) -> features + labels (Scala)
    -> hand off to Python -> train candidates -> select -> register challenger
    -> request approval -> approve as a second person -> promote -> smoke test
    -> publish predictions -> claim-checked report -> verify the audit chain

Every step uses the real services: real Spark jobs through the sandbox, real MLflow, real
approvals in PostgreSQL. Nothing here fakes a result; if a step fails the script says so and
stops with a non-zero exit code.

    python scripts/run_demo.py --customers 600 --months 20
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.graph import queue as q
from agents.graph.pipeline import ChurnPipeline
from triad_core import audit
from triad_core.config import settings
from triad_core.db import session_scope
from triad_core.governance import EvidenceBundle
from triad_core.models import User
from triad_core.storage import storage

SCALA_ROOT = settings().repo_root / "scala_spark"


def log(step: str, message: str, *, ok: bool | None = None) -> None:
    mark = "" if ok is None else ("  OK  " if ok else " FAIL ")
    print(f"[{step:<22}]{mark} {message}", flush=True)


def ensure_jars() -> None:
    jobs = SCALA_ROOT / "jobs" / "target" / "scala-2.13"
    core = SCALA_ROOT / "core" / "target" / "scala-2.13"
    if any(jobs.glob("*.jar")) and any(core.glob("*.jar")):
        log("build", "packaged JARs already present", ok=True)
        return
    log("build", "packaging Scala jobs (sbt core/package jobs/package)")
    proc = subprocess.run(
        ["/opt/sbt/bin/sbt", "-batch", "core/package", "jobs/package"],
        cwd=SCALA_ROOT, capture_output=True, text=True, timeout=3600,
    )
    if proc.returncode != 0:
        log("build", proc.stdout[-2000:], ok=False)
        raise SystemExit("sbt packaging failed")
    log("build", "JARs built", ok=True)


def generate_data(out_dir: Path, *, customers: int, months: int, seed: int) -> dict[str, Path]:
    from datasets.synthetic.generator import GeneratorConfig, SyntheticGenerator

    cfg = GeneratorConfig(
        customers=customers, months=months, seed=seed,
        null_rate_key=0.03,            # the PRD's "3% of lines lack a customer ID"
        duplicate_rate=0.01,
        invalid_price_rate=0.005,
        injection_strings=True,        # ATC-13 material, treated as data
    )
    written = SyntheticGenerator(cfg).write(out_dir)
    log("data", f"generated {len(written)} synthetic tables in {out_dir}", ok=True)
    return written


def spark_session():
    from pyspark.sql import SparkSession

    s = (
        SparkSession.builder.appName("triad-demo")
        .master(settings().spark_master_url)
        .config("spark.sql.ansi.enabled", "true")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.parquet.outputTimestampType", "TIMESTAMP_MICROS")
        .config("spark.sql.shuffle.partitions", "8")
        .config("spark.ui.enabled", "false")
        .config("spark.driver.memory", "1g")
        .getOrCreate()
    )
    s.sparkContext.setLogLevel("ERROR")
    return s


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the TRIAD demonstration end to end.")
    parser.add_argument("--customers", type=int, default=600)
    parser.add_argument("--months", type=int, default=24,
                        help="months of synthetic history; below 24 the 365-day feature "
                             "window plus the 90-day horizon leave too few cutoffs")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--reject", action="store_true",
                        help="reject the promotion instead of approving it (AT-13)")
    parser.add_argument("--fail-smoke", action="store_true",
                        help="force the post-deployment smoke test to fail (AT-14 / ATC-12)")
    args = parser.parse_args()

    started = time.perf_counter()
    cutoff_list = _check_window_is_usable(args.months)
    store = storage()
    demo_root = store.root / "demo" / uuid.uuid4().hex[:8]
    ensure_jars()
    tables = generate_data(demo_root / "source", customers=args.customers,
                           months=args.months, seed=args.seed)

    summary: dict[str, Any] = {"steps": []}

    with session_scope() as session:
        # --- 1. register the run and the datasets ----------------------------
        run = q.create_run(
            session,
            pipeline_name="churn_v1",
            trigger_type="manual",
            triggered_by="demo",
            idempotency_key=f"demo-{uuid.uuid4()}",
            config={"demo": True, "customers": args.customers, "months": args.months},
        )
        log("run", f"created {run.run_key}", ok=True)

        from dataeng.ingest import DatasetRegistry

        registry = DatasetRegistry(session, store)
        registered: dict[str, str] = {}
        for name, path in tables.items():
            registry.register_source(name, entity_type=name, source_type="parquet")
            files = sorted(Path(path).glob("*.parquet"))
            if not files:
                continue
            result = registry.register_version(name, files[0], actor="demo")
            if result.version_id:
                registered[name] = result.storage_uri or ""
        log("ingest", f"registered {len(registered)} dataset versions", ok=True)
        summary["steps"].append({"step": "ingest", "datasets": len(registered)})

        pipeline = ChurnPipeline(session, run, store=store)
        spark = spark_session()

        try:
            # --- 2. profile -------------------------------------------------
            profile_out = pipeline.stage_profile(
                spark, "transactions", registered["transactions"], fmt="parquet"
            )
            if not profile_out:
                log("profile", "profiling failed", ok=False)
                pipeline.finish("ESCALATED", ok=False, reason="profiling failed")
                return 1
            log("profile",
                f"quality {profile_out['quality_score']:.3f}, "
                f"{profile_out['row_count']} rows, "
                f"{profile_out['blocking_issues']} blocking issues", ok=True)
            pipeline._advance("PROFILED", stage="profile")
            summary["steps"].append({"step": "profile", **profile_out})

            # --- 3. clean (Scala job in the sandbox) ------------------------
            clean_dir = store.clean_dir("transactions", 1)
            clean_out = pipeline.stage_scala_job(
                stage="clean",
                template_id="CleanJob",
                inputs={"source": registered["transactions"]},
                output_dir=clean_dir,
                params={"dedup_keys": "order_id,line_id"},
                # Rejected rows are written, never dropped, and the share of affected rows
                # is held against the cleaning.execute limits before the output is committed.
                quarantine_dir=store.quarantine_dir("transactions", 1, run.run_key),
                policy_action="cleaning.execute",
                input_rows=profile_out.get("row_count", 0),
            )
            if not clean_out:
                pipeline.finish("ESCALATED", ok=False, reason="cleaning failed")
                log("clean", "cleaning failed; see the escalation ticket", ok=False)
                return 1
            clean_metrics = clean_out.get("job_metrics", {})
            log("clean", f"clean rows {clean_out.get('row_count')}, "
                         f"quarantined {clean_metrics.get('quarantined_rows', '?')}, "
                         f"duplicates removed {clean_metrics.get('removed_duplicates', '?')}, "
                         f"accounting balanced "
                         f"{clean_metrics.get('row_accounting_balanced', '?')}", ok=True)
            summary["steps"].append({"step": "clean", "rows": clean_out.get("row_count"),
                                     **clean_metrics})

            pipeline._advance("PLANNED", stage="plan")

            # --- 4. features and labels -------------------------------------
            cutoffs = _cutoff_window(args.months)
            log("plan", f"{len(cutoff_list)} month-start cutoffs, "
                        f"{cutoffs['first_cutoff']} to {cutoffs['last_cutoff']}; "
                        f"data ends {cutoffs['data_end']}", ok=True)
            features_dir = store.features_dir("churn_features", 1)
            features_out = pipeline.stage_scala_job(
                stage="features",
                template_id="FeatureJob",
                inputs={"transactions": clean_out["uri"], "products": registered.get("products", "")},
                output_dir=features_dir,
                params={"max_window_days": 365, **cutoffs},
                partition_by=["snapshot_date"],
                required_columns=["customer_id", "snapshot_date", "recency_days"],
                input_rows=clean_out.get("row_count", 0),
            )
            if not features_out:
                pipeline.finish("ESCALATED", ok=False, reason="feature job failed")
                log("features", "feature job failed", ok=False)
                return 1
            log("features", f"{features_out.get('row_count')} customer-snapshot rows", ok=True)

            labels_dir = store.labels_dir("churned_90d", 1)
            labels_out = pipeline.stage_scala_job(
                stage="labels",
                template_id="LabelJob",
                inputs={"transactions": clean_out["uri"]},
                output_dir=labels_dir,
                params={"max_window_days": 365, "horizon_days": 90, **cutoffs},
                required_columns=["customer_id", "snapshot_date", "churned_90d"],
                input_rows=clean_out.get("row_count", 0),
            )
            if not labels_out:
                pipeline.finish("ESCALATED", ok=False, reason="label job failed")
                log("labels", "label job failed", ok=False)
                return 1
            log("labels", f"{labels_out.get('row_count')} labelled rows", ok=True)
            pipeline._advance("FEATURES_READY", stage="features")
            summary["steps"].append({
                "step": "features", "feature_rows": features_out.get("row_count"),
                "label_rows": labels_out.get("row_count"),
            })
        finally:
            spark.stop()

        # --- 5. hand off to Python behind the size guard --------------------
        from triad_core.parquet_io import estimate_in_memory_bytes, read_parquet

        estimate = estimate_in_memory_bytes(store.path(features_out["uri"]))
        route = pipeline._route("handoff", kind="handoff", estimated_memory_bytes=estimate)
        log("handoff", f"{route['rule_id']} -> {route['target']} "
                       f"({estimate / 1e6:.1f} MB estimated)", ok=True)

        features = read_parquet(store.path(features_out["uri"]))
        labels = read_parquet(store.path(labels_out["uri"]))
        dataset = features.merge(labels, on=["customer_id", "snapshot_date"], how="inner")
        log("handoff", f"joined {len(dataset)} labelled rows", ok=True)

        # --- 6. train and select --------------------------------------------
        from python_ml.triad_ml.candidates import shortlist, split_feature_types
        from python_ml.triad_ml.splits import apply_split, plan_split
        from python_ml.triad_ml.train import select_model, train_all

        pipeline._advance("TRAINING", stage="train")
        spec = plan_split(sorted(dataset.snapshot_date.unique()), horizon_days=90)
        split = apply_split(dataset, spec)
        log("split", f"train {len(split.train)} / valid {len(split.valid)} / "
                     f"test {len(split.test)}; churn rate "
                     f"{split.class_balance.get('valid', float('nan')):.3f}", ok=True)

        candidates = shortlist(n_rows=len(split.train), n_features=len(dataset.columns))
        models = train_all(candidates, split, seed=args.seed)
        for m in models:
            state = "failed" if m.failed else f"valid PR-AUC {m.valid_pr_auc:.4f}"
            log("train", f"{m.family:<14} {state}", ok=not m.failed)
        pipeline._advance("EVALUATED", stage="evaluate")

        policy = pipeline.config["acceptance_policy"]
        selection = select_model(models, split, policy, segment_cols=["country"]
                                 if "country" in dataset.columns else None)
        log("select", f"{selection.outcome}: {selection.reason}",
            ok=selection.outcome != "ESCALATE")
        summary["steps"].append({"step": "select", **selection.to_dict()})

        if selection.outcome == "ESCALATE":
            pipeline.finish("ESCALATED", ok=False, reason=selection.reason)
            _write_summary(store, run.run_key, summary)
            return 2

        pipeline._advance("SELECTED", stage="select")
        for name, value in (selection.test_metrics.values or {}).items():
            pipeline._record_metric(name, value, split="test")
        for name, value in (selection.selected.valid_metrics.values or {}).items():
            pipeline._record_metric(name, value, split="valid")

        # --- 7. register the challenger in MLflow ---------------------------
        num_cols, cat_cols = split_feature_types(split.train)
        model_name = "churn_classifier"
        version = None
        try:
            from python_ml.triad_ml.registry import ModelRegistry

            mlflow_registry = ModelRegistry(settings().mlflow_tracking_uri)
            registered_version = mlflow_registry.log_candidate(
                model=selection.selected.pipeline,
                name=f"{run.run_key}-{selection.selected.family}",
                params={"family": selection.selected.family, "seed": args.seed,
                        **selection.selected.candidate.params},
                metrics=dict(selection.test_metrics.values or {}),
                input_example=split.valid[num_cols + cat_cols].head(2),
                tags={"run_key": run.run_key, "feature_set_version": "fsv=1",
                      "split": json.dumps(spec.to_dict())},
                register_as=model_name,
            )
            version = registered_version.version if registered_version else None
            if version:
                mlflow_registry.set_challenger(model_name, version)
                log("registry", f"registered {model_name} v{version} as challenger", ok=True)
        except Exception as exc:
            log("registry", f"could not register the challenger: {exc}", ok=False)
            mlflow_registry = None
            summary["steps"].append({"step": "registry", "registered": False,
                                     "error": str(exc)[:500]})

        # The catalogue row is written whether or not MLflow answered, so the run stays
        # traceable and a missing MLflow link is visible rather than implied.
        from agents.monitoring import MonitoringService

        monitor = MonitoringService(session, store=store, config=pipeline.config,
                                    governance=pipeline.governance)
        catalogue_model = monitor.model(
            model_name, target_definition=pipeline.config["label"]["definition"].strip()
        )
        model_version = monitor.register_model_version(
            catalogue_model, run=run,
            algorithm=selection.selected.family,
            mlflow_version=version,
            params=selection.selected.candidate.params,
            feature_set_version="fsv=1",
            training_data_ref=features_out["uri"],
            threshold=selection.selected.valid_metrics.get("threshold", 0.5),
            governance_status="challenger" if version else "candidate",
        )
        # The reference is the training feature distribution plus the score distribution the
        # model produced on the *validation* split: scores on the training rows themselves
        # would be optimistic and would make every later run look like drift.
        valid_scores = selection.selected.pipeline.predict_proba(
            split.valid[num_cols + cat_cols]
        )[:, 1]
        reference_uri = monitor.capture_reference(
            model_version, split.train, num_cols + cat_cols,
            scores=valid_scores,
            high_risk_threshold=pipeline.config["risk_bands"]["high"],
        )
        log("monitor", f"catalogue version {model_version.id} recorded; "
                       f"reference captured from {len(split.train)} training rows", ok=True)

        # --- 8. approval -----------------------------------------------------
        evidence = EvidenceBundle(
            action_type="model_promotion",
            target_ref=f"{model_name}:{version or 'unregistered'}",
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
            model_ref=evidence.target_ref, evidence=evidence
        )
        log("approval", f"requested {approval_id}; run paused in APPROVAL_REQUIRED", ok=True)

        requester = _user(session, "ml_engineer", "requester@triad.test")
        approver = _user(session, "ml_engineer", "approver@triad.test")

        # Separation of duties, demonstrated rather than asserted.
        from triad_core.governance import SelfApprovalRefused

        approval = pipeline.governance.pending_for(run.id)[0]
        approval.requested_by = str(requester.id)
        session.flush()
        try:
            pipeline.governance.decide(approval.id, approver=requester, approved=True,
                                       comment="approving my own request")
            log("approval", "SELF-APPROVAL WAS ALLOWED - this is a defect", ok=False)
            return 3
        except SelfApprovalRefused:
            log("approval", "self-approval refused with 403, as required", ok=True)

        decided = pipeline.governance.decide(
            approval.id, approver=approver, approved=not args.reject,
            comment=("rejected for the demonstration path" if args.reject
                     else "metrics and segment checks reviewed; promote"),
        )
        log("approval", f"decision: {decided.status} by {approver.email}", ok=True)
        summary["steps"].append({"step": "approval", "status": decided.status})

        # --- 9. promote, smoke test, rollback on failure --------------------
        promoted = None
        if decided.status == "approved" and mlflow_registry and version:
            promoted = mlflow_registry.promote(
                model_name, version, approval_id=str(approval.id),
                approved_by=str(approver.id),
            )
            log("deploy", f"champion -> v{version} "
                          f"(previous {promoted['previous_champion']})", ok=True)
            monitor.set_status(model_version, "champion", promoted_by=approver.id)
            pipeline._advance("DEPLOYED", stage="deploy")

            from python_ml.triad_ml.predict import score
            from python_ml.triad_ml.registry import smoke_test_scores

            champion = mlflow_registry.load(model_name, "champion")
            sample = split.test.head(200)
            scores = champion.predict_proba(sample[num_cols + cat_cols])[:, 1]
            if args.fail_smoke:
                scores = scores * 0 + 5.0   # forced out-of-range failure
            smoke = smoke_test_scores(scores)
            log("smoke", "passed" if smoke["passed"] else f"failed: {smoke['problems']}",
                ok=smoke["passed"])
            summary["steps"].append({"step": "smoke", **smoke})

            if not smoke["passed"]:
                previous = promoted["previous_champion"]
                if previous:
                    rolled = mlflow_registry.rollback(
                        model_name, previous_version=previous,
                        reason="post-deployment smoke test failed",
                    )
                    log("rollback", f"champion restored to v{previous}", ok=True)
                    summary["steps"].append({"step": "rollback", **rolled})
                else:
                    log("rollback", "no previous champion to restore; alerting instead",
                        ok=False)
                pipeline.governance.alert(
                    alert_type="smoke_test_failed", severity="critical", run_id=run.id,
                    message="post-deployment smoke test failed; champion rolled back",
                    evidence=smoke,
                )
                pipeline.finish("ESCALATED", ok=False, reason="smoke test failed after promotion")
                _write_summary(store, run.run_key, summary)
                return 4
        elif decided.status == "rejected":
            log("deploy", "rejected: the champion is unchanged and shadow scores archived",
                ok=True)
        elif decided.status == "approved":
            # Approved but unpromotable: the registry never took the model, so there is
            # nothing to move the champion alias to. Scoring continues as *shadow* below.
            log("deploy", "approved but not promoted: the challenger was never registered",
                ok=False)
            pipeline.governance.alert(
                alert_type="promotion_blocked", severity="critical", run_id=run.id,
                message="promotion was approved but the challenger is not in the registry",
                evidence={"approval_id": str(approval.id), "mlflow_version": version},
            )

        # --- 10. predictions --------------------------------------------------
        from python_ml.triad_ml.predict import OFFICIAL, SHADOW, explain, score

        model_for_scoring = selection.selected.pipeline
        # Official scores belong to a promoted champion. An approval that never reached the
        # registry does not make them official, or the score set would claim a provenance
        # the registry cannot confirm (UC-15).
        set_type = OFFICIAL if promoted else SHADOW
        explanations = explain(model_for_scoring, split.test, num_cols + cat_cols)
        reasons = dict(explanations.per_customer)
        predictions = score(
            model_for_scoring, split.test, feature_cols=num_cols + cat_cols,
            model_name=model_name, model_version=str(version or "unregistered"),
            feature_set_version="fsv=1", set_type=set_type,
            threshold=selection.selected.valid_metrics.get("threshold", 0.5),
            reasons=reasons,
        )
        pred_summary = predictions.summary()
        log("predict", f"{pred_summary['rows']} {set_type} scores, "
                       f"{pred_summary['high_risk_count']} high risk", ok=True)
        log("explain", f"method: {explanations.method}", ok=True)
        summary["steps"].append({"step": "predict", **pred_summary})

        from triad_core.parquet_io import write_parquet

        pred_dir = store.predictions_dir(model_name, str(version or "0"),
                                         pred_summary["score_date"], set_type)
        write_parquet(predictions.frame.drop(columns=["top_reasons"]), pred_dir)

        # --- 10b. drift and retraining triggers (PRD sections 19.1, 19.2) -----
        from python_ml.triad_ml.drift import top_raw_features

        drift = monitor.report_drift(
            model_version,
            reference_uri=reference_uri,
            current=split.test,
            current_ref=store.uri(pred_dir),
            scores=predictions.frame["churn_probability"].to_numpy(),
            high_risk_threshold=pipeline.config["risk_bands"]["high"],
            top_features=top_raw_features(explanations.global_importance,
                                          num_cols + cat_cols),
            run=run,
            reference_sample=split.train,
        )
        flagged = drift.result.flagged_features
        ranked = sorted((f for f in drift.result.features if f.psi == f.psi),
                        key=lambda f: -f.psi)[:3]
        log("drift", "highest PSI: " + ", ".join(
            f"{f.feature} {f.psi:.3f}" + (f" ({f.note.split(';')[0]})" if f.note else "")
            for f in ranked) + f" over {len(drift.result.features)} features", ok=True)
        log("drift", ("features flagged: " + ", ".join(flagged)) if flagged
            else "no feature over the RT-2 threshold among the model's top features",
            ok=not flagged)
        if drift.result.score:
            # A flagged signal is a detection, not a failure: the monitor did its job. It is
            # logged without an OK/FAIL marker so it reads as the finding it is.
            score = drift.result.score
            log("drift", f"score PSI {score.psi:.3f}; "
                         + (f"FLAGGED - {score.reason}" if score.flagged
                            else "score distribution stable"))
        summary["steps"].append({"step": "drift", "report_id": str(drift.report_id),
                                 "flagged": flagged,
                                 "max_psi": round(drift.result.max_psi, 6),
                                 "alert_id": str(drift.alert_id) if drift.alert_id else None})

        from agents.retraining import MonitoringState

        # A first run has no matured cutoff yet: those fields stay None and the rules that
        # need them report "not evaluated" rather than passing by default.
        state = MonitoringState(
            drift=drift.result,
            labelled_rows_at_training=len(split.train),
            labelled_rows_now=len(dataset),
            last_trained_at=run.started_at,
            feature_catalog_version="fsv=1",
            model_feature_catalog_version="fsv=1",
            data_quality_ok=profile_out["quality_score"]
            >= pipeline.config["quality_gate"]["min_quality_score"],
            data_quality_detail=f"quality score {profile_out['quality_score']:.3f}",
        )
        retrain = monitor.evaluate_retraining(state, run=run)
        log("retrain", f"{', '.join(retrain.rule_ids) or 'no trigger'} -> "
                       f"{'retrain' if retrain.should_retrain else (retrain.blocked_by or 'wait')}"
                       f"; {retrain.reason}", ok=True)
        summary["steps"].append({"step": "retraining", **retrain.to_dict()})

        # --- 11. claim-checked report ----------------------------------------
        from python_ml.triad_ml.reports import FactSheet, build_report

        sheet = FactSheet()
        sheet.add("customers_scored", pred_summary["rows"],
                  definition="rows in the prediction set")
        sheet.add("high_risk_count", pred_summary["high_risk_count"],
                  definition="customers in the high-risk band", provenance="Model output")
        sheet.add("test_pr_auc", round(selection.test_metrics.get("pr_auc"), 4),
                  definition="PR-AUC on the held-out test cutoff", provenance="Model output")
        sheet.add("valid_churn_rate", round(split.class_balance.get("valid", 0.0), 4),
                  definition="observed churn rate at the validation cutoff")
        sheet.add("feature_rows", int(features_out.get("row_count") or 0),
                  definition="customer-snapshot rows built by the Spark feature job")

        sections = [
            {
                "heading": "Churn risk this scoring run",
                "provenance": "AI interpretation",
                "text": "{{fact:customers_scored}} customers were scored. "
                        "{{fact:high_risk_count}} of them are in the high-risk band. "
                        "The observed churn rate at the validation cutoff was "
                        "{{fact:valid_churn_rate}}.",
            },
            {
                "heading": "Model confidence",
                "provenance": "Model output",
                "text": "The selected model scored {{fact:test_pr_auc}} PR-AUC on the test "
                        "cutoff, which was used once.",
                "table": {
                    "headers": ["Candidate", "Validation PR-AUC", "Outcome"],
                    "rows": [
                        [m.family,
                         "n/a" if m.failed else f"{m.valid_pr_auc:.4f}",
                         "selected" if (selection.selected and m.family ==
                                        selection.selected.family) else "considered"]
                        for m in models
                    ],
                },
            },
            {
                "heading": "Main associations",
                "provenance": "Model output",
                "text": "The features most associated with a high score are: "
                        + ", ".join(d["feature"] for d in explanations.global_importance[:5])
                        + f". Method: {explanations.method}.",
            },
        ]
        report = build_report(
            title=f"Churn report for {run.run_key}", run_key=run.run_key,
            report_type="business", sections=sections, sheet=sheet,
        )
        path = report.write(store.reports_dir(run.run_key) / "business_report.html")
        store.write_json(store.reports_dir(run.run_key) / "fact_sheet.json", sheet.to_dict())
        log("report", f"claim check {'passed' if report.claim.passed else 'FAILED'}; "
                      f"{report.claim.facts_verified} facts verified; written to {path}",
            ok=report.claim.passed)
        summary["steps"].append({"step": "report", **report.claim.to_dict()})

        # --- 12. audit chain ---------------------------------------------------
        chain = audit.verify_chain(session)
        log("audit", f"{chain.entries_checked} entries, chain "
                     f"{'verified' if chain.ok else 'BROKEN'}", ok=chain.ok)
        summary["steps"].append({"step": "audit", "entries": chain.entries_checked,
                                 "ok": chain.ok})

        # A rejected promotion still publishes: shadow scores and a report are the correct
        # outcome, and the governance decision was honoured rather than bypassed. An approval
        # that could not be carried out is different, and ends the run as an escalation.
        blocked = decided.status == "approved" and not promoted
        if blocked:
            pipeline.finish("ESCALATED", ok=False,
                            reason="promotion approved but the challenger was never registered")
            _write_summary(store, run.run_key, summary)
            log("done", f"{run.run_key} escalated: approved promotion could not be carried out",
                ok=False)
            return 5
        pipeline.finish("PUBLISHED", ok=True, reason="")
        q.set_state(session, run, "COMPLETED")

        elapsed = time.perf_counter() - started
        summary.update({
            "run_key": run.run_key,
            "duration_seconds": round(elapsed, 1),
            "budget": pipeline.budget.to_dict(),
            "llm_calls": pipeline.budget.llm_calls,
        })
        _write_summary(store, run.run_key, summary)
        log("done", f"{run.run_key} completed in {elapsed:.0f}s with "
                    f"{pipeline.budget.llm_calls} LLM calls", ok=True)
    return 0


DATA_START = "2024-01-01"     # the synthetic generator's own start date


def _cutoff_window(
    months: int, *, start: str = DATA_START, max_window_days: int = 365, horizon_days: int = 90
) -> dict[str, str]:
    """Month-start cutoffs that are both computable and labellable.

    Two hard edges, both from the PRD rather than from taste:

      * a cutoff needs `max_window_days` of history behind it, or its window features are
        computed over less data than the definition says;
      * a cutoff needs `horizon_days` of data ahead of it, or its label cannot mature -
        which is what `ChurnTransforms.labels` enforces with
        `snapshot_date + horizon <= data_end`.

    `data_end` is the real end of the generated data. Passing the last cutoff instead, as an
    earlier version did, made every snapshot immature and the label job produced no rows.
    """
    import datetime as dt

    data_start = dt.date.fromisoformat(start)
    data_end = _add_months(data_start, months) - dt.timedelta(days=1)

    first = _month_start_on_or_after(data_start + dt.timedelta(days=max_window_days))
    last = _month_start_on_or_before(data_end - dt.timedelta(days=horizon_days))
    return {
        "first_cutoff": first.isoformat(),
        "last_cutoff": last.isoformat(),
        "data_end": data_end.isoformat(),
    }


def _add_months(day, months: int):
    import datetime as dt

    total = day.year * 12 + (day.month - 1) + months
    return dt.date(total // 12, total % 12 + 1, day.day)


def _month_start_on_or_after(day):
    return day.replace(day=1) if day.day == 1 else _add_months(day.replace(day=1), 1)


def _month_start_on_or_before(day):
    return day.replace(day=1)


def _monthly_cutoffs(first: str, last: str) -> list[str]:
    import datetime as dt

    day = dt.date.fromisoformat(first)
    end = dt.date.fromisoformat(last)
    out: list[str] = []
    while day <= end:
        out.append(day.isoformat())
        day = _add_months(day, 1)
    return out


MIN_CUTOFFS = 9   # test, a horizon gap, validation, a horizon gap, and something to train on


def _check_window_is_usable(months: int) -> list[str]:
    """Fail early and say what to do, instead of failing opaquely inside the Spark job."""
    window = _cutoff_window(months)
    cutoffs = _monthly_cutoffs(window["first_cutoff"], window["last_cutoff"])
    if len(cutoffs) < MIN_CUTOFFS:
        needed = months + (MIN_CUTOFFS - len(cutoffs))
        raise SystemExit(
            f"--months {months} yields only {len(cutoffs)} usable month-start cutoff(s) "
            f"({window['first_cutoff']} to {window['last_cutoff']}). A 365-day feature "
            f"window, a 90-day label horizon and the 90-day gap rule need at least "
            f"{MIN_CUTOFFS}. Re-run with --months {needed} or more."
        )
    return cutoffs


def _user(session, role: str, email: str):
    from sqlalchemy import select

    existing = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing:
        return existing
    u = User(email=email, display_name=email.split("@")[0], role=role, is_active=True)
    session.add(u)
    session.flush()
    return u


def _write_summary(store, run_key: str, summary: dict[str, Any]) -> None:
    path = store.reports_dir(run_key) / "demo_summary.json"
    store.write_json(path, summary)
    print(f"\nSummary written to {path}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
