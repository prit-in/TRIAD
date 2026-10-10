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
| EXP-1 | Small dataset against the non-agentic baselines | **partial** | **held** (P end to end, no manual step; escalated on AP-5 at 243 rows) | `experiments/EXP-1/latest.json` |
| EXP-2 | Medium dataset: where a single node gives out | **partial** | **refuted on this node** (B3 stopped first, at 100 000 customers) | `experiments/EXP-2/latest.json` |
| EXP-3 | Scaling with parallelism | **partial** | **did not hold** | `experiments/EXP-3/latest.json` |
| EXP-4 | Schema errors | **completed** | **held** (every fault detected and handled; the mapping proposed, approved, then applied) | `experiments/EXP-4/latest.json` |
| EXP-5 | Missing values | **partial** | **no verdict on PR-AUC; the reason for that is now measured** | `experiments/EXP-5/latest.json`, `experiments/EXP-5/calibration_noise_floor.json` |
| EXP-6 | Spark failures and recovery | **partial** | **did not hold** | `experiments/EXP-6/latest.json` |
| EXP-7 | Model degradation | **partial** | **held** | `experiments/EXP-7/latest.json` |
| EXP-8 | Scala compile failures | **partial** | **no verdict** | `experiments/EXP-8/latest.json` |
| EXP-9 | Leakage sentinel | **completed** | **held** | `experiments/EXP-9/latest.json` |
| EXP-10 | Governance bypass | **completed** | **held** | `experiments/EXP-10/latest.json` |

`partial` means the experiment ran and produced measurements but could not run the workload the
PRD specifies, and the limitation is recorded in the result. The harness refuses a `partial` or
`not_run` result that names no limitation, and refuses a verdict on a hypothesis when no
measurement was recorded.

All ten have now been run: **five hypotheses held, three did not, and two have no verdict**
because the measurement they turn on could not be taken here — EXP-8's repair rate needs an LLM
provider, and EXP-5's trend needs more than one condition to survive the acceptance policy — which
at its validation size nothing could, for a reason that is now measured rather than guessed at. The
five sections that are not a plain "held" are the ones worth reading first.

## What each experiment found

### EXP-9, leakage sentinel — held

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
3 audit entries added; the hash chain still verifies across 143 chains afterwards (re-run
10-10-2026, `20261010T094439Z`, on the current code).

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

### EXP-7, model degradation — held, partial

| Arm | Max PSI | PSI flagged after | PR-AUC | 10% drop visible after | Triggers |
| --- | --- | --- | --- | --- | --- |
| six stable months | 0.031 | never (0 false alarms) | 0.5064 mean | — | — |
| covariate shift, one feature | 0.311 | 0 scoring months | 0.5538 → 0.7079 | never | RT-2, RT-3, RT-5, RT-6 |
| covariate shift, one segment | 1.084 | 0 scoring months | 0.5538 → 0.7405 | never | RT-2, RT-3, RT-5, RT-6 |
| concept drift, one segment | 0.032 | **never** | 0.5538 → lower | 0 label months | **RT-1**, RT-5, RT-6 |

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

Partial because the approval half of the hypothesis is covered by the pipeline's own approval
gate (AT-08, AT-13) and is not re-tested here, and because replay is over synthetic months
rather than Online Retail II — only the generator can be told to shift one feature or change one
segment's behaviour on demand.

### EXP-4, schema errors — held, completed

Run `20261009T184842Z` (09-10-2026 18:48 to 19:19 UTC), 400 customers, every condition through
ingestion, profiling and cleaning; the baseline and the dropped table also through the feature job.

| Fault | Detected at | What the platform did | Row accounting |
| --- | --- | --- | --- |
| clean baseline | not detected | cleaned; all five feature groups available, 46 feature columns | balanced |
| renamed required column (`customer_id` -> `cust_id`) | **ingest** | refused; proposed `cust_id -> customer_id` (name similarity 0.778, types match); a `schema_change` approval pending, nothing registered, applying it refused; after approval the renamed copy registered and cleaned 229.7 s later | balanced |
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

