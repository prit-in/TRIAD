# 15. Results and Findings

This section reports what the ten experiments of Section 13 measured, grouped by the question each
answers. Every number is read from an experiment's stored output; Table 10 is generated from the
same files, and so are Fig. 21 to Fig. 23. A hypothesis that did not hold is reported as not
holding, with what the measurement shows instead.

<!-- RESULTS-PENDING: 15.1 performance (EXP-1), 15.2 scalability ladder (EXP-2), 15.4 data
robustness (EXP-4, EXP-5, EXP-6) are written once those experiments complete. -->

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

---

## Evidence (not part of the manuscript)

- EXP-3: `experiments/EXP-3/latest.json` (run of 07-10-2026 21:57 UTC); per-stage observations in
  `trials[].observations.stages`; host CPU: Section 13 evidence.
- EXP-8: `experiments/EXP-8/latest.json` (07-10-2026 22:54 UTC), `raw/per_class.json`.
- EXP-7: `experiments/EXP-7/latest.json` (07-10-2026 21:57 UTC), `raw/thresholds.json`.
- EXP-9: `experiments/EXP-9/latest.json` (07-10-2026 21:56 UTC).
- EXP-10: `experiments/EXP-10/latest.json` (07-10-2026 21:56 UTC).
