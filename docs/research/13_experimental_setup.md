# 13. Experimental Setup

This section describes where the experiments ran, what data they used, which configurations they
compared, and the protocol that kept their numbers honest (Fig. 19, Table 8). The results are
reported in Section 15; nothing here depends on them.

**Fig. 19. Experimental Architecture.** *(figures/drawio/Fig 19 Experimental Architecture.drawio)*

## 13.1 Environment

Every experiment ran on one workstation: an AMD Ryzen 5 2500U laptop processor with four
physical cores and eight hardware threads at a 2.0 GHz base clock, 10.96 GB of memory, and
Windows 11 Pro, with the platform in Docker containers on a WSL2 virtual machine given 5.27 GB of
memory and eight virtual CPUs. The requirements assume an environment with 32 GB of memory and a
four-node cluster for their larger tiers; the gap is recorded as a deviation, and every
experiment states which tier it could not run rather than estimating it. Each sandboxed Spark job
is clamped to four CPUs and 3 GB of memory with no swap beyond it, and the container that runs
the experiments has the same 3 GB limit.

The toolchain is pinned in one container image, and the versions below were read from the built
image rather than typed: Spark 4.1.3 with Scala 2.13.17 on Java 17 (Eclipse Temurin 17.0.20.1),
Python 3.11.16, scikit-learn 1.9.1, XGBoost 3.2.0, Optuna 4.9.0, SHAP 0.51.0, MLflow 3.16.1, and
PostgreSQL 16.15. No large language model provider was configured, so every agent ran on its
deterministic, template-based path; this is a limitation of the evaluation, discussed where it
matters, and not a feature of the design.

## 13.2 Data

The requirements name the Online Retail II dataset for the small-data experiment. It contains
transactions only - no customer table, web sessions, support tickets, or returns - so it cannot
support the churn definition or most of the feature catalogue. All experiments therefore use the
platform's seeded synthetic generator, which produces customers, products, promotions,
transactions, returns, web sessions, and support tickets over a chosen number of months with a
base churn rate of 0.35, and which can inject the faults the experiments need: missing keys and
values, duplicates, invalid prices, renamed columns, prices written as text, a skewed customer,
planted instructions in product text, and behavioural drift from a chosen month. The same seed
(42) produces the same data, so every configuration in an experiment reads identical inputs.
Datasets range from 400 customers, about 0.4 MB of Parquet, to PENDING (EXP-2's re-run of
14-10-2026: 200 000 customers and 154 MB on 13-10-2026, the rung at which the first configuration
failed).

## 13.3 Configurations

Four configurations are defined. **B1** is a person following a written runbook; its measure is
engineer hours from a time sheet, and with no second person available it is recorded as not
measured rather than estimated. **B2** is a single-node Python pipeline - pandas and
scikit-learn in one script, written as a competent engineer would write it. **B3** is a
hand-written PySpark pipeline with scikit-learn, a fixed sequence of steps with no repair loop or
governance. **P** is TRIAD itself. B2 and B3 compute a twelve-feature subset of TRIAD's
catalogue, use the same temporal split as P - P's own cutoffs - and screen the same model
families, so that a difference in quality is not a difference in procedure. Because the feature
sets still differ, EXP-1 also refits every configuration's model shortlist on one shared feature
table, which isolates the modelling from the feature engineering.

## 13.4 Protocol

Each experiment runs in a fresh container, strictly one at a time, and writes its configuration,
environment description, raw observations, and result to a timestamped folder; the latest result
is also kept as `latest.json`. The sealed test cutoff is scored exactly once per candidate and is
never used to choose anything. Intervals are 95% bootstrap intervals over 1 000 resamples, and
the comparison of a challenger with the champion uses a paired bootstrap on the same test rows
(equation (10)). A protocol change is recorded as a decision before the experiment is re-run, and
must be justified by a measurement that involves no test data - for example, EXP-5 was enlarged
because a separate study of the calibration estimator showed its 324-row validation split to be
inside the estimator's noise floor (Section 15). Failures caused by the environment rather than by
the system under test, such as the virtual machine running out of memory, are recorded as such
and the experiment re-run, never reported as results. An injected fault must be seen to land -
a container killed while its job runs, a failure classified as the fault it was meant to be -
and a claim whose fault did not land is reported as untested, never as refuted.

