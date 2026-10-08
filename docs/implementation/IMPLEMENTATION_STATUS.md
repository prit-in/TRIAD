# IMPLEMENTATION_STATUS

Updated: 08-10-2026. **Read this file first when resuming.** The newest handoff below supersedes
everything after it where they disagree.

## Handoff, 08-10-2026 (session closed at the user's request, during the experiment re-run)

### Done in this session (commits b11b6b0 .. the commit that adds this section)

- **Override path proven end to end on run-2026-10-07-001** (10 000 customers): AP-6 escalation
  (tenure_band gap 0.331) -> `POST /pipelines/{id}/override` with a written note -> one approval
  each from ml@ and admin@ (group 65eef591) -> DEPLOYED -> PUBLISHED. v18 hist_gb champion with
  sha and card; 6 814 predictions, all with reasons; claim check passed; audit chain verified
  (1 753 entries). All transitions legal under the enforced state machine.
- **Four defects that run found, fixed:** run keys masked as phone numbers on the timeline;
  policy escalations filed as UNKNOWN (now the PRD 19.4 reason code); model card never recorded
  the approval or the override (now `record_approval_on_card`; run 9's card backfilled); the
  override evidence bundle said only "AP-6 failed" (now gaps, threshold review, calibration, read
  on the page via `GET /approvals/{id}/evidence`).
- **Built (DEC-29..32):** FR-036 per-stage Spark metrics (listener in `TriadJob`, TC-036,
  `GET /pipelines/{id}/executions`, run-page panel); FR-022 anomaly detection in the profiler
  (volume, rule breaches, PSI shift); FR-037 one-setting optimisation loop with before-and-after
  records (`agents/optimizer.py`); FR-072 PDF via print of the same document; FR-084 in-app
  notification bell; approval evidence viewer; promoted-by-override label on the run page.
  Should 11/16 met, Could 2/3 (REQUIREMENTS_TRACEABILITY.md).
- Verified: 725 Python tests passing (1 skipped: needs the Docker CLI in the container), 33 Scala
  tests passing. ruff clean on changed files. Frontend type-checks; checked in the browser.
- Manuscript: PRD_FACULTY_TRACEABILITY.md written; Sections 3, 4, 7, 8, 9, 18, 20, 21, 23 updated
  and within their word ranges; Section 10 is 1 word short until the trust-score results go in.
  Charts for Figs 21-25 and 28: `docs/research/figures/build_charts.py` (host Python, 600 DPI,
  provenance in `charts_provenance.json`); `analysis/business_snapshot.py` and `trust_score.py`
  (now counts override approvals) - both run in the dev container.
- Experiment protocol changes (DEC-33): EXP-2 ladder to 200 000 customers with OOM kills recorded
  as results; EXP-3 listener metrics; EXP-5 at 2 000 customers reading escalated candidates'
  PR-AUC. Batch runner now in the repo: `scripts/run_experiments_batch.sh` (logs to
  `experiments/_logs/`, git-ignored).

### Experiment re-run, as it stood at close (outputs committed except where noted)

| Exp | Result | Note |
| --- | --- | --- |
| EXP-9 | completed, held | |
| EXP-10 | completed, held | |
| EXP-7 | partial, held | |
| EXP-3 | partial, did not hold | listener data: task time 35 s (1 core) -> 45 s (2) -> 108 s (4); no spill; host is an AMD Ryzen 5 2500U, 4 cores / 8 threads, 15 W class - `local[4]` shares physical cores |
| EXP-4 | partial, held | |
| EXP-8 | partial, no verdict | no LLM provider, as before |
| EXP-1 | **failed** | configuration P escalated MISCALIBRATED at EXP-1's small size (AP-5 noise floor); the harness counts an escalated P as "did not complete" |
| EXP-2 | **not finished** | ran 1.5 h on the large rungs; the Docker VM ran out of memory (core services + a 3 GB dev container exceed the 5.27 GB VM). Container stopped at close. `experiments/EXP-2/20261007T230658Z/` holds only config and environment and is not committed |
| EXP-5 | **not run** | failed to start: `OSError: [Errno 12] Cannot allocate memory` while EXP-2 held the VM |
| EXP-6 | **not run** | same |

### Next exact steps, in order

1. **EXP-1 protocol (decide, then DEC-34):** like EXP-5, read P's test PR-AUC from the escalated
   selection (the candidate is scored once on the test cutoff since DEC-27) and report the policy
   outcome beside it, and/or run at a size above AP-5's noise floor (>= 1 300 validation rows).
   Decide from the noise-floor study, not from results.
2. **EXP-2 memory headroom:** before re-running, stop what B2/B3 do not need (`docker compose stop
   orchestrator frontend mlflow`), so the dev container's 3 GB is actually available, or cap the
   ladder's top rung at what the VM holds; restart the services afterwards.
3. Run the rest: `ORDER="EXP-1 EXP-2 EXP-5 EXP-6" bash scripts/run_experiments_batch.sh`
   (nothing else running; EXP-6 gets the socket automatically). Then
   `python -m experiments.segment_gap_noise_floor`, update EXPERIMENTS.md from the `latest.json`
   files, rerun `trust_score.py` (include a FAILED run so the score is not saturated) and
   `python docs/research/figures/build_charts.py`.
4. **Figures:** export `docs/research/figures/drawio/*.drawio` to SVG with the drawio-desktop
   container in one batch call (SVG works; PNG hung on Fig 04), then rasterize on the host with
   Chrome headless (`--force-device-scale-factor`) to >= 600 DPI at print width.
5. **Frontend for a browser-only demo (planned, not started):** a "Start a run" panel on the Runs
   page (`POST /pipelines/run`, starter roles) and a "Roll back" control on the Models page
   (`POST /models/{ref}/rollback`, ML Engineer). Then the demo guide (`docs/runbooks/demo.md`:
   seed_demo.py, run_demo.py `--reject` / `--fail-smoke`, replay_run.py, override via the UI,
   container kill for failure injection).
6. Results sections 00, 13 (hardware above, measured), 15, 16, 17, 19, 22; Section 10 trust-score
   values. Then FINAL_COMPLIANCE_AUDIT.md (two audits), MANUSCRIPT_COMPLIANCE_CHECKLIST and
   CURRENT_PROJECT_AUDIT updates, Word assembly, and only then ask for the team details.

### Environment notes

- The API's `--reload` watcher dies with "Cannot allocate memory" (WSL2 inotify): after backend
  or agents changes, `docker compose -f docker/compose.yaml restart api orchestrator`.
- The core stack is left up. To drive the API, seed a password with `scripts/seed_demo.py --force`.
- `make scala-test` / `make scala-package` work again (sbt was dropped from PATH by `bash -lc`).

## Handoff, 04-10-2026 (superseded by 08-10-2026 above)

### Done in this session (commits 9f89a24, 88a3d72, and the commit that adds this section)

- Second audit: 19 defects fixed; FR-025, FR-055 (all nine gates), FR-074 (triggers), ST-08 met.
- **Spark jobs run in containers** via the new `sandbox-runner` service (ADR-0004); ST-06/ST-07
  executed by `tests/security/test_sandbox_isolation.py`. Before: host network, docs claimed otherwise.
- Found by end-to-end runs and fixed: selection crashed after every verdict (`jsonable`); retrain
  copied its source's inputs and used budget (DEC-22, PRD 19.3); quarantine could not be a mount
  point; a no-network container's unresolvable hostname exhausted Spark's threads; pids limits sized
  by measurement (DEC-25: feature job peaks at 292 threads); full jobs run as `medium`, dry runs as
  `small`; thread exhaustion classified `RESOURCE_CONFIG_INVALID`; job errors keep exception lines.
- **AP-6 corrected (DEC-26)**: gaps within each segmentation (was across all attributes mixed),
  tenure band added (PRD names it), full segment table in the decision evidence.
- Memory: MLflow job runner disabled, orchestrator 2600M, gate-5 heaps (DEC-23).
- DEC-21 (segment attributes evaluation-only), DEC-24 (**LangGraph not the runtime - deviation from
  CR-08**), CURRENT_PROJECT_AUDIT.md, ST-01..ST-08 rows in the traceability.
- Research: `docs/research/` - README plus sections 01-12, 14, 18, 20, 21 drafted with verified
  citations; 118 references verified against Crossref (`references/bibliography.json`, 89% journal,
  74% 2020-2025); 26 draw.io diagrams generated (`figures/drawio/`); trust-score script
  (`analysis/trust_score.py`); AP-6 noise-floor experiment (`experiments/segment_gap_noise_floor.py`).

### End-to-end runs this session (all API-started, worker-executed)

| Run | Data | Outcome | What it showed |
| --- | --- | --- | --- |
| run-2026-10-03-002 | 400 customers | ESCALATED (MISCALIBRATED, ECE 0.0617 on 243 rows) | all 9 gates x 3 jobs; live states; lease recovery |
| run-2026-10-04-001/002 | 800 x 30 | ESCALATED (cleaning) | quarantine mount, then thread exhaustion in containers |
| run-2026-10-04-003 | 800 x 30 | ESCALATED (features) | pids limit 256 too low (DEC-25) |
| run-2026-10-04-004 | 800 x 30 | ESCALATED (AP-6 0.160) | the cross-attribute gap bug (DEC-26) |
| run-2026-10-04-005 | 800 x 30 | ESCALATED (AP-6 0.115 within channel) | segments of ~30 churners: noise |
| run-2026-10-04-006 | 10 000 x 30 | ESCALATED (**AP-6 0.331 within tenure_band**) | 82 852 rows; all jobs in containers |

### Next exact steps, in order

1. **Analyse run-2026-10-04-006's AP-6 escalation.** 0.331 within tenure band on 6 263 validation
   rows is far outside the noise floor - probably a real recall disparity by tenure (tenure is a
   model input; churn of new customers may be harder to detect). Read the segment table from the
   acceptance decision (`decision_records.evidence_refs`, `segments`), and report it as a finding.
   Do not loosen AP-6 to obtain a pass.
2. **Decide the override path.** PRD section 30 ("Promotion despite a failed check: two approvers, ML
   Engineer and Admin, override note") and `governance.yaml` define it; no code requests it. Either
   build it (register the escalated candidate, `POST /pipelines/{id}/override`, two-approver
   approvals, publish on both) or record it as unmet. Ask the user - it is a policy question.
3. **Re-run all ten experiments** on the final code (feature catalogue, sandbox, harness fixes all
   changed since): `docker compose -f docker/compose.yaml --profile tools run --rm -T dev python
   scripts/run_experiment.py --all` (sequential; nothing else may run). EXP-6 needs the socket for
   its fault injector: add `-v /var/run/docker.sock:/var/run/docker.sock` for that one only. Then
   re-run `experiments/segment_gap_noise_floor.py` (conclusion text changed) and update
   EXPERIMENTS.md from the new `latest.json` files.
4. **Figures**: export `figures/drawio/*.drawio` to 600-DPI PNG. The headless exporter
   (`rlespinasse/drawio-desktop-headless`) needs `-e DRAWIO_DESKTOP_COMMAND_TIMEOUT=180s` and
   `--shm-size=1g`; at scale 5 the renderer still produced nothing - try scale 3 or SVG then
   rasterize. Mount `C:\Windows\Fonts` read-only for Times New Roman. Then write
   `build_charts.py` (Fig. 21-25, 28 from stored experiment outputs, matplotlib at 600 DPI).
5. **Write the result sections** (00 front matter, 13, 15, 16, 17, 19, 22, 23 appendix) from the
   re-run outputs and `analysis/trust_score.py`; build the reference list in citation order.
6. Then: PRD_FACULTY_TRACEABILITY.md, demo preparation docs, FINAL_COMPLIANCE_AUDIT.md (two final
   audits), FINAL_REPORT refresh, full test suite + ruff, and only at the very end ask the user for
   the team details (names, photos, author order).

### State left running

The Compose core stack is up (postgres, mlflow, api, orchestrator, sandbox-runner, frontend). The
scheduler will not retrain before 10-10-2026 (cooldown after run-2026-10-03-001). To drive the API
in a new session, set a seed password with `scripts/seed_demo.py --force` (TRIAD_SEED_PASSWORD).
Registered datasets now include the 800- and 10 000-customer versions (transactions v13, v14).

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
| 7. Dashboard and reporting | AT-03, AT-08, AT-15 | **PASSED** — 40 endpoints, eleven pages, verified in a browser |
| 8. Testing and optimisation | success criteria audited | **PASSED** — `REQUIREMENTS_TRACEABILITY.md`, and all ten experiments run |
| 9. Documentation and demo | three stable rehearsals | **PASSED** — `run-2026-09-19-006`, `run-2026-09-21-007` and `run-2026-09-21-020` |

Test totals: **473 unit tests**, **84 security tests**, **21 Scala tests**, **9 smoke checks**
(1 skipped by design), **ruff clean**, and the frontend builds under TypeScript strict mode.

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
  with out-of-slot detection and parse-time checks, the validation gates, deterministic routing,
  the PostgreSQL queue with leases, a reaper and race-safe run keys, the bounded stage loop
  with playbook repair and a circuit breaker, retraining triggers RT-1..RT-9 with the 19.3
  validity gate, the Monitor service, **the churn run as two phases**, and **the worker**.
- **backend**: 40 endpoints under `/api/v1`, counted from the running OpenAPI document — argon2
  passwords, HS256 tokens with a start-up
  check that refuses an unusable signing key, RFC 9457 Problem Details, cursor pagination,
  idempotent run creation, and role separation decided by the policy rather than by the route.
- **frontend**: eleven pages (dashboard, datasets, runs, models, predictions, reports,
  approvals, monitoring, experiments, audit, settings) plus a run detail route, in React 19 +
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
  -> 470 passed (520 with tests/security)
docker compose -f docker/compose.yaml --profile tools run --rm -T dev python -m ruff check .
  -> All checks passed
docker compose -f docker/compose.yaml --profile tools run --rm -T dev     python scripts/run_demo.py --customers 400 --months 24
  -> exit 0, run-2026-09-21-020 in 818s; KEEP_CHAMPION, 14 segment metric sets,
     1252 audit entries across 80 chains, verified; logins served throughout
docker compose -f docker/compose.yaml exec -T frontend npm run build
  -> built in 9.71s, 332 kB / 102 kB gzipped
curl -s http://localhost:8010/metrics
  -> 200, 12 metric families read from the database
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
