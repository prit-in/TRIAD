# 15. Results and Findings

This section reports what the ten experiments of Section 13 measured, grouped by the question each
answers. Every number is read from an experiment's stored output; Table 10 is generated from the
same files, and so are Fig. 21 to Fig. 23. A hypothesis that did not hold is reported as not
holding, with what the measurement shows instead.

<!-- RESULTS-PENDING: 15.1 performance (EXP-1) and 15.4 data robustness (EXP-4, EXP-5, EXP-6)
are written once those experiments complete. -->

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

## 15.5 Code generation: what the gates stop, and how quickly

EXP-8 seeded twenty faults into generated Scala, five each of four classes, ran the four gates
that precede any execution - structured output, static checks, dependencies, and compilation -
and recorded which gate stopped each fault (Fig. 23). Fifteen were stopped. All five forbidden
API calls were refused by the static scan of gate 2 in 0.2 s each, without being compiled; all
five type mismatches and all five missing imports failed compilation at gate 4, in 37 to 41 s
each, which is the cost of a Scala compile. None of the five wrong column names was stopped: a
column name is a string until Spark resolves it, so the job compiles and passes all four gates.
The requirements expected semantic faults to escalate more often than syntactic ones; the
measurement is stronger - they are invisible to static analysis entirely. Gates 5 and 6, which
run the job on fixtures and on a data sample, are where such a fault can first appear; this
experiment did not run them, so whether they catch it is not measured here.
Every detected fault produced the same error fingerprint on three successive attempts, so the
circuit breaker stops an unrepairable fault instead of spending the attempt budget. The share of
faults *repaired* - the experiment's headline hypothesis - is not measured: repairing a slot
requires a language model, none was configured, and a measured zero would describe the missing
provider, not the repair loop.

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
refusals were written to the audit trail, and the hash chain still verified across 101 chains.

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

---

## Evidence (not part of the manuscript)

- EXP-2: `experiments/EXP-2/20261008T171242Z/result.json` (08-10-2026, 17:12 to 18:15 UTC). The
  earlier run of 16:17 stopped because the data generator itself was OOM-killed at 100 000
  customers (DEC-37, committed as `20261008T161715Z`). PR-AUC pairs B2/B3: 0.7929/0.7923,
  0.7761/0.7724, 0.7662/0.7649, 0.7848/0.7846, 0.7766/0.7780. Operator observation during B3's
  100 000 run, not recorded by the harness: `docker stats` at 17:55 UTC showed the container at
  2.917 of 2.93 GiB, and `ps` the B3 JVM at 2 261 888 kB resident.
- EXP-3: `experiments/EXP-3/latest.json` (run of 07-10-2026 21:57 UTC); per-stage observations in
  `trials[].observations.stages`; host CPU: Section 13 evidence.
- EXP-8: `experiments/EXP-8/latest.json` (07-10-2026 22:54 UTC), `raw/per_class.json`.
- EXP-7: `experiments/EXP-7/latest.json` (07-10-2026 21:57 UTC), `raw/thresholds.json`.
- EXP-9: `experiments/EXP-9/latest.json` (07-10-2026 21:56 UTC).
- EXP-10: `experiments/EXP-10/latest.json` (07-10-2026 21:56 UTC).
- 15.7: `experiments/EXP-5/calibration_noise_floor.json` (20 000 trials per cell; 324 rows:
  share above 0.05 = 0.421/0.458/0.404 across three concentrations); `experiments/policy_floors/
  segment_gap_noise_floor.json` (false-alarm rate, all four segmentations: 0.655, 0.971, 0.951,
  0.809, 0.396); published run's validation rows for AP-5/AP-6: 6 263 (`ece_n_rows`).
