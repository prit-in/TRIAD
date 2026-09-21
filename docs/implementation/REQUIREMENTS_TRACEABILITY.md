# Requirements traceability

What the PRD asks for, against what exists, with the evidence for each claim. Written by
reading the code and the tests rather than by reading the earlier status documents.

**The point of this document is the "not met" column.** A traceability matrix that maps every
requirement to a tick is a matrix nobody checked.

Verified on 22-09-2026 against commit `2d0b1fd`, with 458 Python tests, 21 Scala tests and
9 smoke checks passing, and with all ten experiments run.

## Summary

| Category | Count | Met | Partly met | Not met |
| --- | --- | --- | --- | --- |
| Functional — Must | 65 | 60 | 5 | 0 |
| Functional — Should | 16 | 8 | 3 | 5 |
| Functional — Could | 3 | 1 | 0 | 2 |
| Acceptance tests (AT) | 16 | 12 | 1 | 3 |
| Agentic acceptance (ATC) | 18 | 13 | 1 | 4 |
| Experiments (EXP) | 10 | 10 run | — | 0 |

No Must requirement is unimplemented. Five are partly met, and each one says below exactly
which part is missing.

## Functional requirements

### Ingestion and profiling (FR-001 to FR-015)

| ID | Requirement | State | Evidence |
| --- | --- | --- | --- |
| FR-001..003 | CSV, JSON, Parquet ingestion | met | `dataeng/ingest.py`; `tests/unit/test_ingest.py` |
| FR-004 | Relational (JDBC) ingestion | **not met** (Should) | Nothing reads a database source. `Dataset.source_type` accepts `jdbc`, which makes the gap look smaller than it is |
| FR-005 | Storage watching | met | `DatasetRegistry.watch_folder`; `tests/unit/test_ingest.py` |
| FR-006 | Immutable dataset versioning | met | `copy_immutable` refuses an existing target; duplicate checksums create no new version |
| FR-007 | Ingest contract validation | met | `triad_core/contracts.py`. EXP-4 found it unreachable: `register_version` enforced a contract only when handed one and no caller on the real path ever did. It now resolves the dataset's own contract, and three tests hold the refused / still-registers / no-contract cases |
| FR-008 | Streaming-ready connector interface | **not met** (Could) | No connector abstraction exists |
| FR-009..012 | Schema, semantic types, nulls, cardinality, distributions | met | `dataeng/profile.py`; `tests/unit/test_profile.py` |
| FR-013 | Outlier detection | met | IQR and z-score in `profile.py` |
| FR-014 | Duplicate detection | met | profiling and `CleaningRules.deduplicate` |
| FR-015 | Version comparison and quality score | met | `compare_profiles`, `quality_score` |

### Cleaning (FR-016 to FR-023)

| ID | Requirement | State | Evidence |
| --- | --- | --- | --- |
| FR-016..019 | Nulls, duplicates, quarantine, safe conversion | met | `CleaningRules.scala`; `CleaningRulesSpec` TC-016..TC-023 |
| FR-020 | Text and unit normalisation | met | `CleaningRules.normaliseText`; TC-020 |
| FR-021 | Category vocabulary management | **partly met** (Should) | Values are normalised and unseen categories are handled, but there is no managed vocabulary a person can edit |
| FR-022 | Anomaly detection in cleaning | **not met** (Should) | Rule-based quarantine only |
| FR-023 | Row accounting and cleaning report | met | `input = output + quarantined + removed_duplicates`, enforced in the job and reported. Observed: 18333 + 649 + 172 = 19154 |

### Features and labels (FR-024 to FR-030)

| ID | Requirement | State | Evidence |
| --- | --- | --- | --- |
| FR-024 | Automated feature suggestions | **not met** (Should) | Features come from the catalogue and the templates |
| FR-025 | Domain-aware churn features | met | `ChurnTransforms.windowFeatures`; TC-028 |
| FR-026 | Point-in-time correctness | met | **TC-026**: events dated on or after the cutoff change no feature value |
| FR-027 | Model-specific encoding and scaling | met | `candidates.build_pipeline`; leak-free, fitted inside the pipeline |
| FR-028 | Temporal features | met | recency, frequency, tenure, inter-purchase statistics |
| FR-029 | Feature catalogue metadata | met | `pipelines/feature_catalog/` |
| FR-030 | Churn label generation | met | `ChurnTransforms.labels`; TC-030, TC-030b/c/d including the maturity rule |

