# Agent design

For developers working on the orchestration. It describes what the agents are allowed to do,
what stops them doing anything else, and what every attempt costs against a budget.

The short version: **an agent here is a named caller with a permission set, not a conversation.**
Each one exists so that a permission can be attached to it. Nothing in this document requires an
LLM, and this environment configures none (ADR-0003).

## The agents

| Agent | What it does | What it may call | What it may never do |
| --- | --- | --- | --- |
| `data_profiling` | Profiles a registered dataset version on Spark and writes the profile document | `storage.read_manifest` | Write anything to the catalogue |
| `code_generation` | Renders a Scala job from a template, fills only the declared slots, runs it through the gates and submits it | `spark.submit_job`, `storage.commit_output`, `storage.read_manifest` | Change a dependency, edit outside a slot, move an alias |
| `spark_planning` | Chooses the engine, language and deploy mode for a stage from LR-01..LR-10 | nothing — it is a pure decision | Choose anything the rules do not imply |
| `spark_optimisation` | Applies per-job Spark settings from the governance allowlist | `spark.submit_job` | Touch cluster defaults (LR-10) |
| `model_selection` | Screens families, tunes finalists, calibrates, and judges the result against AP-1..AP-8 | nothing outside the ML process | Promote; it can only *propose* |
| `data_engineering` | Commits a cleaned or derived output to the catalogue | `storage.commit_output`, `storage.read_manifest` | Submit a job |
| `governance` | Records the policy verdict for an action | nothing | Decide an approval on a person's behalf |
| `orchestrator` | Leases tasks, counts budgets, retries, escalates | everything, as the process that owns the run | Spend past a ceiling |

## Tools

A tool is the only way an agent touches anything outside its own process. Every call goes
through `REGISTRY.call`, which checks four things in order and stops at the first failure:

1. **Is this agent permitted to call this tool?** An allowlist per tool, not per agent.
2. **Do the arguments match the tool's JSON schema?** Rejected before anything runs.
3. **Does the policy allow the action?** Only for tools that declare a `policy_action`. A tool
   that declares one and is handed no policy engine is **refused, not allowed**.
4. **Was it recorded?** The call, its arguments, its outcome and its duration are appended to
   the run's hash chain before the result is returned.

| Tool | Side effect | Permitted agents | Policy action |
| --- | --- | --- | --- |
| `spark.submit_job` | `SUBMIT_JOB` | `code_generation`, `spark_optimisation` | `sandbox.run` |
| `storage.commit_output` | `WRITE_CATALOG` | `code_generation`, `data_engineering` | none, deliberately |
| `storage.read_manifest` | `NONE` | `code_generation`, `data_engineering`, `ml_engineering`, `analysis` | none |

`storage.commit_output` has no policy action on purpose: committing a computed output to its own
versioned folder is the normal end of a stage that has already passed its gates, and gating it
again on a policy the run has already satisfied would put an approval in front of every stage.
What it *is* subject to is the manifest schema and the content hash, both of which are checked.

**There is no tool that moves a model alias.** ATC-02 — an agent cannot promote a model — holds
because there is nothing to call. `agents/tools/pipeline_tools.py` ends with a comment saying so,
because an absence is easy to delete by accident and the comment is what makes it a decision.

## Code generation and its gates

Generation is template-first: a job is a `Template` with named slots, and only the slots may
vary. An out-of-slot change is a checked property, not a convention.

| Gate | What it checks | Where it runs | Runs here |
| --- | --- | --- | --- |
| 1 | The proposal is structured output matching the expected shape | before rendering | yes |
| 2 | Static scan: forbidden APIs, unbounded driver-side collection, out-of-slot edits | on the rendered source | yes |
| 3 | Dependencies are unchanged against the declared set | on the rendered source | yes |
| 4 | It compiles (`sbt core/package jobs/package`), or byte-compiles for Python, or `EXPLAIN`s for SQL | in the toolchain container | yes |
| 5 | Fixture tests pass | in the toolchain container | **no** |
| 6 | Dry run produces a plausible report on a sample | in the sandbox | **no** |
| 7 | The output satisfies the dataset contract | after execution | yes |
| 8 | The artifact is stored and its content hash matches | on store | yes |
| 9 | Execution succeeded and the contract still passes | after execution | yes |

**Seven of the nine run. Gates 5 and 6 do not, and that is stated rather than implied.** Gate 5
is the Scala fixture suite: it exists, `sbt core/test` passes its 21 cases, and running it on
every generated job would add minutes to every stage on top of gate 4's compile. Gate 6 is a dry
run on a sample, and this pipeline does not do one — it runs the job once, on the whole input.

The gate functions for 7, 8 and 9 were themselves dead code until recently: the *checks* were
being made — the contract was evaluated, the execution status decided the stage, the artifact was
stored under its content hash — but none of them was recorded as a gate, so a reader of
`gate_results` saw four gates where the documentation claimed nine. They are now recorded, which
is what makes the count above checkable rather than asserted.

