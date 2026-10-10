# Appendix

The appendix gives the algorithms, configurations, and settings a reader needs to reproduce the
work. Every value is copied from a file in the repository, named in the Evidence block.

## A. Algorithms

**Algorithm 1. A governed run.**

```text
input:  versioned inputs V, pipeline definition P, policy G
state  <- RECEIVED                                   # persisted; every move checked and committed
check the input contracts of V                       # refusal -> REJECTED_INPUT
profile V; quality Q <- equation (1)                 # Q below threshold -> ESCALATED
for stage in (clean, features, labels):              # P3-P7
    route <- first routing rule matching (stage, sizes)        # recorded with its evidence
    job   <- Algorithm 2(template of stage)                    # nine gates
    out   <- run job in the sandbox; check contract; publish atomically
apply hygiene H2, H3, H7 (names); split by time with 90-day gaps; H4 (training rows)
candidates <- train five families
(outcome, model, evidence) <- Algorithm 3(candidates)
record the acceptance decision (rules, evidence, rejected alternatives)
if outcome = PROPOSE:   state <- APPROVAL_REQUIRED; wait for a person who is not the requester
if outcome = ESCALATE and the failed rules are AP-5/AP-6 and AP-8 holds:
                        register the candidate; a promotion needs two approvers (DEC-27)
on approval:            move the champion alias; smoke test (roll back on failure);
                        score customers with reasons; measure drift; claim-checked report
```

**Algorithm 2. Template-constrained generation with nine gates.**

```text
input:  template T with one slot s, inputs, attempt budget k
for attempt in 1..k:
    body <- propose(s)                      # LLM if configured; else T's reference body
    J    <- T with s := body                # rendered; must equal T outside s exactly
    for g in gates 1..4 (structure, static scan, dependencies, compile):
        if g fails: diagnose(g.error) -> remedy; continue to next attempt
    gate 5: run the fixture suite on J      # cached by (J, core library, fixtures, build)
    gate 6: run J on r(n) rows, equation (5), in the sandbox, small profile
    run J on all rows in the sandbox, medium profile
    gates 7-9: output contract, content-hashed storage, final contract
    if all pass: return J
    if this error's fingerprint repeated: stop                 # circuit breaker
escalate with the diagnoses
```

The gates run in order of cost. The four static gates take from a fraction of a second to under
a minute and stop most faults before any data is read (Section 15); the fixture tests take four to
five minutes for new code and nothing for code that has passed them before; the run on a sample
costs a sandbox start; and only a job that has passed them all runs on the full input.
The circuit breaker ends a repair loop that is making no progress: an attempt that fails with an
error fingerprint already seen stops the stage at once, rather than spending the rest of the
budget on a remedy that has already failed.

**Algorithm 3. Model selection (stages A to E).**

```text
A: screen every family on validation: AP-3, AP-7, leakage alarm; keep the survivors
B: tune the top two survivors with Optuna (TPE, 30 trials, 900 s, median pruning)
   rank by validation PR-AUC; AP-2: logistic regression wins unless beaten by >= 0.01;
   AP-1: the chosen model's PR-AUC must be at least the churn rate + 0.10
C: calibrate the best (isotonic if >= 500 rows, else sigmoid); threshold = top 15% of validation
   AP-4, AP-5 with remedies: sigmoid, then sigmoid without class weighting
   AP-6 within each segmentation; on failure, the segment-aware threshold review (not applied)
D: score the test cutoff once
E: AP-8 against the champion: paired bootstrap, equation (10)
   if a rule in C failed: ESCALATE (override-eligible if AP-8 holds or there is no champion)
   elif no champion or AP-8 holds: PROPOSE; else KEEP the champion
```

AP-2 encodes a preference for the simpler model: logistic regression is kept unless another family
beats its validation PR-AUC by at least 0.01, because a model whose reasons are an exact
decomposition of its score is easier for a reviewer to check and to defend. Stage D scores the
test cutoff once, after every choice has been made on validation data, so that the reported test
metrics are not the product of selecting on them.

**Algorithm 4. The between-run configuration loop (one setting, FR-037).**

```text
on starting stage s of a run of pipeline P:
  e <- the latest successful execution of s in an earlier run of P that recorded stage metrics
  if none: k <- 8; run s; stop
  k <- e.shuffle_partitions
  if e spilled any bytes and k < 64:              k' <- min(2k, 64)   (OPT-1)
  elif median reduce-stage task < 50 ms and k > 2: k' <- max(k/2, 2)   (OPT-2)
  else: run s with k; stop
  record the change k -> k' with e's measurements; run s with k'
  record before (e) and after (this run): faster, slower, or within +/-10% of e's wall clock
```

The loop never changes a setting during a run, never leaves the allowlist's range, and on a
replay reuses the replayed run's value, because a replay exists to reproduce a run rather than
improve on it. Its comparison is between two single runs, which is why a difference inside the
noise band is recorded as no difference.

## B. Spark configuration