**Table 8. Experimental Configurations**

| Experiment | Question | Data | Independent variable | Dependent measures |
| --- | --- | --- | --- | --- |
| EXP-1 Small dataset | Does P run end to end without manual steps, and what does its overhead cost? | 400 customers, 24 months | configuration (B1, B2, B3, P) | wall clock, manual steps, LLM calls, test PR-AUC, like-for-like PR-AUC |
| EXP-2 Single-node limit | Is single-node pandas the first to fail as data grows? | 400 to 200 000 customers, stopping at the first failure | dataset size, configuration (B2, B3) | completion, wall clock, peak memory |
| EXP-3 Parallelism | Does doubling the cores give at least 1.6 times speed-up? | 6 000 customers | Spark parallelism (1, 2, 4 cores) | wall clock, work time, shuffle bytes, spill, task skew, core utilization |
| EXP-4 Schema errors | Are schema faults stopped before they corrupt anything? | 400 customers | fault (renamed key, price as text, missing table) | where detected, mapping proposed, approval, time to recovery, rows affected |
| EXP-5 Missing values | Are key nulls quarantined without losing rows, and does quality decline gradually? | 2 000 customers, each condition in its own process | non-key null rate 0-50% with keys at 1%; one condition at 10% keys | quarantine, row accounting, test PR-AUC with interval, calibration error, approval requested |
| EXP-6 Spark failures | Are infrastructure failures recovered or escalated, never lost? | 400 customers; the memory arm 20 000 | fault (low memory, killed container, skew) | fault landed, recovered, attempts, remedy, output identical, spill, task skew |
| EXP-7 Drift | Do the monitors detect drift and fire retraining without false alarms? | 1 500 customers | drift type (covariate, segment, concept) | PSI, months to detection, triggers, false alarms |
| EXP-8 Code faults | Do the gates stop faulty generated code before execution? | 20 seeded faults | fault class (four, five each) | gate that stopped it (1 to 5), time to verdict, escalation on a repeated fingerprint |
| EXP-9 Leakage | Are leaked features blocked? | 1 200 customers | leak type (post-cutoff, lifetime total) | which defence blocked it, PR-AUC inflation |
| EXP-10 Governance | Are attempts to bypass governance refused and recorded? | one run per attempt | attempt (alias move, self-approval, injection, budget) | refused, audited, chain verified |

---

## Evidence (not part of the manuscript)

- Hardware: `Get-CimInstance Win32_Processor` on 08-10-2026 (AMD Ryzen 5 2500U, NumberOfCores 4,
  NumberOfLogicalProcessors 8, MaxClockSpeed 2000); `docker info` (NCPU 8, MemTotal 5 659 824 128
  bytes = 5.27 GiB); host RAM and OS: `configs/versions.yaml`, ADR-0002.
- Versions: `configs/versions.yaml` (read from the image); PostgreSQL `SHOW server_version` = 16.15.
- Clamp and no swap: `sandbox/runner.py` (`HOST_LIMITS`, `memswap_limit`), DEC-35; ST-07 test.
- Generator: `datasets/synthetic/generator.py` (`GeneratorConfig`, base churn rate 0.35).
- Configurations: `experiments/baseline_b2.py`, `baseline_b3.py`, `baselines.py`
  (`BASELINE_FEATURES`, twelve features), `exp01_small_dataset.py` (like-for-like trial).
- Sizes: the constants at the top of each `experiments/exp*.py`; EXP-2 ladder and ceilings,
  EXP-5 size: DEC-33, DEC-34, DEC-35. Faults that must land, EXP-5's process per condition and
  same-draw reference, EXP-6's memory arm (two pilots, `experiments/EXP-6/pilot_20261010.md`),
  EXP-8's anchors and two-attempt breaker measurement: DEC-42.
- Bootstrap: `python_ml/triad_ml/evaluate.py` (`DEFAULT_BOOTSTRAP = 1000`, `bootstrap_difference`).
