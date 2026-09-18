# INITIAL_STATE — workspace inspection before any implementation

Recorded: 17-09-2026. Inspector: Claude Code (implementation engineer).
Workspace root: `D:\Claude_Cowork_Code\PDS\Case_Study`

## 1. Detected project structure

The workspace contained exactly one file and nothing else:

| Path | Size | Note |
| --- | --- | --- |
| `TRIAD_PRD_Agentic_Data_Science_Case_Study.md` | 285,356 bytes / 3,867 lines | The PRD. Source of truth. Unmodified. |

- Not a git repository (`git status` → exit 128). Initialised as one during setup.
- No source code, no Docker files, no dependency files, no tests, no migrations, no frontend, no backend, no generated code, no `.env`.

**Conclusion: this is a greenfield build, not a continuation.** Nothing to preserve, repair or replace.

## 2. Completed-looking modules

None.

## 3. Incomplete modules

All 18 PRD modules (M01–M18) are unimplemented.

## 4. Broken modules

None (nothing exists).

## 5. Failing tests

No test suite existed.

## 6. Environment status at inspection time

| Component | Found | Version | Verdict |
| --- | --- | --- | --- |
| OS | Windows 11 Pro 10.0.26200 | — | Supported per NFR-POR-01 (Windows + WSL2) |
| CPU | 8 logical cores | — | Meets ASM-02 core count |
| RAM | **10.96 GB** | — | **Below ASM-02 (32 GB). Binding constraint.** |
| Disk | C: 68 GB free, D: 725 GB free | — | Adequate; project on D: |
| Git | yes | 2.45.1 | OK |
| Docker Engine | yes | 29.8.0 (linux server, WSL2 backend) | OK |
| Docker Compose | yes | v5.5.1 | OK |
| Docker VM resources | 8 CPU / **5.27 GB RAM** | — | **Binding constraint for the Spark cluster** |
| JDK | Eclipse Temurin | **17.0.20.1** | Matches CR-03 exactly |
| Scala runner | `D:\Scala` | runner 1.16.0, default Scala 3.9.0 | Irrelevant; sbt pins 2.13 per project (CR-02) |
| sbt | `D:\Scala\bin\sbt.bat` | runner 2.0.8 | Usable; builds run in-container for reproducibility |
| Python | 3.14.7 and 3.12 | — | **3.11 (CR-04) not present** |
| PySpark | absent | — | To be installed pinned to the cluster Spark version |
| Spark | absent on host | — | Runs in containers per PRD §10.3 |
| Node.js | yes | 24.19.0 LTS, npm 11.17.0 | OK |
| PostgreSQL client | absent on host | — | Runs in a container; `psql` used via container |
| MLflow | absent | — | Container service |
| WSL2 | installed (Ubuntu, docker-desktop; stopped) | v2 | OK |
| `uv` / `conda` / `scoop` | absent | — | `uv` installed into the images |
| LLM API key | **none set** (ANTHROPIC/OPENAI/GEMINI/OLLAMA all unset) | — | See blocker B-2 |

Unrelated pre-existing Docker artefacts belonging to another project were found and
**left untouched**: containers `finverify-api`, `finverify-frontend`, `finverify-postgres`,
`finverify-qdrant` (all exited), images `case_study-api`, `case_study-frontend`,
`python:3.12-slim`, `postgres:16-alpine`, `qdrant/qdrant`, and volumes
`case_study_postgres_data`, `case_study_qdrant_data`, `case_study_uploads`.

## 7. Major mismatches with the PRD

| # | PRD expectation | Reality | Handling |
| --- | --- | --- | --- |
| MM-1 | ASM-02: ≥ 8 cores **and ≥ 32 GB RAM**; small multi-node Spark cluster for scale tests | 8 cores, 10.96 GB host RAM, 5.27 GB inside the Docker VM, single host | Env-A-mini profile. 10 GB / 100 GB / 1 TB tiers are **not** validated here; the tier actually exercised is recorded honestly in the evaluation report. ADR-0002. |
| MM-2 | CR-04: Python 3.11 | 3.12 and 3.14 on host | Python 3.11 is installed **inside the images**, where all Python runs. Host Python is used only for bootstrap scripts. ADR-0001. |
| MM-3 | ASM-04: an LLM with tool calling is reachable | No API key of any kind is configured | Template-only + replay modes are first-class (PRD §44, §65). The LLM adapter ships with `null`, `recorded` (cassette) and live providers; live paths are exercised only if a key appears. ADR-0003. |
| MM-4 | §43: REES46 / Olist / Telco via Kaggle | No Kaggle credentials | UCI Online Retail II (direct download, CC BY 4.0) + the seeded synthetic generator carry the MVP. Kaggle sets stay optional and scripted. |
| MM-5 | §31 Env-B lab cluster (1 master + 3 workers × 16 GB) | One workstation | Compose runs master + 1–2 small workers; scale experiments are reported as not-run with the reason, never with guessed numbers. |

## 8. Immediate blockers

| ID | Blocker | Severity | Resolution |
| --- | --- | --- | --- |
| B-1 | Docker VM has 5.27 GB RAM; Spark master + 3 workers + Postgres + MLflow + API + orchestrator will not fit | High | Small-footprint Compose profiles; Spark local mode as the default execution route (LR-07 sanctions it for small inputs); raise WSL memory only if measurement shows it is needed. |
| B-2 | No LLM provider configured | High for §22 (agent) requirements that need live generation | Deterministic template-first generation is the default path and is fully implemented; LLM slot-filling is exercised through recorded cassettes. Anything that genuinely cannot be tested without a live key is reported as untested, not as passing. |
| B-3 | PySpark on Windows needs `winutils.exe`/`hadoop.dll` for local filesystem writes | Medium | Avoided entirely: **all Python and Spark execution happens inside Linux containers.** The host is used for editing, git and orchestration of `docker compose`. |
| B-4 | Bind-mounting the repo from `D:` into the Docker VM is slow (grpcfuse) | Low | Source is bind-mounted (small); bulk data, caches and Spark scratch live in named Docker volumes. |

## 9. Decision taken from this state

Continue-from-existing does not apply. Build from zero against the PRD, phase by phase,
with the deviations above recorded as ADRs before they are relied upon.
