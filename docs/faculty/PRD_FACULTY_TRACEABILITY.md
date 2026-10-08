# PRD to manuscript traceability

Team **26FSPDSCPMID4096**. This matrix ties each PRD requirement group to the code that implements
it, the test that checks it, the manuscript section that describes it, and the evidence a reader can
open. Its purpose is the one the continuation brief gives it: to show that every claim the
manuscript makes about TRIAD is carried by the system, and that nothing the system lacks is
claimed.

How to read it:

- **Status** is copied from `docs/implementation/REQUIREMENTS_TRACEABILITY.md`, which is the
  per-requirement record. A row here is never more optimistic than that file.
- **Manuscript section** numbers follow `docs/research/README.md` (the faculty template order).
- **Evidence** is an artifact in the repository or the database: a test, a recorded run, or an
  experiment's stored output under `experiments/EXP-n/`. Experiment outputs are referred to by
  experiment, because each re-run writes a new timestamped folder and `latest.json`.
- A requirement that is **not met** appears with the section that *states* it is not built, so the
  matrix also traces the limitations the manuscript reports.

## Matrix

| PRD requirement | Module | Implementation | Test | Manuscript section | Evidence | Status |
| --- | --- | --- | --- | --- | --- | --- |
| FR-001..003 file ingestion (CSV, JSON, Parquet) | M02 | `dataeng/ingest.py` | `tests/unit/test_ingest.py`, `test_parquet_io.py` | 7 | Dataset versions in `dataset_versions`; EXP-1 | met |
| FR-005, FR-006 storage watching, immutable versions | M02 | `dataeng/ingest.py`, `triad_core/storage.py` | `test_ingest.py`, `test_storage_hashing.py`, `tests/security/test_storage_paths_stay_in_the_data_root.py` | 7, 12 | content hashes on every version | met |
| FR-007 ingest contract validation | M02, M04 | `triad_core/contracts.py` | `test_ingest.py` | 7, 15 | EXP-4 (schema errors refused before training) | met |
| FR-009..015 profiling, outliers, duplicates, quality score | M03 | `dataeng/profile.py` | `tests/unit/test_profile.py` | 7, Eq. 1 | profile JSON per version | met |
| FR-016..020, FR-023 cleaning, type conformance, quarantine, row accounting | M04, M06 | `scala_spark/core/.../CleaningRules.scala` (incl. `conformNumeric`, DEC-36), generated `CleanJob_*` | `CleaningRulesSpec.scala` (TC-019b/c), `GeneratedJobsFixtureSpec.scala` | 7, Table 5, Eq. 2 | EXP-4 (prices as text), EXP-5 (accounting balanced at every null rate) | met |
| FR-021 category vocabulary | M04 | normalisation in `CleaningRules.scala` | `CleaningRulesSpec.scala` | 7 (limitation stated) | - | partly met |
| FR-022 anomaly detection | M03, M04 | `dataeng/profile.py` (`detect_anomalies`, `bin_shares`) | `tests/unit/test_profile.py` (seeded spike, breaches, shift) | 7, Eq. 7 | issues with evidence in every profile | met (DEC-30) |
| FR-025..030 churn features, point-in-time, labels | M05, M06 | `ChurnTransforms.scala`, `LabelJob.scala`, `agents/graph/churn.py` | `ChurnTransformsSpec.scala`, `FeatureCatalogueSpec.scala`, `test_feature_hygiene.py` | 7, 8 | feature catalogue; EXP-9 (leakage) | met |
| FR-031..034 Spark transformations, joins, windows, partitioning | M05, M06 | `ChurnTransforms.scala`, `TriadJob.scala` | `ChurnTransformsSpec.scala` | 9, Table 6 | EXP-2, EXP-3 | met |
| FR-035 caching decisions | M05 | - | - | 20 | - | not met |
| FR-036 execution metrics and shuffle analysis | M05, M16 | `TriadJob.scala`, `StageMetrics.scala`, `/pipelines/{id}/executions` | `StageMetricsSpec.scala` (TC-036), `test_api.py`, `test_queue_and_sandbox.py` | 9, 13, 15 | per-stage metrics on every execution; EXP-3 | met (DEC-29) |
| FR-037 configuration optimisation loop | M11 | `agents/optimizer.py`, `ChurnPipeline._plan_shuffle_partitions` | `tests/unit/test_optimizer.py` | 9, 11 | `spark_setting` and `spark_setting_outcome` decision records | met (DEC-31) |
| FR-038, FR-039, FR-041 template generation, compile, package | M06, M08 | `agents/codegen/`, `scala_spark/build.sbt` | `test_codegen.py`, `GeneratedJobsFixtureSpec.scala` | 6, 9 | gate results on every `code_artifacts` row | met |
| FR-040 typed Dataset operations | M06 | - | - | 9 (stated as untyped) | - | not met |
| FR-042, FR-056 mediated job submission | M06, M07 | `sandbox/runner.py`, `sandbox/service.py` (ADR-0004) | `test_sandbox_runner.py`, `tests/security/test_sandbox_isolation.py` | 9, 12, Eq. 4 | sandbox containers with no network, read-only inputs | met |
| FR-043, FR-057, FR-059 structured errors, diagnosis, repair | M07, M08 | `TriadJob.scala` (`StructuredError`), `triad_core/errors.py`, `agents/graph/stages.py` | `test_error_taxonomy.py`, `test_stage_runner.py` | 6, 11 | EXP-6 (Spark failures classified and retried) | met |
| FR-044..049 leak-free training, candidates, evaluation, tuning, calibration | M09, M10, M11 | `python_ml/triad_ml/train.py`, `evaluate.py`, `tune.py`, `hygiene.py`, `splits.py` | `test_ml.py`, `test_tune.py`, `test_feature_hygiene.py` | 8, 14, Eq. 7-10 | MLflow runs; evaluation JSON per version | met |
| AP-1..AP-8 acceptance policy, PRD 19 | M10 | `train.py` (`select_model`), `evaluate.py` | `test_ml.py`, `test_override_path.py` | 8, 14, 17 | escalations with reason codes; `experiments/policy_floors/` (noise floors) | met |
| PRD 19.4 segment threshold review | M10 | `evaluate.py` (`segment_threshold_review`) | `test_override_path.py` | 8, 17 | evidence bundle of an AP-6 escalation | met (analysis only) |
| FR-050, FR-051 batch prediction, explanations | M13, M10 | `python_ml/triad_ml/predict.py` | `test_ml.py` | 10, 17 | `predictions.top_reasons` | met |
| FR-052..055 planning, permissioned tools, generation, nine gates | M07, M08 | `agents/graph/pipeline.py`, `agents/tools/`, `agents/codegen/` | `test_tool_mediation.py`, `test_codegen.py`, `test_call_signatures.py` | 6, 9, Eq. 5 | EXP-8 (15 of 20 seeded faults stopped before execution) | met |
| FR-058, FR-061 bounded retries, budgets, escalation | M07 | `agents/graph/stages.py`, `queue.py` | `test_stage_runner.py`, `test_queue_and_sandbox.py` | 6, 11 | budget meters per run | met |
| FR-060, FR-070 decision records and search | M07, M15 | `triad_core/models.py` (`Decision`), `frontend/src/pages/Runs.tsx` | `test_api.py` | 11 | decision log on every run page | met |
| FR-062 fix memory | M08 | `agents/graph/stages.py` (fingerprints) | `test_stage_runner.py` | 11 | circuit breaker in EXP-8 | met |
| FR-063 untrusted content | M08 | `agents/llm/` | `test_injection.py` | 12 | - | met |
| FR-064..068, FR-071 reports, charts, claim check | M14 | `python_ml/triad_ml/reports.py` | `test_reports.py` | 10, 17 | claim-checked reports in `reports/` | met |
| FR-069 execution timeline | M15 | `frontend/src/pages/Runs.tsx`, `/pipelines/{id}/logs` | `test_api.py` | 11 | run page timeline | met |
| FR-072 report export | M14, M15 | HTML download; PDF printed from the same document (`frontend/src/pages/Reports.tsx`) | browser check | 10 | - | met (DEC-32) |
| FR-073, PRD 12.2-12.4 enforced state machine | M07 | `agents/graph/state.py`, `queue.set_state` | `test_state_machine.py` | 6, 11 | transitions in `audit_logs` | met |
| FR-074 triggers and scheduling | M07 | `agents/scheduler.py`, `agents/retraining.py` | `test_triggers.py`, `test_worker.py` | 11 | trigger type on every run | met |
| FR-075, PRD 30 approvals and promotion despite a failed check | M18 | `triad_core/governance.py`, `backend/app/api/v1/approvals.py` (incl. `/approvals/{id}/evidence`), `pipelines.py`, `frontend/src/pages/Approvals.tsx` | `test_governance.py`, `test_override_path.py` | 12, 17 | approvals with `group_id`; `override_evidence.json`; the override recorded on the model card; run-2026-10-07-001 | met |
| FR-076, FR-077 registry aliases, rollback, experiment tracking | M12 | `python_ml/triad_ml/registry.py`, MLflow, `backend/app/api/v1/models.py` (rollback to the previous recorded champion only, alert raised) | `test_ml.py`, `test_audit_findings_2026_10.py`, `test_api.py` rollback tests | 8, 10, 12 | champion and challenger aliases; model cards; rollback alerts | met |
| FR-078 tamper-evident audit | M17 | `triad_core/audit.py` | `test_governance.py`, `tests/security/test_refusals_are_audited.py` | 12 | `/audit-logs/verify` | met |
| FR-079 authentication and roles | M01 | `backend/app/security.py`, `deps.py` | `tests/security/test_endpoint_authorisation.py` | 12, Table 7 | role checks on every endpoint | met |
| FR-080, FR-081 monitoring and drift | M16 | `agents/monitoring.py`, `python_ml/triad_ml/drift.py` | `test_monitoring.py`, `test_drift.py` | 10, Eq. 7 | EXP-7 (PSI under seeded drift) | met |
| FR-082, FR-083 cancel, resume, memoisation | M07 | `queue.py`, `pipeline.py` | `test_queue_and_sandbox.py` | 11 | replayed runs | met |
| FR-084 in-app notifications | M15 | `frontend/src/notifications.tsx` (polls approvals and alerts) | browser check, 08-10-2026 | 12 | - | met, in-app only; webhook not built |
| FR-004, FR-008 JDBC ingestion, streaming connector | M02 | - | - | 20, 21 | - | not met |
| ST-05 TLS | - | - | - | 12, 20 | - | not met |
| ST-06, ST-07 sandbox egress and limits | M06 | `sandbox/runner.py` | `tests/security/test_sandbox_isolation.py` | 12 | ADR-0004 | met |
| ST-08 model hash before load | M12 | `registry._load_verified` | `test_audit_findings_2026_10.py` | 12 | `artifact_sha256` on every version | met |
| Trust score (defined post hoc) | - | `docs/research/analysis/trust_score.py` | its `__main__` check | 10, 17, Eq. 6 | computed from stored run evidence | met (analysis) |
| Baselines B1, B2, B3, P | - | `experiments/baselines.py`, `baseline_b2.py`, `baseline_b3.py` | `experiments/harness.py` | 13, 16, Table 11 | EXP-1, EXP-2 | met |

## What the manuscript may and may not say

- A section may describe a row marked **met** as built, citing the evidence column.
- A row marked **partly met** is described with its gap in the same paragraph.
- A row marked **not met** is never described as built. It appears in Section 20 (Challenges and
  Limitations) or Section 21 (Future Research Directions), and where a reader would expect it - for
  example JDBC ingestion in Section 7 - with one sentence saying it is not built.
- A status changes here only after `REQUIREMENTS_TRACEABILITY.md` has changed, and that file
  changes only when a requirement's test passes.
