# 15. Results and Findings

This section reports what the ten experiments of Section 13 measured, grouped by the question each
answers. Every number is read from an experiment's stored output; Table 10 is generated from the
same files, and so are Fig. 21 to Fig. 23. A hypothesis that did not hold is reported as not
holding, with what the measurement shows instead.

## 15.1 End to end: what governance costs at a small size

EXP-1 ran TRIAD (P) and the two conventional pipelines on the same 400 customers, with the same
cutoffs (Fig. 21). P completed every stage - ingestion, profiling, three generated Spark jobs
behind the nine gates, features and labels, five candidate families, tuning, calibration, and
the acceptance policy - with no manual step, which is the first half of the hypothesis. Its
selected model, gradient-boosted trees, was escalated rather than proposed: its calibration
error stayed above the 0.05 limit after both remedies, on a validation split of 243 rows -
smaller than the 324 rows at which Section 15.7 shows a perfectly calibrated model already fails
the same test about four times in ten. The escalation hands that decision to a person, which is
the policy working as written.

**Fig. 21. Performance Benchmark Results.** *(figures/png/Fig 21 Performance Benchmark Results.png)*

P took 831 s; the Spark pipeline without agents took 76 s and the single pandas process 7.1 s.
The gates account for about 395 s of P's time - 188 s of compilation and 206 s of dry runs on
samples - and the fixture tests none, because their result for exactly these sources was reused.
An earlier run took 989 s: its first compile included about four minutes of downloading the
build's dependencies, because the build caches were mounted where the build did not look (Section
20.3). The first run after a template change shows the other end: every fixture run was fresh,
the build was compiled from scratch, and P took 3 397 s, downloads included.
In return P produced 8 decision records and 17 audit entries, ran each job in a sandbox, and
calibrated, tested, and judged its model against a written policy; the baselines produced none of
that evidence. Measured test PR-AUC was 0.741 for P and 0.790 and 0.787 for the baselines, but
these are not comparable: the baselines use twelve features and P uses 44. Refitted on P's own
feature table and split, the baselines' shortlist reached 0.776 with a random forest on the same
280 test rows. No interval was computed for that pair, so no ranking is claimed; what the
experiment shows is that P's extra time buys evidence, not accuracy. The hypothesis's second
half, that agent overhead dominates P's time, needs a configured language model and is not
measured.

## 15.2 Scalability: where a single node gives out

EXP-2 ran the same churn pipeline as a single pandas process (B2) and as a Spark pipeline without
agents (B3) on synthetic datasets of 400 to 100 000 customers, each configuration in a process of
its own inside one 3 GB container, with a 30-minute ceiling on each run (Fig. 22a, b). Both
completed every size up to 50 000 customers - 36.4 MB of Parquet and 92 429 training rows - and
their models agreed, with test PR-AUC within 0.004 of each other at every size. B2 was the faster
at every size, by 4.0 to 8.4 times, because Spark's start-up and per-stage scheduling cost more
than the data does at these volumes. At 100 000 customers (76.3 MB) B2 completed in 130 s with a
peak of 2 294 MB; B3 did not complete within 30 minutes.

**Fig. 22. Scalability Analysis.** *(figures/png/Fig 22 Scalability Analysis.png)*

The hypothesis was the opposite: that the single-node configuration would be the first to fail
and Spark would keep going. On this node it is refuted, and the memory measurements show why
(Fig. 22b). B2's memory grew with the data, from 229 MB at 400 customers to 2 294 MB at 100 000.
B3's did not start small: its JVM held 1 329 MB at 400 customers and 2 223 MB at 50 000, and its
Python driver another 607 MB, so the two processes' peaks already summed to 2 830 MB of the
container's 3 000 MB at the last size both configurations completed. Spark's ability to spill to
disk protects it from data larger than memory; it does not protect it from a node too small for
its own fixed overhead. When that headroom ran out, B3's time went from 227 s at 50 000
customers to beyond the 1 800 s ceiling at 100 000 - at least eight times longer for twice the
data - while B2 took 2.3 times longer.

