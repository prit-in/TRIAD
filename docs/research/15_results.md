# 15. Results and Findings

This section reports what the ten experiments of Section 13 measured, grouped by the question each
answers. Every number is read from an experiment's stored output; Table 10 is generated from the
same files, and so are Fig. 21 to Fig. 23. A hypothesis that did not hold is reported as not
holding, with what the measurement shows instead.

## 15.1 End to end: what governance costs at a small size

EXP-1 ran TRIAD (P) and the two conventional pipelines on the same 400 customers, with the same
cutoffs (Fig. 21). P completed every stage - ingestion, profiling, three generated Spark jobs
behind the nine gates, features and labels, the candidate families, tuning, calibration, and the
acceptance policy - with no manual step and no language-model call, which is the first half of
the hypothesis. Its selected model, gradient-boosted trees, was escalated rather than proposed:
its calibration error stayed above the 0.05 limit after both remedies, on a validation split of
243 rows, a size at which Section 15.7 shows that even a perfectly calibrated model fails the
same test about four times in ten. The escalation hands that decision to a person, which is the
policy working as written.

**Fig. 21. Performance Benchmark Results.** *(figures/png/Fig 21 Performance Benchmark Results.png)*

P took 1 230 s; the Spark pipeline without agents (B3) took 53 s and the single pandas process
(B2) 6.9 s. B3 does P's Spark work and modelling with no agents, so P's time beyond B3's is the
agentic loop's cost: 0.957 of P's wall clock. The second half of the hypothesis - that agent
overhead dominates P's time - therefore holds as measured, although none of that overhead is
language-model time, because no provider was configured. The gates took 553 s of it (262 s of
compilation and 292 s of dry runs on samples; the fixture tests were reused), the full runs
299 s, and profiling 116 s. Two earlier runs agree with this one on every count and score but
took 786 s and 2 296 s; the baselines took the same time in all three, so the spread lies in P's
compile and sandbox steps, and the overhead share stayed between 0.938 and 0.957. In return P
wrote 8 decision records and 17 audit entries; the baselines wrote none. Test PR-AUC was 0.741 for
P and 0.790 and 0.787 for the baselines, but the baselines use twelve features and P uses 44.
Refitted on P's own feature table and split, the baselines' shortlist reached 0.776 with a random
forest on the same 280 test rows. No interval was computed for that pair, so no ranking is
claimed; P's extra time buys evidence, not accuracy.

## 15.2 Scalability: where a single node gives out

EXP-2 grew the data from 400 to 200 000 customers, 0.4 to 154 MB of Parquet, on the 3 GB node,
with each configuration in a process of its own (Fig. 22a, b). The single pandas process (B2)
completed every rung up to 100 000 customers, two to six times faster than the Spark pipeline
(B3), PR-AUC within 0.004; at 200 000 the container's memory limit killed it 28 s
in. B3 completed every rung, in 632 s at 200 000, its JVM never above 2.4 GB because Spark spills
what does not fit. The first clause of the hypothesis held in both ladders.

TRIAD did not finish at that rung, in three attempts on code corrected after each. In the last
two its feature job, sandboxed with 1 GB of heap, made no progress for its hour and was stopped;
the retry, with twice the shuffle partitions, stalled with a third of one stage's time in
garbage collection; and during the third attempt the 5.27 GB virtual machine stopped responding
and restarted. P runs its own process and Spark session in one 3 GB container and each job in
another, so on this node it needs more memory than B3, which does both in one. On 14-10 it had
run 5 493 s, 8.7 times B3's whole time, when it stopped, so the 20% clause is refuted whatever a
larger node would show.

**Fig. 22. Scalability Analysis.** *(figures/png/Fig 22 Scalability Analysis.png)*

## 15.3 Parallelism: what doubling the cores bought

EXP-3 built the same features from 6 000 customers with Spark limited to one, two, and four cores
in one JVM. Against one core, two cores ran the work 1.26 times faster and four 1.33 times;
doubling from two to four gave 1.06 times, against the hypothesis's 1.6, so it did not hold
(Fig. 22c). Every setting shuffled 21 to 24 MB with nothing spilled, and core utilization fell
from 0.73 to 0.67 as cores were added: the window aggregations shuffle, and a shuffle does not
halve when the cores double. The workstation's four physical cores carry two hardware threads
each and are shared with the operating system and the other containers, so added tasks competed
for cores rather than adding capacity; clock speed under load was not measured. An earlier run
measured four cores as slower than two (0.84 times). Both refute the hypothesis, and neither says
anything about distributed workers, which were not run.

## 15.4 Data robustness: schema faults, missing values, and failing jobs

