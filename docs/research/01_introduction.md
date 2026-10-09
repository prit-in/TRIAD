# 1. Introduction

Data science programming has moved through four stages, and each solved the previous stage's
bottleneck while creating a new one. The first stage was the single-machine scientific stack: an
analyst wrote Python against array and statistics libraries such as NumPy and SciPy, which made
numerical computing accessible and reproducible on one workstation [@harris2020numpy]
[@virtanen2020scipy]. Its bottleneck was scale. The second stage moved the same work onto
clusters. MapReduce showed that a simple programming model could spread a computation over many
machines [@dean2008mapreduce], and Apache Spark generalized it into an in-memory engine with a
relational layer, so that transformations, queries, and machine learning ran in one system
[@zaharia2016spark] [@armbrust2015sparksql] [@tang2020sparksurvey]. Its bottleneck was people
and pipelines: a production pipeline now spans Python for modelling and Scala or SQL for
distributed transformation. The third stage answered with process. The machine learning
operations (MLOps) literature describes the tests, deployment, and monitoring that putting a
model into use requires, and reports that this apparatus is most of the work
[@kreuzberger2023mlops] [@paleyes2022deploying]; its bottleneck is the laborious discipline it
demands. The fourth stage, which this report addresses, asks whether software agents can carry
part of that work.

Fig. 1 summarizes the progression. The point of the figure is not that later stages replace
earlier ones - a modern platform still uses array libraries, a distributed engine, and an
operations layer - but that each stage moved the binding constraint somewhere else.

**Fig. 1. Evolution of Data Science Programming.** *(figures/fig01_evolution.drawio)*

## 1.1 Why manual pipelines remain fragile

Studies of how machine learning is built in practice report the same failure modes repeatedly.
Data change underneath a model, and nobody notices until a business metric moves; a feature is
computed from information that would not have been available at prediction time; a model that
was evaluated carefully is deployed through steps nobody recorded [@amershi2019se4ml]
[@lwakatare2020industrial] [@paleyes2022deploying]. Leakage is serious enough that a study
of its occurrence across many scientific fields attributes a reproducibility crisis in machine
learning based science to it [@kapoor2023leakage]. Testing a learned system is also harder than testing
conventional software, because the oracle for a correct output is often the very thing the
system is meant to learn [@zhang2022mltesting]. None of these is a shortage of algorithms. Each
is a shortage of discipline, applied consistently, at every step of a long pipeline - which is
precisely the kind of work that is tedious for people and tempting to automate.

## 1.2 Agents, and what they should not decide

Large language models (LLMs) can now plan multi-step tasks, call tools, and write code, and
surveys of LLM-based autonomous agents describe a rapid growth of systems that combine those
abilities [@wang2024agents] [@xi2025agents]. Agentic systems have already run laboratory
experiments end to end [@boiko2023chemistry], and LLMs are being applied across software
engineering, from code generation to testing [@hou2024llm4se] [@wang2024llmtesting].

The same literature documents why an agent cannot simply be handed a data platform. Generated
code is frequently insecure [@pearce2022asleep] [@tihanyi2024secure]; content that an agent
reads can carry instructions that redirect it, an attack known as indirect prompt injection
[@greshake2023injection]; and a model that writes the code and also judges whether the code is
correct shares its own failure modes with its judge. An agent that can promote a model, delete
rows, or change a threshold on its own judgement is an unaudited operator with root access.

This report therefore takes a specific position: **agents may propose and compose, but
deterministic components decide**. Which engine runs a stage is decided by ordered routing rules.
Whether generated code may run is decided by nine validation gates. Whether a model is good
enough is decided by an acceptance policy with stated thresholds. Whether it is deployed is
decided by a person, who cannot be the person who requested it. Every one of those decisions is
written down, with its evidence, in a tamper-evident record.

## 1.3 TRIAD, and this report

TRIAD (Tri-engine Reasoning for Intelligent, Autonomous Data Science) is a platform built on that
position. It combines Python for profiling, modelling, and orchestration; Scala for typed,
compiled Spark jobs; and Apache Spark for distributed transformation, and it places thirteen
agent roles under a deterministic orchestrator. Fig. 2 gives the overview, with the seven roles on
the main data path (the other six appear in Section 6): data enter through a
governed ingestion layer, are transformed by generated Scala jobs running in an isolated sandbox,
become point-in-time features and labels, are modelled in Python, and reach production only
through an approval.

**Fig. 2. Agentic Analytics Ecosystem Overview.** *(figures/fig02_ecosystem_overview.drawio)*

Every agent role has a deterministic path, and in the configuration evaluated here it was the
only path: no LLM provider was available, so code was generated from templates and every
decision was made by rules, policies, and people (Section 13). That makes the results a
measurement of the governed platform rather than of any particular model's skill.

The platform was implemented end to end and evaluated on one case study, customer churn
prediction for an e-commerce business, using seeded synthetic data whose generator ships with
the code. That scope is deliberate. A single, fully implemented case study with every result
traceable to a stored artifact says more about agentic data science than many sketched ones, and
the transfer to other domains is discussed in Section 18 as analysis, not as measurement.

The contributions of this report are:

1. **An architecture in which agent autonomy is bounded by deterministic decision points**:
   routing rules, validation gates, an acceptance policy, and separation of duties for
   promotion, each recording its decision with evidence (Sections 5, 6, and 11).
2. **Template-constrained code generation for Scala Spark jobs**, in which a generated job may
   vary only in named slots, must pass nine validation gates - including its own fixture tests
   and a dry run on a sample - and runs in a container with no network access (Sections 6, 7,
   9, and 12).
3. **Point-in-time correct feature engineering for churn**, with a catalogue of 42 feature
   definitions, hygiene rules that run on every training set, and leakage checks that are
   tested against deliberately leaking features (Sections 7 and 8).
4. **An evidence-first evaluation discipline**: calibrated probabilities, segment-level recall
   checks, a champion comparison with bootstrap intervals, and a hash-chained audit trail
   (Sections 10, 14, and 17).
5. **An evaluation that reports what it could not show.** Ten experiments were run on one
   workstation; where a hypothesis failed, or a measurement was not possible in the available
   environment, the report says so (Sections 13, 15, and 20).

The rest of the report follows the faculty's planning structure. Sections 2 to 4 state the
problem, review the literature, and derive requirements. Sections 5 to 12 describe the
architecture layer by layer. Sections 13 to 17 present the experimental setup, metrics, results,
comparison, and reliability analysis. Sections 18 to 22 discuss applications, business impact,
limitations, future work, and conclusions.

---

## Evidence (not part of the manuscript)

- Position "agents propose, deterministic components decide": PRD section 11.0 table;
  `agents/routing.py`, `agents/codegen/gates.py`, `python_ml/triad_ml/train.py` (AP-1..AP-8),
  `triad_core/policy.py`, `agents/policies/governance.yaml`.
- Nine gates run on every generated job: run-2026-10-03-002, `code_artifacts.gate_results`.
- Container with no network: ADR-0004; `tests/security/test_sandbox_isolation.py`.
- 42 catalogue definitions, 38 implemented: `configs/feature_catalog.yaml`; FR-025 row of
  `docs/implementation/REQUIREMENTS_TRACEABILITY.md`.
- Thirteen agent roles: PRD section 11.
- Ten experiments: `docs/implementation/EXPERIMENTS.md`, `experiments/EXP-*/`.
- Synthetic data and generator: `datasets/synthetic/generator.py`; ADR-0002.