### EXP-6, Spark failures — did NOT hold, partial

This is the one experiment whose hypothesis failed, and it is the most informative of the four.

| Arm | Recovered | Attempts | Remedies applied | Output identical to the clean run |
| --- | --- | --- | --- | --- |
| clean baseline | yes | 2 | — | reference |
| low driver memory (256m) | **no** | 2 | none | no |
| sandbox container killed | yes | 1 | — | **no** |
| skew, one account holds 20% | yes | 1 | — | not compared |

Four findings, none of which a passing test would have surfaced:

1. **The content hash of a Spark output was not reproducible.** The killed-container arm read
   byte-identical source data and ran identical code, produced the same 8078 rows with balanced
   accounting, and committed a *different* content hash. **Fixed**, and it took two goes,
   because there were two causes and only the first was visible from this experiment:

   - Spark mints a UUID per job and puts it in every part-file name, and `sha256_dir` folded
     the names into the digest.
   - `OutputValidator` writes without sorting, so which rows land in which part file — and in
     what order inside one — depends on task scheduling. A replay with the names dealt with
     reproduced the identical 7815 clean rows and *still* wrote different bytes.

   A table's content is now defined as the multiset of its rows: each row hashed with its
   column names attached, the hashes sorted, the digest over that. Measured stable across 3, 4
   and 5 files holding the same rows. `sha256_dir` keeps its name-sensitive behaviour for code
   trees. The cost is a read-back on commit, O(rows); the cheaper alternative is a
   deterministic sort before write in the Scala job, and that trade is recorded in the code.

   Confirmed end to end afterwards: a replay of run-2026-09-21-007 reused 3/3 stored artifacts,
   made 0 LLM calls, and reproduced all three outputs identically — clean `061e97b5…`, features
   `2a97e511…`, labels `ce277092…`. ATC-18 passes.
2. **A 256m Spark driver's out-of-memory failure is classified `UNKNOWN`,** so the playbook
   prescribed `none` and `RAISE_EXECUTOR_MEMORY` never fired. The classifier does not recognise
   the signature this failure actually produces. **Fixed (DEC-20):** Spark's
   `[INVALID_DRIVER_MEMORY]` refusal is now `RESOURCE_CONFIG_INVALID`, escalated naming the
   setting rather than remedied, because the policy does not let the agent raise driver memory.
3. **`docker` is not on the PATH inside the dev container,** so `get_runner()` falls back to
   `LocalRunner` and Spark jobs are *not* running in containers in this environment. The
   container kill could not land — recorded as `container_killed: false` rather than reported as
   a recovery that never happened. Every claim about container isolation therefore describes
   `DockerRunner`, which is implemented and exercised by its own tests, but is not the path this
   machine takes. **Resolved 04-10-2026 (ADR-0004)** - and the finding was worse than it read:
   `DockerRunner` shelled out to a CLI the image does not have and could never have run. Jobs now
   run in containers started by the `sandbox-runner` service, so the killed-container arm can
   land; EXP-6 is to be re-run to measure it.
4. **sbt's dependency download failure is classified as `SCALA_COMPILE_ERROR`.** The clean
   baseline took two attempts because attempt 1 failed on a coursier network fetch inside
   `gate4_compile`. A network problem is not a compile error and the prescribed remedy
   (`regenerate_code`) cannot help it. **Fixed (DEC-20):** `DEPENDENCY_RESOLUTION`, resubmitted
   once and never sent to code regeneration; the compile pattern now requires a file and line.

EXP-6 is also what proved the `RAISE_EXECUTOR_MEMORY` plumbing fix: that remedy bumped an input
nothing read, so an out-of-memory retry was submitted with exactly the memory that had just
failed. The plumbing is fixed and the classifier gap in (2) now stands between it and the
failure it exists for.

### EXP-1, TRIAD against the non-agentic baselines — held, partial

Run `experiments/EXP-1/20261009T172131Z/` (09-10-2026, 17:21 UTC, 400 customers).

