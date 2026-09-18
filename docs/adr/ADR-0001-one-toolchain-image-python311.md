# ADR-0001: One container toolchain image, Python 3.11 inside it

- Status: accepted
- Date: 17-09-2026
- PRD anchors: CR-03, CR-04, section 36.1, section 36.2, NFR-POR-01

## Context

The PRD pins Python 3.11, Eclipse Temurin JDK 17, Scala 2.13 and Spark 4.1.x, and expects
every machine and CI to use one version set. The host workstation runs Windows 11 with
Python 3.12 and 3.14 (no 3.11), JDK 17 (Temurin, correct), sbt and Node, but no Spark.

Two extra facts shaped the decision:

1. PySpark on Windows needs `winutils.exe` and `hadoop.dll` for local-filesystem writes.
   Parquet commits fail without them, and that failure mode has nothing to do with TRIAD.
2. The PRD's deployment view is already container-based, and CI must match developer
   machines.

## Decision

All Python and all Spark execution happens **inside Linux containers**. A single image,
`triad-base`, carries Python 3.11 (from `python:3.11-slim-bookworm`), Eclipse Temurin JDK 17
(Adoptium tarball), Apache Spark 4.1.3 (SHA-512 verified) and sbt 1.13.0, plus every pinned
Python dependency. `api`, `orchestrator`, `ml-worker`, `mlflow`, `spark-master`,
`spark-worker`, sandbox jobs and the `dev`/test shell are all the same image with different
commands.

The Windows host is used only for editing, git, and driving `docker compose`.

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| Install Python 3.11 + Spark + winutils natively on Windows | Reintroduces the Hadoop-on-Windows failure class; diverges from CI. |
| Develop inside WSL2 Ubuntu directly | Works, but the repo still lives on `/mnt/d`, so I/O is no faster, and it adds a second environment to keep pinned. |
| Six purpose-built images (api, ml, spark, ...) | Six near-identical 2 GB layer sets on a workstation with 68 GB free on C:. One image with different commands costs a little image size and saves build time and disk. |

## Consequences

- Positive: one pinned set, identical in dev and CI; no Windows-specific Spark bugs; the
  same image is the sandbox image, so PRD section 22 "pinned images, no run-time installs" is natural.
- Negative: the `api` container carries Spark and sbt it never uses (~700 MB of dead weight);
  the edit/test loop pays container start-up (~1-2 s) plus bind-mount latency from `D:`.
- Bulk data, Ivy/sbt caches and Spark scratch live in **named volumes**, not on the bind
  mount, so the slow path is only source code.