Every generated job is submitted as `spark-submit --master local[2] --class <generated object>
--jars <core library>` with `spark.ui.enabled=false`, `spark.driver.memory=1g`,
`spark.sql.ansi.enabled=true`, `spark.sql.session.timeZone=UTC`, and
`spark.sql.parquet.outputTimestampType=TIMESTAMP_MICROS`, eight shuffle partitions on a stage's
first run and the optimisation loop's value after that (Algorithm 4), which a failure remedy may
still raise within a run, and `SPARK_LOCAL_IP=127.0.0.1` because the container has no network.
Every job registers the stage-metrics listener before its first read and stops Spark before it
writes its report, so that every stage's measurements are complete. Spark 4.1.3, Scala 2.13.17,
and Java 17 come from the pinned toolchain image.

**Resource profiles** (equation (4); clamped to 4 CPUs and 3 GB on the evaluation workstation):

| Profile | Used for | CPUs | Memory | Processes | Timeout | Scratch |
| --- | --- | --- | --- | --- | --- | --- |
| small | dry runs (gate 6), tests | 2 | 4 GB | 512 | 10 min | 2 GB |
| medium | full job execution | 4 | 16 GB | 1 024 | 60 min | 10 GB |
| large | large ML jobs | 8 | 32 GB | 2 048 | 3 h | 50 GB |

## C. Agent workflow and governance settings

| Setting | Value |
| --- | --- |
| Code repair attempts per stage | 3 |
| Infrastructure retries | 2 |
| Optimization cycles | 2 |
| Identical error fingerprints before the circuit breaker | 2 |
| Wall clock per run | 240 min |
| LLM calls and cost per run (when configured) | 60 calls, 3.0 USD |
| Approval expiry | 72 h (expired counts as rejected) |
| Task lease / worker poll | 300 s / 1 s |
| Scheduler tick | 300 s |
| Retraining cooldown | 7 days |
| Volume anomaly | modified z-score above 3.5 against the median day; at least 14 whole days |
| Distribution shift | PSI at least 0.10 (moderate) or 0.25 (major) on the previous version's bins |
| Rule breaches reported | non-positive price, zero quantity (the rules cleaning quarantines on) |
| Shuffle partitions (loop) | 2 to 64; doubled on spill, halved below a 50 ms median reduce task |
| Before-and-after noise band | 10% of the earlier run's wall clock |
| Minimum profile quality to plan | 0.70 |
| Cleaning passes | 2 |
| Risk bands | high at or above 0.66, medium at or above 0.33 |

**Acceptance policy:** AP-1 PR-AUC at least churn rate + 0.10; AP-2 margin 0.01; AP-3 ROC-AUC at
least 0.70; AP-4 Brier skill at least 0.10; AP-5 ECE at most 0.05 after calibration; AP-6 recall gap
at most 0.10, segments of at least 50 rows and 30 churners; AP-7 30 minutes per family, 200 MB,
5 minutes per million rows scored; AP-8 positive lower bound; leakage alarm at validation ROC-AUC
0.98.

**Retraining rules:** PSI 0.25 on any top-10 feature or 0.10 on three (RT-2); score PSI 0.20 or a
50% change in the high-risk share (RT-3); a PR-AUC drop of 0.05 from deployment (RT-1); three
declining matured cutoffs (RT-4); 20% more labelled data and at least 500 new rows (RT-5); 30 days
(RT-6); any catalogue, schema, or definition change (RT-7 to RT-9).

## D. Benchmark settings

The data come from the seeded generator `datasets/synthetic/generator.py` (base churn rate 0.35,
start date 2024-01-01). The configurations of the experiments in Section 15 - customers, months,
seeds, fault rates, and the baselines - are listed with their results in Section 13, Table 8, and
are stored with each experiment's output.

## E. A code template

The feature template's only variable part is its slot; everything else, including the core
library calls that validate, stage, and account for the output, is fixed:

```scala
object FeatureJob extends TriadJob {
  override def name: String = "churn-features"
  override def transform(spark: SparkSession, in: Inputs, cfg: JobConfig): DataFrame = {
    // BEGIN GENERATED SLOT: transform
    ... the composition of the feature groups, point-in-time at each cutoff ...
    // END GENERATED SLOT: transform
  }
}
```

---

## Evidence (not part of the manuscript)

- Spark configuration: `sandbox/runner.py` (`spark_submit_request`); toolchain: ADR-0001.
- Profiles: `sandbox/runner.py` (`RESOURCE_PROFILES`, `HOST_LIMITS`), DEC-25.
- Settings, policy, retraining, tuning, threshold, risk bands: `pipelines/churn_v1.yaml`;
  `agents/policies/governance.yaml`; lease and poll: worker start-up log line.
- Template: `scala_spark/jobs/src/main/scala/triad/jobs/FeatureJob.scala`.
- Generator defaults: `datasets/synthetic/generator.py` (`GeneratorConfig`).
- Algorithm 4 and the loop settings: `agents/optimizer.py` (`QUOTA`, `TINY_TASK_MS`, `NOISE`),
  `ChurnPipeline._plan_shuffle_partitions`; DEC-31. Listener: `TriadJob.scala`, DEC-29.
- Anomaly settings: `dataeng/profile.py` (`VOLUME_Z`, `VOLUME_MIN_DAYS`, `SHIFT_PSI`,
  `BUSINESS_RULES`); DEC-30.