### Spark and code generation (FR-031 to FR-043)

| ID | Requirement | State | Evidence |
| --- | --- | --- | --- |
| FR-031..034 | Distributed transformations, joins, windows, partitioning | met | `scala_spark/`, run on real data at every demo run |
| FR-035 | Caching decisions | **not met** (Should) | Nothing decides what to cache |
| FR-036 | Execution metrics and shuffle analysis | **partly met** (Should) | `executions` records duration, exit code, Spark application id and job metrics; there is no shuffle analysis |
| FR-037 | Configuration optimisation loop | **not met** (Should) | The budget counts optimisation cycles; no loop spends them |
| FR-038 | Template-based Scala generation | met | `agents/codegen/templates.py`; out-of-slot changes are a checked property |
| FR-039 | DataFrame and Spark SQL operations | met | the three job templates |
| FR-040 | Typed Dataset operations | **not met** (Should) | Everything is untyped `DataFrame` |
| FR-041 | Compilation and packaging | met | gate 4 runs `core/package jobs/package` (DEC-09) |
| FR-042 | Job submission | met | `sandbox/runner.py`, thin JARs with `--jars` (DEC-04) |
| FR-043 | Structured error reporting | met | `TRIAD_JOB_ERROR` with a structured payload; the repair loop never parses a stack trace |

### Machine learning (FR-044 to FR-051)

| ID | Requirement | State | Evidence |
| --- | --- | --- | --- |
| FR-044 | Leak-free preprocessing | met | transformers fitted inside the pipeline; `hygiene.py`; shuffled-label test |
| FR-045 | Size-guarded feature matrix | met | `estimate_in_memory_bytes` + routing rule LR-06, logged on every run |
| FR-046 | Candidate training | met | `train_all` over the registered families |
| FR-047 | Evaluation suite | met | `evaluate.py` with bootstrap intervals |
| FR-048 | Hyper-parameter tuning | met | `tune.py`, finalists only, budgeted and seeded; `tests/unit/test_tune.py` |
| FR-049 | Calibration and threshold selection | met | isotonic → sigmoid → drop weighting (DEC-03); capacity threshold |
| FR-050 | Batch prediction | met | `predict.py`; rows recorded in `predictions` |
| FR-051 | Model explanations | met | SHAP, coefficients or permutation importance, with the method always named |

### Agents (FR-052 to FR-063)

| ID | Requirement | State | Evidence |
| --- | --- | --- | --- |
| FR-052 | Run planning | met | `agents/routing.py`, LR-01..LR-10, every decision recorded |
| FR-053 | Permissioned tool calls | met (Must) | `agents/tools/pipeline_tools.py` registers the real tools and every Spark submission and catalogue commit goes through `REGISTRY.call`: agent allowlist, argument schema, the `sandbox.run` policy action, and the run's hash chain. Verified on a full run - 6 mediated submissions and 6 mediated commits in `audit_logs`. `tests/unit/test_tool_mediation.py` proves a refusal at each gate stops the work |
| FR-054 | Code generation | met | template-first, slots only |
| FR-055 | Validation gates | **partly met** (Must) | **Seven of the nine run.** Gates 1-4 always did; gates 7, 8 and 9 were performing their checks - contract evaluation, artifact storage under a content hash, execution status - without recording any of them as gates, so `gate_results` showed four where the PRD names nine. They are now recorded. Gate 5 (the Scala fixture suite) and gate 6 (a sandbox dry run on a sample) do not run, and neither is claimed. `tests/unit/test_codegen.py`; EXP-8's `first_failed_gate` agrees, reporting only gates 2 and 4 |
| FR-056 | Mediated execution | met | `sandbox/runner.py` with resource profiles and refusals |
| FR-057 | Structured feedback | met | `JobReport` and `StructuredError` |
| FR-058 | Bounded retries and budgets | met | `agents/llm/budget.py`; circuit breaker on repeated fingerprints |
| FR-059 | Diagnosis and repair | met | `triad_core/errors.py` taxonomy and playbook |
| FR-060 | Decision records | met | every stage writes one; `/pipelines/{id}/decisions`. EXP-5 found the exception: a run stopped by the acceptance policy recorded no acceptance decision at all, so three escalations on MISCALIBRATED left a one-line reason and none of the numbers behind it. The decision is now written whatever it decided, with its rule ids, evidence and validation metrics, before the escalation branch |
| FR-061 | Human escalation | met | escalation tickets, `ESCALATED` state, resume endpoint |
| FR-062 | Fix memory | met (Could) | `fix_memory` table, `lookup_fix` / `remember_fix` |
| FR-063 | Untrusted content handling | met | masking, fencing, injection flags; `tests/unit/test_injection.py` |

