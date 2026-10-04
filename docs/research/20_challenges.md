# 20. Challenges and Limitations

This section collects the limits of the work in one place. Each is also stated where it arises; here
they are grouped as the faculty plan asks - technical complexity, interoperability, computational
cost, skills, and organization - followed by the limits of the evaluation itself. Fig. 29 arranges
them as a roadmap from the limits that need only engineering to those that need research.

**Fig. 29. Challenges and Limitations Roadmap.** *(figures/fig29_challenges_roadmap.drawio)*

## 20.1 Technical complexity

Three languages, two runtimes, a compiler, a cluster engine, a database, a model registry, and a
container engine make TRIAD a system with many moving parts, and most of the defects found while
building it lived at the boundaries between them: timestamp precision between Python and Spark, the
type of a partition column on read, a quarantine folder that a containerized job could not
overwrite because it was the job's own mount point, and a hostname that could not be resolved
inside a container with no network, which made Spark exhaust its thread budget. Each was found by
running the system end to end, not by its unit tests, and each is now covered by a test. The lesson
is general: in a multi-engine platform, the integration run is the test that matters most.

## 20.2 Interoperability

The boundary between the engines is a file format and a manifest, which is robust but not free:
every hand-off writes and re-reads Parquet, and the Spark-to-Python hand-off is guarded by an
in-memory size estimate. The orchestration is a PostgreSQL state machine rather than the LangGraph
graph the requirements specified; the guarantees are equivalent and verified, but the deviation
means the platform does not benefit from that framework's tooling. Relational database ingestion
and a streaming connector are not implemented, so TRIAD reads files only.

## 20.3 Computational cost

Safety has a measurable price. Compiling a generated job takes one to five minutes, running its
fixture tests on first sight six to twelve minutes, and its dry run one to two minutes, before the
job does any useful work; caching makes a repeated job's fixture tests free, but a changed job pays
again. Running each job in its own container adds a container start to every submission. And the
platform's memory budget had to be raised for one of its own gates: running the fixture suite
beside the compiler needs two Java virtual machines at once.

## 20.4 Skills

Operating TRIAD needs fluency in Python, Scala, and Spark, plus the containers and database around
them - a combination that is uncommon in a single person. The design reduces the Scala burden by
confining generated code to template slots and keeping the templates small, but a template author
still needs to write correct, compiling Spark code, and a reviewer of an escalation needs to read
a Spark error.

## 20.5 Organizational challenges

Governance creates work for people. Every promotion needs an approver who is not the requester, and
the requirements add that promoting despite a failed check needs two approvers - a path the policy
defines but this implementation does not yet build, so an escalated run currently has no governed
way forward except a new run. Escalations arrive with evidence, but someone must read it.
The evaluation in Section 15 shows that two of the acceptance rules, applied as fixed thresholds to
a few hundred validation rows, escalate on sampling noise much of the time - calibration error and
the segment recall gap - so at small data sizes the platform would generate escalations faster than
a team could usefully review them. Thresholds on point estimates should become thresholds on
intervals before such a platform is used on small data.

## 20.6 Limits of the evaluation

- **One machine.** The evaluation workstation has 10.96 GB of memory, 5.27 GB of it available to
  containers. The 10 GB, 100 GB, and 1 TB data tiers in the requirements, and the scalability
  claims that depend on them, were not run; they are reported as not run, with configurations
  preserved, and no figure is estimated.
- **No LLM provider.** Every agent ran on its deterministic path. Nothing in this report measures an
  LLM's ability to generate or repair code, or the narrative quality of generated reports.
- **One case study, synthetic data.** The data come from a seeded generator designed for this
  study, whose behaviour-to-churn relationship is a modelling assumption; results on real retail
  data could differ in every predictive metric reported.
- **No manual baseline.** The requirements' manual baseline is defined by engineer time sheets,
  which do not exist for this project, so no figure of time saved against people is reported.
- **Security.** Transport encryption is not configured in the local deployment, the container
  engine is not rootless, and deleting an entire run's audit chain is not detected by chain
  verification alone.
- **Unimplemented requirements.** Eight Should or Could requirements are not met (Section 4),
  among them caching decisions, a Spark configuration optimization loop, anomaly detection in
  cleaning, and PDF export.

---

## Evidence (not part of the manuscript)

- Boundary defects: DEC-05, DEC-11; quarantine mount and hostname: run-2026-10-04-001 and
  run-2026-10-04-002, DEC-25; commit messages of 88a3d72 and its successor.
- Gate timings: run-2026-10-03-002 `code_artifacts.gate_results` (gate 4: 45-279 s; gate 5:
  394-722 s first time, 0 s cached; gate 6: 69-220 s).
- Memory budget: DEC-23. LangGraph: DEC-24. Noise floors: `experiments/EXP-5/calibration_noise_floor.json`,
  `experiments/policy_floors/segment_gap_noise_floor.json`.
- Environment: ADR-0002. LLM: ADR-0003. Security limits: ST-05, ADR-0004, DEC-17.
