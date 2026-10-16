# 7. Autonomous Data Engineering Layer

The data engineering layer turns registered source files into clean, versioned, point-in-time
correct tables that a model may learn from. It is where most of the automation in TRIAD lives,
and also where most of the platform's refusals happen, because a data error that passes this
layer becomes a modelling error that is much harder to see. Fig. 10 shows the pipeline; Table 5
assigns its responsibilities.

**Fig. 10. Autonomous Data Engineering Pipeline.** *(figures/fig10_data_engineering_pipeline.drawio)*

## 7.1 Ingestion and versioning

Every source is registered once, and every file that arrives for it becomes an immutable version:
the bytes are copied into a write-once folder, their SHA-256 checksum is recorded, and a file
whose checksum matches an existing version is recognized as a duplicate rather than registered
again. Each source has a contract - a YAML file declaring its grain, its columns with types and
whether each is required and nullable, and row-level checks with a severity. A file that lacks a
required column is refused at registration with a reason code and never reaches profiling. New
files can arrive by an API upload, by registering a path in the platform's storage, or by being
dropped into a watched landing folder, which the scheduler polls and which starts a run when a
new version is registered (Section 11).

## 7.2 Profiling and the quality score

Each version is profiled in Spark: per-column statistics, null rates, cardinality, quantile
sketches, an inferred semantic type, and a list of issues, each with a severity and the evidence
that produced it. Nulls in an identifier column are always reported, whatever their rate,
because such rows cannot be joined to a customer and will be quarantined downstream. The profile
also yields a quality score, a fixed weighting of four dimensions:

$$
Q = 0.35\,C + 0.25\,U + 0.30\,V + 0.10\,K \tag{1}
$$

where $C = 1 - \frac{1}{m}\sum_{j=1}^{m} \mathrm{null}_j$ is completeness over the $m$ columns,
$U = 1 - \min\!\left(1, \frac{d}{n}\right)$ is uniqueness with $d$ duplicate rows among $n$,
$V = \max(0,\, 1 - 0.25\,b - 0.05\,w)$ is validity with $b$ blocking and $w$ warning issues, and
$K$ is the share of columns whose semantic type was recognized. The weights are a design choice,
stated in the code and constant across versions, so that $Q$ is comparable over time rather than
an absolute measure of fitness. A second version of a source is also compared with the first:
added and removed columns, type changes, and shifts in distribution.

The profile also looks for three kinds of anomaly, each reported with the evidence that raised
it. A day whose row count lies more than 3.5 robust standard deviations from the median day is a
volume anomaly; the first and last days are excluded, because an extract window cuts them short.
A row that breaks a rule cleaning enforces, such as a non-positive price, is a rule breach,
reported with its count and example keys before cleaning quarantines it. And each numeric column
is binned on the previous version's quantiles, so that a population stability index, equation
(7) in Section 11, of 0.10 or more marks a distribution shift. Only a rule breach lowers $V$: a
volume anomaly or a shift describes a different population, not defective data, and acting on
drift belongs to the retraining rules of Section 11.

## 7.3 Cleaning without deletion

Cleaning runs as a generated Scala job whose single slot holds the cleaning plan, composed from a
fixed library of rules: deduplication on declared keys, safe casts that send unparseable values
to quarantine rather than failing the job, text normalization, median or constant imputation,
and quarantine rules for rows that violate a blocking check. Cancellations are recognized and
converted to returns: an order line cancelled after purchase is not a purchase, and counting it as
one inflated order counts and revenue and, through the label, made a customer whose only recent
activity was a cancellation appear retained. That defect was found by auditing the feature
definitions against the data, and its fix is covered by a hand-checked fixture.

The plan conforms types before any rule reads a value. A column the contract declares numeric
that arrives as text - prices written as "INR 871.59", for instance - is converted with a safe
cast after its unit is removed, and the currency code is removed only where it matches the row's
own currency: an amount in another currency, or one with a thousands separator, is not guessed
at but quarantined with its original text. Without this step a single text price failed the
whole job, because comparing text with a number is an error under the ANSI rules the platform
keeps on.

No rule deletes a row. A row that cannot be used goes to a run-scoped quarantine partition with a
reason code, and every cleaning job must balance its row accounting,

$$
n_{\mathrm{in}} = n_{\mathrm{out}} + n_{\mathrm{quarantined}} + n_{\mathrm{duplicates}} \tag{2}
$$

failing if it does not. The accounting also feeds a governance check before the cleaned output
is published: if the share of rows affected exceeds the policy's limit, the stage pauses for a
person instead of publishing (Section 12).

## 7.4 Schema adaptation

Two kinds of schema change are distinguished, because they need opposite responses. A change to
a *required* column - a rename, a removal, a type the contract does not allow - must stop the
run: in an experiment that injected faults into otherwise clean data (EXP-4), a renamed required
column was refused at ingestion and a price column delivered as text with currency symbols was
caught at cleaning and escalated, while the clean baseline passed untouched. No mapping was
applied without a person, and no condition lost rows silently. A missing *optional* source must
not stop the run: when the returns, web-session, support-ticket, or product table is absent, the
feature groups that depend on it are marked unavailable and the run continues with the rest,
recording which groups were available in the job's metrics. The design anticipates a third
response - an LLM proposing a column mapping for a person to confirm - which was not exercised,
because no LLM provider was configured.

