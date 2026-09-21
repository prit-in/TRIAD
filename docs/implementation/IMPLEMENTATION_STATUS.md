# IMPLEMENTATION_STATUS

Updated: 22-09-2026. **Read this file first when resuming.**

## Current position

Phases 1–7 are complete and gated. A governed run now goes the whole way through the
platform rather than through a script: the API queues it, the orchestrator worker executes
it, it pauses for a person at the approval gate, a second person decides through the API, the
worker carries the decision out, and a dashboard shows the result with provenance on every
number.

| Phase | Gate | Status |
| --- | --- | --- |
| 1. Environment | Phase-1 smoke test passes | **PASSED** — 9/9, evidence in ENVIRONMENT_STATUS.md |
| 2. Ingestion and profiling | UC-01, UC-02 tests pass | **PASSED** — AT-01, AT-02 |
| 3. Spark and Scala | TC-026, TC-030 pass | **PASSED** — 21 ScalaTest cases |
| 4. Python ML | AT-06, AT-07 pass | **PASSED** |
| 5. Agent orchestration | real end-to-end run + ATC-01..05 | **PASSED** — `python -m agents.worker` executes queued runs |
| 6. Evaluation and retraining | drift measured, RT-1..RT-9 evaluated | **PASSED** — drift reports and trigger decisions are rows |
| 7. Dashboard and reporting | AT-03, AT-08, AT-15 | **PASSED** — 37 endpoints, five pages, verified in a browser |
| 8. Testing and optimisation | success criteria audited | **PASSED** — `REQUIREMENTS_TRACEABILITY.md`, and all ten experiments run |
| 9. Documentation and demo | three stable rehearsals | **PASSED** — `run-2026-09-19-006`, `run-2026-09-21-007` and `run-2026-09-21-020` |

Test totals: **458 Python tests**, **21 Scala tests**, **9 smoke checks** (1 skipped by
design), **ruff clean**, and the frontend builds under TypeScript strict mode.

All eight pages the PRD's section 26.1 names now exist. Dataset, Report and Settings were the
three that did not, and building the first of them found that the profile endpoint behind it had
never once succeeded.

## The loop, as it actually behaved

`run-2026-09-18-012`, started through the API and executed by the worker:

```
POST /api/v1/pipelines/run           -> 202, Location: /api/v1/pipelines/run-2026-09-18-012
worker claimed task (run/pipeline)
  [profile ] quality 0.763, 19154 rows, 2 blocking issues
  [clean   ] 18333 rows, quarantined 649, duplicates removed 172, accounting balanced true
  [plan    ] cutoffs 2025-02-01 to 2026-04-01; data ends 2026-06-30   (derived from the data)
  [features] 6422 customer-snapshot rows
  [labels  ] 6422 labelled rows
  [split   ] train 2437 / valid 512 / test 555; churn rate 0.271
  [registry] registered churn_classifier v3 as challenger
  [approval] requested; run paused in APPROVAL_REQUIRED
task finished: APPROVAL_REQUIRED
POST /api/v1/approvals/{id}/decision -> 200 approved, by a second person
worker claimed task (publish/publish)
  [rebuild ] restored 555 test rows from stored features and labels
  [deploy  ] champion -> v3 (previous 2); smoke test passed
  [predict ] 555 official scores, 10 high risk, recorded in the catalogue
  [drift   ] score PSI 0.026; FLAGGED - high-risk share moved from 0.010 to 0.018
  [retrain ] RT-3, RT-5 -> cooldown
  [report  ] claim check passed
task finished: PUBLISHED
```

`[rebuild]` is the part worth noticing: the publish phase is a **separate task** that
reconstructs the split, the model and the drift reference from what the preparation phase
wrote down. Nothing is carried across in memory, so a run can pause for days and a worker can
be restarted or killed in between.

**AT-11 was observed rather than asserted.** The worker was killed mid-task during a restart;
its lease expired, the reaper re-queued the task (`re-queued 1 task(s) whose lease had
expired`), and the worker re-claimed and restarted it.

### What the acceptance policy did once it had a champion to compare against

```
[compare] champion v3 scored on the same test cutoff
[select ] KEEP_CHAMPION: challenger does not beat the champion with confidence:
          difference 0.0000, lower bound 0.0000
```

