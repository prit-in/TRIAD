# Deployment

For whoever runs this. It covers the Compose topology, the environment variables that matter,
what counts as a secret, and how to back it up and restore it.

## Topology

```
docker/compose.yaml
├── postgres     16, named volume, the only stateful service that matters
├── mlflow       tracking + registry, backed by postgres and the shared data volume
├── api          FastAPI, runs `alembic upgrade head` at start
├── orchestrator the worker: leases tasks, executes stages
├── frontend     Vite dev server (see below for production)
├── spark        optional profile: a master and a worker
└── dev          `--profile tools`, the toolchain image for one-off commands
```

All services share one image, `triad-base:0.1`, on Python 3.11 (ADR-0001), so the environment
cannot drift between the API and the jobs.

```bash
docker compose -f docker/compose.yaml up -d postgres mlflow api orchestrator frontend
docker compose -f docker/compose.yaml ps
docker compose -f docker/compose.yaml logs -f orchestrator
```

## Ports

| Variable | Default | Service |
| --- | --- | --- |
| `TRIAD_API_PORT` | 8010 | API (container port 8000) |
| `TRIAD_WEB_PORT` | 5173 | Frontend |
| `TRIAD_MLFLOW_PORT` | 5500 | MLflow (container port 5000) |
| `TRIAD_PG_PORT` | 55432 | PostgreSQL (container port 5432) |
| `TRIAD_SPARK_UI_PORT` | 8090 | Spark master UI, when the profile is up |

They are deliberately not the defaults for those services: a 5432 or an 8000 already in use on
the host is the most common reason a first `up` appears to succeed and then behaves strangely.

## Environment variables

Every setting is overridable by a `TRIAD_*` variable; `triad_core/config.py` is the complete
list. The ones that change behaviour rather than addresses:

| Variable | Default | What it changes |
| --- | --- | --- |
| `TRIAD_ENV` | `dev` | Outside `dev`, a weak JWT secret is **refused** rather than warned about |
| `TRIAD_JWT_SECRET` | a named insecure value | Must be replaced. See below |
| `TRIAD_JWT_TTL_MINUTES` | 60 | Token lifetime |
| `TRIAD_DB_URL` / `TRIAD_DB_DSN` | local postgres | SQLAlchemy and psycopg forms of the same target |
| `TRIAD_DATA_ROOT` | `/data/triad` | Object storage root |
| `TRIAD_SPARK_MASTER_URL` | `local[2]` | `local[N]` or a cluster master |
| `TRIAD_SPARK_DRIVER_MEMORY` / `TRIAD_SPARK_EXECUTOR_MEMORY` | `1g` | Per-job memory |
| `TRIAD_LLM_PROVIDER` | `template_only` | `template_only`, `recorded`, or a live provider |
| `TRIAD_WORKER_NAME` | `worker-1` | Identifies the lease owner; must be unique per worker |
| `TRIAD_LEASE_SECONDS` | 300 | How long before an abandoned task is re-queued |
| `POSTGRES_PASSWORD` | `triad_dev_pw` | Used to build both DSNs |

### Secrets

Three values are secrets and none of them belongs in the repository:

1. **`TRIAD_JWT_SECRET`** — at least 32 bytes. The shipped default is named
   `dev-only-insecure-secret-change-me-before-any-real-deployment` so that seeing it in a log is
   unambiguous, and `check_jwt_secret` refuses to start outside `dev` with it or with anything
   shorter than the digest.
2. **`POSTGRES_PASSWORD`** — set it before the first `up`; changing it later means changing it
   inside the database too.
3. **The seed password**, passed to `scripts/seed_demo.py` as `TRIAD_SEED_PASSWORD` for one
   command. If it is omitted a random one is generated and printed **once**, to standard output
   only. It is never written to a file and never stored except as an argon2 hash.

Supply the first two through a `.env` file beside the compose file, or through your
orchestrator's secret mechanism. `.env` and `.env.*` are gitignored; `.env.example` is not and
holds no values.

There is no API key for any LLM provider in this deployment, because none is configured
(ADR-0003).

## First run

```bash
# 1. Bring up the stack
docker compose -f docker/compose.yaml up -d postgres mlflow api orchestrator frontend

# 2. Migrations run automatically for the API; confirm
docker compose -f docker/compose.yaml --profile tools run --rm -T dev alembic current

# 3. Create the accounts
docker compose -f docker/compose.yaml --profile tools run --rm -T \
    -e TRIAD_SEED_PASSWORD=<choose one> dev python scripts/seed_demo.py

# 4. Verify
docker compose -f docker/compose.yaml --profile tools run --rm -T dev python -m pytest tests/smoke -q
curl -s http://localhost:8010/health
```

`seed_demo.py --force` resets the password of accounts that already exist. Without `--force` an
existing account is left alone.

## Frontend in production

The `frontend` service runs Vite's dev server, which is right for development and wrong for
anything else. For a real deployment, build once and serve the static output:

```bash
docker compose -f docker/compose.yaml exec -T frontend npm run build   # -> frontend/dist
```

Serve `frontend/dist` from any static host, with `/api` proxied to the API service. The build is
about 332 kB, 102 kB gzipped, with three runtime dependencies and no chart or UI library.

## Backups

Two things hold state, and they must be backed up **together**: a database that references a
storage URI whose bytes are gone is worse than either loss alone.

```bash
# Database
docker compose -f docker/compose.yaml exec -T postgres \
    pg_dump -U triad -Fc triad > triad-$(date +%F).dump

# Object storage (the named volume mounted at /data/triad)
docker run --rm -v triad_data:/data -v "$PWD":/backup alpine \
    tar czf /backup/triad-data-$(date +%F).tar.gz -C /data .
```

Restore in the same order — storage first, then the database — so that no row points at a file
that has not arrived yet.

```bash
docker compose -f docker/compose.yaml exec -T postgres \
    pg_restore -U triad -d triad --clean --if-exists < triad-YYYY-MM-DD.dump
```

**Verify the audit chain after any restore:**

```bash
curl -s http://localhost:8010/api/v1/audit-logs/verify -H "Authorization: Bearer $TOKEN"
```

A partial restore breaks the chain, and the chain is designed to say so. `ok: false` with a
`broken_at` entry id after a restore means rows are missing, not that anyone tampered — but the
two are indistinguishable from inside, which is the point of having it.

## Upgrading

1. Stop the worker first: `docker compose stop orchestrator`. A worker holds task leases, and
   leaving it running during a migration means a task can write against the old schema.
2. Back up, as above.
3. Pull or rebuild the image, `docker compose build`.
4. `docker compose up -d api`, which runs `alembic upgrade head`.
5. Start the worker again.

Runs in flight survive a worker restart: the lease expires, the reaper re-queues the task, and
the new worker picks it up. That path is exercised rather than assumed — the worker has been
killed mid-task and the run completed.

## Monitoring

`GET /metrics` serves 22 Prometheus series, read from the database on each scrape. A minimal
scrape config:

```yaml
scrape_configs:
  - job_name: triad
    metrics_path: /metrics
    static_configs:
      - targets: ["triad-api:8000"]
```

Worth alerting on: runs in `ESCALATED`, approvals pending past their expiry, open alerts by
severity, and any failure of the audit-chain verification.
