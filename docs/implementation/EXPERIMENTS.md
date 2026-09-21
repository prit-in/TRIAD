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
| EXP-1 | Small dataset against the non-agentic baselines | **partial** | **held** | `experiments/EXP-1/latest.json` |
| EXP-2 | Medium dataset: where a single node gives out | **partial** | **did not hold** | `experiments/EXP-2/latest.json` |
| EXP-3 | Scaling with parallelism | **partial** | **did not hold** | `experiments/EXP-3/latest.json` |
| EXP-4 | Schema errors | **partial** | **held** | `experiments/EXP-4/latest.json` |
| EXP-5 | Missing values | **partial** | **held** | `experiments/EXP-5/latest.json` |
| EXP-6 | Spark failures and recovery | **partial** | **did not hold** | `experiments/EXP-6/latest.json` |
| EXP-7 | Model degradation | **partial** | **held** | `experiments/EXP-7/latest.json` |
| EXP-8 | Scala compile failures | **partial** | **no verdict** | `experiments/EXP-8/latest.json` |
| EXP-9 | Leakage sentinel | **completed** | **held** | `experiments/EXP-9/latest.json` |
| EXP-10 | Governance bypass | **completed** | **held** | `experiments/EXP-10/latest.json` |

`partial` means the experiment ran and produced measurements but could not run the workload the
PRD specifies, and the limitation is recorded in the result. The harness refuses a `partial` or
`not_run` result that names no limitation, and refuses a verdict on a hypothesis when no
measurement was recorded.

All ten have now been run. Three hypotheses did not hold and one has no verdict; those
four sections are the ones worth reading first.

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
3 audit entries added; the hash chain still verifies across 35 chains afterwards.

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

### EXP-4, schema errors — held, partial

| Fault | Detected at | Cleaning | Row accounting |
| --- | --- | --- | --- |
| clean baseline | not detected | succeeded | balanced |
| renamed required column | **ingest** | not reached | — |
| price as text with currency symbols | **clean** | escalated | — |
| dropped optional table | not detected (not scored) | succeeded | balanced |

2 of 2 scored faults detected, 0 mappings applied without a person, 0 conditions lost rows
silently. The dropped optional table is excluded from the detection rate on purpose: the
contract declares that table optional, so nothing at these stages ought to object to it, and
counting it as a miss would make the rate a statement about the experiment's scope rather than
about the platform. The clean baseline is what makes the other numbers mean anything — a
detector that flagged everything would otherwise score perfectly.

Two deviations from the PRD's expected observation, both honest:

- The renamed column is caught at **ingest**, not by a proposed mapping. FR-007 is now reachable
  (see below); no mapping proposal is generated, so "a mapping is proposed but not applied" is
  half met — nothing is applied, and nothing is proposed either.
- The bad price **escalates** rather than being "fixed by `try_cast` and quarantine". The
  playbook prescribes `TRY_CAST_WITH_QUARANTINE`, and honouring it means rewriting the generated
  slot, which needs an LLM provider. The escalation ticket now says so explicitly rather than
  reporting only "attempt budget exhausted".

EXP-4 found three defects on the live path, all now fixed: FR-007's contract enforcement existed
in `register_version` and no caller ever passed it a contract, so a renamed required column was
registered, profiled and cleaned without a word; the generator's `rename_column` option had
never worked at all, because it renamed the column before `returns` derived from it; and the
profiler called a nullable-by-design column blocking, so every healthy run reported a blocking
issue and the count was useless as a signal.

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
   the signature this failure actually produces. **Open.**
3. **`docker` is not on the PATH inside the dev container,** so `get_runner()` falls back to
   `LocalRunner` and Spark jobs are *not* running in containers in this environment. The
   container kill could not land — recorded as `container_killed: false` rather than reported as
   a recovery that never happened. Every claim about container isolation therefore describes
   `DockerRunner`, which is implemented and exercised by its own tests, but is not the path this
   machine takes.