That is correct, and it is new. Until this session selection was never given the champion's
scores, so AP-8 had nothing to compare against and every run behaved as if it were the first.
A challenger trained on the same data now correctly fails to displace the champion — and a
run that proposes nothing no longer asks anyone to approve a promotion.

## Reproduce

```
docker compose -f docker/compose.yaml up -d postgres mlflow api orchestrator frontend
docker compose -f docker/compose.yaml --profile tools run --rm -T \
    -e TRIAD_SEED_PASSWORD=<choose one> dev python scripts/seed_demo.py
docker compose -f docker/compose.yaml --profile tools run --rm -T \
    -e TRIAD_SEED_PASSWORD=<the same one> dev python scripts/api_smoke.py --start-run
```

The dashboard is on <http://localhost:5173>, the API on <http://localhost:8010>, and
`scripts/run_demo.py --customers 800 --months 30` runs the whole scenario in one process
without the worker.

## What exists and is verified

- **Environment**: `triad-base:0.1` (Temurin 17.0.20.1, Spark 4.1.3 / Scala 2.13.17,
  Python 3.11.16, sbt 1.13.0, PySpark 4.1.3), Compose stack, Postgres + MLflow healthy,
  21 tables migrated by Alembic, `requirements.lock.txt` frozen.
- **triad_core**: config, the PRD's tables, hashing and error fingerprints, the hash-chained
  audit (one chain per run, appended under an advisory lock), the fail-closed policy engine,
  storage with atomic commit and manifests, contracts with row accounting, the error taxonomy,
  Spark-safe Parquet IO including partition-type restoration, governance.
- **dataeng**: ingestion and Spark profiling.
- **scala_spark**: `TriadJob` with per-job metrics, `ChurnTransforms`, `CleaningRules`, and the
  three job templates with declared slots; 21 fixture tests including the post-cutoff sentinel
  and the label maturity rule.
- **python_ml**: splits with the gap rule, candidates, the metric suite with bootstrap
  intervals, AP-1..AP-8 **including the champion comparison**, the 18.4 selection procedure,
  hygiene and leakage checks, the MLflow registry with aliases and rollback, predictions with
  risk bands and explanations, claim-checked reporting, PSI/KS/chi-square drift.
- **agents**: the tool registry, the LLM adapter with budgets and redaction, template slots
  with out-of-slot detection and parse-time checks, the nine gates, deterministic routing,
  the PostgreSQL queue with leases, a reaper and race-safe run keys, the bounded stage loop
  with playbook repair and a circuit breaker, retraining triggers RT-1..RT-9 with the 19.3
  validity gate, the Monitor service, **the churn run as two phases**, and **the worker**.
- **backend**: 37 endpoints under `/api/v1` — argon2 passwords, HS256 tokens with a start-up
  check that refuses an unusable signing key, RFC 9457 Problem Details, cursor pagination,
  idempotent run creation, and role separation decided by the policy rather than by the route.
- **frontend**: five pages (dashboard, runs, models, predictions, approvals) in React 19 +
  Vite + TypeScript strict. Every panel carries a provenance badge, a metric that was never
  recorded reads "not measured", and a Viewer is served aggregates by the server.
- **sandbox**: the runner with container controls, secret refusal, run-scoped writable mounts,
  and an honest `isolation="process"` fallback.

## Next exact step

Re-run EXP-5. Three of its four conditions escalated on MISCALIBRATED and the database held no
acceptance decision for any of them, so whether that is the policy correctly refusing an
uncalibratable model or a calibration chain giving up too early cannot be told from what was
recorded. The decision record is now written on the escalation path with its evidence - the
measured ECE, the Brier skill, and which remedies the chain tried - so a re-run can answer it.
Nothing else in the project is waiting on an unanswered question.

## Not started

- FR-004 JDBC ingestion, FR-008 streaming connector, FR-022 anomaly detection in cleaning,
  FR-024 automated feature suggestions, FR-035 caching decisions, FR-037 the Spark optimisation
  loop, FR-040 typed Datasets, FR-072 PDF export (HTML only), FR-084 alert delivery. All are
  Should or Could; no Must is unimplemented.
- Schedule and data-arrival triggers (FR-074 in part). `watch_folder` exists and nothing runs it
  on a timer; manual and retraining triggers both work end to end.
