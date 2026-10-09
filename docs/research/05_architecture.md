# 5. Python-Scala-Spark Ecosystem Architecture

TRIAD is organized as five layers with one job each, crossed by a governance layer that every
other layer must pass through. Fig. 6, the core architecture figure, shows the components and the
three languages; Fig. 7 shows the same system as a stack, from storage at the bottom to the
dashboard through which people inspect and approve its outputs at the top.

**Fig. 6. Agentic Data Science Programming Architecture Integrating Python, Scala, and Apache
Spark.** *(figures/fig06_core_architecture.drawio)*

## 5.1 The three languages, and why each is where it is

**Python** carries everything that benefits from a rich library ecosystem or changes often:
the orchestrator and its agents, profiling through PySpark, the application programming
interface (API), and the whole machine learning layer - preprocessing, five candidate model
families, calibration, evaluation, explanation, and drift measurement, built on scikit-learn,
XGBoost [@chen2016xgboost], and Optuna [@akiba2019optuna], with MLflow as the experiment tracker
and model registry.

**Scala** carries the distributed transformations. Cleaning, feature construction, and labelling
run as compiled Spark jobs. A small core library, `TriadJob`, gives every job the same argument
parsing, schema validation, staging writes, row accounting, metrics, and structured error
reporting, so a generated job contains only its transformation. Compilation is a check in its
own right: a generated job that refers to a column with the wrong type, or calls a method that
does not exist, fails at build time - inside a validation gate - rather than halfway through a
run.

**Apache Spark** executes those jobs [@zaharia2016spark] with the DataFrame and SQL layer
[@armbrust2015sparksql]. Every job writes to a run-scoped staging folder and the platform, not
the job, publishes the result atomically under a content hash. Whether a stage runs in Spark at
all, and in which deployment mode, is decided by routing rules (Section 9).

The boundary between the languages is a file format and a contract, not a function call. Spark
writes Parquet with microsecond timestamps and a manifest declaring the schema, row count, and
content hash; Python reads it back and restores column types from the manifest. Two early defects
were exactly this boundary failing silently - Python wrote nanosecond timestamps that Spark
refused, and a partition column came back as a string so that a join matched nothing while both
row counts looked right - and both are now prevented in shared input-output code rather than at
each call site.

## 5.2 The layers

**Fig. 7. Multi-Layer Analytics Stack.** *(figures/fig07_layer_stack.drawio)*

1. **Presentation.** A React and TypeScript dashboard with eleven pages - datasets, runs, models,
   predictions, reports, approvals, monitoring, experiments, audit, settings, and an overview -
   in which every number carries a provenance badge, and a metric that was never recorded reads
   "not measured" rather than zero.
2. **Service.** A FastAPI application exposing 41 versioned endpoints with role-based access,
   problem-details error responses, cursor pagination, and idempotent run creation. It starts
   runs; it never executes them.
3. **Orchestration.** A worker that claims queued tasks from PostgreSQL under a lease, drives the
   run's state machine, applies routing rules, generates and validates code, and calls tools
   only through a permissioned registry. A scheduler on the same worker evaluates drift and
   schedule triggers and watches landing folders (Section 11).
4. **Compute.** Generated Scala jobs on Spark, each in its own container with no network, started
   by a sandbox runner that is the only component allowed to start containers (Section 12); and
   the Python machine learning worker.
5. **State.** PostgreSQL holds 21 tables - runs, tasks, decisions, approvals, audit entries,
   model versions, predictions, drift reports, metrics, and lineage. Versioned Parquet on a shared
   volume holds raw, clean, feature, label, and quarantine data. MLflow holds experiment runs and
   model artifacts.

**Governance** crosses all five: a fail-closed policy engine evaluated before governed actions,
an approval workflow with separation of duties, and an audit trail in which every entry is
hash-chained to the previous entry of its run (Section 12).

## 5.3 One run, end to end

A run moves through stages P0 to P10 of the pipeline definition: register and check the input
versions, profile them, clean and conform transactions, aggregate to customer-days, expand to
monthly snapshots, compute labels and window features, check the feature contract, build
training sets, and build the scoring set. The run then splits by time with a gap, trains and
calibrates candidates, judges them against the acceptance policy, and - if a candidate beats the
deployed champion with confidence - registers it as a challenger and pauses for approval. A
second phase, which may start days later and rebuilds everything it needs from what the first
phase wrote down, moves the champion alias, runs a smoke test that can roll the alias back,
scores customers, measures drift, and writes a claim-checked report.

Each state transition is committed as it happens, with its audit entry, so a dashboard viewer
sees a run's progress live and a worker that dies mid-run loses nothing: its lease expires, the
task is re-queued, and another claim resumes it.

## 5.4 Deployment

The minimum viable product (MVP) runs as six containers under Docker Compose - PostgreSQL,
MLflow, the API, the orchestrator, the sandbox runner, and the dashboard - built from one
toolchain image that pins the Java, Spark, Scala, sbt, and Python versions. The memory limits
are sized for the evaluation workstation (Section 13), and an optional profile adds a Spark
master and worker for cluster-mode demonstrations.

---

## Evidence (not part of the manuscript)

- 41 endpoints: OpenAPI document of the running API, 04-10-2026 (42 operations, 41 under
  `/api/v1`). 21 tables: `information_schema` excluding LangGraph checkpoint tables and
  `alembic_version`. Eleven pages: `frontend/src/pages/`.
- `TriadJob`: `scala_spark/core/src/main/scala/triad/core/TriadJob.scala`; staging and atomic
  publish: `OutputValidator.scala`, `triad_core/storage.py`.
- Timestamp and partition defects: DEC-05, DEC-11.
- Two phases: DEC-14; per-transition commits: FR-073 row; lease recovery observed on
  run-2026-10-03-002.
- Containers: `docker/compose.yaml`; ADR-0001 (toolchain), ADR-0004 (sandbox runner).