## 7.5 Metadata-driven programming

Almost nothing in the pipeline's behaviour is hard-coded. The pipeline definition declares its
stages, their default engines and responsible agents, the split strategy, the candidate model
families, the validation settings, and the triggers. Contracts declare what each source must
look like. A feature catalogue declares each of the 42 churn features with its group, its source
tables, its definition, its leakage guard, and its status: 38 are implemented, two are joined for
evaluation only because they are the attributes the fairness check groups by, and two are
recorded as unavailable because the data hold only their current value, which would leak a later
state into earlier cutoffs. Every output carries a manifest with its schema, row count, and
content hash, and the next stage verifies the manifest before it reads.

## 7.6 Point-in-time correctness

A churn feature must be computed only from what was known at its cutoff date. TRIAD builds
features at monthly cutoffs $T$: transactions are first aggregated to one row per customer per
day, then each day is attached only to cutoffs strictly after it, so a cutoff can see only its
past. Facts with their own time ranges are checked against $T$ after that expansion: a support
ticket resolved after $T$ is still open at $T$ and contributes neither its resolution time nor its
satisfaction score, and a web session counts only if it ended before $T$. Labels are computed over
the 90 days after $T$ and only for cutoffs whose horizon has fully matured. A fixture puts a
post-cutoff event into every feature group and requires that no feature changes.

## 7.7 Self-healing, within bounds

When a data engineering job fails, the diagnosis and repair loop of Section 6.4 applies: the
failure is classified, the playbook's remedy is tried within the stage's attempt budget, and a
repeated identical failure stops the stage. In the evaluated configuration the loop can repair
only by resubmitting or by changing an allowlisted setting; a failure whose remedy is new code
is escalated, because generating a repair requires an LLM. The layer therefore heals what can be
healed by settings and asks a person about the rest - the intended division of labour - and
Section 15 reports how often each happened under injected faults.

**Table 5. Data Engineering Agent Responsibilities**

| Agent | Responsibility | Decides | Never does | Implemented in |
| --- | --- | --- | --- | --- |
| Data Discovery | Register sources and versions; enforce contracts at ingestion | Whether a file is a new version, a duplicate, or refused | Register an unknown source without confirmation | `dataeng/ingest.py` |
| Data Profiling | Statistics, semantic types, issues, quality score, version comparison | Issue severities from fixed thresholds | Change a computed statistic | `dataeng/profile.py` |
| Data Cleaning | Compose a cleaning plan from library rules | Which rules, in which order | Delete rows or invent rules | `CleaningRules.scala`, cleaning template |
| Spark Planning | Route each stage; size its resources | Engine and profile, by ordered rules | Exceed quotas or bypass routing rules | `agents/routing.py` |
| Code Generation | Fill the template slot; propose repairs | The slot body | Add dependencies or edit outside the slot | `agents/codegen/` |
| Execution | Submit jobs to the sandbox; classify failures | The remedy, from the playbook | Run code that has not passed the gates | `sandbox/`, `triad_core/errors.py` |
| Verification | Check contracts, manifests, and gate results | Pass or fail, by rule | Pass anything on judgement | `agents/codegen/gates.py`, `triad_core/contracts.py` |
| Governance | Hold the cleaned output against the policy's limits | Whether a person must approve | Approve anything | `triad_core/policy.py` |

---

## Evidence (not part of the manuscript)

- Quality score (1): `dataeng/profile.py`, function computing the four dimensions (FR-015).
- Type conformance (FR-019, FR-020): `CleaningRules.conformNumeric`, the CleanJob plan; DEC-36;
  `CleaningRulesSpec` TC-019b/c; EXP-4 price condition.
- Anomalies (FR-022): `dataeng/profile.py` `detect_anomalies`, `daily_volume_anomalies`
  (modified z-score 3.5, Iglewicz-Hoaglin, first and last day excluded), `BUSINESS_RULES` (the
  rules of `CleaningRules.standardTransactionRules`), `bin_shares` and
  `population_stability_index` (threshold 0.10); severities DEC-30; seeded tests in
  `tests/unit/test_profile.py`.
- Row accounting (2): `CleaningRules.scala` (`balanced`, `requireBalanced`).
- Contracts: `pipelines/contracts/*.yaml`; enforcement at ingest found unreachable and fixed by
  EXP-4. Identifier nulls: DEC-08. Cleaning policy gate: DEC-12.
- Cancellations as returns: commit `6bfda2c` (FR-025 audit); fixture `ChurnFixtures`.
- EXP-4: `docs/implementation/EXPERIMENTS.md`; `experiments/EXP-4/`.
- Feature availability: FeatureJob metrics, run-2026-10-03-002 ("all five groups available").
- Catalogue: `configs/feature_catalog.yaml` (`fsv=2`); DEC-21.
- Point-in-time rules: `ChurnTransforms.scala` (`expandToSnapshots`); TC-026.