| Configuration | Wall clock | Test PR-AUC | Model | Features | Hand-written lines | Manual steps | Audit entries |
| --- | --- | --- | --- | --- | --- | --- | --- |
| P (TRIAD) | 989 s | 0.741 (escalated) | xgboost | full catalogue (44) | 0 | 0 | 17 |
| B2 (pandas only) | 5.5 s | 0.790 | logreg | 12 | 188 | 0 | 0 |
| B3 (Spark, no agents) | 71 s | 0.787 | logreg | 12 | 228 | 0 | 0 |
| B1 (manual runbook) | **not measured** | - | - | - | - | - | - |

The hypothesis is that P finishes end to end with no manual step except approval. **It holds:**
P ran four sandboxed Spark jobs, wrote 8 decision records and 17 audit entries, and needed no
manual step; its candidate (xgboost) was escalated by AP-5 - calibration error above 0.05 after
both remedies - on a validation split of 243 rows, which Section 15 shows is inside the
calibration estimator's noise floor at that size. An escalation is the policy handing a person the
decision, which is what it is for.

**Where P's time goes.** Of P's 989 s, the gates took about 600 s: compilation 415 s (the first
compile of a run starts sbt cold - CleanJob 326 s, FeatureJob 48 s, LabelJob 41 s), the dry runs
on a sample 185 s, and the fixture suite nothing, because its result for these exact sources was
reused (FR-083). The rest is the four full Spark runs, training with tuning and calibration, and
the governance writes. The first run after the template fix, `20261009T161555Z`, is kept as the
cold case: every fixture run was fresh (1 867 s of gate 5), the first compile after the build
change rebuilt everything (718 s), and P took 3 397 s.

**The PR-AUC column is not a quality comparison.** P uses its 44-feature catalogue, the baselines
twelve hand-picked features, and the baselines train on P's training and calibration cutoffs
together (597 rows), where P holds the calibration cutoff back (390). The experiment therefore
refits the baselines' shortlist on P's own feature table and split: it reaches **0.776** with a
random forest (validation PR-AUC: random forest 0.703, histogram GB 0.699, logreg 0.603), against
0.741 for P's tuned and calibrated xgboost on the same 280 test rows. No interval was computed for
either, so no ranking is claimed; the comparison says that the modelling is not where P earns its
time - the evidence is.

**History.** The run of 07-10-2026 failed when P escalated and the harness had nothing to read
(DEC-34). The run of 08-10-2026 23:53 failed because the DEC-36 CleanJob template compiled but its
generated copies did not (a varargs deprecation made fatal in generated code); every repair
attempt failed gate 4 identically and P escalated at cleaning. The 16:15 run on 09-10-2026 then
reached the end but its like-for-like refit crashed: the shortlist's preprocessing chose
categorical columns from the baselines' three names and sent P's payment-method column to a
median imputer. Both are fixed, with tests; the second-half claim, "agent overhead dominates P's
time", stays untestable while no LLM provider is configured (ADR-0003).

### EXP-2, where a single node gives out — refuted on this node, partial

Run `experiments/EXP-2/20261008T171242Z/` (08-10-2026, 17:12 to 18:15 UTC), the whole ladder in
one run, each configuration in a process of its own inside the 3 GB dev container, 30-minute
ceiling per run.

| Customers | On disk | Training rows | B2 (pandas) | B2 peak | B3 (Spark) | B3 JVM peak | B3 driver peak | Test PR-AUC B2 / B3 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 400 | 0.4 MB | 769 | ok, 6.8 s | 229 MB | ok, 37.5 s | 1 329 MB | 214 MB | 0.793 / 0.792 |
| 1 500 | 1.4 MB | 2 688 | ok, 5.7 s | 251 MB | ok, 47.6 s | 1 162 MB | 226 MB | 0.776 / 0.772 |
| 6 000 | 5.1 MB | 11 331 | ok, 7.8 s | 343 MB | ok, 44.0 s | 1 379 MB | 257 MB | 0.766 / 0.765 |
| 20 000 | 14.9 MB | 37 265 | ok, 19.0 s | 590 MB | ok, 150.2 s | 2 141 MB | 315 MB | 0.785 / 0.785 |
| 50 000 | 36.4 MB | 92 429 | ok, 56.4 s | 1 295 MB | ok, 226.6 s | 2 223 MB | 607 MB | 0.777 / 0.778 |
| 100 000 | 76.3 MB | 184 492 | ok, 130.4 s | 2 294 MB | **not completed**: stopped at the 1 800 s ceiling | - | - | 0.784 / - |