### Reporting (FR-064 to FR-072)

| ID | Requirement | State | Evidence |
| --- | --- | --- | --- |
| FR-064 | Metrics reporting | met | `metrics` table, `/models/{id}`, the dashboard |
| FR-065 | Standard charts | met (Must) | `reports/<run_key>/evaluation.json` carries ROC, precision-recall, calibration bins and a lift/gain sweep, served at `GET /models/{ref}/versions/{id}/evaluation`, and `frontend/src/charts.tsx` draws them in plain SVG - ROC, PR, reliability diagram, lift and cumulative gain, confusion matrix, per-family and per-segment bars. Every point is read from the stored document; a version with no document says so rather than drawing empty axes |
| AP-6 | Segment recall gap | **now enforced**, verified: run-2026-09-21-007 recorded **14 segment metric sets** where every previous run recorded none. It never compared anything: `churn.prepare` passed `segment_cols=["country"] if "country" in dataset.columns else None`, and the feature job emits only numeric aggregates, so every run evaluated an empty list. Customer attributes are joined at the hand-off for evaluation and kept out of the model's features. Two tests pin that the gate bites and does not over-fire |
| FR-066 | Model comparison | met (Must) | Bootstrap intervals reach the database - `metrics.ci_low`/`ci_high` were columns nothing filled, so "comparison with confidence intervals" had no intervals. The evaluation document carries every screened family and the champion delta, and the Model page compares any two versions side by side with both intervals and overlaid ROC/PR curves, marking the rows where the two intervals overlap |
| FR-067 | Feature importance | met | in the report and on the predictions page, labelled as association. Now also persisted with its method in the evaluation document, instead of being recomputed each publish and dropped |
| FR-068 | Data quality report | met | the profile document, `/datasets/{id}/versions/{n}/profile` and the Dataset page. Building that page found the endpoint had never once succeeded: `dataset_versions.profile_uri` and `.quality_score` were NULL on all 130 rows, because the profiling stage returned them to a caller that used the score for the quality gate and dropped the rest, and the endpoint refuses when `profile_uri` is unset. `stage_profile` now writes both back; `tests/unit/test_ingest.py::TestTheProfileReachesTheVersionItDescribes` holds it |
| FR-069 | Execution timeline | met | `/pipelines/{id}/logs`, redacted; the run page |
| FR-070 | Agent decision log | met | `/pipelines/{id}/decisions`; the run page |
| FR-071 | Business report with claim verification | met | **no digit reaches the report without a fact sheet entry**; `tests/unit/test_reports.py` |
| FR-072 | Report export | **partly met** (Should) | HTML is served, shown in a sandboxed frame on the Report page and downloadable from it; PDF is not produced, and the page says so where the PDF button would be rather than offering one that 404s |

### Orchestration and governance (FR-073 to FR-084)

| ID | Requirement | State | Evidence |
| --- | --- | --- | --- |
| FR-073 | Persisted run state machine | met | `pipeline_runs`, state changes committed with their audit entry |
| FR-074 | Triggers and scheduling | **partly met** (Must) | Manual triggers work end to end, and a retraining trigger now queues a run. **Schedule ticks and data-arrival triggers are not wired**: `watch_folder` exists but nothing runs it on a timer |
| FR-075 | Approval workflow | met | separation of duties, mandatory comment, expiry; `tests/unit/test_api.py`, `test_governance.py` |
| FR-076 | Registry aliases and rollback | met | aliases never stages (CR-14); rollback restores a recorded version only |
| FR-077 | Experiment tracking | met (Must) | Runs, parameters, metrics and artifacts go to MLflow, and the `experiments` row - split spec, objective, MLflow experiment id - is written per run, whether or not MLflow was reachable |
| FR-078 | Tamper-evident audit log | met | one hash chain per run under an advisory lock; tampering and concurrency both tested |
| FR-079 | Authentication and RBAC | met | argon2, HS256, roles decided by the policy; 62 API tests, most of them refusals. `GET /settings` serves the accounts and their roles to an admin and nobody else, and a test asserts the whole payload carries no credential |
| FR-080 | Monitoring and alerting | met | `alerts` with severity, acknowledgement and audit, and `GET /metrics` now serves 12 Prometheus metric families read from the database on each scrape. It was documented in `main.py` and served nowhere, with `prometheus-client` an unused dependency |
| FR-081 | Drift detection | met | PSI with KS and chi-square; `tests/unit/test_drift.py` |
| FR-082 | Cancel and resume | met | both endpoints, with ownership and state checks |
| FR-083 | Memoisation and incremental reruns | met | `StageRunner._memo_key` over stage, inputs and config hash |
| FR-084 | Internal notifications | **not met** (Could) | Alerts are stored and served; nothing delivers them anywhere |