The crossover the hypothesis describes should exist, but above this node's memory, where the
JVM's overhead is a small share of the machine; it was not reached here, and is not estimated.
What the measurement does support is a routing decision. At these sizes and on nodes of this
size, a single process is both faster and smaller, which is why TRIAD hands the modelling table to
pandas whenever its estimated size fits the worker's memory (LR-06, Section 9.2), and keeps Spark
for the transformation jobs, where the same compiled job runs at any size (LR-07) and where the
sandbox and the gates, not speed, are the reason for using it.

## 15.3 Parallelism: why four cores were slower than two

EXP-3 built the same features from 6 000 customers - 19 332 customer-snapshot rows from ten
monthly cutoffs - with Spark limited to one, two, and four cores in one JVM, and timed the best of
two runs at each setting after paying the JVM's start-up once (12.0 s) outside the measurement.
Two cores ran the work 1.63 times faster than one; four cores ran it 0.84 times as fast as two,
so the hypothesis of at least 1.6 times per doubling did not hold (Fig. 22c).

The per-stage measurements explain the result, and they were not available to the first run of
this experiment. Every setting ran the same 32 Spark stages and shuffled about 21 to 24 MB, with
nothing spilled to disk, so the work was neither memory-bound nor redistributed differently. What
changed was the time each task took: the sum of all task run times grew from 35.2 s on one core
to 45.2 s on two and 107.7 s on four, while the cores were busy for about two thirds of the wall
clock at every setting (core utilization 0.65 to 0.68). Four parallel tasks each ran almost three
times slower than one task alone. The workstation's processor has four physical cores with two
hardware threads each, shared with the operating system and the other containers, so four tasks
competed for the same cores rather than adding capacity (Fig. 22d); the clock speed under load
was not measured, so how much of the slowdown is frequency rather than contention is not
separated here. Task skew stayed low (at most 1.87 times
the median task), which rules out an uneven partition as the cause. This is intra-node scaling on
one machine; it says nothing about distributed workers, which were not run.

## 15.4 Data robustness: schema faults, missing values, and failing jobs

EXP-4 put three schema faults through ingestion, profiling, cleaning and, where the claim needs
it, feature construction, each beside a clean baseline that ran the same stages and was flagged
by none of them. All three were detected and handled as the hypothesis states. A renamed
customer key, `customer_id` arriving as `cust_id`, was refused at ingestion: the contract found
the required column missing, proposed `cust_id` as its source - the most similar unnamed column,
of the same type - and filed a schema-change approval. While that request was pending nothing
was registered and an attempt to apply the mapping was refused; once a data engineer approved it
(in the experiment, the harness acting in that role), the platform registered a renamed copy
whose version records the original file and the approval, and the copy cleaned with balanced row
accounting 136 s after the decision. Prices written as text with currency codes were converted by
the cleaning job, every value parsing, with the row accounting balanced. Without the optional
support tickets table, the feature job marked the customer-service group unavailable and produced
none of its six features - 40 columns where the baseline had 46. No mapping was applied without a
person, and no condition lost rows silently. The first two runs of this experiment could not
show this: the mapping proposal the requirements describe did not exist, and the feature stage
was not part of the protocol.

EXP-5 removed values at random from 2 000 customers' transactions - 1% of the customer keys in
every condition, and none, 5%, 20%, or 50% of three non-key columns (payment method, channel,
discount) - and ran the whole first phase of the pipeline on each. Every null key was quarantined
by the cleaning job's key rule, 391 of 391 in each condition, and nothing else was: the 58 476
non-key nulls of the 50% condition cost no row. The model's test PR-AUC did not decline, so the
hypothesis did not hold: it was 0.634, 0.642, 0.639, and 0.635 from none to half of the values
missing, each inside the others' 95% intervals of about 0.046 either side on 1 366 test rows. With
half of three columns empty, the model ranked customers as well as with none; which features
carried its signal was not measured here. One step of the protocol mattered: an earlier run whose
reference condition drew different random numbers from the others - and so different returns,
sessions, and tickets - scored 0.653 and made the same series look like a steady decline. A fifth
condition, with 10% of the keys missing, took the cleaning plan to 10.1% of rows, over the 5% the
policy allows without a person: the run paused with its output uncommitted and an approval request
filed, the expected observation that none of the PRD's own conditions comes near.

