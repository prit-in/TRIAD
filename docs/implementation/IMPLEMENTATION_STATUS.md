# IMPLEMENTATION_STATUS

Updated: 18-09-2026, end of session 2. **Read this file first when resuming.**

## Current position

Phases 1-6 are complete and gated. **The end-to-end governed run passes**: one command takes
synthetic source data through profiling, a generated Scala cleaning job, feature and label
jobs, the hand-off to Python, training, evidence-based selection, MLflow registration,
a two-person approval, champion promotion, a post-deployment smoke test, published
predictions, drift measurement, retraining triggers, a claim-checked report and a verified
audit chain.

| Phase | Gate | Status |
| --- | --- | --- |
| 1. Environment | Phase-1 smoke test passes | **PASSED** — 9/9, evidence in ENVIRONMENT_STATUS.md |
| 2. Ingestion and profiling | UC-01, UC-02 tests pass | **PASSED** — AT-01, AT-02 |
| 3. Spark and Scala | TC-026, TC-030 pass | **PASSED** — 21 ScalaTest cases |
| 4. Python ML | AT-06, AT-07 pass | **PASSED** |
| 5. Agent orchestration | real end-to-end run + ATC-01..05 | **PASSED** — see the run record below |
| 6. Evaluation and retraining | drift measured, RT-1..RT-9 evaluated and recorded | **PASSED** — drift reports and trigger decisions are rows, not logs |
| 7. Dashboard and reporting | AT-03, AT-08, AT-15 | reporting done and claim-checked; **no API, no frontend yet** |
| 8. Testing and optimisation | success criteria audited | not started |
| 9. Documentation and demo | three stable rehearsals | not started (one clean rehearsal so far) |

Test totals: **239 Python tests pass** (`pytest tests/unit`), **21 Scala tests pass**
(`sbt core/test`), **9 smoke checks pass** (`pytest tests/smoke`), **ruff clean**.

## The end-to-end run, as it actually behaved

`run-2026-09-18-009`, 478 seconds, 0 LLM calls (template-only, no provider configured):

```
[profile ] quality 0.763, 19154 rows, 2 blocking issues
[clean   ] clean rows 18333, quarantined 649, duplicates removed 172, accounting balanced true
[plan    ] 16 month-start cutoffs, 2025-01-01 to 2026-04-01; data ends 2026-06-30
[features] 6682 customer-snapshot rows
[labels  ] 6682 labelled rows
[handoff ] LR-06 -> pandas (0.9 MB estimated); joined 6682 labelled rows
[split   ] train 2697 / valid 512 / test 555; churn rate 0.271
[train   ] dummy 0.2715 | logreg 0.5791 | random_forest 0.5672 | hist_gb 0.5018 | xgboost 0.5079
[select  ] PROPOSE_PROMOTION: no champion yet: the first model needs approval
[registry] registered churn_classifier v1 as challenger
[approval] self-approval refused with 403, as required; approved by a second person
[deploy  ] champion -> v1; smoke test passed
[predict ] 555 official scores, 9 high risk
[drift   ] highest PSI: snapshot_month 12.132 (single value in the scoring window),
           tenure_days 0.260, frequency_180d 0.086 over 23 features
[drift   ] score PSI 0.012; FLAGGED - high-risk share moved from 0.006 to 0.016
[retrain ] RT-3, RT-5 -> cooldown; cooldown active: 6d 23h remaining of 7 days
[report  ] claim check passed; 4 facts verified
[audit   ] 161 entries, chain verified
```

Three numbers worth reading twice:

- **18333 + 649 + 172 = 19154.** The cleaning row-accounting identity closes exactly against
  the profiled input count. Nothing was silently dropped.
- **dummy scores 0.2715 against a churn rate of 0.271.** The PR-AUC floor lands on the class
  balance, which is what says the metric is wired correctly.
- **snapshot_month PSI 12.132, not flagged.** The scoring window is a single cutoff, so a
  month-of-year feature is constant in it. The index is reported with that note rather than
  suppressed, because a feature pinned by a broken upstream source looks identical.

Reproduce with:

```
docker compose -f docker/compose.yaml up -d postgres mlflow
docker compose -f docker/compose.yaml --profile tools run --rm -T dev \
    python scripts/run_demo.py --customers 800 --months 30
```

`--months` below 24 is refused up front: a 365-day feature window, a 90-day label horizon and
the 90-day gap rule leave too few cutoffs to split, and the message says the minimum.

## What exists and is verified

- **Environment**: `triad-base:0.1` (Temurin 17.0.20.1, Spark 4.1.3 / Scala 2.13.17,
  Python 3.11.16, sbt 1.13.0, PySpark 4.1.3), Compose stack with core/spark/tools profiles,
  Postgres + MLflow healthy, 21 tables migrated by Alembic, `requirements.lock.txt` frozen.
- **triad_core**: config, 19 PRD tables + events + fix_memory, hashing and error
  fingerprints, hash-chained audit with tamper detection, fail-closed policy engine,
  storage with atomic commit + manifests, contracts with row accounting, error taxonomy and
  playbook, Spark-safe Parquet IO **including partition-type restoration**, governance.
- **dataeng**: ingestion (immutable versions, duplicate-checksum rule, contract gating,
  folder watcher) and Spark profiling (statistics, semantic types, issues, quality score).
- **scala_spark**: `TriadJob` core with per-job metrics, `ChurnTransforms`, `CleaningRules`,
  and the FeatureJob / LabelJob / CleanJob templates with declared slots; 21 fixture tests
  including the post-cutoff sentinel and the label maturity rule.
