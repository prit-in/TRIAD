# Setup

For developers getting a working environment. Everything runs in Docker; nothing is installed on
the host except Docker itself.

## Prerequisites

| | |
| --- | --- |
| Docker Desktop or Engine | with Compose v2 |
| Memory available to Docker | **at least 5 GB**. The reference environment has 5.27 GB and containers are clamped to 3 GB |
| Disk | about 12 GB for images, plus whatever the data volume grows to |
| Git | for the repository |

Nothing else. No Python, no Java, no Node on the host — and deliberately so: one toolchain image
(ADR-0001) is what stops the API and the Spark jobs drifting apart.

## Pinned versions

The image pins these, and `configs/versions.yaml` is the record. They are read from the image at
runtime rather than restated in code, so a mismatch is visible instead of latent.

| Component | Version |
| --- | --- |
| Python | 3.11 |
| Apache Spark | 4.1.3 |
| Scala | 2.13.17 |
| PySpark | 4.1.3 |
| pandas | 2.3.3 |
| scikit-learn | 1.9.1 |
| XGBoost | 3.2.0 |
| Optuna | 4.9.0 |
| MLflow | 3.16.1 |
| FastAPI | 0.141.1 |
| Pydantic | 2.13.5 |
| SQLAlchemy | 2.0.54 |
| PostgreSQL | 16 |
| React | 19.2 |
| Vite | 7 |
| TypeScript | 5.9, strict |

Python dependencies are locked in `requirements.lock.txt`, generated from `requirements.in`.
Adding a dependency is an L0-forbidden action for an agent: it goes through code review.

## Build and start

```bash
git clone <this repository> && cd Case_Study

# The toolchain image. First build takes a while; it carries Spark and the JDK.
docker compose -f docker/compose.yaml build

docker compose -f docker/compose.yaml up -d postgres mlflow api orchestrator frontend
docker compose -f docker/compose.yaml ps        # all should be healthy
```

The Scala toolchain (sbt, coursier) is fetched separately by `scripts/fetch_toolchains.sh` or
`.ps1`; it is needed only for gate 4, the compile gate.

## Smoke test

Nine checks, one skipped by design. Run this before anything else — it is what tells you whether
the environment is the problem.

```bash
docker compose -f docker/compose.yaml --profile tools run --rm -T dev python -m pytest tests/smoke -q
```

It verifies the database is reachable and migrated, that Spark starts with ANSI mode and UTC,
that MLflow answers, that storage is writable, and that the pinned versions in the image match
`configs/versions.yaml`.

## Accounts

```bash
docker compose -f docker/compose.yaml --profile tools run --rm -T \
    -e TRIAD_SEED_PASSWORD=<choose one> dev python scripts/seed_demo.py
```

Seven accounts, one per role, so every role check can actually be exercised. The password is
never written to a file — if you omit `TRIAD_SEED_PASSWORD`, a random one is generated and
printed once. `--force` resets existing accounts.

## The full loop

```bash
docker compose -f docker/compose.yaml --profile tools run --rm -T dev \
    python scripts/run_demo.py --customers 400 --months 24
```

About fourteen minutes. It generates synthetic data with the faults the PRD names, ingests and
profiles it, generates and compiles Scala for cleaning, features and labels, runs them,
trains and calibrates, judges against the acceptance policy, plays both sides of the approval
gate, scores, measures drift, and writes a claim-checked report.

Useful variants:

```bash
python scripts/run_demo.py --reject        # the approver rejects; the champion must survive
python scripts/run_demo.py --fail-smoke    # the post-deploy smoke test fails; rollback must fire
python scripts/replay_run.py --latest      # ATC-18: replay with no LLM calls, identical outputs
```

## Tests

```bash
# 470 Python tests. About nine minutes; several start a real SparkSession.
docker compose -f docker/compose.yaml --profile tools run --rm -T dev python -m pytest tests/unit -q

docker compose -f docker/compose.yaml --profile tools run --rm -T dev python -m ruff check .
docker compose -f docker/compose.yaml exec -T frontend npm run build   # tsc -b && vite build
cd scala_spark && sbt test                                             # 21 ScalaTest cases
```

Tests that need PostgreSQL skip themselves cleanly when it is not reachable rather than failing
with a connection error.

## Common setup problems

**A port is already in use.** The defaults are deliberately unusual — 8010, 5173, 5500, 55432 —
but if one collides, set `TRIAD_API_PORT`, `TRIAD_WEB_PORT`, `TRIAD_MLFLOW_PORT` or
`TRIAD_PG_PORT` and bring the stack up again.

**Spark dies with an exit code and no message.** Almost always memory. Check what Docker has:
`docker info | grep -i memory`. Below about 5 GB the demo will not complete; the sandbox clamps
containers to 3 GB and Spark needs room inside that.

**`sbt` cannot download dependencies.** Gate 4 shells out to sbt, which fetches through
coursier on first use. Without network access, run `scripts/fetch_toolchains.sh` once while you
have it. Note that this failure is currently misclassified as `SCALA_COMPILE_ERROR` — a known
defect, recorded in the [troubleshooting runbook](runbooks/troubleshooting.md).

**A test fails on `psycopg` connection refused.** Postgres has not finished starting.
`docker compose ps` should show it healthy, not just running.

**The frontend shows a blank page.** The dev server proxies `/api` to the API container. If the
API is not up, every request 502s. Check `curl http://localhost:8010/health` first.

**Everything 401s after working fine.** Tokens last an hour. Sign in again.

**Every gated action is denied.** The governance policy did not load. `GET /settings` as an
admin shows the load error, and the engine is behaving correctly — it fails closed.

## Working on the code

The API and worker mount the repository, so a Python change is picked up by the API's reloader.
The worker is not reloaded: restart it with `docker compose restart orchestrator`.

The frontend runs Vite with hot module replacement, so a `.tsx` change is live immediately.

For one-off commands, the `dev` service under `--profile tools` gives you the toolchain image
with the repository mounted:

```bash
docker compose -f docker/compose.yaml --profile tools run --rm -T dev python -c "..."
docker compose -f docker/compose.yaml --profile tools run --rm -T dev alembic current
```