EXP-4 put three schema faults through ingestion, profiling, cleaning and feature construction,
each beside a clean baseline that none of the checks flagged. All three were detected at the
stage built to catch them and handled as the hypothesis states. A renamed customer key,
`customer_id` arriving as `cust_id`, was refused at ingestion: the contract proposed `cust_id` as
its source and filed a schema-change approval; nothing was registered while it waited and an
attempt to apply the mapping was refused; once a data engineer approved it (in the experiment,
the harness acting in that role), the mapped copy registered and cleaned with balanced row
accounting 172 s after the decision. Prices written as text were converted by the cleaning job,
every value parsing. Without the optional support-tickets table, the feature job marked the
customer-service group unavailable and produced none of its six features. No mapping was applied
without a person, and no condition lost rows silently.

EXP-5 removed values at random from 2 000 customers' transactions - 1% of the customer keys in
every condition, and none, 5%, 20%, or 50% of three non-key columns - and ran the whole first
phase of the pipeline on each. Every null key was quarantined by the cleaning job's key rule, 391
of 391 in each condition, and nothing else was: the 58 476 non-key nulls of the 50% condition
cost no row. The model's test PR-AUC did not decline, so the hypothesis did not hold: 0.634,
0.642, 0.639, and 0.635 from none to half of the values missing, each inside the others' 95%
intervals on 1 366 test rows. A second run reproduced every measurement exactly, apart from the
wall-clock times and an approval's identifier. A fifth condition, with 10% of the keys missing, took the cleaning plan to
10.1% of rows, over the 5% the policy allows without a person: the run paused with its output
uncommitted and an approval request filed.

EXP-6 injected the PRD's three Spark faults, and an arm counted only if its fault was seen to
land. On the compose cluster, the executor running a cleaning job was killed 31.9 s into the job:
Spark's own log named the task it lost, its retries absorbed the loss within the same job, and
the output matched the clean cluster run exactly - the hypothesis's first clause, as stated. In
local mode, where the executor is the driver, a sandbox container killed 10.3 s into its job was
reported as a lost executor, resubmitted by the playbook, and recovered with identical output.
Executor memory at Spark's minimum, 460 MB, produced no out-of-memory at 20 000 customers: the
feature job spilled 184 MB to disk and finished, so the out-of-memory remedy was never exercised
and that clause is untested. One customer holding a fifth of the lines left the slowest task at
1.0 times the median, so skew handling is not assessable at this size. The hypothesis therefore
has no verdict: its first clause held in both forms, and the other two could not be made to
happen on this node.

## 15.5 Code generation: what the gates stop, and how quickly

EXP-8 seeded twenty faults into generated Scala, five each of four classes, and recorded which of
the five gates that run before a job reads data stopped each one (Fig. 23). The static gates
stopped fifteen: the static scan of gate 2 refused all five forbidden API calls before any
compile, and all five type mismatches and five missing imports failed compilation at gate 4. No
static gate saw a wrong column name, because a name is a string until Spark resolves it; the
fixture tests of gate 5 stopped four of those five, in 252 to 336 s each. The fifth misspelled both
de-duplication keys, so the job fell back to removing whole-row duplicates, which the fixture's
exact duplicate cannot tell apart from the intended behaviour: it passed every gate. Nineteen of
twenty faults were stopped before a job read any data, and each failed identically on its second
attempt, where the circuit breaker stopped it. The share of faults repaired - the headline
hypothesis - is not measured: repair needs a language model, none was configured, and a measured
zero would describe the missing provider, not the repair loop.

**Fig. 23. Agent Efficiency Comparison.** *(figures/png/Fig 23 Agent Efficiency Comparison.png)*

## 15.6 Monitoring, leakage, and governance

**Drift (EXP-7).** A champion was scored on six undrifted months and on three kinds of drift. No
undrifted month raised an alarm (highest population stability index 0.031 against a 0.25
threshold). Covariate shift in one feature, or in one segment, was flagged on the first scored
month, before any label had matured, and in both cases PR-AUC rose; concept drift in one segment
barely moved the distributions (index 0.032) while PR-AUC fell from 0.554 to 0.328, visible only
once labels matured, when the performance trigger fired. Neither family of trigger catches both
cases. End to end on the platform, the chain the hypothesis describes held at every step: drift
was flagged on a 1 500-customer champion's latest cutoff (`monetary_90d`, index 6.54); the
scheduler started a drift-triggered retrain on exactly the three months that had arrived; the
challenger was compared with the champion on its test cutoff, with a PR-AUC difference of +0.004
(95% interval -0.024 to +0.034), not a significant lift, so it was neither eligible for an
override nor promoted; and the champion's own promotion waited for its two approvals.

**Leakage (EXP-9).** Two leaks were planted: a feature computed after the cutoff, and a lifetime
total computed over the whole dataset. With the defences bypassed they would have inflated
held-out PR-AUC by 0.547 and 0.498. Both were blocked - the first by name before any model was
fitted, the second, with an innocent name, only by the behavioural checks - and the point-in-time
control was not blocked.