- Two error classifications EXP-6 found are still wrong: an out-of-memory failure is classified
  `UNKNOWN` so its remedy never fires, and a coursier download failure is classified
  `SCALA_COMPILE_ERROR`, which prescribes regenerating code that was never the problem.

## Done since the last revision of this document

- **All ten experiments have been run**, and three of them reported a result that was then
  deleted as false rather than kept: EXP-8's "20 of 20 faults detected" was fifteen real
  detections and five jobs failing to compile on the experiment's own invalid Scala identifier;
  EXP-5's "hypothesis held" was a monotone trend asserted from one surviving data point; EXP-9's
  first run measured a generator in which a correct model and a leaking one are
  indistinguishable. EXP-2 and EXP-3 had never run at all - both passed `valid_cutoffs` to a
  `SplitSpec` whose field is `valid_cutoff`.
- **The Dataset, Report and Settings pages exist**, with an admin-only `GET /settings` behind
  the last of them serving the policy files, the budget ceilings, the redacted connection
  targets and the accounts.
- **`dataset_versions.profile_uri` and `.quality_score` are written.** They were NULL on all 130
  rows, so `GET /datasets/{ref}/versions/{n}/profile` - the whole of FR-068's API surface -
  refused every request it could ever be given. Verified on `run-2026-09-21-020`: the endpoint
  now answers 200 and the page draws the profile.
- **An escalating run records its acceptance decision.** It used to be written only on the paths
  that continued, so a run stopped by the policy left a one-line reason and none of the numbers
  behind it.
- **The login blockage is fixed and measured as fixed.** During `run-2026-09-21-020`, with a
  transaction idle for 12 minutes, eight consecutive logins returned 200 in 0.18-0.45s, and 17
  probes across the run had a worst case of 2.0s. The failure being compared against was three
  logins queued for 4:32, 3:08 and 1:47.
- **Comparing any two model versions** (FR-066), with both bootstrap intervals and a note on
  every row where they overlap.
- **Optuna tuning** runs: `tune.py`, the `tuning:` block, finalists only.
- **A fired trigger starts a run** (`start_retraining`).
- **Replay mode (ATC-18)**: `scripts/replay_run.py` exists, `ChurnPipeline._replay_scala` reads
  the stored artifact back and refuses code whose hash no longer matches, and
  `pipeline_runs.input_versions` is now written so a replay can read the same bytes.
- **The prompt-injection test (ATC-13)** is executable, and found a class the scanner missed:
  chat-template control tokens are now flagged and broken up before fencing.
- **The tool registry is on the runtime path (FR-053)**: every Spark submission and catalogue
  commit goes through `REGISTRY.call`, which checks the agent, the argument schema and - for
  `sandbox.run` - the governance policy, and appends the call to the run's hash chain.
- **`/metrics` exists.** It was documented in `main.py` and served nowhere;
  `prometheus-client` was an unused dependency. It now reads its numbers from the database on
  each scrape, because the work it describes is done by the worker and not by the API.
- **Curve data is persisted** (`reports/<run_key>/evaluation.json`, served at
  `GET /models/{ref}/versions/{id}/evaluation`): ROC, precision-recall, calibration bins and a
  lift/gain sweep, plus bootstrap intervals, per-segment metrics and feature importance. All of
  it was computed and discarded before, which made FR-065 unmeetable without inventing numbers.
- **`metrics.ci_low`/`ci_high` and `metrics.segment` are written.** All three were columns that
  nothing filled.
- **The `experiments` table is written** (FR-077).
- **The approval queue is role-gated.** `GET /approvals` and `GET /approvals/{id}` had no role
  dependency, so any authenticated account including a viewer could read every pending
  promotion.

## Known limits, stated rather than hidden