EXP-6 injected the PRD's three Spark faults, and an arm counted only if its fault was seen to land.
A sandbox container killed 10 s into its cleaning job was reported by the runner as a lost
executor, resubmitted by the playbook, and recovered on the second attempt with output identical
to the clean run; in local mode it is the platform, not Spark's task retries, that absorbs the
loss, so that claim was observed in this form only. Executor memory set as low as Spark accepts -
460 MB, below which it refuses to start and the playbook leaves the setting to a person - produced
no out-of-memory at 400 customers or at 20 000: the feature job spilled 179 MB to disk and
finished, so the playbook's out-of-memory remedy was never exercised and that claim is untested
here. That run's cleaned output matched the run at normal memory exactly; its feature table
differed in the last bit of three monetary sums in 9 of 110 900 rows, because spilling changed
the order of floating-point additions - an output is reproducible bit for bit under the same
plan, not across memory settings. One customer holding a fifth of the lines changed nothing
measurable at this size (slowest task 1.0 times the median), so whether adaptive execution
handles skew is not assessable here. Two defects the experiment exposed were fixed before this
run: an out-of-memory in local mode was classified as a driver fault whose remedy needs a
language model, and a killed container as an unknown error.

## 15.5 Code generation: what the gates stop, and how quickly

EXP-8 seeded twenty faults into generated Scala, five each of four classes, ran the five gates
that come before a job reads any data - structured output, static checks, dependencies,
compilation, and fixture tests on hand-checked data - and recorded which gate stopped each fault
(Fig. 23). Nineteen were stopped. All five forbidden API calls were refused by the static scan of
gate 2 in 0.2 to 0.3 s, without being compiled; all five type mismatches and all five missing
imports failed compilation at gate 4, in 37 to 52 s each (one took 96 s), the cost of a Scala
compile once its dependencies are cached. No static gate saw a wrong column name: a name is a
string until Spark resolves it, so all five compiled. The fixture tests of gate 5 stopped four of
them, in 257 to 318 s each - a misspelled de-duplication key that merged different orders, a
cancellation test on a column that does not exist, an order key declared numeric, and a filter on
a name Spark could not resolve. The fifth misspelled both de-duplication keys, so the job fell back
to removing whole-row duplicates, which the fixture's exact duplicate cannot tell apart from the
intended behaviour: it passed every gate, as reading the fixture had predicted, and would have
reached the dry run on a sample. Semantic faults are therefore stopped later and at more cost than
syntactic ones, and what escapes is whatever the fixtures do not exercise. Every detected fault
failed identically when its gates ran a second time on the same code, and the stage runner's
circuit breaker stopped it there rather than spending the attempt budget. The share of faults
*repaired* - the experiment's headline hypothesis - is not measured: repairing a slot requires a
language model, none was configured, and a measured zero would describe the missing provider, not
the repair loop.

## 15.6 Monitoring, leakage, and governance

**Drift (EXP-7).** A champion trained on one month was scored on six further months without
drift and on three kinds of drift. No undrifted month raised an alarm (highest population
stability index 0.031 against a 0.25 threshold). Covariate shift in one feature, and in one
segment, was flagged by the stability index on the first scored month, before any label had
matured - and in both cases the model's PR-AUC rose rather than fell, so a distribution alarm is
not evidence of damage. Concept drift in one segment was the opposite: the feature distributions
barely moved (index 0.032), and the harm - PR-AUC from 0.554 to 0.328 - showed only once labels
matured, when the performance trigger fired. Neither family of trigger would catch both cases,
which is why the retraining rules include both.