Gate 2 is the one the governance story depends on: in EXP-8, five seeded forbidden-API faults
were all refused there in under a third of a second each, and none was compiled, let alone
submitted. The same experiment measured what gates cannot see: a **wrong column name passes all
of them**, because a column name is a string until Spark resolves it.

## Policy

`agents/policies/governance.yaml` is version 1.0 and covers 23 actions across four autonomy
levels:

| Level | Meaning | Examples |
| --- | --- | --- |
| L3 `bounded` | The agent may act inside declared limits | `profiling.run`, `cleaning.execute` under 5% of rows, `code.generate` templates-and-slots-only, `sandbox.run` |
| L2 `supervised` | A named approver must decide | `model.promote` with separation of duties, `schema.change`, `churn_definition.change`, `report.publish_external` |
| L1 `advisory` | The agent proposes, a person performs | `source_mapping.confirm` |
| L0 `forbidden` | Never, whatever the context | `dependency.add`, `data.delete`, `external_message.send`, `policy.change`, `registry.set_alias` |

Two of the L0 entries deserve their reasons repeating: `policy.change` is forbidden because
**autonomy must never widen itself**, and `registry.set_alias` because an agent that can move an
alias has promoted a model whatever the approval table says.

The engine **fails closed**. A missing, unparseable or internally inconsistent policy file does
not raise at import; it records the load error and denies every gated action. The Settings page
shows whether the file loaded, because "every run escalates" and "the policy did not parse" look
identical from the outside.

## Routing

Ten deterministic rules decide engine, language and deploy mode. No LLM is consulted and no rule
has a probability.

| Rule | Decides |
| --- | --- |
| LR-01 | Event-level data above the row threshold must not leave Spark |
| LR-02 | Cleaning, labels and features are compiled, typed, versioned Scala |
| LR-03 | Typed Datasets and aggregators instead of Python UDFs on event data |
| LR-04 | Read-only profiling runs from PySpark |
| LR-05 | Modelling and statistics live in Python |
| LR-06 | The Spark-to-Python hand-off is Parquet, and pandas is used only when the estimated in-memory size fits the budget |
| LR-07 | Small inputs run the *same* Spark job in local mode — not a separate pandas path |
| LR-08 | Scoring is in-process below the threshold, an MLflow Spark UDF above it |
| LR-09 | Unbounded driver-side collection is blocked by the static scan |
| LR-10 | Cluster defaults are never edited; per-job settings come from the allowlist |

LR-07 is why TRIAD is 183 times slower than a pandas baseline on a 400-customer dataset
(EXP-1): the same Spark job runs, in local mode, rather than a second implementation that would
have to be kept correct alongside the first.

## Budgets

Ceilings live in the orchestrator, never in the agent — an agent cannot decide to keep going.
Counters are persisted with the run, so a restarted orchestrator resumes with the budget already
spent.

| Ceiling | Default |
| --- | --- |
| `max_llm_calls` | 60 |
| `max_llm_cost_usd` | 3.0 |
| `max_wall_clock_minutes` | 240 |
| `max_code_repair_attempts` | 3 |
| `max_infra_retries` | 2 |
| `max_optimisation_cycles` | 2 |
| `max_identical_fingerprint` | 2 |

The last one is the circuit breaker: an error whose fingerprint repeats stops the loop instead of
spending the remaining attempts on the same failure. EXP-8 measured it working — every detected
fault produced one distinct fingerprint across three attempts.

The Settings page shows these side by side with the pipeline configuration's copy, because two
ceilings that disagree is how a run ends up bounded by a number nobody chose.

## Errors, repair and escalation

Failures are classified into a taxonomy, and the class — never a parsed stack trace — selects a
remedy. The full table is in the [troubleshooting runbook](runbooks/troubleshooting.md).

The shape of it: a `TYPE_CHANGE` gets one retry with `try_cast` and quarantine; an
`EXECUTOR_OOM` gets two retries with more shuffle partitions; a `RENAMED_REQUIRED_COLUMN`
proposes a mapping and **asks a person**; a `LEAKAGE_SUSPECTED` or `ROW_ACCOUNTING_MISMATCH`
stops the run with no retry at all, because neither is something a retry could fix.

An escalation writes a ticket carrying the evidence, the remedies that were tried, and — where a
remedy was computed but not applied — an `unapplied_remedies` list, so a person is not left
guessing what the loop considered.

## Untrusted content

Anything that came from data is fenced before it reaches a prompt, and four defences are tested
separately: masking of identifiers, fencing of the content, an injection scanner, and a flag on
the record. EXP-10 found a fifth class the scanner read straight past — chat-template control
tokens such as `<|im_start|>` — which are now flagged and broken up before fencing.