**Governance (EXP-10).** An agent moving the champion alias, a requester approving their own
promotion, instructions hidden in product data, and a run continuing after its language-model
budget was spent were all refused, the refusals were written to the audit trail, and the hash
chain still verified across 178 chains.

## 15.7 The acceptance policy at the sizes it meets

Two escalations raised the question whether the policy was measuring the model or the sample, so
its two point-threshold rules were measured with no model involved. Labels drawn from scores
that are exactly their own probabilities - a perfectly calibrated predictor - exceeded AP-5's
0.05 calibration limit in 40% to 46% of 20 000 draws at 324 validation rows, 4% to 7% at 650, and
almost never from 1 300. A model with identical recall in every segment, simulated with the
platform's own segmentations, failed AP-6's 0.10 recall-gap limit in 65.5% of draws at 494 rows,
97.1% at 1 000 and 39.6% at 8 000: the rate rises while more segments pass the size rule and
falls as each is measured on more churners. A tenure-band gap of 0.331 on 6 263 rows is far
outside that noise; a gap of 0.115 on 494 rows is inside it. The verdicts are informative at
production sizes and unreliable at pilot sizes, which argues for stating such rules as
intervals (Section 21).

Table 10 collects the ten experiments: each hypothesis as written, the status of its run, the
verdict, and the result it turned on.

{{include: tables/table10_results_summary.md}}

---

## Evidence (not part of the manuscript)

- EXP-1: `experiments/EXP-1/20261013T132108Z/result.json` (13-10-2026, 13:21 to 13:43 UTC, quiet
  host): P 1 230.24 s, B3 53.24 s, B2 6.94 s; overhead share 0.957; gates 4/6/9 261.58/291.88/
  299.09 s, profile 116.4 s; P xgboost test PR-AUC 0.7405, escalated on AP-5 at 243 validation
  rows; like-for-like random_forest 0.7757 on 44 features, 390 training and 280 test rows. Earlier
  runs: `20261011T174929Z` (785.83 s, share 0.938), `20261012T145209Z` (2 296.01 s, share 0.944,
  busy host, NOTE.md).
- EXP-2: ladder `experiments/EXP-2/20261014T171107Z/result.json` (B2 28.4 s killed at 200 000, ok
  to 100 000 at 2.0x-6.2x B3's speed; B3 631.5 s at 200 000, JVM peak 2 326 MB; PR-AUC gaps at most
  0.004), the same shape as `20261013T134333Z` (B2 23.2 s killed; B3 591.5 s). P: NOTE.md of
  `20261013T134333Z` (host share ENOMEM after 1 604 s, DEC-54), `20261014T171107Z` (feature job
  killed at the 1 h limit; 5 493 s, DEC-55), `20261014T214513Z` (no result.json: attempt 1 to the
  hour, attempt 2 at 16 partitions 2 038 s with 579 s GC in a 1 394 s stage, attempt 3's dry run
  frozen with the VM; over four hours from 22:48).
- EXP-3: `experiments/EXP-3/20261011T064506Z/result.json`: work 27.8/22.1/20.9 s on 1/2/4 cores,
  speed-ups 1.258 and 1.33, doubling 1.057; utilisation 0.731/0.721/0.67; shuffle 21.3/23.8/24.3
  MB, no spill. The 0.84 figure: run of 07-10-2026.
- EXP-4: `experiments/EXP-4/20261011T065017Z/result.json` (3/3 detected and handled; recovery
  172.2 s after the decision).
- EXP-5: `experiments/EXP-5/20261011T111552Z/result.json`, identical to `20261010T094502Z` in 149
  of 155 measurements (the rest are wall clocks and an approval id).
- EXP-6: `experiments/EXP-6/20261011T070906Z/result.json`: cluster kill 31.9 s into the job, 1 lost
  task named by Spark, 1 platform attempt, identical output; local kill 10.3 s, `EXECUTOR_LOST`,
  resubmitted, identical; 460m spill 183 563 593 bytes; skew 1.0 against 1.0.
- EXP-7: `experiments/EXP-7/20261011T165840Z/result.json` (detector arms and the end-to-end chain;
  retrain run-2026-10-11-030).
- EXP-8: `experiments/EXP-8/20261011T084444Z/result.json` (15/20 static, 19/20 before data, fixtures
  252 to 336 s, breaker 19/19).
- EXP-9: `experiments/EXP-9/20261011T064933Z/` (+0.5470, +0.4975). EXP-10:
  `experiments/EXP-10/20261011T064957Z/` (4/4 refused, 178 chains).
- 15.7: `experiments/EXP-5/calibration_noise_floor.json`; `experiments/policy_floors/
  segment_gap_noise_floor.json`; published run's validation rows for AP-5/AP-6: 6 263.
