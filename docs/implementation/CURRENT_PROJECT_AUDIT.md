# CURRENT_PROJECT_AUDIT

Team 26FSPDSCPMID4096. Updated 08-10-2026 (entry below); the body was last rewritten 04-10-2026
and is kept where the entry does not change it. Rewritten every session from what was run, not from
what earlier documents say. Classes: **WORKING** (implemented and exercised end to end),
**PARTIAL** (implemented, with a named part missing), **BROKEN** (implemented and failing),
**MISSING** (not implemented), **BLOCKED** (cannot be done in this environment), **DEFERRED**
(out of MVP scope by decision).

## Entry, 08-10-2026

- **Verdict unchanged:** all 65 Must requirements met; nothing BROKEN. Should 11/16 and Could 2/3
  met (FR-022, FR-036, FR-037, FR-072, FR-084 moved to met with tests, DEC-29..32).
- **WORKING end to end, newly:** the PRD section 30 override (run-2026-10-07-001: escalation, two
  approvals by role, promotion, publish); per-stage Spark metrics on every job; the between-run
  optimisation loop's decision records (unit-tested; not yet exercised by a live second run).
- **Fixed this session:** run keys redacted as phone numbers on the run timeline; policy
  escalations recorded as UNKNOWN; model cards without their approval; a thin override evidence
  bundle; a published override run shown with its escalation as an error; `make scala-*` targets
  unable to find sbt; a test filing a committed run under `churn_v1`.
- **BLOCKED (environment), newly measured:** EXP-2's upper rungs exhaust the 5.27 GB Docker VM
  while the core stack runs (ENOMEM in other containers); the next run stops the services the
  baselines do not need. EXP-1 failed on an AP-5 escalation at its small size (protocol decision
  pending, IMPLEMENTATION_STATUS.md); EXP-5 and EXP-6 still to run.
- **Still MISSING:** FR-004, FR-008, FR-024, FR-035, FR-040; FR-021 partly; ST-05 TLS.

## Verdict

All 65 Must requirements are met; no module is BROKEN. Three things were broken at the start of
this session and are fixed (below). What remains is Should/Could scope, two environment
limits, and one deviation from the PRD's chosen framework (DEC-24).

## The eighteen modules (PRD section 37)

| ID | Module | Class | Evidence | What is missing |
| --- | --- | --- | --- | --- |
| M01 | Authentication | WORKING | argon2id, signed expiring tokens, start-up check on the signing key; ST-01 | - |
| M02 | Dataset management | PARTIAL | registration, write-once versions, checksum de-duplication, upload, landing-folder watching by the scheduler (03-10-2026) | FR-004 JDBC ingestion (Should), FR-008 streaming connector (Could) |
| M03 | Profiling | WORKING | Spark profiling, quality score, issues with evidence, schema diff; `profile_uri` written | - |
| M04 | Data quality | PARTIAL | contracts, Scala cleaning rules, quarantine, balanced row accounting, cleaning policy gate (DEC-12) | FR-022 anomaly detection (Should), FR-021 managed vocabulary (Should, partial) |
| M05 | Spark processing | PARTIAL | deterministic routing LR-01..LR-10, execution settings, feature catalogue `configs/feature_catalog.yaml` | FR-035 caching decisions, FR-037 configuration optimisation loop (both Should) |
| M06 | Scala jobs | WORKING | `TriadJob` core, three templates, thin JARs; 28 core tests, 4 gate-5 tests on generated objects | FR-040 typed Datasets (Should) |
| M07 | Agent orchestration | WORKING | persisted state machine with per-transition commits, PostgreSQL queue with leases, task and stranded-run reapers, budgets, schedule/drift/data/manual triggers, cancel and resume | LangGraph is not the runtime (DEC-24, deviation from CR-08) |
| M08 | Code generation | WORKING | template slots, out-of-slot detection, all nine gates on every generated job (run-2026-10-03-002), playbook repair loop | live-LLM generation BLOCKED (no provider, ADR-0003); `template_only` is the implemented path |
| M09 | ML training | WORKING | preprocessing, five candidate families, size guard, Optuna tuning of finalists | - |
| M10 | Evaluation | WORKING | metrics with bootstrap intervals, calibration with remedy chain, segments (DEC-02), AP-1..AP-8, per-customer reasons | - |
| M11 | Optimization | PARTIAL | Optuna tuning runs | Spark setting suggestions / FR-037 loop (Should) |
| M12 | Model registry | WORKING | MLflow aliases, one champion per model, model cards, rollback, artifact hash verified on every load (ST-08) | - |
| M13 | Prediction | WORKING | batch scoring, risk bands, shadow sets, reasons per customer | - |
| M14 | Reporting | PARTIAL | fact sheets, curves, claim check, HTML report | FR-072 PDF export (Should) |
| M15 | Dashboard | WORKING | eleven pages, provenance on every number, SSE progress stream | AT-03's stream has no automated test; FR-084 notifications (Could) |
| M16 | Monitoring | PARTIAL | drift reports, RT-1..RT-9 on a timer, alerts table, `/metrics` exporter | no Prometheus/Grafana deployed; alert delivery (FR-084, Could) |
| M17 | Audit | WORKING | one hash chain per run, append-only trigger, chain verification, decision records with alternatives, input lineage | - |
| M18 | Approval workflow | WORKING | policy engine, approvals, separation of duties (ATC-17), run pauses in `APPROVAL_REQUIRED` | - |

