# IMPLEMENTATION_STATUS

Updated: 18-09-2026, end of session 1. **Read this file first when resuming.**

## Current position

Phases 1–4 are complete and gated. Phase 5 (agent orchestration) is built and unit-tested;
the end-to-end demo run reaches the first Scala stage and fails there for one known,
diagnosed reason (see "Next exact step").

| Phase | Gate | Status |
| --- | --- | --- |
| 1. Environment | Phase-1 smoke test passes | **PASSED** — 9/9, evidence in ENVIRONMENT_STATUS.md |
| 2. Ingestion and profiling | UC-01, UC-02 tests pass | **PASSED** — 22 tests incl. AT-01, AT-02 |
| 3. Spark and Scala | TC-026, TC-030 pass | **PASSED** — 19 ScalaTest cases |
| 4. Python ML | AT-06, AT-07 pass | **PASSED** — 27 tests |
| 5. Agent orchestration | real end-to-end run + ATC-01..05 | **partial** — components done and tested; the full run stops at the clean stage |
| 6. Evaluation and retraining | failure/retraining experiments | not started (drift/triggers unwritten) |
| 7. Dashboard and reporting | AT-03, AT-08, AT-15 | reporting done + tested; **no API, no frontend yet** |
| 8. Testing and optimisation | success criteria audited | not started |
| 9. Documentation and demo | three stable rehearsals | not started |

Test totals: **149 Python tests pass** (`pytest tests/unit`), **19 Scala tests pass**
(`sbt core/test`), **9 smoke checks pass** (`pytest tests/smoke`).

## Next exact step (start here)

The demo run fails at the `clean` stage with:

```
Error: Failed to load class triad.jobs.generated.CleanJob_bbffadd2
```

**Cause (confirmed, not a guess):** `ChurnPipeline.stage_scala_job` generates the artifact
and compiles it (gate 4 passes), but then submits the JAR that was packaged *earlier*. The
freshly generated class is compiled into `jobs/target/scala-2.13/classes/` and never
re-packaged, so `spark-submit` cannot find it.

**Fix:** after gate 4 succeeds and before execution, run `sbt jobs/package` (and reuse the
resulting JAR path) inside `agents/graph/pipeline.py::stage_scala_job`. Options, cheapest
first:

1. add a `gate8_package` step that shells `sbt -batch jobs/package` and returns the new JAR
   path, then pass that path into `spark_submit_request` — this also makes gate 8 ("store and
   version") do real work;
2. or submit with `--class` plus `--jars core.jar,jobs.jar` after packaging both.

Reproduce with:

```
docker compose -f docker/compose.yaml --profile tools run --rm -T dev \
    python scripts/run_demo.py --customers 400 --months 20
docker compose -f docker/compose.yaml --profile tools run --rm -T dev \
    python .triad/show_errors.py      # prints the newest escalation's compile/runtime errors
```

The stages before it already work end to end: 7 synthetic tables generated, 7 dataset
versions registered with checksums, Spark profiling returning quality 0.763 over 6,789 rows
with 2 blocking issues, and a routing decision recorded for every stage.

## What exists and is verified

- **Environment**: `triad-base:0.1` (Temurin 17.0.20.1, Spark 4.1.3 / Scala 2.13.17,
  Python 3.11.16, sbt 1.13.0, PySpark 4.1.3), Compose stack with core/spark/tools profiles,
  Postgres + MLflow healthy, 21 tables migrated by Alembic, `requirements.lock.txt` frozen.
- **triad_core**: config, 19 PRD tables + events + fix_memory, hashing and error
  fingerprints, hash-chained audit with tamper detection, fail-closed policy engine,
  storage with atomic commit + manifests, contracts with row accounting, error taxonomy and
  playbook, Spark-safe Parquet IO, governance/approval service.
- **dataeng**: ingestion (immutable versions, duplicate-checksum rule, contract gating,
  folder watcher) and Spark profiling (statistics, semantic types, issues, quality score,
  version diff).
- **scala_spark**: `TriadJob` core, `ChurnTransforms`, `CleaningRules`, and the FeatureJob /
  LabelJob / CleanJob templates with declared slots; 19 fixture tests including the
  post-cutoff sentinel test and the label fixtures.
- **python_ml**: splits with the gap rule, candidate registry and leak-free pipelines, the
  full metric suite with bootstrap intervals, acceptance policy AP-1..AP-8, the section 18.4
  selection procedure, hygiene/leakage checks, MLflow registry with aliases and rollback,
  predictions with risk bands and SHAP explanations, claim-checked reporting.
- **agents**: tool registry with permissions, LLM adapter (template_only / recorded / http)
  with budgets and redaction, template slot rendering with out-of-slot detection, the nine
  gates, deterministic routing LR-01..LR-10, the PostgreSQL queue with leases and a reaper,
  and the bounded stage loop with playbook repair, fingerprint circuit breaker and
  escalation tickets.
- **sandbox**: the single runner with container controls, secret refusal, run-scoped
  writable mounts, and an honest `isolation="process"` fallback when no docker socket exists.

## Not started

- REST API (`backend/`) and the React frontend (`frontend/`) — nothing exists yet.
- Drift/PSI monitoring, retraining triggers RT-1..RT-9, the cooldown logic.
- Replay mode (ATC-18) and the recorded-cassette agent tests.
- Prompt-injection test (ATC-13) as an executable test — the generator already seeds the
  strings and `agents/llm/redaction.py` fences and flags them, but no test asserts it yet.
- Experiments EXP-1..EXP-10, the traceability matrix, user/deployment/troubleshooting guides.
- `Makefile` targets `migrate`, `seed`, `demo`, `replay` reference scripts that are partly
  missing (`seed_demo.py`, `replay_run.py`).

## Blocked / environment-limited (unchanged)

| Item | Reason | Handling |
| --- | --- | --- |
| 10 GB / 100 GB / 1 TB tiers, NFR-PER-02/03, NFR-SCA-01, EXP-2, EXP-3 | 10.96 GB host, 5.27 GB Docker VM, single machine | reported as *not run, environment-limited* (ADR-0002) |
| Live-LLM behaviour | no API key for any provider | `template_only` path is the default and is fully implemented (ADR-0003) |
| Kaggle datasets | no credentials | UCI Online Retail II + the seeded generator carry the MVP |

## Defects found and fixed this session (none deferred)

1. pandas wrote nanosecond timestamps that Spark 4 refuses (`PARQUET_TYPE_ILLEGAL`).
2. `storage.commit` hashed before writing the manifest, so `verify()` could never pass.
3. `jobs/package` alone omitted the core classes (`NoClassDefFoundError`).
4. The forked ScalaTest JVM needed Spark's full JDK 17 `--add-opens` set.
5. MLflow needed the psycopg3 dialect and was OOM-killed with 8 uvicorn workers.
6. A swallowed pyarrow API mismatch silently returned no row count.
7. Profiling missed null identifiers below the 20% warn rate.
8. An untyped NULL parameter made every queue claim fail on PostgreSQL.
9. A stale ORM identity map after raw-SQL claims silently dropped a later write.
10. The claim checker split ISO dates into fragments and flagged them as unverified.
11. `decimal(18,2)` unquoted in a YAML flow mapping split on its comma.
12. Version numbers collided with folders orphaned by rolled-back runs.
13. A template referring to itself by name broke after the generated object was renamed
    (now refused at parse time with a clear message).
14. An unused import in the generated module (warnings are fatal there, by design).
15. Nanosecond→microsecond casts needed explicit truncation.

## Decisions

See `docs/implementation/DECISIONS.md` (DEC-01..DEC-08) and `docs/adr/ADR-0001..0003`.

## Last verified commands

```
docker compose -f docker/compose.yaml --profile tools run --rm -T dev python -m pytest tests/unit -q
  -> 149 passed
docker compose -f docker/compose.yaml --profile tools run --rm -T dev python -m pytest tests/smoke -v
  -> 9 passed, 1 skipped (covered by the host-side cross-container check)
docker compose -f docker/compose.yaml --profile tools run --rm -T dev bash -c "cd scala_spark && sbt -batch core/test"
  -> 19 passed
```