## Acceptance tests

| ID | What it checks | State |
| --- | --- | --- |
| AT-01, AT-02 | Ingestion and profiling | met — `tests/unit/test_ingest.py`, `test_profile.py` |
| AT-06, AT-07 | Splits and evidence-based selection | met — `tests/unit/test_ml.py` |
| AT-08 | Approval evidence bundle | met — `tests/unit/test_governance.py` |
| AT-10 | Alerts fire | met — drift and retraining alerts observed on real runs |
| AT-11 | A crashed orchestrator strands nothing | met — **observed**, not simulated: the worker was killed mid-task, the lease expired, the reaper re-queued it and the worker restarted it |
| AT-13 | A rejected promotion keeps the champion | met — `run_demo.py --reject`, and the publish phase handles it |
| AT-14 | Rollback after a failed smoke test | met — `run_demo.py --fail-smoke`; the rollback path is in `churn.publish` |
| AT-15 | Claim-checked report | met — `tests/unit/test_reports.py` |
| AT-03 | Dashboard shows a live run | **partly met** — the dashboard reads real runs; the SSE stream is implemented but not exercised by a test |
| AT-04, AT-05, AT-09 | (scale and performance) | **not met** — environment-limited, see ADR-0002 |

## Agentic acceptance tests

| ID | What it checks | State |
| --- | --- | --- |
| ATC-01..05 | Planning, generation, gates, repair, escalation | met — `test_codegen.py`, `test_stage_runner.py` |
| ATC-02 | An agent cannot promote a model | met — only `ModelRegistry.promote` moves the alias, and it refuses without an approval id |
| ATC-03, ATC-04 | Slots only; forbidden APIs refused | met — out-of-slot changes and the API scan are both checked properties |
| ATC-12 | Smoke-test failure rolls back | met — implemented and reachable with `--fail-smoke` |
| ATC-13 | Prompt injection in data | met — `tests/unit/test_injection.py`, four defences tested separately. EXP-10 found a fifth class the scanner read past: chat-template control tokens, now flagged and broken up before fencing |
| ATC-17 | Self-approval refused | met — asserted through the service *and* through the API |
| ATC-18 | Replay with no LLM calls | **met** — verified end to end on run-2026-09-21-007: 3/3 stored artifacts reused with identical content hashes, 0 LLM invocations, and all three outputs identical (clean `061e97b5…`, features `2a97e511…`, labels `ce277092…`). Getting there needed two fixes, because there were two causes: Spark's per-job UUID in every part-file name, and row-to-file assignment that varies with task scheduling. A table's content is now the multiset of its rows |
| ATC-06..11, 14..16 | (various) | **partly covered** by the tests above; not individually mapped |

## Experiments

**All ten have been run.** Results are in `experiments/<EXP-n>/latest.json` and a reading of each
is in `docs/implementation/EXPERIMENTS.md`.

| ID | Hypothesis | What it produced |
| --- | --- | --- |
| EXP-1 | **held** | P finishes with 0 manual steps beyond approval, 15 audit entries and 6 decision records, against baselines that produce none. The only like-for-like quality comparison - same features, same split - is a fifth trial, because P's 23 features and the baselines' 12 are not comparable |
| EXP-2 | **did not hold** | No crossover at any size this machine can hold: pandas finished every rung and beat Spark on all of them. Reported as a failure to observe, not as evidence of absence |
| EXP-3 | **did not hold** | 1.301x from one core to two, 0.789x from two to four, against a 1.6x threshold. The fixed cost is measured and subtracted, which is why the raw numbers flatter the result |
| EXP-4 | **held** | Schema faults are refused at ingest; it also found FR-007's contract enforcement unreachable on the real path |
| EXP-5 | **no verdict** | Row accounting balanced at every null rate and key nulls quarantined exactly as specified; the PR-AUC trend has no verdict because three of four conditions escalated on MISCALIBRATED and produced no PR-AUC |
| EXP-6 | **did not hold** | Four findings, two still open: an out-of-memory failure classified `UNKNOWN`, and a coursier download failure classified `SCALA_COMPILE_ERROR` |
| EXP-7 | **held** | Drift and degradation are detected and the retraining triggers fire |
| EXP-8 | **no verdict** | 15 of 20 seeded faults caught before execution; 5 of 5 forbidden APIs refused at gate 2. Repair rate needs an LLM provider and is recorded as not measured |
| EXP-9 | **held** | The behavioural leakage checks catch what the name list misses |
| EXP-10 | **held** | 4 of 4 governance bypasses blocked; it also found the injection scanner reading past chat-template control tokens |