**Leakage (EXP-9).** Two leaks were planted: a feature computed after the cutoff, and a lifetime
total computed over the whole dataset. With the defences bypassed, they would have inflated the
held-out PR-AUC by 0.547 and 0.498. Both were blocked. The first is caught by name before any
model is fitted; the second has an innocent name and is caught only by the behavioural checks,
so both kinds of defence are needed. The point-in-time control was not blocked.

**Governance (EXP-10).** Four attempts to bypass governance were made: an agent moving the
champion alias, a requester approving their own promotion, instructions hidden in product data,
and a run continuing after its language-model budget was spent. All four were refused, the
refusals were written to the audit trail, and the hash chain still verified across 143 chains.

## 15.7 The acceptance policy at the sizes it meets

Two of the experiments escalated in ways that raised the question whether the acceptance policy
was measuring the model or the sample, so the policy's two point-threshold rules were measured
directly, with no model involved. For calibration, labels were drawn from scores that are
exactly their own probabilities - a perfectly calibrated predictor by construction - and the
standard ten-bin calibration error was computed 20 000 times at each split size. At 324 rows, the
validation size of the first missing-value experiment, it exceeded AP-5's 0.05 limit in 40% to
46% of draws; at 650 rows in 4% to 7%; from 1 300 rows almost never. The limit is sound, but
below about a thousand rows it refuses perfectly calibrated models about as often as it accepts
them. For segment fairness, a model whose recall is identical in every segment was simulated
with the platform's own segmentations and its rule that a segment needs 50 rows and 30 churners:
it failed AP-6's 0.10 recall-gap limit in 65.5% of draws at 494 validation rows, 97.1% at 1 000,
95.1% at 2 000, 80.9% at 4 000, and 39.6% at 8 000. The rate first rises because more segments
pass the size rule and each is compared with every other, then falls as each segment's recall is
measured on more churners. A tenure-band gap of 0.331 on 6 263 rows, as in the published run, is
far outside this noise; a gap of 0.115 on 494 rows, which escalated an earlier run, is well
inside it. The policy's verdicts are therefore informative at production sizes and unreliable at
the sizes of a pilot, which is the case for stating such rules as intervals (Section 21).

Table 10 collects the ten experiments: each hypothesis as written, the status of its run, the
verdict, and the result it turned on.

{{include: tables/table10_results_summary.md}}

---

## Evidence (not part of the manuscript)

- EXP-1: run `experiments/EXP-1/20261010T143000Z/` (10-10-2026 14:30 UTC, warm build caches,
  DEC-43): P 830.58 s, B3 75.62 s, B2 7.05 s; P xgboost test PR-AUC 0.7405, escalated on AP-5 at
  243 validation rows; like-for-like random_forest 0.7757 on 44 features, 390 training and 280 test
  rows; gate durations from `code_artifacts.gate_results` of run-2026-10-10-027 (compile
  82.4/62.7/43.3 s, dry runs 64.2/96.1/46.1 s, gate 5 reused). Earlier: `20261009T172131Z`
  (P 988.97 s; first compile 325.9 s with the dependency download; run-2026-10-09-009). Cold case:
  run `20261009T161555Z` (P 3 396.87 s; gate 5 1 867 s, all fresh; CleanJob compile 718 s;
  run-2026-10-09-008).
- EXP-2: `experiments/EXP-2/20261008T171242Z/result.json` (08-10-2026, 17:12 to 18:15 UTC). The
  earlier run of 16:17 stopped because the data generator itself was OOM-killed at 100 000
  customers (DEC-37, committed as `20261008T161715Z`). PR-AUC pairs B2/B3: 0.7929/0.7923,
  0.7761/0.7724, 0.7662/0.7649, 0.7848/0.7846, 0.7766/0.7780. Operator observation during B3's
  100 000 run, not recorded by the harness: `docker stats` at 17:55 UTC showed the container at
  2.917 of 2.93 GiB, and `ps` the B3 JVM at 2 261 888 kB resident.
- EXP-3: `experiments/EXP-3/latest.json` (run of 07-10-2026 21:57 UTC); per-stage observations in
  `trials[].observations.stages`; host CPU: Section 13 evidence.
