# 16. Comparative Analysis

Section 3 compared the families of analytics platform on the properties this work needs, as the
literature describes them (Table 3). This section compares by measurement wherever a comparison
could be run, and says where it could not. Two configurations stand in for the conventional
alternatives the planning document names: a single-node Python pipeline (B2) for traditional
Python analytics, and a hand-written Spark pipeline with no agents and no governance (B3) for a
Spark-native environment; TRIAD itself is P. A Scala-only platform and the existing autonomous
frameworks were not run, and are compared through the measurements that bear on them (Fig. 24,
Table 11).

**Fig. 24. Comparative Radar Chart.** *(figures/png/Fig 24 Comparative Radar Chart.png)*

## 16.1 Against traditional Python analytics and a Spark-native pipeline

On 400 customers the conventional pipelines are far faster: the single pandas process finished in
6.9 s and the Spark pipeline in 53 s, against 1 230 s for TRIAD (Table 11). Neither needed a
manual step, and neither did TRIAD; the difference is in what each run leaves behind. The
baselines produce a model and its scores. TRIAD produced the same kind of model together with 8
decision records, 17 hash-chained audit entries, three generated Spark jobs that each passed the
nine gates and ran in a sandbox, a calibrated probability, and a verdict of its acceptance policy
- here an escalation, because calibration could not be confirmed on 243 validation rows.
Hand-written code is the other side of the ledger: the baselines are 188 and 291 lines of
pipeline code that a person writes and maintains for this task, where TRIAD's pipeline for the
same task is configuration and generated jobs, whose templates belong to the platform.

Model quality is not where the configurations differ. Their test PR-AUCs (0.741 for TRIAD, 0.790
and 0.787 for the baselines) come from different feature sets - twelve hand-picked features
against TRIAD's 44 - and so are not a comparison of modelling. Holding the features constant,
the baselines' model shortlist refitted on TRIAD's feature table and split reached 0.776, on the
same 280 test rows as TRIAD's 0.741, with no interval computed for either. What TRIAD adds over
the conventional pipelines is therefore not a better model at this size but a governed one, at a
cost of about 20 minutes per run on this workstation - 13 to 38 minutes across three runs - of
which the validation gates took about nine.

{{include: tables/table11_comparative_performance.md}}

## 16.2 Where each configuration stops

Speed at small sizes and survival at large ones are different questions, and EXP-2 asked the
second one on this workstation. Up to 100 000 customers both conventional pipelines completed and
agreed on model quality, with the single process two to six times faster; at 200 000 it was the
single process that ran out of memory, while the Spark pipeline finished, spilling what did not
fit (Section 15). TRIAD stopped at the same rung for a different reason. Its transformations run
as compiled Spark jobs, each in a sandbox container of its own beside the process that drives
them, so it holds two JVMs where the Spark pipeline holds one, and at 200 000 customers its
feature job could not finish on this node in three attempts. The isolation that makes generated
code safe to run therefore costs memory, and on a node this small that cost decides where the
platform stops. The cluster sizes at which Spark's distribution pays for its overhead - the
requirements' 10 GB tier and above - were not run and are not estimated.

## 16.3 Against a Scala-only platform

No Scala-only configuration was built, so this comparison is by the measurements that isolate
what the compiled language contributes. In EXP-8 every type mismatch and every missing import in
generated Scala was stopped by compilation, before the job touched data, at a cost of under a
minute per attempt; a dynamically typed script would have revealed the same faults only when the
offending line ran. That is the case for Scala in the transformation layer, where generated code
meets data at scale. A Scala-only platform would keep it and give up the modelling layer that the
results also depend on: calibration, exact tree explanations, hyperparameter search, and the
statistical tests of the acceptance policy are all Python libraries here, and the published
model's calibration error of 0.019 and its per-customer reasons came from them. The division
TRIAD makes - compiled Scala where code is generated and must be checked before it runs, Python
where the analysis libraries are - is the one these measurements support, though it costs a
second runtime and a hand-off between them (Section 9).

## 16.4 Against existing autonomous frameworks

Comparing an LLM agent framework of the kind reviewed in Section 3 would need its own deployment
and a configured language model, and this evaluation had neither, so the comparison is of
mechanisms, each tested by an experiment. Where such a framework typically trusts the model to
use its tools well, TRIAD checks what the model could get wrong: all five forbidden API calls
seeded into generated code were refused by a static scan before compilation (EXP-8); both planted
leaks were blocked before they could inflate a held-out score (EXP-9); and all four attempts to
bypass governance, including instructions hidden in product data, were refused and recorded
(EXP-10). Promotion despite a failed check required two named people, and the end-to-end walk of
that path found defects that rule-by-rule tests had missed (Section 17). What the frameworks offer
and TRIAD does not is equally plain: open-ended planning, tool use beyond a fixed registry, and a
model that repairs its own code. TRIAD's repair loop is built and bounded but was not measured with
a model, so its repair rate is unknown.

## 16.5 What the comparison establishes

The comparison establishes three things on this workstation. At small sizes a governed pipeline
costs more time than the conventional ones and produces a model of the same quality with a
record behind every decision; up to 100 000 customers a single process is the faster and the
smaller engine, and beyond that only the Spark pipeline finished on this node; and the checks that distinguish TRIAD from an unconstrained agent framework
are effective against the faults they target. It does not establish what an organization would
save in engineer time, because the manual baseline B1 needs time sheets that do not exist, and it
does not establish how the configurations compare on a cluster, on real customer data, or in
another domain. Those are the conditions under which the comparison should be repeated, and the
repository contains everything needed to repeat it.

---

## Evidence (not part of the manuscript)

- 16.1: EXP-1 run `20261013T132108Z` (P 1 230.24 s, B3 53.24 s, B2 6.94 s; gates 553 s; earlier
  runs 785.83 s and 2 296.01 s); hand-written lines 188 and 291; Table 11 from `docs/research/tables/build_tables.py`;
  Fig. 24 from `docs/research/figures/build_charts.py`.
- 16.2: EXP-2 runs `20261014T171107Z` and `20261013T134333Z` and P's three attempts (Section 15
  evidence); sandbox containers per job: `sandbox/runner.py` (`RESOURCE_PROFILES`, `HOST_LIMITS`).
- 16.3: EXP-8 run `20261011T084444Z`: type and import faults at gate 4, 77-114 s per fault over
  its two attempts;
  published model's ECE 0.019 and reasons (Section 17 evidence, run-2026-10-07-001).
- 16.4: EXP-8 (forbidden API 5/5 at gate 2), EXP-9 (2/2), EXP-10 (4/4); override walk-through
  and defects: Section 17 evidence; repair rate not measured: ADR-0003.
- 16.5: B1 not measured: `experiments/exp01_small_dataset.py` docstring.