- **python_ml**: splits with the gap rule, candidate registry, the full metric suite with
  bootstrap intervals, acceptance policy AP-1..AP-8, the 18.4 selection procedure, hygiene
  and leakage checks, MLflow registry with aliases and rollback, predictions with risk bands
  and explanations, claim-checked reporting, **PSI/KS/chi-square drift measurement**.
- **agents**: tool registry, LLM adapter with budgets and redaction, template slot rendering
  with out-of-slot detection **and parse-time template checks**, the nine gates, deterministic
  routing LR-01..LR-10, the PostgreSQL queue with leases, a reaper and **race-safe run keys**,
  the bounded stage loop with playbook repair and a fingerprint circuit breaker,
  **retraining triggers RT-1..RT-9 with the 19.3 validity gate**, and the **Monitor service**
  that writes catalogue rows, drift reports and trigger decisions.
- **sandbox**: the single runner with container controls, secret refusal, run-scoped writable
  mounts, and an honest `isolation="process"` fallback when no docker socket exists.

## Next exact step

Phase 7: the REST API under `backend/`. Nothing exists there yet, and it is now the only
thing standing between the working pipeline and the dashboard. The data it needs is already
in PostgreSQL: runs, tasks, decision records, approvals, alerts, drift reports, metrics,
model versions and the audit chain. Start with the read endpoints the PRD's dashboard pages
need (`/runs`, `/runs/{id}`, `/approvals`, `/models`, `/drift`), then the two write paths
that matter (`POST /runs`, approval decisions), because those are the ones the policy engine
and separation-of-duties rules already guard.

## Not started

- REST API and the React frontend. `backend/db/` already holds the Alembic migrations
  that built the 21 tables; there is no FastAPI application and no `frontend/` at all.
- **Optuna tuning (`tune.py`).** `configs`' `tuning:` block is read by nothing; `train_all`
  fits each family with fixed parameters. PRD 18 and the module map both specify it.
- **`predictions` and `reports` rows.** Predictions are written to Parquet and reports to
  HTML, but neither is written to its PostgreSQL table, which the dashboard will need.
- Acting on a fired trigger: `evaluate_retraining` records and alerts, but nothing starts the
  retraining run yet (FR-074's trigger loop).
- Replay mode (ATC-18) and the recorded-cassette agent tests.
- Prompt-injection test (ATC-13) as an executable test — the generator seeds the strings and
  `agents/llm/redaction.py` fences and flags them, but no test asserts it yet.
- Experiments EXP-1..EXP-10, the traceability matrix, user/deployment/troubleshooting guides.
- `Makefile` targets referencing `seed_demo.py` and `replay_run.py`, which do not exist.

## Blocked / environment-limited (unchanged)

| Item | Reason | Handling |
| --- | --- | --- |
| 10 GB / 100 GB / 1 TB tiers, NFR-PER-02/03, NFR-SCA-01, EXP-2, EXP-3 | 10.96 GB host, 5.27 GB Docker VM, single machine | reported as *not run, environment-limited* (ADR-0002) |
| Live-LLM behaviour | no API key for any provider | `template_only` path is the default and is fully implemented (ADR-0003) |
| Kaggle datasets | no credentials | UCI Online Retail II + the seeded generator carry the MVP |

## Defects found and fixed (none deferred)

Session 1 found 15 (see git history). Session 2 found nine more, every one of them by running
the thing rather than reading it:

16. `spark-submit` loaded a JAR packaged before the generated class existed (DEC-09).
17. The cleaning stage was never given a quarantine path, so the job correctly refused to run
    rather than drop 649 rejected rows (DEC-12).
18. An unused import in `LabelJob.scala` was fatal only in the generated copy (DEC-13).
19. `data_end` defaulted to the last cutoff, so no label could ever mature and the job wrote
    zero rows (DEC-10).
20. Partitioned and unpartitioned reads disagreed on the type of `snapshot_date`, so the
    feature/label join returned 0 rows from 6682 on each side (DEC-11).
21. `load_templates` swallowed real template defects and turned them into a later `KeyError`.
22. Explanation feature names never matched raw columns, which would have silently stopped
    RT-2 flagging anything (caught by design review, pinned by test).
23. MLflow 3 serialises sklearn models with skops and refused the model outright, so the
    challenger was never registered while the run still reported success.
24. Official predictions were published for a model that was never promoted, because the set
    type keyed off the *approval* rather than the promotion.
25. Run keys were numbered from a row count, so two concurrent creators collided — observed
    for real when the test suite and a demo run raced on `run-2026-09-18-009`.

## Decisions

See `docs/implementation/DECISIONS.md` (DEC-01..DEC-13) and `docs/adr/ADR-0001..0003`.

## Last verified commands

```
docker compose -f docker/compose.yaml --profile tools run --rm -T dev python -m pytest tests/unit -q
  -> 239 passed
docker compose -f docker/compose.yaml --profile tools run --rm -T dev python -m pytest tests/smoke -v
  -> 9 passed, 1 skipped (covered by the host-side cross-container check)
docker compose -f docker/compose.yaml --profile tools run --rm -T dev \
    bash -c "cd scala_spark && /opt/sbt/bin/sbt -batch core/test"
  -> 21 passed
docker compose -f docker/compose.yaml --profile tools run --rm -T dev python -m ruff check .
  -> All checks passed
docker compose -f docker/compose.yaml --profile tools run --rm -T dev \
    python scripts/run_demo.py --customers 800 --months 30
  -> exit 0, run-2026-09-18-009
```