| Limit | Why | What lifting it would take |
| --- | --- | --- |
| **A long transaction touching a shared audit chain can block writers of that chain** (the observed case is **fixed**) | `audit.record` takes `pg_advisory_xact_lock` per chain, and the lock must stay held to commit - it is what stops two writers reading the same chain head and forking it. The observed failure was `churn.prepare` holding its ingest writes, which land in the **system** chain, for the length of a run: measured at one `idle in transaction` for 9m56s with three logins queued at 4:32, 3:08 and 1:47. `run_demo.py` now commits its ingest before the run begins, and the same situation was re-measured on `run-2026-09-21-020` - 12 minutes of open transaction, eight logins in 0.18-0.45s | The general shape remains possible anywhere a long transaction writes to the system chain, and the defence is transaction boundaries rather than a change to the lock |
| One run of a pipeline at a time | Runs share `clean/`, `features/` and `labels/`, and committing supersedes what is there | Version those outputs per run: a storage-layout change, not a lock |
| Chain verification does not detect a whole run's chain being removed | The trail is one chain per run, which is what makes concurrent writes correct | A periodic seal over the chain heads |
| Drift on a single-cutoff scoring window | A month-of-year feature is constant within one month, so its PSI is enormous and says nothing about behaviour | Reported with that note rather than suppressed; the flag still fires, because a feature pinned by a broken source looks identical |
| 10 GB / 100 GB / 1 TB tiers, NFR-PER-02/03, NFR-SCA-01, EXP-2, EXP-3 | 10.96 GB host, 5.27 GB Docker VM, single machine | reported as *not run, environment-limited* (ADR-0002) |
| Live-LLM behaviour | no API key for any provider | the `template_only` path is the default and is fully implemented (ADR-0003) |

## Defects found and fixed (none deferred)

Session 1 found 15 and session 2 found 15 more, every one of them by running the thing rather
than reading it. The ones that mattered most:

- **The audit chain forked.** Appending read the chain head without a lock, so two writers
  chained from the same entry and `verify_chain` reported tampering that had not happened.
- **Two concurrent runs overwrote each other's outputs**, which is how a feature job failed on
  data that had changed underneath it.
- **Selection never compared against the champion**, so AP-8 never applied.
- **A run that proposed no promotion still asked for one**, and it was approved — the
  acceptance policy said keep the champion and the pipeline promoted anyway.
- **MLflow refused to serialise any model** (skops), so the challenger was never registered
  while the run still reported success.
- **Official predictions were published for a model that was never promoted.**
- **Partitioned and unpartitioned reads disagreed on a key's type**, so the feature/label join
  returned 0 rows from 6422 on each side while both counts looked right.
- **Run keys were numbered from a row count**, so two concurrent creators collided.

The full list with causes and fixes is in `docs/implementation/ENVIRONMENT_STATUS.md` (F-1 to
F-18) and `docs/implementation/DECISIONS.md` (DEC-01 to DEC-19).

## Last verified commands

```
docker compose -f docker/compose.yaml --profile tools run --rm -T dev python -m pytest tests/unit -q
  -> 458 passed
docker compose -f docker/compose.yaml --profile tools run --rm -T dev python -m ruff check .
  -> All checks passed
docker compose -f docker/compose.yaml --profile tools run --rm -T dev     python scripts/run_demo.py --customers 400 --months 24
  -> exit 0, run-2026-09-21-020 in 818s; KEEP_CHAMPION, 14 segment metric sets,
     1252 audit entries across 80 chains, verified; logins served throughout
docker compose -f docker/compose.yaml exec -T frontend npm run build
  -> built in 9.71s, 332 kB / 102 kB gzipped
curl -s http://localhost:8010/metrics
  -> 200, 22 series read from the database
docker compose -f docker/compose.yaml --profile tools run --rm -T dev python -m experiments.exp04_schema_errors
  -> partial, hypothesis held: rename caught at ingest, price-as-text caught at clean
docker compose -f docker/compose.yaml --profile tools run --rm -T dev python -m experiments.exp06_spark_failures
  -> partial, hypothesis NOT held; see the EXP-6 result for the four findings it produced
docker compose -f docker/compose.yaml --profile tools run --rm -T dev python -m experiments.exp07_drift
  -> partial, hypothesis held: 0/6 false alarms; PSI blind to concept drift, RT-1 catches it
docker compose -f docker/compose.yaml --profile tools run --rm -T dev python -m experiments.exp09_leakage
  -> completed, hypothesis held: control 0.4530 PR-AUC unblocked, both leaks blocked
docker compose -f docker/compose.yaml --profile tools run --rm -T dev python -m experiments.exp10_governance
  -> completed, hypothesis held: 4 of 4 bypass attempts blocked, chain verified
```

Not yet re-run since the changes above: the Scala test suite and `scripts/api_smoke.py`. They
are listed here as *not re-verified* rather than carried over from an older revision.