The hypothesis is that B2 fails first as the data grows while B3 keeps going. **On this node the
opposite happened, so it is refuted here:** B3 stopped at 100 000 customers and B2 did not. The
memory columns say why. B2's memory grows with the data; B3's starts high - its JVM held 1.3 GB
at 400 customers - and with its Python driver it already used about 2.8 GB of the container's
3 GB at 50 000 customers (the sum of the two processes' peaks, an upper bound on what it held at
once). Spark's spill protects it from data larger than memory, not from a node too small for its
fixed overhead: B3's time went from 227 s to beyond 1 800 s when the data doubled, B2's from
56 s to 130 s. During that run `docker stats` showed the container at 2.917 of 2.93 GiB and `ps`
the JVM at 2.16 GiB resident (operator observation at 17:55 UTC, not recorded by the harness).
The models agreed at every size both completed (PR-AUC within 0.004), and B2 was 4.0 to 8.4
times faster throughout.

What this supports: at these sizes and on nodes of this size a single process is faster and
smaller, which is what LR-06 acts on when it hands the modelling table to pandas. What it does
not show: the crossover the hypothesis describes, which should exist above this node's memory
and was not reached; it is not estimated. The ladder stops at the first rung where a
configuration fails, so 200 000 customers was not run. The PRD's 10 GB tier was not run either
(ADR-0002): on a 5.27 GB Docker VM with 3 GB containers it would fail for every configuration.

**How this run came to be the one reported.** Two earlier runs that day are kept, not replaced.
The 11:10 run measured the same rungs to 50 000 customers and then crashed *in its conclusion*,
on a rung whose generation had failed and which therefore had no measurements (a `KeyError`,
fixed with a tolerant getter; its outputs are in `experiments/_logs/aborted/`). The 16:17 run
(`20261008T161715Z`, committed) reached 50 000 customers and stopped because **the data
generator itself** was OOM-killed at 100 000 - the harness, not either configuration, was the
limit. DEC-37 rewrote the generator to store columns and write one table at a time with exactly
the same random draws (identical output on seven configurations, pinned by a test); the 17:12
run is the first to reach the rung that decides the hypothesis. Its outcome logic also gained
the two outcomes it had folded into "no crossover": B3 failing first (refuted, this run) and
both failing together (not supported).

### EXP-3, scaling with parallelism — did NOT hold, partial

| Cores | Wall clock (best of 2) | Fixed cost | Work | Speed-up on work | Raw speed-up |
| --- | --- | --- | --- | --- | --- |
| `local[1]` | 31.76s | 8.57s | 23.19s | 1.000 | 1.000 |
| `local[2]` | 18.29s | 0.46s | 17.83s | **1.301** | 1.736 |
| `local[4]` | 22.83s | 0.24s | 22.59s | **1.026** | 1.391 |

The hypothesis asks for at least 1.6x from doubling the cores. Two to four cores gives **0.789x**
— slower, not faster. One to two gives 1.301x, below the threshold as well.

Two things in this table are worth more than the verdict. First, the **fixed cost is measured,
not assumed**: a trivial job is timed at each parallelism and subtracted, which is why the raw
column looks so much better than the work column. `local[1]`'s 8.57s of JVM start-up flatters
every raw speed-up computed against it, and a scaling experiment that did not subtract it would
report 1.736x and call the hypothesis half-held. Second, sub-linear scaling is the expected
observation: these are windowed aggregations, they shuffle, and a shuffle does not halve when
the cores double.

