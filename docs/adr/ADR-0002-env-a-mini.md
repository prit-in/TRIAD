# ADR-0002: Env-A-mini is the only reference environment available

- Status: accepted
- Date: 17-09-2026
- PRD anchors: ASM-02, section 31.1, section 31.2, section 31.4, NFR-SCA-02

## Context

PRD section 31.1 defines three reference environments. Env-A assumes 8+ cores and **32 GB RAM**;
Env-B assumes a master plus three 16 GB workers.

The available machine is: 8 logical cores, **10.96 GB** total RAM, of which the Docker VM
currently gets **5.27 GB**. There is no second machine and no cloud budget. Disk is ample
(725 GB free on D:).

The whole stack cannot hold Postgres + MLflow + API + orchestrator + frontend + a Spark
master + three workers inside 5.27 GB.

## Decision

Define **Env-A-mini** and record every measurement against it:

| Property | Env-A (PRD) | Env-A-mini (actual) |
| --- | --- | --- |
| Cores | 8+ | 8 |
| Host RAM | 32 GB | 10.96 GB |
| Container RAM budget | n/a | 5.27 GB (Docker VM default = 50% of host) |
| Spark | local mode + Compose cluster | local[2] by default; `--profile spark` starts master + 1 worker (2 cores / 1.8 GB) |
| Data tier exercised | 10 GB must | small tier only (documented per experiment) |

Consequences for the PRD's targets:

1. Routing rule **LR-07** already says inputs below the small-data threshold run the same
   Spark job in local mode. On this machine that is the normal path, and it is a routing
   decision recorded per stage, not a special case.
2. The 10 GB, 100 GB and 1 TB tiers, NFR-PER-02/03, NFR-SCA-01 and EXP-2/EXP-3 **cannot be
   validated here**. They are reported as *not run, environment-limited*, with the
   configuration preserved so another machine can run them unchanged. No number is guessed.
3. Experiments that do not depend on data volume (EXP-1, EXP-4, EXP-6, EXP-8, EXP-9, EXP-10)
   run in full.

Raising the Docker VM to ~7-8 GB via `%UserProfile%\.wslconfig` is possible and reversible,
but it needs `wsl --shutdown`, which stops the user's other containers. It is therefore only
done if a measurement shows it is required, and it is recorded here if it happens.

## Consequences

- The MVP is demonstrable end to end; the scale claims are not.
- Every performance table in the evaluation report carries the environment row, so no
  Env-A-mini number is ever mistaken for an Env-B result.
