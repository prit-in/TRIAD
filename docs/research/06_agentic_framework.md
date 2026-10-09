# 6. Agentic Programming Framework

An agent in TRIAD is a role with four properties: the tools it may call, the inputs and outputs
it is responsible for, the budget it may spend, and a statement of what it may never do. Thirteen
roles work under a deterministic orchestrator (Fig. 8). The organizing principle, taken from the
risks reviewed in Section 3.7, is that **the component that produces something never also
decides whether it is acceptable** [@pearce2022asleep] [@greshake2023injection].

**Fig. 8. Agentic Programming Architecture.** *(figures/fig08_agentic_architecture.drawio)*

## 6.1 Roles

The roles fall into four groups. The *data* roles - Discovery, Profiling, and Cleaning - register
sources, describe them, and choose cleaning rules from a fixed library. The *build* roles - Spark
Planning, Code Generation, and Execution - plan the distributed work, write the code for it, and
run it. The *model* roles - Model Selection, Training, Evaluation, and Optimization - choose
candidate families, train them, judge them, and tune the finalists. The *assurance* roles -
Verification, Governance, and Reporting - check work, enforce policy, and explain results.

Only three roles rely mainly on an LLM in the design: Cleaning (choosing and ordering library
rules), Code Generation (writing slot bodies and repairs), and Reporting (narrative text).
Training and Verification use none at all, and Governance, Evaluation, Execution, and
Optimization use one only to summarize evidence or suggest options, never to decide - so the
checker does not share the generator's failure modes. Each role's prohibition is part of its
definition: Cleaning may not delete rows or invent rules, Code Generation may not add
dependencies or edit outside a slot, Training may not change the data split, Evaluation may not
alter a metric or threshold, Optimization may not apply a setting outside the allowlist, and
Governance may not approve anything.

In the configuration evaluated in this report no LLM provider was available, and every role ran
on its deterministic path: code came from the templates' reference implementations, cleaning
plans from the rule library's defaults, and narratives from fact sheets. This is a supported
mode of the platform rather than a degraded one. It means the results in Section 15 measure the
governed framework itself - its gates, policies, and records - with no model's skill mixed in.

## 6.2 Tools, and who may call them

Agents act on the platform only through a tool registry. Each tool declares the agents permitted
to call it, a schema for its arguments and result, the governance action it requires, and its
side effect. A call is refused - and the refusal recorded - if the agent is not on the tool's
allowlist, if the arguments do not match the schema, or if the policy engine does not allow the
action; an allowed call is appended to the run's audit chain. Submitting a Spark job, for
example, is permitted only to the Code Generation and Optimization roles, requires the
`sandbox.run` action with a known resource profile, and is mediated by the sandbox runner. Tests
show that a refusal at each of the three checks stops the work. Refusals by the authentication
and governance layers are committed to the audit trail on their own, because a refusal is
delivered by raising an error and would otherwise be rolled back with the request that caused
it.

## 6.3 Code generation inside templates

A generated Spark job is never free-form. Each of the three job types - cleaning, feature
construction, and labelling - has a template: a complete, compiling, tested Scala program with
one declared slot whose body may vary. Generation replaces the slot body and nothing else, and
the renderer proves that it did so by comparing the rendered file with the template outside the
slot; a single differing character fails validation. Generated objects get a name derived from
their content hash, so several can coexist and a stored artifact can be replayed exactly.

Every generated job then passes nine validation gates before its output is published, the first
six before it reads its full input (Table 5 in Section 7 lists the agents involved; Fig. 9 shows
where the gates sit):

1. the proposal is structured output of the expected shape;
2. a static scan finds no forbidden API, unbounded collection to the driver, or out-of-slot edit;
3. its dependencies are unchanged and its imports allowlisted;
4. it compiles and packages;
5. its fixture tests pass, run against the generated object itself on hand-checked data;
6. a dry run on a sample of the input completes in the sandbox and writes a plausible report;
7. its output satisfies the stage's data contract;
8. the artifact is stored and its content hash verified;
9. the full execution succeeds and the contract still holds.

Gates 1 to 3 cost milliseconds; gates 4 to 6 cost minutes, so gate 5's result is cached under a
key that covers the generated source, the core library, the fixtures, and the build definition,
and it is reused only when none of them has changed.

**Fig. 9. Multi-Agent Collaboration Workflow.** *(figures/fig09_collaboration_workflow.drawio)*

## 6.4 Feedback, repair, and budgets

When a stage fails, its error is classified against a taxonomy of 27 error classes, each with a
remedy in a playbook: resubmit, raise shuffle partitions, raise executor memory, enable skew
handling, quarantine and continue, regenerate the code, or none. The classifier works from the
job's own structured error report when there is one, and from the exception lines of its log when
there is not. Two classes exist specifically so that the wrong remedy is not applied: a
dependency download failure is resubmitted rather than sent to code regeneration, and a resource
limit outside the agent's allowlist - driver memory, or the container's process limit - is
escalated naming the setting rather than retried.

Retries are bounded three ways. Each stage has a maximum number of repair attempts; a circuit
breaker stops a stage whose error fingerprint repeats, because an identical failure after a
repair means the repair did not address it; and each run has budgets for wall-clock time,
infrastructure retries, optimization cycles, and - when an LLM is configured - calls and cost.
Exhausting any of them escalates the run to a person with a ticket that names the stage, the
diagnoses, and the evidence.

## 6.5 Untrusted content

Anything that came from data - column names, cell values, file names - is treated as untrusted
before it can reach a prompt. It is fenced, so that the model is told where data begin and end,
and scanned for injection patterns, including chat-template control tokens that an attacker
could use to impersonate a system message; matches are flagged and neutralized. This is the
platform's defence against indirect prompt injection [@greshake2023injection], and it is tested
by planting instructions in a dataset (Section 12).

---

## Evidence (not part of the manuscript)

- Roles and prohibitions: PRD section 11.0. Template-only mode: ADR-0003.
- Tool registry: `agents/tools/registry.py`, `agents/tools/pipeline_tools.py`;
  `tests/unit/test_tool_mediation.py`; refusals audited: `tests/security/test_refusals_are_audited.py`.
- Templates: `agents/codegen/templates.py`; slots `cleaning_plan`, `transform`, `transform` in
  `scala_spark/jobs/src/main/scala/triad/jobs/{CleanJob,FeatureJob,LabelJob}.scala`.
- Gates: `agents/codegen/gates.py`; gate-5 cache key `fixture_key`; all nine recorded on
  run-2026-10-03-002.
- 27 error classes, 17 remedies: `triad_core/errors.py`; DEC-20, DEC-25.
- Budgets: `agents/llm/budget.py`; circuit breaker observed on run-2026-10-04-002
  ("error fingerprint repeated").
- Injection scanning: `tests/unit/test_injection.py` (ATC-13); EXP-10.
- Refusals committed despite rollback: `audit.record_and_commit` in `backend/app/api/v1/auth.py`
  and `triad_core/governance.py`; `tests/security/test_refusals_are_audited.py`.
