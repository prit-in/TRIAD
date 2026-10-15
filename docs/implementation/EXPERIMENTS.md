# The ten experiments

PRD section 33 defines ten experiments. Each writes its configuration, raw measurements and an
environment record to `experiments/<EXP-n>/<timestamp>/`, with the newest run also at
`experiments/<EXP-n>/latest.json`. Run one with `python scripts/run_experiment.py EXP-9`, or all
of them in sequence with `--all`; `--list` shows which need Spark or sbt.

Nothing in this document is copied from an intention. Every number below is read back from a
`result.json` that the harness wrote, and where a measurement is absent it says so rather than
carrying an estimate.

## Status

| ID | Title | Status | Hypothesis | Where |
| --- | --- | --- | --- | --- |
| EXP-1 | Small dataset against the non-agentic baselines | **partial** | **held** (P end to end in 1 230 s with no manual step, 0.957 of it beyond B3's time; escalated on AP-5 at 243 rows) | `experiments/EXP-1/latest.json` |
| EXP-2 | Medium dataset: where a single node gives out | **partial** | **did not hold** (B2 the first to fail, at 200 000 customers, and B3 finished; P did not finish there on this 5.27 GB VM, in three attempts, and was 8.7 times B3's time when it stopped) | `experiments/EXP-2/latest.json`, NOTE.md of each kept run |
| EXP-3 | Scaling with parallelism | **partial** | **did not hold** | `experiments/EXP-3/latest.json` |
| EXP-4 | Schema errors | **completed** | **held** (every fault detected and handled; the mapping proposed, approved, then applied) | `experiments/EXP-4/latest.json` |
| EXP-5 | Missing values | **completed** | **did not hold** (PR-AUC flat within noise; key nulls quarantined, non-key nulls cost no row, the over-limit plan paused for approval) | `experiments/EXP-5/latest.json`, `experiments/EXP-5/calibration_noise_floor.json` |
| EXP-6 | Spark failures and recovery | **partial** | **no verdict** (the executor loss absorbed with identical output; no out-of-memory lands on this node; skew not assessable at this size) | `experiments/EXP-6/latest.json` |
| EXP-7 | Model degradation | **completed** | **held** (covariate shift flagged before any label matured, concept drift only by the performance trigger; drift to retrain to comparison to approval held end to end) | `experiments/EXP-7/latest.json` |
| EXP-8 | Scala compile failures | **partial** | **no verdict** (repair needs an LLM; 19 of 20 faults stopped before reading data, forbidden APIs never run, the breaker trips at attempt 2) | `experiments/EXP-8/latest.json` |
| EXP-9 | Leakage sentinel | **completed** | **held** | `experiments/EXP-9/latest.json` |
| EXP-10 | Governance bypass | **completed** | **held** | `experiments/EXP-10/latest.json` |

`partial` means the experiment ran and produced measurements but could not run the workload the
PRD specifies, and the limitation is recorded in the result. The harness refuses a `partial` or
`not_run` result that names no limitation, and refuses a verdict on a hypothesis when no
measurement was recorded.

All ten have final runs: EXP-3, EXP-4, EXP-5, EXP-6, EXP-7, EXP-8, EXP-9 and EXP-10 on
11-10-2026, EXP-1 on 13-10-2026, and EXP-2 on 13-10 to 15-10-2026 (its last run of P on the code
after DEC-55). **Five hypotheses held, three did not (EXP-2, EXP-3, EXP-5), and two have no
verdict.** EXP-8's has
none because its headline clause, the repair rate, needs a language model; EXP-6's because the
out-of-memory fault cannot be made to land on this node and skew handling cannot act at these sizes
- each recorded as untested, not as refuted. The sections that are not a plain "held" are the ones
worth reading first.

## What each experiment found

### EXP-9, leakage sentinel — held

Run `20261011T064933Z` (11-10-2026); the same results as the runs of 10-10-2026.

| Condition | Held-out PR-AUC | Inflation | Static check | Sentinel test | Leakage alarm |
| --- | --- | --- | --- | --- | --- |
| point-in-time control | 0.4530 | — | pass | pass | pass |
| post-cutoff feature | 1.0000 | +0.5470 | **blocked** | **blocked** | **blocked** |
| lifetime total | 0.9505 | +0.4975 | pass | **blocked** | **blocked** |

The finding is the middle column of the last row. `customer_lifetime_orders` is a name no static
rule can object to, so the cheap check misses it entirely; only the behavioural checks — the
point-in-time sentinel and the implausible-ROC-AUC alarm — catch it. The two kinds of defence
are not redundant, and a project that had only the name list would have shipped this leak.

The first version of this experiment reported its hypothesis as failed, correctly: the generator
picked churners first and then gave them distinctive behaviour, so `recency_days` separated the
classes perfectly and the honest control scored 0.99 and tripped the leakage alarm itself.
Behaviour is now drawn first and the label second, from a noisy function of it. A simulation in
which a correct model is indistinguishable from a leaking one cannot test this.

### EXP-10, governance bypass — held

Four attempts, all against the real database, policy engine and tool registry. 4 of 4 blocked;
3 audit entries added; the hash chain still verifies across 178 chains afterwards (run
`20261011T064957Z`, 11-10-2026; the same outcome as `20261010T094439Z`, across 143 chains then).

- **Moving the champion alias**: three independent refusals — no such tool is registered, the
  policy denies `registry.set_alias` outright, and the registry client refuses a promotion with
  no recorded approval.
- **Self-approval**: refused, the approval stays pending, and a genuinely separate approver then
  succeeds — so the control is separation of duties and not a blanket refusal.
- **An instruction hidden in product data**: 3 of 3 seeded strings flagged, no card number
  survived masking, and all four tools the strings asked for do not exist.
- **Budget exhaustion**: stops at the fifth of five calls, not later.

Alerts are 0 for these four, and the result says so plainly: all four are refused before any
state changes, and there is no incident to alert on when a tool does not exist.

This experiment found a real gap. The injection scanner caught two of three seeded strings and
read straight past `<|im_start|>system …<|im_end|>`. Chat-template control tokens are a
different class from instruction phrases — a tokenizer may treat one as a role delimiter, and a
data fence is ordinary text that loses to that. They are now scanned and broken up before
fencing.

### EXP-7, model degradation — held, completed

Run `20261011T165840Z` (11-10-2026 16:58 to 17:46 UTC), on the code after DEC-48 and DEC-49.

| Arm | Max PSI | PSI flagged after | PR-AUC | 10% drop visible after | Triggers |
| --- | --- | --- | --- | --- | --- |
| six stable months | 0.031 | never (0 false alarms) | 0.5064 mean | — | — |
| covariate shift, one feature | 0.311 | 0 scoring months | 0.5538 → 0.7079 | never | RT-2, RT-3, RT-5, RT-6 |
| covariate shift, one segment | 1.084 | 0 scoring months | 0.5538 → 0.7405 | never | RT-2, RT-3, RT-5, RT-6 |
| concept drift, one segment | 0.032 | **never** | 0.5538 → 0.3281 | 0 label months | **RT-1**, RT-5, RT-6 |

The two kinds of drift behave oppositely, and separating them is the result. PSI catches
covariate shift on the first scoring run with no labels at all — and covariate shift did not
hurt: widening a predictive feature raised PR-AUC, so treating a PSI flag as evidence of damage
would be wrong. Concept drift is the reverse: its max PSI (0.032) is indistinguishable from the
stable arm, nothing flags, and the harm appears only once labels mature, where RT-1 catches it.
Neither family of trigger would catch both cases, which is why RT-1 and RT-4 exist alongside
RT-2 and RT-3.

Two corrections to the experiment were needed to get there, both of which would otherwise have
produced a confident wrong answer. The deployment PR-AUC was first measured in-sample, so every
later month looked 10% worse immediately and the label delay came out as 0 for both drift types
— what that measured was the ordinary train/test gap. And the first two arms only moved feature
distributions, so PR-AUC went *up*; reporting that as degradation would have been wrong, so
concept drift is a third arm rather than a relabelling of the first two.

**End to end, on the platform itself.** The detector arms above do not use the platform's
retraining; one arm runs the hypothesis's whole chain through it - drift flagged, retraining
started, the challenger compared with the champion, promotion waiting for approval - and each
part is judged on its own, untested if the chain never reached it (DEC-48). 1 500 customers; the
champion is trained by the platform's pipeline on 24 synthetic months, the generator's
basket-size and channel-mix shift runs at full strength from month 18 (inside the champion's test
window, after its training cutoffs), and three more months arrive afterwards.

- **The champion's promotion waited.** It escalated on AP-6 and took the two-approver path
  (`model.promote_with_failed_check`): the run sat in APPROVAL_REQUIRED with no champion until
  both approvals were given, then became version 1.
- **Drift was flagged.** The champion's publish flagged `monetary_90d` (PSI 6.54) while its score
  distribution barely moved (PSI 0.018); publish's own retraining evaluation, under the
  configured 7-day cooldown, held back (`cooldown`).
- **Retraining started, on the data that arrived.** With the arrived months registered (7 new
  dataset versions), the scheduler's evaluation fired RT-2 and queued run-2026-10-11-030 as a
  drift-triggered retrain of the same model, reading exactly the versions that had arrived.
- **The challenger was compared with the champion.** AP-8 scored both on the challenger's test
  cutoff: PR-AUC difference +0.004, 95% interval [-0.024, +0.034] - no significant lift, so not
  eligible for an override. It also failed AP-6 (tenure-band recall gap 0.289) and escalated;
  nothing was promoted and the champion stayed version 1.

Earlier end-to-end runs are kept with notes: `20261010T175642Z`, whose retrain read unrelated data
(fixed by DEC-48); `20261010T184800Z`, not an experiment run; and `20261011T075932Z`, whose
harness misread the escalated retrain as never compared (fixed by DEC-49).

Limits: the scheduler's evaluation uses a 0-day cooldown, because the champion is minutes old,
with publish's own 7-day evaluation recorded beside it; the approvals are given by the harness
acting as the approvers, so they show that promotion waits for a decision, not how long a person
takes; and replay is over synthetic months rather than Online Retail II - only the generator can
be told to shift one feature or change one segment's behaviour on demand.

### EXP-4, schema errors — held, completed

Run `20261011T065017Z` (11-10-2026 06:50 to 07:08 UTC; `20261010T121337Z` and
`20261009T184842Z` had the same outcomes), 400 customers, every condition through
ingestion, profiling and cleaning; the baseline and the dropped table also through the feature job.

| Fault | Detected at | What the platform did | Row accounting |
| --- | --- | --- | --- |
| clean baseline | not detected | cleaned; all five feature groups available, 46 feature columns | balanced |
| renamed required column (`customer_id` -> `cust_id`) | **ingest** | refused; proposed `cust_id -> customer_id` (name similarity 0.778, types match); a `schema_change` approval pending, nothing registered, applying it refused; after approval the renamed copy registered and cleaned 172.2 s later (135.6 s on 10-10-2026) | balanced |
| price as text with currency codes | **clean** | converted by `try_cast` after the currency code was checked against the row's own `currency` (DEC-36); 0 values quarantined | balanced |
| dropped optional table (`support_tickets`) | **features** | the customer-service group marked unavailable; none of its 6 catalogue features produced (40 columns) | balanced |

3 of 3 faults detected and handled correctly, 0 mappings applied without a person, 0 conditions
lost rows silently. The approval was given by the harness acting as a Data Engineer, through the
same `GovernanceService.decide` the API calls, so the time to recovery covers the platform's work
after a decision and not the time a person takes to make one.

Earlier runs, kept as records, are why the protocol looks the way it does:

- `20261009T174535Z` held on a narrower reading: the renamed column was refused but **no mapping
  was proposed** - `Remedy.PROPOSE_MAPPING` existed and nothing used it - and the feature stage
  was not run, so "the missing optional table disables its features" was unmeasured and the
  condition was excluded from the detection rate. `optional_table_absent` was also recorded as
  true for the baseline, which had dropped nothing. The mapping proposal is DEC-39.
- `20261009T003414Z` failed its baseline on the CleanJob varargs deprecation that `-Wconf:any:error`
  made fatal (fixed in `CleanJob.scala`).
- Before those, EXP-4 found FR-007's contract enforcement unreachable (no caller passed a
  contract), the generator's `rename_column` broken, the profiler calling a nullable-by-design
  column blocking, and prices as text escalating instead of being converted (DEC-36).

### EXP-6, Spark failures — no verdict, partial

Run `20261011T070906Z` (11-10-2026 07:09 to 07:59 UTC). An arm counts only if its fault was seen
to land (DEC-42); the two cluster arms ran on the compose cluster, which EXP-6 starts for them
alone (DEC-46, DEC-48).

| Arm | Fault landed | Attempts | First failure | Remedy | Output against its reference |
| --- | --- | --- | --- | --- | --- |
| clean baseline, 400 customers (local mode) | - | 1 | - | - | reference |
| container killed 10.3 s into the job (local mode) | **yes** | 2 | `EXECUTOR_LOST` | resubmit | **identical** |
| clean run on the cluster | - | 1 | - | - | **identical** to local mode's |
| executor JVM killed 31.9 s into the cluster job, 2 s after its first task began | **yes**: Spark's log names task 0.0 of stage 0.0 lost with the executor | 1 | - | none needed: Spark re-ran the task on a replacement executor | **identical** |
| memory reference, 20 000 customers | - | 1 | - | - | reference (no spill) |
| executor memory 460m, the same 20 000 | **no** out-of-memory; 183.6 MB spilled | 1 | - | - | clean identical; features differ in the last bits of monetary sums |
| one customer holds 20% of the lines | - (task skew 1.0) | 1 | - | - | not compared (different data) |

- **Spark's own task retries absorb a lost executor - the PRD's claim, tested as written.** On the
  cluster the driver stays in its sandbox container and the executor runs in the worker; killing
  the executor under a running task left `ERROR TaskSchedulerImpl: Lost executor 0 ... exited with
  code 137` and `WARN TaskSetManager: Lost task 0.0 in stage 0.0 ... ExecutorLostFailure` in the
  driver's log, and the same job finished, with no platform resubmission, byte-identical to the
  clean run. Five pilots got the fault to land there (`experiments/EXP-6/pilot_cluster_20261011.md`):
  a network the sandboxed driver could not reach the master on, a kill script that killed itself,
  and two kills too early to catch a task.
- **In local mode the loss is absorbed by the platform instead**: the runner read the engine's exit
  (a kill from outside, not the kernel's memory limit), the playbook resubmitted, and the output was
  identical.
- **Out-of-memory recovery is untested here.** Two pilots and the run itself found no heap Spark
  starts with at which these jobs fail: they spill instead (`experiments/EXP-6/pilot_20261010.md`).
  Below about 450 MiB Spark refuses to start, which is `RESOURCE_CONFIG_INVALID` and a person's
  (DEC-20); and even a landed out-of-memory could only be remedied with more partitions, because
  raising the local JVM's heap is `spark.driver.memory`, outside the allowlist (DEC-41).
- **Spill changed the last bit of some sums.** The diagnostic
  (`experiments/EXP-6/diag_feature_determinism_20261010.md`) rebuilt both feature tables: every
  key and every non-monetary feature identical, three monetary sums different by less than 1e-16
  relative. Bit-for-bit reproduction holds under the same plan (ATC-18), not across memory
  settings; summing money as `decimal` would make it exact.
- **Skew handling is not assessable at this size**: no partition approaches adaptive execution's
  thresholds, and the skewed feature job's slowest task was the median's.

The run of 10-10-2026 (`20261010T113128Z`) measured the same local arms (container kill identical,
460m spilling 178.8 MB, no skew) without the cluster. What the earlier runs found, all fixed: the
content hash folded Spark's per-job file names in and
depended on row placement (a table's content is now the multiset of its rows, ATC-18); a 256m
driver is below Spark's minimum, so that arm never was an out-of-memory (DEC-20); the kill thread
called a docker CLI the image lacked; a local-mode heap exhaustion was read as a driver fault or a
Python exception, the memory raise read only gigabytes, and a killed container was `UNKNOWN`
(DEC-41); and every first compile in a fresh container downloaded sbt and its dependencies,
because the caches were mounted where nothing looked (DEC-43).

### EXP-1, TRIAD against the non-agentic baselines — held, partial

Run `experiments/EXP-1/20261013T132108Z/` (13-10-2026, 13:21 to 13:43 UTC, 400 customers), on the
code after DEC-53, with P, B2 and B3 each in a process of their own (DEC-44) and nothing else
running on the host.

| Configuration | Wall clock | Test PR-AUC | Model | Features | Hand-written lines | Manual steps | Audit entries |
| --- | --- | --- | --- | --- | --- | --- | --- |
| P (TRIAD) | 1 230 s | 0.741 (escalated) | xgboost | full catalogue (44) | 0 | 0 | 17 |
| B2 (pandas only) | 6.9 s | 0.790 | logreg | 12 | 188 | 0 | 0 |
| B3 (Spark, no agents) | 53 s | 0.787 | logreg | 12 | 291 | 0 | 0 |
| B1 (manual runbook) | **not measured** | - | - | - | - | - | - |

The hypothesis has two halves. **The first holds:** P ran four Spark jobs - profiling in the
worker's own session, then three generated jobs in the sandbox - wrote 8 decision records and 17
audit entries, made no LLM call, and needed no manual step; its candidate (xgboost) was escalated
by AP-5 - calibration error above 0.05 after both remedies - on a validation split of 243 rows,
which Section 15 shows is inside the calibration estimator's noise floor at that size. An
escalation is the policy handing a person the decision, which is what it is for. **The second,
"agent overhead dominates P's time", holds as measured:** B3 does the same Spark work and
modelling with no agents, so P's time beyond B3's is the agentic loop's cost - 1 177 s, or 0.957
of P's wall clock (DEC-48). None of it is LLM time, because no provider is configured (ADR-0003);
it is the gates, the sandbox start-ups, the tuning and calibration P does and B3 does not, and the
governance writes.

**Where P's time goes**, read off the platform's own records: profiling 116 s (the stage times
itself since DEC-51); the gates before the generated jobs 553 s - compilation 262 s and the dry
runs on a sample 292 s, the fixture suite nothing because its result for these exact sources was
reused (FR-083); the three full runs 299 s, 88 to 120 s each; and the rest training with tuning
and calibration, and the governance writes. B3's 53 s splits into 45 s of Spark, 0.14 s to read
its feature table back (the parquet hand-off of DEC-50, which its 291 lines include; 262 before)
and 6 s of modelling.

**P's wall clock varies more than anything else it reports.** Three runs on the same code path agree on every count and score - P's test PR-AUC 0.7405, 17 audit entries, 8 decision records, B3 0.787, B2 0.790 - and not on P's time: 786 s on 11-10-2026 (`20261011T174929Z`, straight after EXP-7 had used the same images for 50 minutes), 2 296 s on 12-10-2026 (`20261012T145209Z`, beside a three-agent review on the same host, kept with a note) and 1 230 s here. The baselines took 6.9 s and 53 s here against 6.6 s and 49 s on 11-10, so the difference lies in what only P does: compiling with sbt (262 s against 160 s), and the dry runs and full runs in sandbox containers (292 s against 183 s, and 299 s against 166 s). This run was the first after Docker Desktop restarted, and the likeliest reason is that those steps read their toolchain caches and container images cold; the environment record does not capture that, so it is not measured. The share of P's time beyond B3's was 0.938, 0.944 and 0.957: the verdict does not depend on which run is read.

**The PR-AUC column is not a quality comparison.** P uses its 44-feature catalogue, the baselines
twelve hand-picked features, and the baselines train on P's training and calibration cutoffs
together (597 rows), where P holds the calibration cutoff back (390). The experiment therefore
refits the baselines' shortlist on P's own feature table and split: it reaches **0.776** with a
random forest (validation PR-AUC: random forest 0.703, histogram GB 0.699, logreg 0.603), against
0.741 for P's tuned and calibrated xgboost on the same 280 test rows. No interval was computed for
either, so no ranking is claimed; the comparison says that the modelling is not where P earns its
time - the evidence is.

**History.** The runs of 11-10-2026 (`20261011T063017Z`, P 773 s, before B3's parquet hand-off;
`20261011T174929Z`, whose profile stage recorded no time) and of 12-10-2026 are kept. The run of
07-10-2026 failed when P escalated and the harness had nothing to read (DEC-34). The run of
08-10-2026 23:53 failed because the DEC-36 CleanJob template compiled but its generated copies did
not (a varargs deprecation made fatal in generated code); every repair attempt failed gate 4
identically and P escalated at cleaning. The 16:15 run on 09-10-2026 then reached the end but its
like-for-like refit crashed: the shortlist's preprocessing chose categorical columns from the
baselines' three names and sent P's payment-method column to a median imputer. Both are fixed,
with tests. The LLM part of "agent overhead" stays untestable while no LLM provider is configured
(ADR-0003); the overhead that is present is measured above.

### EXP-2, where a single node gives out — did not hold, partial

The ladder reported is the run `experiments/EXP-2/20261014T171107Z/` (14-10-2026, 17:11 to 20:14
UTC, on the code after DEC-54): the whole ladder in one run, each configuration in a process of
its own inside the 3 GB dev container. The run of 13-10-2026 (`20261013T134333Z`) has the same
shape at every rung.

| Customers | On disk | Training rows | B2 (pandas) | B2 peak | B3 (Spark) | B3 JVM peak | B3 driver peak | Test PR-AUC B2 / B3 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 400 | 0.4 MB | 769 | ok, 14.2 s | 229 MB | ok, 84.8 s | 1 204 MB | 227 MB | 0.793 / 0.792 |
| 1 500 | 1.4 MB | 2 688 | ok, 12.5 s | 252 MB | ok, 77.7 s | 1 135 MB | 232 MB | 0.776 / 0.772 |
| 6 000 | 5.1 MB | 11 331 | ok, 22.7 s | 345 MB | ok, 103.7 s | 1 490 MB | 263 MB | 0.766 / 0.765 |
| 20 000 | 14.9 MB | 37 265 | ok, 41.3 s | 629 MB | ok, 214.1 s | 2 130 MB | 361 MB | 0.785 / 0.785 |
| 50 000 | 36.4 MB | 92 429 | ok, 92.0 s | 1 305 MB | ok, 515.2 s | 2 326 MB | 589 MB | 0.777 / 0.778 |
| 100 000 | 76.3 MB | 184 492 | ok, 170.3 s | 2 325 MB | ok, 335.4 s | 2 238 MB | 969 MB | 0.784 / 0.784 |
| 200 000 | 153.9 MB | 369 270 | **killed** after 28.4 s (cgroup OOM kill) | - | ok, 631.5 s | 2 299 MB | 1 766 MB | - / 0.785 |

The hypothesis has three clauses.

**"B2 is the first to fail" held.** The single pandas process completed every rung to 100 000
customers, two to six times faster than B3 with the same PR-AUC to within 0.004, and was killed by
the container's memory limit at 200 000. B3, which spills what does not fit, completed every rung.
Every kept run that reached 100 000 customers had B2 completing there, in 118 to 170 s; every one
that reached 200 000 had B2 killed by the memory limit within 19 to 28 s.

**"P finishes where B2 fails" did not hold on this node.** P was run at 200 000 customers in three
runs, each on code corrected after the one before, and finished in none:
- **13-10 (`20261013T134333Z`):** the host share refused to write the generated feature source
  for want of memory after 1 604 s (DEC-54).
- **14-10 (`20261014T171107Z`):**
  - The feature job, sandboxed with 1 GB of heap and 8 shuffle partitions, made no recorded
    progress for an hour and was stopped at the deadline.
  - A scheduled retrain then rebuilt the shared JAR under P's retry (DEC-55).
  - P had run 5 493 s.
- **14-10 to 15-10 (`20261014T214513Z`, with every fix in place and the orchestrator stopped):**
  - The first feature attempt again ran to its hour.
  - The retry, at 16 partitions, stalled after 2 038 s, with 579 s of garbage collection in a
    1 394 s stage.
  - During the third attempt the 5.27 GB Docker VM stopped responding and restarted. No
    result.json was written; its NOTE.md cites the platform's records and the jobs' logs.

The cause is structural. P drives its jobs from a process that holds its own Spark session, in
one 3 GB container, and runs each generated job in a sandbox container of its own, clamped to
3 GB. That is two JVMs where B3 holds one, on a VM that does not have room for both under load.

**"P stays within 20% of B3" is refuted** whatever a larger node would show. On 14-10 P had run
5 493 s, 8.7 times B3's 632 s for its whole pipeline, when it stopped. The overhead EXP-1
measures at 400 customers (0.957 of P's time beyond B3's) says the same at the other end of the
ladder.

What this supports: on a small node the isolation that makes generated code safe to run costs
memory, and that cost decides where the platform stops. What it does not show: P on a node with
more memory, or the PRD's 10 GB tier (ADR-0002), neither of which was run or is estimated. A
fifth attempt at 200 000 customers was not made: each of the last three exhausted the VM in the
same stage.

**How these runs came to be the ones reported.** Seven earlier runs are kept, not replaced. Each
found a defect in a baseline, the harness or the platform, and each was fixed before the next run,
with a decision record.

On 08-10-2026:
- **11:10:** it crashed in its conclusion on a rung whose generation had failed (outputs in
  `experiments/_logs/aborted/`).
- **16:17 (`20261008T161715Z`):** the data generator was OOM-killed at 100 000 customers. DEC-37
  made it write one table at a time, with the same draws.
- **17:12 (`20261008T171242Z`):** it recorded B3 timing out at 100 000 customers and was reported
  as refuting the hypothesis. A diagnostic run traced that to B3's idle Spark JVM, left resident
  beside the modelling until the kernel killed it. DEC-44 ends the JVM first and puts P on the
  ladder.

On 11-10-2026:
- **`20261011T095854Z`:** it reached 200 000 customers, where B2 was killed and B3's JVM died
  inside a `toPandas` collect that P never makes. DEC-50 hands B3's table over as parquet.
- **`20261011T180425Z`:** it lost P to a stalled container engine (DEC-52).

On 12-10-2026 (`20261012T153533Z`), Docker Desktop stopped while P ran.

The runs of 13-10 and 14-10 are described above.

### EXP-3, scaling with parallelism — did NOT hold, partial

Run `20261011T064506Z` (11-10-2026, 6 000 customers, 10 cutoffs, B3's feature build on Spark;
best of two runs per arm, the cold JVM start of 6.79 s paid and recorded before any arm).

| Cores | Wall clock (best of 2) | Fixed cost | Work | Speed-up on work | Core utilisation | Shuffle written |
| --- | --- | --- | --- | --- | --- | --- |
| `local[1]` | 28.33 s | 0.50 s | 27.83 s | 1.000 | 0.731 | 21.3 MB |
| `local[2]` | 22.59 s | 0.47 s | 22.13 s | **1.258** | 0.721 | 23.8 MB |
| `local[4]` | 21.19 s | 0.27 s | 20.93 s | **1.330** | 0.670 | 24.3 MB |

The hypothesis asks for at least 1.6x from doubling the cores. Two to four cores gives **1.057x**,
and one to two 1.258x; neither reaches it. Nothing spilled, so memory is not what limits it. Sub-
linear scaling is the PRD's expected observation: these are windowed aggregations, they shuffle -
the shuffle grows a little with the cores - and a shuffle does not halve when the cores double;
core utilisation, the tasks' run time over wall clock times cores, falls as cores are added.

**Doubling here is not doubling compute.** The host is an AMD Ryzen 5 2500U with 4 cores and 8
threads, so the VM's 8 vCPUs are hardware threads, and `local[4]` shares physical cores with
itself and with everything else in the VM; the 2-to-4 ratio is a lower bound on what doubling
real cores would give. Distributed workers were not used: with 8 threads and about 3 GB usable,
two Compose workers would contend for the same cores and measure scheduling, not scaling, and the
100 GB tier cannot fit (ADR-0002). This is intra-node scaling inside one JVM.

The run of 07-10-2026 (`20261007T215740Z`) measured 54.5, 33.7 and 39.7 s for the same arms and
0.842x from two to four; an earlier one subtracted the cold JVM start (8.57 s) from a warm
one-core run, which understated one-core work and reported 1.301x from one to two cores where its
own raw timings said 1.74x. The fixed cost is now measured warm at each parallelism.

### EXP-8, seeded Scala faults — no verdict, partial

Run `20261011T084444Z` (11-10-2026 08:44 to 09:58 UTC), gates 1 to 5, warm caches (DEC-43); the
same outcomes as `20261010T131443Z`.

| Fault class | Seeded | Stopped before reading data | Gate that caught it | Time to the verdict |
| --- | --- | --- | --- | --- |
| forbidden API | 5 | **5** | 2 (static scan) | 0.2 s |
| type mismatch | 5 | **5** | 4 (compile) | 39-62 s |
| missing import | 5 | **5** | 4 (compile) | 39-43 s |
| wrong column name | 5 | **4** | 5 (fixture tests) | 295-393 s |

- **19 of 20 stopped before the job read any data.** The static gates stopped 15; a wrong column
  name passed all four of them, because a name is a string until Spark resolves it, and the
  fixture tests caught four of five: a misspelled de-duplication key that merged different orders
  (WC-1), a cancellation test on a missing column (WC-3), an order key declared numeric (WC-4),
  and a filter on a name Spark could not resolve (WC-5).
- **The fifth wrong column passed every gate, as predicted from the fixture.** Misspelling both
  de-duplication keys (WC-2) makes the job fall back to whole-row de-duplication, and the fixture's
  duplicate is an exact one, so the fixture cannot tell the two apart. What escapes is what the
  fixtures do not exercise.
- **Forbidden APIs never run**: 5 of 5 refused at gate 2, never compiled. **Repeated fingerprints
  escalate**: each detected fault's gates were run a second time on the identical artifact, both
  results classified and fingerprinted as the stage runner does, and the real circuit breaker
  tripped at attempt 2 for all 19.
- **No verdict** because the headline clause - 70% repaired within 3 attempts - needs a language
  model to rewrite the slot (ADR-0003); a measured 0% would describe the missing provider.

How the measurement got here, each step a defect the experiment had in itself:

- The first run reported 20 of 20: the generated object's name carried a hyphen (`wc-1`), every
  mutant failed to compile for that reason, and fifteen detections were the experiment's own bug.
- Until DEC-38 the experiment stopped at gate 4, so the five wrong columns were "reaching
  execution" and the question that matters - does anything stop a semantic fault before it reads
  data - was unmeasured.
- The review of 10-10-2026 found twelve of the twenty anchors stale since the slot changed (the
  run would have crashed at its second fault), WC-3's anchor matching a type declaration instead of
  the cancellation test, three wrong-column faults that changed nothing yet counted as escapes, any
  gate failure counted as a detection even when the toolchain failed, and a fingerprint check that
  classified one string three times (DEC-42). The run of that morning then failed outright on a
  dependency download inside the control's compile, which is how DEC-43 was found.

### EXP-5, missing values — did not hold, completed

Run `20261011T111552Z` (11-10-2026 11:15 to 12:43 UTC, on the code after the review of 11-10,
DEC-48) reproduced run `20261010T094502Z` (10-10-2026 09:45 to 11:31 UTC) exactly: of 155
measurements, all but the wall-clock times and the approval request's id are identical. 2 000
customers, each condition in a process of its own, the reference drawing the same random numbers
as the others (DEC-42); keys are 1% missing in every trend condition.

| Condition | Non-key nulls (counted) | Key nulls quarantined | Rows affected | Test PR-AUC [95% interval] | ECE | Policy |
| --- | --- | --- | --- | --- | --- | --- |
| none of the non-key values missing | 0 | 391 of 391 | 1.01% | 0.6338 [0.5875, 0.6817] | 0.0283 | ESCALATE (AP-6) |
| 5% missing | 5 853 | 391 of 391 | 1.01% | 0.6419 [0.5963, 0.6901] | 0.0364 | ESCALATE (AP-6) |
| 20% missing | 23 274 | 391 of 391 | 1.01% | 0.6391 [0.5924, 0.6841] | 0.0287 | ESCALATE (AP-6) |
| 50% missing | 58 476 | 391 of 391 | 1.01% | 0.6351 [0.5905, 0.6841] | 0.0225 | ESCALATE (AP-6) |
| 10% of keys missing (added) | 0 | 3 901 of 3 901 | **10.05%** | - | - | **paused: APPROVAL_REQUIRED** |

- **Key nulls are quarantined, all of them and only them**: the cleaning job's `null_customer`
  rule quarantined exactly the nulls counted in each registered file, and no other row was
  quarantined or de-duplicated in any trend condition, so the non-key nulls cost no row.
- **PR-AUC did not decline, so the hypothesis did not hold.** The four estimates are within 0.008
  of each other and inside each other's 95% intervals (1 366 test rows, prevalence 0.289). With
  half of three columns empty the model ranked customers as well as with none.
- **Approval when more than 5% of rows are affected** - the PRD's expected observation, which none
  of its own conditions reaches - holds: at 10% missing keys the cleaning plan filed a
  `destructive_cleaning` request and the run paused with its output uncommitted (DEC-40).
- Every trend model escalated on AP-6 (tenure-band recall gaps 0.23 to 0.28 on 1 181 validation
  rows), inside the segment rule's measured noise floor at that size (Section 15.7). Calibration
  passed: ECE 0.02 to 0.04.

Runs that shaped this protocol, kept as records:

- `20261009T233715Z` died in its fifth condition with `Cannot allocate memory`; its reference had
  no key nulls and drew four masks' fewer random numbers, so its returns, sessions and tickets were
  a different sample. It scored **0.6527** and made the same series look like a monotone decline -
  the decline disappears once the reference draws the same numbers. `20261009T192937Z` died the
  same way in its fourth condition. Hence one process per condition, and production's JVM heaps
  for the `dev` service.
- The 400-customer runs of 21-09-2026 escalated MISCALIBRATED in every condition with nulls. The
  cause was the size of the validation split, not the models: at 324 rows a perfectly calibrated
  model exceeds AP-5's 0.05 about as often as not (`experiments/calibration_noise_floor.py`, a
  model-free simulation), which is why the experiment runs 2 000 customers (DEC-33):

| Validation rows | Median ECE | p90 | P(ECE > 0.05) |
| --- | --- | --- | --- |
| **324** (EXP-5's size) | 0.046–0.049 | 0.064–0.067 | **40–46%** |
| 650 | 0.033–0.034 | 0.045–0.048 | 4–7% |
| 1 300 | 0.023–0.024 | 0.032–0.034 | ~0% |
| 2 600 | 0.017 | 0.023 | 0% |
| 5 200 | 0.012 | 0.017 | 0% |

## Deviations from PRD section 33 that apply to every experiment

- **The dataset is TRIAD's synthetic generator, not UCI Online Retail II.** Online Retail II has
  transactions and nothing else — no customer table, no web sessions, no support tickets, no
  returns — so the churn definition and two thirds of the feature catalogue would have nothing
  to read (ADR-0002). The generator exists for exactly this reason and carries every fault
  option section 33.1 names.
- **B1, the manual baseline, is not implemented and cannot be.** It is defined as a person
  following a written runbook, and its metrics are engineer hours from a time sheet. There is no
  second person in this environment and inventing hours would fabricate the central comparison
  of the case study.
- **LLM calls are 0 everywhere**, because no provider is configured (ADR-0003). Where a
  hypothesis depends on LLM behaviour — EXP-1's "agent overhead dominates P's time", EXP-8's
  repair rate — that part is recorded as not measured, with the reason, rather than reported as
  a measured zero.
- **The 10 GB and 100 GB tiers are not run and not estimated.** Docker has a 5.27 GB VM here and
  the sandbox clamps any container to 3 GB, so those workloads would fail for every
  configuration including the ones the hypotheses expect to finish. EXP-2 and EXP-3 test the
  *shape* of their hypotheses instead, at sizes this machine can hold, and say so.