4. **sbt's dependency download failure is classified as `SCALA_COMPILE_ERROR`.** The clean
   baseline took two attempts because attempt 1 failed on a coursier network fetch inside
   `gate4_compile`. A network problem is not a compile error and the prescribed remedy
   (`regenerate_code`) cannot help it. **Open.**

EXP-6 is also what proved the `RAISE_EXECUTOR_MEMORY` plumbing fix: that remedy bumped an input
nothing read, so an out-of-memory retry was submitted with exactly the memory that had just
failed. The plumbing is fixed and the classifier gap in (2) now stands between it and the
failure it exists for.

### EXP-1, TRIAD against the non-agentic baselines — held, partial

| Configuration | Wall clock | Test PR-AUC | Features | Hand-written lines | Approvals | Audit entries |
| --- | --- | --- | --- | --- | --- | --- |
| P (TRIAD) | 749.3s | 0.7801 | full catalogue (23) | 0 | 0 | 15 |
| B2 (pandas only) | 4.1s | 0.7900 | 12 | 188 | 0 | 0 |
| B3 (Spark, no agents) | 25.7s | 0.7871 | 12 | 201 | 0 | 0 |
| B1 (manual runbook) | **not measured** | — | — | — | — | — |

The hypothesis is that P finishes end to end with no manual step except approval. It does: 0
approvals were required on this run, 0 other human actions, 4 Spark jobs, 6 decision records and
15 audit entries. P is 183 times slower than B2 at this size, which is the expected observation
and the argument for LR-07 rather than a defect.

**The PR-AUC column is not a quality comparison and is labelled as one nowhere.** P uses the full
feature catalogue and the baselines twelve hand-picked features, so the three numbers measure
different feature sets. The experiment therefore runs a fifth trial that holds the features
constant: on P's own 23-feature table and P's split, the baselines' shortlist reaches **0.7894**
with logreg. That is the only line in this experiment where the numbers are comparable, and it
says the modelling itself is not where the agentic loop earns its time — the governance is:
the baselines produce no decision record, no audit entry, no calibration, no acceptance policy
and no approval gate, and P's extra 745 seconds is what those cost.

The second half of the hypothesis — "agent overhead dominates P's time" — **cannot be tested
here**, and the result says so rather than claiming it. P made 0 LLM calls because no provider
is configured (ADR-0003), so the overhead that is present is validation gates including an sbt
compile, per-job sandbox start-up, governance and audit writes. Attributing those to "agent
overhead" would be reading a different experiment's conclusion into this one.

### EXP-2, where a single node gives out — did NOT hold, partial

| Customers | On disk | B2 (pandas) | B3 (Spark) | Peak RSS |
| --- | --- | --- | --- | --- |
| 400 | 0.4 MB | ok, 8.9s | ok, 46.1s | 246 MB |
| 1 500 | 1.4 MB | ok, 6.7s | ok, 29.1s | 312 MB |
| 6 000 | 5.1 MB | ok, 9.2s | ok, 32.8s | 525 MB |
| 20 000 | 14.9 MB | ok, 19.8s | ok, 54.8s | 1 065 MB |

The hypothesis is that B2 fails first as the data grows while B3 keeps going. **No crossover was
observed at any size this machine can hold.** B2 finished every rung and was two to five times
faster than B3 on all of them, because at 15 MB the Spark job is paying for planning and
shuffling that pandas never does.

This is a *failure to observe*, not evidence of absence, and the distinction is the whole point
of reporting it this way. The PRD's 10 GB tier is where the hypothesis expects the crossover and
that tier was not run: Docker has a 5.27 GB VM here and the sandbox clamps a container to 3 GB
(ADR-0002), so 10 GB would fail for every configuration — three failures and no information.
What is reported is the ladder that fits, with the rung the hypothesis is actually about marked
absent.

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
