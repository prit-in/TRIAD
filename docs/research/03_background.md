# 3. Background and Literature Review

This section reviews the eight bodies of work TRIAD draws on, organized as in Fig. 4, and ends
with a comparison of the approaches they represent (Table 3).

**Fig. 4. Literature Taxonomy.** *(figures/fig04_literature_taxonomy.drawio)*

## 3.1 Python analytics

Python's position in data science rests on a small number of libraries that the rest of the
ecosystem builds on. NumPy's array model is the common substrate for numerical work
[@harris2020numpy], SciPy supplies the statistical and scientific routines around it
[@virtanen2020scipy], and a survey of high-performance Python documents the techniques - compiled
extensions, just-in-time compilation, parallel back ends - that make an interpreted language
viable for heavy computation [@castro2023hppython]. For the tabular problems that dominate
business analytics, gradient-boosted trees remain the strongest general-purpose family. XGBoost
made them scalable [@chen2016xgboost], building on gradient boosting [@friedman2001gbm] and the
random forests that preceded it [@breiman2001rf], and two independent reviews conclude that deep
networks do not reliably outperform tree ensembles on tabular data [@shwartz2022tabular]
[@borisov2024tabular]. Python's weakness is the single process: beyond one machine's memory, the
same code does not scale.

## 3.2 Scala, Spark, and distributed processing

MapReduce established that a restricted programming model could be distributed reliably over
commodity machines [@dean2008mapreduce]. Spark generalized it with in-memory datasets and a
unified engine for batch, streaming, graph, and machine learning workloads [@zaharia2016spark],
and Spark SQL added a relational layer whose optimizer plans DataFrame programs written in Scala,
Python, or SQL alike [@armbrust2015sparksql]. Surveys of the Spark ecosystem describe its storage,
processing, and library layers and their applications [@tang2020sparksurvey], and benchmark
comparisons show Spark's advantage over disk-based MapReduce for iterative work while noting
that its performance depends heavily on configuration [@ahmed2020hadoopspark]. Storage has
evolved alongside: transactional table formats such as Delta Lake add atomic commits and
versioning to files in object storage [@armbrust2020delta], and the resulting "lakehouse"
architecture has been surveyed and benchmarked [@harby2025lakehouse]. Distributed training
itself is a separate problem with its own survey literature [@verbraeken2020distml]. Scala is
Spark's implementation language and its most direct interface: a compiled, typed job fails at
build time for errors that a dynamically typed script would reveal only at run time, which
matters when the code was written by a generator.

## 3.3 Configuring and tuning distributed jobs

Spark exposes hundreds of settings, and choosing them well is hard enough that automatic
parameter tuning for big data systems has its own survey [@herodotou2020tuning]. Approaches
include multi-objective optimization over cloud configurations combined with performance
prediction [@cheng2021sparkcloud], online tuning that improves configurations across the
recurring runs of production jobs [@lin2023sparktuning], and cost-efficient scheduling of Spark
applications [@islam2020sparkscheduling]. TRIAD takes two design decisions from this work. A
tuning loop needs measured execution metrics to act on, so the platform records them for every
job; and because a wrong setting can cost more than the tuning saves, an agent may change only an
explicit allowlist of settings, with each change recorded.

## 3.4 MLOps and the engineering of machine learning systems

Kreuzberger et al. define MLOps from a literature review and practitioner interviews as the
practices that automate and monitor the machine learning lifecycle, and describe its typical
architecture of pipelines, registries, and monitoring [@kreuzberger2023mlops]. Case-study surveys
find that deployment, rather than modelling, is where projects struggle [@paleyes2022deploying],
and studies at Microsoft and across industry describe how machine learning changes development
practices [@amershi2019se4ml] [@wan2020mlchange], with a review of the challenges reported by
industrial large-scale systems [@lwakatare2020industrial]. A systematic review frames
these as open software engineering problems [@giray2021se4ml]. Testing learned systems is
surveyed separately [@zhang2022mltesting], as is assuring each lifecycle stage
[@ashmore2021assuring] and making production pipelines robust [@bodor2025mlopsrobust]. This
literature agrees on what should be done - versioned data and models, automated validation,
monitored deployments - and reports that it is often not done, because it is laborious.

## 3.5 Data quality, cleaning, and provenance

Polyzotis et al. describe the data lifecycle of production machine learning and argue that data
validation deserves the attention given to model validation [@polyzotis2018lifecycle]. Systems
such as unit tests for data make that validation declarative and automatic
[@schelter2018dataquality], while empirical work shows that error-detection tools each find a
different subset of real errors, so no single detector suffices [@abedjan2016errors]. Data
cleaning has been surveyed as a field [@chu2016cleaning], and its interaction with learning runs
in both directions [@cote2022cleaning]. Recent surveys catalogue the data-quality requirements
that matter for machine learning pipelines [@priestley2023dataquality] and methods for evaluating
and improving training data [@gong2021dataeval]. Provenance - the record of where each result came
from - is the precondition for both reproducibility and accountability; it has been surveyed in
general [@herschel2017provenance], captured transparently for data science pipelines
[@chapman2020provenance], and argued for as a requirement of responsible AI [@werder2022provenance].

## 3.6 AutoML and hyperparameter optimization