The sandbox (PRD section 22, DD-04) is not one of the eighteen but carries ST-06/ST-07: **WORKING
since 04-10-2026** (ADR-0004). Before that it was the largest gap in the project.

## Broken at the start of this session, and fixed

| What | How it was found | Fix |
| --- | --- | --- |
| Every run that reached model selection crashed after the verdict, and the worker retried it from profiling | the first end-to-end run since 21-09 (run-2026-10-03-002) | `jsonable` accepts any JSON shape; it took dicts only, and `decision_records.alternatives` is a list (introduced 23-09) |
| Spark jobs ran on the host network, able to reach PostgreSQL and the internet; the docs said otherwise | reading `get_runner` while the run was in progress | `sandbox-runner` service, SDK-based `DockerRunner`, no network for local jobs, least-privilege mounts (ADR-0004) |
| A retrain started by a trigger was rejected for want of inputs, and inherited its parent's used budget | the scheduler's first live tick | PRD 19.3: newest window; limits only |
| Gate 5 could not fit in the orchestrator (two JVMs in 1.6 GB); MLflow sat at its memory limit | `docker stats` before the run | DEC-23 |
| `docs/limitations-and-ethics.md` claimed sandbox isolation that did not exist, and listed two fixed defects as open | the sandbox finding | corrected |

The nineteen findings of the second audit (03-10-2026) are in commit `9f89a24`.

## BLOCKED (environment)

| Item | Why | Reference |
| --- | --- | --- |
| 10 GB / 100 GB / 1 TB tiers, NFR-PER-02/03, NFR-SCA-01, AT-04/05/09 | 10.96 GB host, 5.27 GB Docker VM | ADR-0002 |
| Live-LLM behaviour (generation, repair rate, narrative) | no provider key | ADR-0003 |
| B1 manual baseline | needs a second person and a time sheet | EXPERIMENTS.md |

## Not met and not blocked

| Item | Status |
| --- | --- |
| ST-05 TLS | not configured in the local stack |
| Rootless container engine for the runner host (PRD 22.3 recommendation) | Docker Desktop is not rootless |
| Should/Could requirements listed under M02, M04, M05, M06, M11, M14, M16 | MISSING; none is a Must |

## End-to-end evidence this session

- **run-2026-10-03-002** (400 customers, API-started, worker-executed): all nine gates passed for
  CleanJob, FeatureJob and LabelJob; feature hygiene kept 44 inputs; all five feature groups
  available; selection escalated on MISCALIBRATED (ECE 0.0617 on 243 rows against 0.05, Brier
  skill 0.336) - consistent with EXP-5's noise floor at this size, and recorded with its
  evidence and alternatives. A worker restart mid-run resumed the task on its lease.
- **run-2026-10-04-001 to -006** (800 and 10 000 customers, every Spark job in the container
  sandbox): each found or confirmed a defect, now fixed (quarantine mount point, hostname and
  thread exhaustion, pids limits, the cross-attribute AP-6 gap); run-2026-10-04-006 ran every job
  in containers on 82 852 labelled rows and escalated on a 0.331 recall gap within tenure band - to
  be analysed (IMPLEMENTATION_STATUS.md, "Next exact steps").
- **Open policy question:** PRD section 30's "promotion despite a failed check" (two approvers) is
  defined in `governance.yaml` but no code path requests it, so an escalated run has no governed
  way forward. FR-075's own acceptance criterion (self-approval refused) is met; the unbuilt part
  is the section-30 autonomy row and UC-17's alternative flow ("several approvers for overrides").
