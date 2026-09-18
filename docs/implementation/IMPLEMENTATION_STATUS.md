# IMPLEMENTATION_STATUS

Updated: 18-09-2026. Read this file first when resuming work.

## Current phase

**Phase 1 (Environment) — COMPLETE, gate passed.**
Phase 2 (Ingestion and profiling) — starting.

## Phase gate status

| Phase | Gate | Status |
| --- | --- | --- |
| 1. Environment | Phase-1 smoke test passes | **PASSED** — 9/9 checks, see ENVIRONMENT_STATUS.md section 3 |
| 2. Ingestion and profiling | UC-01, UC-02 acceptance tests pass | in progress |
| 3. Spark and Scala | TC-026, TC-030 pass | partial (jobs exist, fixtures pending) |
| 4. Python ML | AT-06, AT-07 pass | not started |
| 5. Agent orchestration | real end-to-end run + ATC-01..05 | not started |
| 6. Evaluation and retraining | failure/retraining experiments run | not started |
| 7. Dashboard and reporting | AT-03, AT-08, AT-15 | not started |
| 8. Testing and optimisation | success criteria audited | not started |
| 9. Documentation and demo | three stable rehearsals | not started |

## Completed

- Repository skeleton matching PRD section 38, git initialised.
- `triad-base:0.1` image: Temurin 17.0.20.1, Spark 4.1.3 (Scala 2.13.17), Python 3.11.16,
  sbt 1.13.0, all pinned Python dependencies; `requirements.lock.txt` frozen from the image.
- Compose stack with `core` / `spark` / `tools` profiles; postgres + mlflow healthy.
- `configs/versions.yaml` generated from the running image.
- **triad_core**: config, SQLAlchemy models for all 19 PRD tables (+ `events`, `fix_memory`),
  db session handling, hashing/fingerprints, hash-chained audit log, fail-closed policy engine,
  storage interface with atomic commit + manifests, data contracts + row accounting,
  error taxonomy + playbook + Scala compile-error parsing, Spark-safe Parquet IO.
- **agents**: tool registry with permissions and schema validation, LLM adapter with four
  providers, budget enforcement, redaction/fencing for untrusted content.
- **scala_spark**: `TriadJob` core (config parsing, inputs, output contract, staging write,
  structured errors, job report), `ChurnTransforms`, `CleaningRules`, and the `FeatureJob` /
  `LabelJob` / `CleanJob` templates with declared slots.
- **policies**: `governance.yaml` (L0–L3 matrix), `routing.yaml` (LR-01..LR-10).
- **pipelines**: `churn_v1.yaml`, 42-entry feature catalogue, transactions contract.
- Phase-1 smoke suite (`tests/smoke/test_phase1_smoke.py`), all passing.
- ADR-0001 (container toolchain), ADR-0002 (Env-A-mini), ADR-0003 (no LLM provider).

## In progress

- Phase 2: dataset registration, immutable versioning, watcher, PySpark profiling, quality score.

## Blocked / environment-limited

| Item | Reason | Handling |
| --- | --- | --- |
| 10 GB / 100 GB / 1 TB tiers, NFR-PER-02/03, NFR-SCA-01, EXP-2, EXP-3 | 10.96 GB host, 5.27 GB Docker VM, single machine | Report as *not run, environment-limited*; configurations preserved (ADR-0002) |
| Live-LLM agent behaviour | No API key for any provider | `template_only` + `recorded` providers; live paths marked untested (ADR-0003) |
| Kaggle datasets (REES46, Olist, Telco) | No Kaggle credentials | UCI Online Retail II + seeded synthetic generator carry the MVP |

## Failing tests

None. (Six defects found during Phase 1 were fixed, not deferred — see ENVIRONMENT_STATUS section 4.)

## Known deviations from the PRD

DEV-01 Python 3.11 only inside images · DEV-02 Spark tarball fetched host-side ·
DEV-03 Env-A-mini · DEV-04 no LLM provider. All four are recorded in `configs/versions.yaml`
and the ADRs.

Structural addition: a shared `triad_core/` package (not in PRD section 38) holds config, DB,
storage, audit, policy and contracts so backend, agents and python_ml do not duplicate them.

## Next exact implementation step

Phase 2, step 1: `triad_core/ingest.py` + `agents/tools/storage_tools.py` — dataset
registration with SHA-256 checksums, immutable version folders, duplicate detection
(identical checksum creates no version), schema hash, and the `datasets`/`dataset_versions`
rows; then the Alembic migration that creates all tables.

## Last verified commands

```
docker run --rm -v "$PWD:/workspace" -w /workspace triad-base:0.1 python scripts/report_versions.py
docker compose -f docker/compose.yaml --profile tools run --rm -T dev python -m pytest tests/smoke -v
  -> 9 passed, 1 skipped (covered by the host-side cross-container check)
```

## Last verified commit

See `git log` — the Phase-1 milestone commit carries this status file.
