# ENVIRONMENT_STATUS

Recorded 18-09-2026 against image `triad-base:0.1`. Every version below was read from the
running image, not copied from the PRD.

## 1. Installed versions

Command used:

```
docker run --rm -v "$PWD:/workspace" -w /workspace triad-base:0.1 python scripts/report_versions.py
```

| Component | PRD requirement | Installed | Verdict |
| --- | --- | --- | --- |
| Java | Eclipse Temurin JDK 17 (CR-03) | Temurin **17.0.20.1+1** | matches |
| Scala | 2.13.x, the patch Spark uses (CR-02) | **2.13.17** (= `scala-library-2.13.17.jar` in the Spark dist) | matches |
| sbt | latest 1.x | **1.13.0** | matches |
| Apache Spark | 4.1.x latest patch (CR-01) | **4.1.3** (`spark-4.1.3-bin-hadoop3`, SHA-512 verified) | matches |
| Python | 3.11 (CR-04) | **3.11.16** | matches |
| PySpark | exactly the cluster Spark version | **4.1.3** | matches |
| pandas | >= 2.2 | **2.3.3** | matches |
| PyArrow | >= 15.0 | **22.0.0** | matches |
| NumPy | >= 1.21 | **2.4.6** | matches |
| scikit-learn | current 1.x | **1.9.1** | matches |
| XGBoost | current | **3.2.0** | matches |
| MLflow | current, aliases API | **3.16.1** | matches |
| Optuna / SHAP | current | **4.9.0** / **0.51.0** | matches |
| LangGraph | current stable, checkpointer + interrupt | **1.1.10** (+ `langgraph-checkpoint-postgres` 2.0.25) | matches |
| FastAPI / Pydantic / SQLAlchemy / Alembic | current | **0.141.1 / 2.13.5 / 2.0.54 / 1.20.0** | matches |
| PostgreSQL | 16+ | **16-alpine** | matches |
| Node.js / npm | current LTS | **24.19.0 / 11.17.0** (host) | matches |
| Docker / Compose | current | **29.8.0 / v5.5.1** | matches |
| pytest / Hypothesis / ruff / bandit / pip-audit / detect-secrets | — | 9.1.1 / 6.168.0 / 0.16.8 / 1.9.4 / 2.10.1 / 1.5.0 | present |
| ScalaTest | — | 3.2.19 (sbt dependency) | present |

Locations: JDK `/opt/java/temurin-17`, Spark `/opt/spark`, sbt `/opt/sbt`, Python
`/usr/local/bin/python3.11`, data volume `/data/triad`.
Lockfile: `requirements.lock.txt` (177 pinned distributions, produced by `pip freeze` in the image).

## 2. Installation record

| Step | What happened |
| --- | --- |
| Temurin JDK 17 | Downloaded in-image from the Adoptium API (41.7 s). |
| Apache Spark 4.1.3 | **Deviation DEV-02.** The container's route to `dlcdn.apache.org` and `archive.apache.org` collapsed to a few KB/s (two builds stalled >20 min each; the host reached 6.2 MB/s on the same file). Resolved by fetching on the host with `scripts/fetch_toolchains.*` and `COPY`ing into the image. SHA-512 is verified on the host **and** in the image against the publisher's `.sha512`. |
| sbt 1.13.0 | Downloaded in-image from GitHub releases. |
| Python dependencies | `pip install -r requirements.in`, then frozen to `requirements.lock.txt`; later builds install from the lockfile. `pyspark` ships as a 455 MB sdist, which dominates build time. |
| Image size | 7.23 GB (Spark dist + the copy bundled inside the PySpark wheel + JDK + ML stack). Acceptable on this host; de-duplicating the two Spark copies is a possible later optimisation. |

## 3. Compatibility results — Phase-1 smoke test (PRD section 36.3)

Command:

```
docker compose -f docker/compose.yaml --profile tools run --rm -T dev \
    python -m pytest tests/smoke -v
```

Result: **9 passed, 1 skipped-and-covered-otherwise** (`tests/smoke/test_phase1_smoke.py`).

