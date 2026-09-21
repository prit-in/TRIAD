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
| EXP-1 | Small dataset against the non-agentic baselines | implemented, not yet run | — | `experiments/exp01_small_dataset.py` |
| EXP-2 | Medium dataset: where a single node gives out | implemented, not yet run | — | `experiments/exp02_medium_dataset.py` |
| EXP-3 | Scaling with parallelism | implemented, not yet run | — | `experiments/exp03_large_dataset.py` |
| EXP-4 | Schema errors | **partial** | **held** | `experiments/EXP-4/latest.json` |
| EXP-5 | Missing values | implemented, not yet run | — | `experiments/exp05_missing_values.py` |
| EXP-6 | Spark failures and recovery | **partial** | **did not hold** | `experiments/EXP-6/latest.json` |
| EXP-7 | Model degradation | **partial** | **held** | `experiments/EXP-7/latest.json` |
| EXP-8 | Scala compile failures | implemented, not yet run | — | `experiments/exp08_codegen_faults.py` |
| EXP-9 | Leakage sentinel | **completed** | **held** | `experiments/EXP-9/latest.json` |
| EXP-10 | Governance bypass | **completed** | **held** | `experiments/EXP-10/latest.json` |

`partial` means the experiment ran and produced measurements but could not run the workload the
PRD specifies, and the limitation is recorded in the result. The harness refuses a `partial` or
`not_run` result that names no limitation, and refuses a verdict on a hypothesis when no
measurement was recorded.

## What the four completed experiments found

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
