# Agentic Data Science Programming: Autonomous Python–Scala–Spark Ecosystems for Intelligent Analytics

[TEAM MEMBER 1], [TEAM MEMBER 2], [TEAM MEMBER 3], [TEAM MEMBER 4]
[FINAL AUTHOR DETAILS - TO BE CONFIRMED] · [CORRESPONDING AUTHOR - TO BE CONFIRMED]

## Abstract

Data science programs now span Python for modelling, Scala for compiled data transformations,
and Apache Spark for scale, and agents built on large language models offer to write and run much
of that code. This report designs, builds, and evaluates a platform in which such agents propose
and explicit rules decide. Generated Spark jobs pass nine validation gates and run in containers
with no network; candidate models are calibrated and judged by a written acceptance policy;
promotion requires a person other than the requester; and every decision is recorded on a
tamper-evident audit trail. Evaluated for customer churn prediction on synthetic retail data on a
single workstation, the gates stopped every forbidden call, type error, and missing import before
execution, while misnamed columns passed every static check and only tests on fixture data caught
them, four times in five. The published model reached 30
percent of churners by contacting 10 percent of customers, yet the acceptance policy refused it
for missing most churners among the newest customers, and the disparity became a recorded decision
by two named approvers. Fixed thresholds proved unreliable at small samples: a perfectly
calibrated model failed the calibration rule in up to 46 percent of trials. The results support
governed autonomy for analytics and call for statistically aware acceptance rules, generation
measured inside the same gates, and evaluation on cluster hardware.

**Index Terms** - Acceptance policy, Agentic AI, Apache Spark, Audit trail, Autonomous data
engineering, Code generation, Customer churn prediction, Data governance, Data science
programming, Human approval, Intelligent analytics, MLOps, Model calibration, Python, Scala,
Validation gates.

## Executive Summary

Modern data science is three programming problems at once - statistical learning in Python,
large-scale transformation on Spark, and the compiled, typed code that makes such transformations
reliable - and each is laborious to get right by hand. Agents that write code promise to remove
the labour, but an agent that can write a data pipeline can also write a wrong one, run it on the
wrong data, or promote the model it produced. The question this work asks is not whether agents can
do the work, but what it takes for an organization to let them.

TRIAD answers with a strict division of labour. Agents plan, generate, diagnose, and propose;
rules decide; people approve. Generated Scala may change only a template's slot and must pass nine
gates - from a static scan for forbidden calls to a dry run on a data sample - before it runs, in a
container with no network. Data are profiled, conformed, and cleaned without deleting a row.
Models are trained on point-in-time features, calibrated, and judged by an eight-rule acceptance
policy; a model reaches customers only after a person other than the requester approves it, and a
model that failed a rule only after two people do. Every choice is a decision record and every
action an entry on a hash-chained audit trail.

The evaluation ran ten experiments on one workstation (Table 1). It found the gates effective
against syntactic faults and, until a job runs on fixtures, blind to semantic ones, the drift
monitors complementary, the
leakage and governance defences intact, and the acceptance policy right at production sizes but
unreliable at pilot sizes, where fixed thresholds refuse sound models by chance. It also found
that governance must be exercised to be trusted: walking a promotion despite a failed check end to
end exposed three defects that rule-by-rule tests had missed.

The price of governance was measured as well: on 400 customers a governed run took 831 s against
76 s for a hand-written Spark pipeline, about 395 s of it in the validation gates, and as the data
grew on
a 3 GB node it was Spark, not the single Python process, that gave out first.

**Table 1. Executive Summary of Contributions and Outcomes**

| Contribution | Outcome | Section |
| --- | --- | --- |
| A governed agentic architecture over Python, Scala, and Spark | Every stage implemented and exercised end to end; all 65 mandatory requirements met | 5, 4 |
| Nine validation gates for generated code | 19 of 20 seeded faults stopped before reading data; misnamed columns invisible to static checks, 4 of 5 caught by fixture tests | 6, 15 |
| Sandboxed execution with per-stage metrics | Jobs in network-less containers with hard memory limits; shuffle, spill, and skew measured for every stage | 9, 12 |
| Cleaning without deletion | Row accounting balances in every run; text-typed values conformed or quarantined, never guessed | 7, 15 |
| Calibrated models under a written acceptance policy | Published model: 2.98 times random lift at 10% of customers; calibration error 0.019 | 8, 19 |
| Human authority over promotion | Two-approver override walked end to end; self-approval and bypass attempts refused | 12, 17 |
| A measured account of policy noise | Fair or calibrated models refused by chance at small samples (up to 97% and 46%) | 15, 21 |
| Honest limits | No language-model provider, synthetic data, one workstation - stated, not estimated | 13, 20 |

---

## Evidence (not part of the manuscript)

- Abstract numbers: business snapshot (gain 0.298 at 10%); calibration noise floor (0.458 max
  share above 0.05 at 324 rows); EXP-8 (forbidden API 5/5 at gate 2, type mismatch and missing
  import 5/5 each at gate 4, wrong column 0/5 static and 4/5 at gate 5; run 20261010T131443Z).
- Table 1: requirement counts (`REQUIREMENTS_TRACEABILITY.md`); EXP-8, EXP-9, EXP-10; segment and
  calibration noise floors; DEC-35 (memory limits), DEC-36 (conformance).
- Abstract length: 213 words, one paragraph, no abbreviations, references, or equations; index
  terms 32 words, alphabetical; section 722 words before the pending EXP-1/EXP-2 sentence.