Three experiments reported a result that was later deleted as false, and each is recorded in
`EXPERIMENTS.md` with what it actually measured: EXP-8's "20 of 20" was 15 real detections and
five jobs failing to compile on the experiment's own invalid Scala identifier; EXP-5's "held"
was a monotone trend asserted from one data point; EXP-9's first run measured a generator in
which a correct model and a leaking one are indistinguishable. Nothing is reported as the
result of an experiment that did not run.

## Non-functional requirements

| Group | State |
| --- | --- |
| Security (NFR-SEC) | Met where testable: argon2, no secret in source or log, redaction, fenced untrusted content, sandbox refusals, separation of duties, tamper-evident audit |
| Reproducibility (NFR-REP) | Met: pinned toolchain read from the image, content hashes, seeded sampler, manifest per output |
| Observability (NFR-OBS) | Met: decision records, audit trail, alerts, execution records |
| Performance and scale (NFR-PER, NFR-SCA) | **Not run** — environment-limited (ADR-0002). Reported as not run, never estimated |

## What a reader should take from this

The governed loop is real and the governance is enforced where it matters: a promotion needs a
second person, an agent cannot move an alias, a report cannot contain a number nobody measured,
and the audit trail detects both tampering and its own concurrency bug.

The gaps are concentrated in one place, and it is not a governance gap:

1. **Autonomy at the edges.** Schedule and data-arrival triggers, the Spark optimisation loop,
   caching decisions and the streaming connector are not built (FR-035, FR-037, FR-074 in part,
   FR-008). This is where the remaining Should-level gaps are concentrated.
2. **Two error classifications EXP-6 found are still wrong.** An out-of-memory failure is
   classified `UNKNOWN`, so the remedy that exists for it never fires; a dependency-download
   failure is classified `SCALA_COMPILE_ERROR`, and regenerating the code cannot fix a network
   problem. Both are recorded in `EXPERIMENTS.md` with the evidence.
3. **Presentation is no longer the gap it was.** All eight PRD pages exist, the charts are drawn
   from stored evaluation documents in plain SVG, and the interface has tabs, a confirmation
   dialog before the destructive Cancel-run POST, toasts, cursor pagination, search, a live SSE
   stream and ARIA attributes throughout. What is still missing is narrow and named: PDF export
   (FR-072) and alert delivery (FR-084).

**FR-053 is now closed.** The tool registry was a real control with real tests that nothing was
routed through; every Spark submission and catalogue commit now goes through it, and a full run
shows twelve mediated calls in the audit trail.

The reproducibility defect EXP-6 found is closed: ATC-18 passes end to end, and NFR-REP-02 has
a definition of "identical content" that a Spark output can actually satisfy.

The item that would most repay attention next is one EXP-6 did not find, because nothing was
looking: **a pipeline run in progress blocks every login.** `churn.prepare` held one
transaction for the length of a run, and the ingest writes inside it take the *system* audit
chain's advisory lock, which `auth.login` also needs. Measured: one transaction idle for 9m56s
with three logins queued behind it, all released the instant the run finished. `run_demo.py`
now commits its ingest first, which removes the observed blockage; the general shape - a long
transaction touching a shared chain - is recorded in the known-limits table because it is
still possible elsewhere.

A closing note on method, because it changes how this document should be read. Nine of the
defects recorded here were found by *running* the system, not by reading it: the audit chain
fork, the pipeline-busy deadlock, the skops trusted-types omission, FR-007's unreachable
enforcement, the generator's broken `rename_column`, the profiler's spurious blocking issue,
three playbook remedies that were computed and discarded, three database columns nothing ever
filled, an injection class the scanner read past, and a content hash that cannot be
reproducible. Every one of them had passing tests around it. A requirement marked *met* here
means an execution produced the evidence, not that a test asserted the intention.