| PRD check | Test | Result |
| --- | --- | --- |
| Scala job compiles, packages, runs with `spark-submit` | `test_scala_job_compiles_packages_and_runs_on_spark` | **pass** — `sbt core/package jobs/package`, then `spark-submit --class triad.jobs.FeatureJob`; job wrote features, contract check and manifest passed |
| PySpark session, Parquet read, profiling query | `test_pyspark_session_reads_parquet_and_profiles` | **pass** |
| Arrow round trip: timestamps, decimals, nullable ints, booleans | `test_arrow_round_trip_preserves_types` | **pass** — decimal stays `decimal(18,2)`, microseconds survive, nullable long round-trips |
| ANSI behaviour: `try_cast` safe, plain cast fails | `test_ansi_mode_is_on_and_try_cast_is_the_safe_path` | **pass** — `CAST_INVALID_INPUT` raised as expected; ANSI never disabled |
| XGBoost single node | `test_xgboost_single_node_trains` | **pass** |
| XGBoost Spark estimator | `test_xgboost_spark_estimator_trains` | **pass** (`SparkXGBClassifier`, 1 worker) |
| MLflow log, register, alias, load by alias, Spark UDF | `test_mlflow_log_register_alias_load_and_spark_udf` | **pass** — `set_registered_model_alias("champion")`, `models:/name@champion` load, `mlflow.pyfunc.spark_udf` scored 20 rows |
| LangGraph interrupt, PostgreSQL checkpoint, resume | `test_langgraph_interrupt_and_resume_with_postgres_checkpointer` | **pass** — paused at `interrupt()`, resumed **through a second saver instance**, proving the checkpoint is in PostgreSQL |
| Shared storage write, cross-container read, permissions | `test_shared_storage_write_and_manifest` (in-process) + host-side check | **pass** — manifest/commit verified in-process; `dev` wrote `/data/triad/probe.txt` and the **mlflow** container read it back; non-root (uid 1000) write succeeded. The docker-in-docker variant skips inside the dev container, which has no docker CLI by design (only the sandbox runner gets the socket, DD-04). |

## 4. Failures encountered and how they were fixed

| # | Failure | Root cause | Fix |
| --- | --- | --- | --- |
| F-1 | Two image builds stalled indefinitely on the Spark download | Container route to the Apache mirrors throttled to ~0; host was fine | Host-side fetch + `COPY`, SHA-512 verified twice (DEV-02) |
| F-2 | `make`/compose warned `The "SPARK_HOME" variable is not set` | Compose interpolated `$SPARK_HOME` on the host instead of passing it to the container | Escaped as `$$SPARK_HOME` in the Spark service commands |
| F-3 | MLflow container exited 1: `ModuleNotFoundError: psycopg2` | MLflow's `postgresql://` URI selects the psycopg2 dialect; the image ships psycopg **3** | Backend store URI changed to `postgresql+psycopg://` |
| F-4 | MLflow container exited 137 (OOM) | MLflow 3 forks one uvicorn worker per CPU (8), each ~150 MB, against a 700 MB limit | `--workers 1`, limit raised to 1200 MB, explicit `--allowed-hosts` |
| F-5 | `storage.verify()` reported a content-hash mismatch | `commit()` hashed the folder *before* writing `_manifest.json`, then `verify()` hashed it *after* | `sha256_dir` now excludes commit markers (`_SUCCESS`, `_manifest.json`, `.crc`), so the hash describes content only |
| F-6 | `spark-submit` failed: `NoClassDefFoundError: triad/core/Inputs` | `jobs/package` produces a thin JAR without the `core` project | Build both and pass `--jars core.jar`; this is how the Execution Agent submits jobs |
| F-7 | `spark-submit` failed: `[PARQUET_TYPE_ILLEGAL] Illegal Parquet type: INT64 (TIMESTAMP(NANOS,false))` | pandas/PyArrow write **nanosecond** timestamps by default; Spark 4 refuses them. Exactly the interop hazard PRD 13.5 warns about | Added `triad_core/parquet_io.write_parquet`, which coerces timestamps to microseconds; every Python-side Parquet write now goes through it |

No failure was left documented-but-unfixed.

## 5. Services

| Service | Image | State | Notes |
| --- | --- | --- | --- |
| postgres | postgres:16-alpine | healthy | `triad` + `mlflow` databases, `triad_app` role created at init |
| mlflow | triad-base:0.1 | healthy | backend store on PostgreSQL, artifacts on `/data/triad/mlartifacts` |
| api / orchestrator / frontend | triad-base:0.1 / node:24-alpine | not started yet | Phase 5 and 7 |
| spark-master / spark-worker | triad-base:0.1 | profile `spark`, on demand | local[2] is the default route on Env-A-mini (LR-07) |

## 6. Remaining environment risks

- **Memory.** 5.27 GB in the Docker VM. Postgres + MLflow + API + orchestrator fit; adding the
  Spark cluster profile leaves little headroom, so cluster runs and ML runs are not started
  simultaneously. Raising the VM (via `.wslconfig`) is available if a measurement demands it (ADR-0002).
- **No LLM provider.** Deterministic paths are first-class; live-model behaviour is untestable
  here (ADR-0003).
- **Image size.** 7.23 GB; a future optimisation is to drop the standalone Spark dist and use
  the one bundled inside PySpark.