This is intra-node scaling inside one JVM. Distributed workers were not used deliberately —
with 8 host cores and ~3 GB usable, two Compose workers would contend for the same cores and
measure scheduling overhead rather than scaling. Shuffle bytes and spill come from Spark event
logs, which are disabled here; they are absent rather than approximated.

### EXP-8, seeded Scala faults — no verdict, partial

| Fault class | Seeded | Detected before execution | Gate that caught it |
| --- | --- | --- | --- |
| forbidden API | 5 | **5** | 2 (static scan) |
| type mismatch | 5 | **5** | 4 (compile) |
| missing import | 5 | **5** | 4 (compile) |
| wrong column name | 5 | **0** | none |

**15 of 20, and the five that get through are the interesting ones.** A wrong column name passes
every static gate because the name is a string until Spark resolves it: gate 2 has nothing to
scan for, gate 4 compiles it happily, and the fault only exists at execution. The PRD expects
semantic faults to escalate more often; what this measures is stronger than that — they are
invisible to static analysis entirely.

The forbidden-API result is the PRD's strongest claim about codegen and it holds exactly: 5 of 5
refused at gate 2 in under a third of a second each, never compiled, never submitted. And
fingerprints repeat identically across three attempts on every detected fault, so an unrepairable
fault trips the circuit breaker rather than burning the attempt budget.

**The hypothesis has no verdict because its headline number cannot be measured here.** "At least
70% repaired within 3 attempts" needs an LLM to rewrite the slot, and no provider is configured
(ADR-0003); `StageRunner._apply_fix` adjusts infrastructure inputs only, so `generate` returns
identical code on every attempt. A measured 0% would describe the missing provider, not the
repair loop, so the harness records no verdict rather than a number that reads like one.

This experiment first reported **20 of 20**, and that result was deleted. The generated object
name carried the experiment's run suffix — `object CleanJob_exp08wc-1` — and a hyphen is not a
legal Scala identifier, so every job failed to compile and the harness read the compile failure
as the seeded fault being caught. Fifteen of the twenty detections were real; five were the
experiment detecting its own bug. The suffix is now sanitised and the false result is gone from
`experiments/`.

### EXP-5, missing values — no verdict, partial

| Non-key null rate | Key null rate | Quarantined | Row accounting | Outcome | Test PR-AUC |
| --- | --- | --- | --- | --- | --- |
| 0% | 0% | 0 of 8 078 | balanced | SELECTED | 0.7259 |
| 5% | 1% | 72 of 8 078 (0.89%) | balanced | **ESCALATED, MISCALIBRATED** | not measured |
| 20% | 1% | 72 of 8 078 (0.89%) | balanced | **ESCALATED, MISCALIBRATED** | not measured |
| 50% | 1% | 72 of 8 078 (0.89%) | balanced | **ESCALATED, MISCALIBRATED** | not measured |

The half of the hypothesis about rows **holds, and holds cleanly**. Row accounting balanced in
every condition — `input = output + quarantined + removed_duplicates` — so no row was lost
silently at any null rate. Key nulls quarantine and non-key nulls do not: the 72 quarantined
rows are the 1% key-null injection and the number does not move between 5% and 50% non-key
nulls, which is exactly the behaviour the rule describes. Cleaning affected 0.89% of rows
against the `cleaning.execute` policy limit of 5%, so it ran under bounded autonomy with no
approval request in any condition.

The half about PR-AUC declining gradually **has no verdict, because three of the four
conditions never produced a PR-AUC**. Every condition carrying nulls escalated on
MISCALIBRATED after the full AP-5 remedy chain — isotonic, then sigmoid, then sigmoid with
class weighting removed. The pipeline does not degrade gracefully as values go missing; it
stops. That is the acceptance policy behaving as specified rather than a defect, and it means
the trend the hypothesis asks about cannot be measured on this pipeline without changing the
policy the experiment is supposed to observe.

