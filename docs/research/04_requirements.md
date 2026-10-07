# 4. Requirements Analysis for Autonomous Analytics Ecosystems

TRIAD's requirements were written before its code, as a product requirements document (PRD) of
84 functional requirements and 48 non-functional requirements, each with a priority and a
testable acceptance criterion. Fig. 5 shows how they were engineered and checked. Each
requirement maps to one or more tests - unit, acceptance (AT), agentic acceptance (ATC), and
security (ST) - and the mapping is kept in a traceability matrix that records, for every
requirement, its status and the evidence for it: a test, a recorded run, or an experiment's
stored output. The matrix was re-audited by reading the code rather than earlier status
documents, and requirements were downgraded when the audit found the evidence missing; that
process is what Section 17 calls governance effectiveness.

**Fig. 5. Requirements Engineering Framework.** *(figures/fig05_requirements_framework.drawio)*

## 4.1 Functional requirements

The functional requirements fall into eight areas (Table 4, part A). Priorities follow MoSCoW:
65 are *Must*, 16 *Should*, and 3 *Could*. All 65 Must requirements are met, each with named
evidence; of the rest, 13 are met, 1 is partly met, and 5 are not met. The unmet ones are stated
in the sections they belong to: relational database ingestion and a streaming connector
(Section 7), automated feature suggestions (Section 8), and caching decisions and typed Dataset
operations (Section 9); category vocabularies are normalized but cannot yet be edited by a
person (Section 7).

Three requirement groups define what makes the system *agentic* rather than merely automated:

- **Bounded generation** (FR-038, FR-041 to FR-043, FR-053 to FR-056): generated code may change
  only template slots, every tool call is permissioned, and a job must compile, package, and pass
  nine validation gates before it executes, through a mediated runner.
- **Recorded decisions** (FR-057 to FR-061, FR-063): feedback is structured, retries are bounded,
  every diagnosis, routing choice, acceptance verdict, and escalation is written as a decision
  record naming its rule, its evidence, and the alternatives it rejected, and content from data
  is treated as untrusted.
- **Human authority** (FR-075, FR-078, FR-079): promotion requires an approval by a person other
  than the requester, roles are enforced server-side, and the audit trail is tamper-evident.

## 4.2 Non-functional requirements

The 48 non-functional requirements cover eleven qualities (Table 4, part B). Two of them -
performance and scalability on 10 GB to 1 TB inputs - could not be validated, because the
evaluation workstation has 10.96 GB of memory with 5.27 GB available to containers
(Section 13). They are reported as *not run* with the configurations preserved, never estimated.

**Table 4. Functional and Non-Functional Requirements Matrix**

*Part A - functional requirements, by area*

| Area | IDs | Must met | Should / Could | Representative requirement |
| --- | --- | --- | --- | --- |
| Ingestion and profiling | FR-001 to FR-015 | 12 of 12 | 1 met, 2 not met | Versioned, checksum-deduplicated ingestion with a profile per version |
| Cleaning | FR-016 to FR-023 | 5 of 5 | 2 met, 1 partly | Quarantine instead of deletion; balanced row accounting |
| Features and labels | FR-024 to FR-030 | 6 of 6 | 1 not met | Point-in-time features at every monthly cutoff; matured labels only |
| Spark and code generation | FR-031 to FR-043 | 9 of 9 | 2 met, 2 not met | Template slots; compiled, packaged, and fixture-tested jobs |
| Machine learning | FR-044 to FR-051 | 6 of 6 | 2 met | Temporal split with a gap; calibrated, explained candidates |
| Agents | FR-052 to FR-063 | 11 of 11 | 1 met | Permissioned tool calls; nine validation gates; decision records |
| Reporting | FR-064 to FR-072 | 8 of 8 | 1 met | Claim-checked reports with provenance on every number |
| Orchestration and governance | FR-073 to FR-084 | 8 of 8 | 4 met | Persisted run state; triggers; approvals; tamper-evident audit |
| **Total** | **84** | **65 of 65** | **13 met, 1 partly, 5 not met** | |

*Part B - non-functional requirements, by quality*

| Quality | Count | Status | Principal evidence |
| --- | --- | --- | --- |
| Security | 6 | Met except transport encryption | ST-01 to ST-04, ST-06 to ST-08 executed; TLS (ST-05) not configured locally |
| Reproducibility | 3 | Met | Pinned toolchain image; content hashes; replay reproduces all three Spark outputs |
| Reliability | 5 | Met where testable | Lease-based task recovery after a worker restart; bounded retries; circuit breaker |
| Observability | 3 | Met | Decision records, audit chain, execution records, metrics endpoint |
| Explainability | 4 | Met | Global importance and per-customer reasons, with the method named |
| Maintainability | 4 | Met | Module boundaries; 18 modules with narrow interfaces |
| Usability | 5 | Met | Eleven dashboard pages; provenance badge on every metric |
| Portability | 1 | Met | One container image for every Python and JVM component |
| Cost | 4 | Met | Budgets on wall clock, retries, and optimization cycles per run |
| Performance | 8 | Not run beyond the small tier | Environment-limited (Section 13) |
| Scalability | 5 | Not run beyond the small tier | Environment-limited (Section 13) |

## 4.3 What counts as met

A requirement is marked met only when something would fail if it broke: a test that exercises
it, a recorded run that shows it, or an experiment's stored output. Code that exists is not
evidence on its own. The rule has consequences in both directions. During the audits, five
gates of the nine that a Must requirement claimed were found to be called by nothing, and the
requirement was downgraded until they ran on every generated job. Conversely, per-stage
execution metrics were marked met only after a test ran a real shuffling job and observed
non-zero shuffle bytes for each of its stages, and the configuration optimization loop only
after a test showed a change applied to the following run and both halves of its
before-and-after record written. A requirement whose acceptance criterion names a measurement
that this environment cannot take is reported as not run, never as met.

The matrix is the specification the rest of the report is measured against. Sections 5 to 12
describe the design that meets it; Sections 15 to 17 report what the measurements show.

---

## Evidence (not part of the manuscript)

- Counts: computed from the PRD's FR table and `docs/implementation/REQUIREMENTS_TRACEABILITY.md`
  on 08-10-2026 (grouped rows such as `FR-001..003` expanded); NFR counts from the PRD's NFR
  table (48 rows over eleven qualities).
- Unmet Should/Could: FR-004, FR-008, FR-024, FR-035, FR-040; partly met: FR-021. Met on
  08-10-2026: FR-022, FR-036, FR-037, FR-072, FR-084 (DEC-29 to DEC-32).
- Section 4.3: the gate downgrade is FR-055 (03-10-2026, traceability summary); FR-036 is
  `StageMetricsSpec` TC-036, FR-037 is `tests/unit/test_optimizer.py`.
- Workstation: ADR-0002. Security tests: traceability section "Security tests".
- Replay: ATC-18, run-2026-09-21-007.