AutoML automates model selection, hyperparameter tuning, and sometimes feature construction
[@hutter2019automl], and surveys trace its progress and limits [@he2021automl]
[@karmaker2021automl]. Hyperparameter optimization is reviewed in theory and practice
[@yang2020hpo] [@bischl2023hpo], and Optuna's define-by-run interface is a widely used
implementation [@akiba2019optuna]. Automated feature selection [@fan2022autofs] and feature
engineering, including engineering for fairness [@salazar2021fairfeatures], extend the same idea.
AutoML optimizes a metric. It does not, by itself, check calibration, compare against a deployed
model with confidence intervals, test segments, or obtain approval, and these are the decisions
that determine whether a model may be used.

## 3.7 LLM agents and generated code

Surveys of LLM-based autonomous agents describe architectures built from profiles, memory,
planning, and tool use [@wang2024agents] and the breadth of their applications [@xi2025agents];
broader LLM surveys cover the models themselves [@naveed2024llmsurvey]. Multi-agent systems
divide work among cooperating agents [@chen2024mas], and in software engineering such systems
plan, write, test, and repair code [@he2025masse] [@hou2024llm4se]. Self-planning improves code
generation by separating the plan from the code [@jiang2024selfplanning], DB-GPT applies LLMs to
database interaction [@xue2024dbgpt], and an LLM-driven system has planned and executed
chemistry experiments autonomously [@boiko2023chemistry]. The risks are documented with equal
care: generated code contains vulnerabilities at substantial rates [@pearce2022asleep]
[@tihanyi2024secure], LLM-integrated applications can be redirected by instructions hidden in
the content they read [@greshake2023injection], and the security and privacy challenges of LLMs
have been surveyed [@das2025llmsecurity]. The consequence for a data platform is that an agent's
output must be treated as untrusted input to the platform's own checks.

## 3.8 Explainability, trust, and fairness

Explainable artificial intelligence (XAI) has been organized into concepts and taxonomies
[@arrieta2020xai] and surveyed for high-stakes domains [@tjoa2021xai]. For tree ensembles,
TreeSHAP computes exact Shapley-value attributions efficiently and connects local explanations to
global understanding [@lundberg2020trees]. Trustworthy AI is reviewed as a set of requirements
spanning robustness, transparency, accountability, and fairness [@kaur2022trustworthy]
[@li2023trustworthy] [@thiebes2021trustworthy]. Surveys of fairness define the competing
notions and discuss why they cannot, in general, all be satisfied at once [@mehrabi2021fairness]
[@pessach2022fairness] [@caton2024fairness]. Documentation practices such as model cards and
datasheets make the context of a model and its data explicit [@mitchell2019modelcards]
[@gebru2021datasheets]. Calibration - whether a predicted probability of 0.3 means three in ten -
is reviewed in its own right [@silvafilho2023calibration] and is decisive wherever probabilities
drive actions.

## 3.9 Customer churn prediction

Churn prediction is a well-studied application with a known structure. Profit-driven evaluation
in telecommunications showed that the most accurate model is not necessarily the most valuable
one [@verbeke2012churn], hybrid models combine the interpretability of logistic regression with
the accuracy of trees [@decaigny2018churn], and recency, frequency, and monetary (RFM) variables
remain the backbone of customer-base analysis [@fader2005rfm]. Recent work applies a range of
learners to churn [@lalwani2022churn], a review surveys the methods and their business use
[@manzoor2024churnreview], and churn models have
been built on Spark for data too large for one machine [@ahmad2019churnbigdata]. What this work
adds is not a new churn model but the governance around one: point-in-time features, a temporal
split with a gap, calibration, segment checks, and a champion comparison, all enforced.

## 3.10 Positioning

Table 3 compares the families of approach reviewed above on the properties this work requires.
It characterizes each family as the cited literature describes it, not any single product.

**Table 3. Comparative Review of Existing Analytics Platforms**

| Property | Python notebook and script analytics [@castro2023hppython] | Hand-written Spark pipelines [@tang2020sparksurvey] | MLOps platforms [@kreuzberger2023mlops] | AutoML systems [@he2021automl] | LLM agent frameworks [@wang2024agents] | TRIAD |
| --- | --- | --- | --- | --- | --- | --- |
| Scales beyond one machine | No | Yes | Depends on back end | Rarely | Depends on tools | Yes, by routing rule |
| Automates code writing | No | No | No | Partly (pipelines) | Yes | Yes, in template slots |
| Bounds what generated code may change | - | - | - | - | Rarely | Slots, nine gates |
| Point-in-time feature correctness enforced | Manual | Manual | Manual | Rarely | No | Enforced and tested |
| Calibration and segment checks before use | Manual | Manual | Optional | Rarely | No | Policy AP-1 to AP-8 |
| Human approval with separation of duties | No | No | Sometimes | No | Rarely | Required for promotion |
| Tamper-evident decision record | No | No | Partial logs | No | Rarely | Hash-chained per run |
| Replay from recorded inputs and code | Manual | Manual | Partial | No | No | Tested |

No reviewed family combines automated code generation with enforced bounds on it, enforced
acceptance criteria, and a tamper-evident record of every decision. That combination is the gap
TRIAD is designed to fill, and Sections 5 to 12 describe how.

---

## Evidence (not part of the manuscript)

- TRIAD column: routing `agents/routing.py`; slots and gates `agents/codegen/`; point-in-time
  `scala_spark/core/.../ChurnTransforms.scala` and TC-026; policy `python_ml/triad_ml/train.py`;
  approvals `triad_core/governance.py` (ATC-17); hash chain `triad_core/audit.py` (DEC-17); replay
  `scripts/replay_run.py`, ATC-18 (run-2026-09-21-007).
- Allowlisted Spark settings: `agents/policies/governance.yaml` (`spark.config_change`).