- EXP-4: `experiments/EXP-4/20261010T121337Z/result.json` (10-10-2026 12:13 to 12:33 UTC, on the
  code after the review; the same outcomes as `20261009T184842Z`):
  renamed column refused at ingest, proposal `cust_id -> customer_id` (name similarity 0.778,
  types match), `schema_change` approval pending before the decision, `registered_before_decision`
  false, `applied_while_pending` false, recovery 135.6 s after the decision (229.7 s in the run of
  09-10); price as text
  converted, `price_quarantined` 0, balanced; dropped tickets: `customer_service` unavailable,
  `ticket_features_present` [] of 6, feature columns 40 against 46. Earlier runs kept as records:
  `20261009T003414Z` (CleanJob deprecation), `20261009T174535Z` (no proposal, no feature stage).
- EXP-5: `experiments/EXP-5/20261010T094502Z/result.json` (10-10-2026 09:45 to 11:31 UTC),
  each condition in its own process (DEC-42): key nulls 391/391 x4 and 3 901/3 901; non-key nulls
  0 / 5 853 / 23 274 / 58 476; test PR-AUC 0.6338 [0.5875, 0.6817], 0.6419 [0.5963, 0.6901],
  0.6391 [0.5924, 0.6841], 0.6351 [0.5905, 0.6841], 1 366 test rows, prevalence 0.289; every trend
  condition ESCALATE on AP-6 (tenure_band gaps 0.23 to 0.28 at 1 181 validation rows); over-limit
  10.0539% of rows, one `destructive_cleaning` request, APPROVAL_REQUIRED. The confounded
  reference: `20261009T233715Z` (0.6527; that run then died in its fifth condition).
- EXP-6: `experiments/EXP-6/20261010T113128Z/result.json` (10-10-2026 11:31 to 12:09 UTC): kill
  10.3 s into the job, `EXECUTOR_LOST`, `resubmit`, 2 attempts, content hash identical; memory arm
  460m on 20 000 customers, no failure, feature spill 178 765 620 bytes against 0 at the configured
  memory, clean identical, features not (`experiments/EXP-6/diag_feature_determinism_20261010.md`:
  3 monetary columns, 9 of 110 900 rows, relative difference below 1e-16); pilots in
  `experiments/EXP-6/pilot_20261010.md`; skew arm task skew 1.0 against 1.0.
- EXP-8: `experiments/EXP-8/20261010T131443Z/result.json` (10-10-2026 13:14 to 14:29 UTC, warm
  caches, DEC-42/43), `raw/per_class.json`: gate 2 5/5 (0.2-0.3 s), gate 4 10/10 (verdicts 37.4 to
  51.6 s, MI-5 95.5 s), gate 5 4/5 (fixtures 257.0 to 318.5 s), WC-2 reached execution; escalation
  `circuit_breaker` at attempt 2 for 19/19, fingerprints identical. Earlier: `20261007T225415Z`
  (gates 1-4 only); `20261010T120955Z` failed on a dependency download (DEC-43).
- EXP-7: `experiments/EXP-7/latest.json` (07-10-2026 21:57 UTC), `raw/thresholds.json`.
- EXP-9: `experiments/EXP-9/20261010T094418Z/` (10-10-2026 09:44 UTC; the same inflation as the
  07-10 run, +0.5470 and +0.4975).
- EXP-10: `experiments/EXP-10/20261010T094439Z/` (10-10-2026 09:44 UTC; 4 of 4 refused, 143 chains
  verified).
- 15.7: `experiments/EXP-5/calibration_noise_floor.json` (20 000 trials per cell; 324 rows:
  share above 0.05 = 0.421/0.458/0.404 across three concentrations); `experiments/policy_floors/
  segment_gap_noise_floor.json` (false-alarm rate, all four segmentations: 0.655, 0.971, 0.951,
  0.809, 0.396); published run's validation rows for AP-5/AP-6: 6 263 (`ece_n_rows`).