**The previous run of this experiment reported this hypothesis as held, and that was wrong in
two separate ways.** It called a monotone decline from a single surviving data point, because
`all()` over a one-element list is vacuously true; and on escalation it discarded the cleaning
evidence it had already collected, so the quarantine and row-accounting measurements — the
half that genuinely holds — were thrown away in the conditions that produced them. Both are
fixed: the trend is reported as *not assessable* below two points, and an escalated condition
now keeps every measurement it made before it stopped. The table above exists because of the
second fix.

#### The question this experiment left open, and its answer

The previous revision of this section asked whether MISCALIBRATED at 5% non-key nulls was the
acceptance policy correctly refusing an uncalibratable model, or a calibration chain that gave
up too early. It is neither, and the evidence was already in the run's own artifact.

**The gate is inside the estimator's noise floor at this validation size.** AP-5 requires
ECE ≤ 0.05. `expected_calibration_error` is the standard 10-bin equal-width estimator, which is
biased upward on small samples: each bin's observed rate is an average over few rows, so it
differs from the bin's mean predicted probability by sampling noise alone, and the estimator
sums those differences as though they were miscalibration. `experiments/calibration_noise_floor.py`
measures the floor directly, with no model involved — a predictor whose scores *are* the
probabilities its labels are drawn from is perfectly calibrated by construction, so drawing
20 000 of them at each size gives the distribution of the estimator under perfect calibration:

| Validation rows | Median ECE | p90 | P(ECE > 0.05) |
| --- | --- | --- | --- |
| **324** (EXP-5's size) | 0.046–0.049 | 0.064–0.067 | **40–46%** |
| 650 | 0.033–0.034 | 0.045–0.048 | 4–7% |
| 1 300 | 0.023–0.024 | 0.032–0.034 | ~0% |
| 2 600 | 0.017 | 0.023 | 0% |
| 5 200 | 0.012 | 0.017 | 0% |

At n=324 the threshold sits at the *median* of that distribution: a perfectly calibrated model
is refused about as often as it is accepted. The winning model's measured 0.0554 is therefore not
evidence of miscalibration, and neither are 0.0737 and 0.0666 at the higher null rates.

The run said the same thing from the inside, and it had said it all along. Its `dummy`
classifier — a constant at the base rate, which is exactly calibrated in expectation — measured
**ECE 0.0511** and was itself flagged `MISCALIBRATED`. A gate that a constant predictor cannot
pass is not measuring the model.

**The threshold is sound; the split was too small.** The floor falls to 4–7% by n=650 and to
nothing by n=1 300, so 0.05 is a reasonable requirement wherever the validation split is a
thousand rows or more — which the production configuration reaches and a 400-customer experiment
does not. The threshold is therefore unchanged. AP-5 now records `ece_n_rows` beside the value,
because a reader of an escalation cannot otherwise tell a miscalibrated model from a small split.

**Two defects in the reporting were found in the course of answering it**, both of which had made
the question harder than it was.

- The escalation message read "calibration rules still failing after isotonic and sigmoid" in all
  four conditions, and **isotonic was never fitted in any of them**. `calibrate` substitutes
  sigmoid when the calibration slice has fewer than 500 rows, silently, and EXP-5's slice is
  smaller than that; the remedy loop then skipped its own sigmoid step as redundant. So a chain
  reported at three steps ran one calibrator and one variant of it. The substitution is now
  recorded, and the message names the remedies actually tried.
- `remedies_tried` was built on every path and read only on the successful one, so an escalation —
  the single path where a person has to decide what to do next — recorded the ECE that failed and
  none of the attempts made to fix it. It is now in the escalation's evidence, with the
  calibration method, the note about any substitution, and whether class weighting was removed.

Neither changes the verdict: the row-accounting half of the hypothesis holds, and the PR-AUC half
still has no verdict on this pipeline, because the conditions carrying nulls still escalate. What
has changed is that the escalation is now legible, and the reason for it is measured rather than
speculated about.

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
