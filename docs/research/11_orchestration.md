# 11. Agentic Workflow Orchestration and Decision Intelligence

The orchestrator decides nothing about content. It decides *what happens next*: which task runs,
when a run starts, whether a failure is retried or escalated, and when the platform must stop and
wait for a person. This section describes the run lifecycle, the scheduler and its triggers, how
decisions are made and recorded, and how the platform adapts within bounds (Fig. 16, Fig. 17).

**Fig. 16. Agent Orchestration Workflow.** *(figures/fig16_orchestration_workflow.drawio)*

## 11.1 The run as a persisted state machine

A run is a row in PostgreSQL with a state, and every transition is committed together with its
audit entry at the moment it happens. A run moves through RECEIVED, PROFILED, PLANNED,
FEATURES_READY, TRAINING, and EVALUATED; it then either pauses in APPROVAL_REQUIRED or ends; after
an approval it is published. It can also end in REJECTED_INPUT, ESCALATED, FAILED, or CANCELLED,
and the two states that wait for a person - APPROVAL_REQUIRED and ESCALATED - can be resumed.
Because the state is committed as it changes, a dashboard shows a run's progress live, and nothing
is lost if the process driving the run dies. An early implementation held a whole preparation phase
in one transaction, so a run was invisible until it ended and lost entirely if the worker stopped;
fixing that turned an implementation detail into a reliability property.

Work is distributed through a task queue in the same database. A worker claims a task under a
lease of 300 seconds, renews the lease while it works, and records the result; a periodic reaper
re-queues any task whose lease has expired, so a worker killed in the middle of a run is replaced
by the next claim. A second reaper fails runs that no task can ever advance and that have
outlived their own wall-clock budget, so that abandoned runs do not accumulate. One run of a given
pipeline executes at a time, enforced by an advisory lock, because runs share output folders.

The PRD specified LangGraph, with a PostgreSQL checkpointer, as the engine of this control loop.
The implementation instead uses the state machine and queue described here, which provide the
same three guarantees - state checkpointed after every step, a human pause that survives restarts,
and resumption - with each verified in a recorded run. LangGraph's checkpointer was installed and
passed a smoke test but is not on the runtime path; the deviation is recorded as a decision.

## 11.2 What starts a run

Runs start four ways, and the cause is recorded with the run. A person starts one through the API.
A scheduler on the worker, ticking every five minutes, starts the other three: it evaluates the
deployed champion against nine retraining rules and starts a *drift*, *data*, or *schedule* run
when one fires; and it polls configured landing folders, registers any new file as a dataset
version, and starts a *data* run on the latest version of every input. The nine rules are those of
the PRD: performance below the floor or decaying (RT-1, RT-4), feature or score distribution drift
(RT-2, RT-3), 20% more labelled data (RT-5), a monthly schedule (RT-6), and changes to the feature
catalogue, the schema, or the churn definition (RT-7 to RT-9).

Feature and score drift are measured with the population stability index (PSI) against the
training data [@lu2019drift] [@gama2014drift]:

$$
\mathrm{PSI} = \sum_{b=1}^{B} (a_b - e_b) \ln \frac{a_b}{e_b} \tag{7}
$$

where $e_b$ and $a_b$ are the shares of the training and current rows in bin $b$; bins are the
training deciles, with a separate bin for nulls so that missing values cannot vanish from the
comparison, and shares are floored at a small constant so that an empty bin does not produce an
infinite index. Categorical features use their categories as bins.

A fired rule does not start a run directly. A validity gate first requires the data quality to be
acceptable, the seven-day cooldown since training to have passed, and - for the volume rule -
enough new labelled rows. A retraining run trains on the newest version of every input, not on the
snapshot that drifted, and starts with a fresh budget. At most one retraining attempt is made per
cooldown window; a failed attempt counts, because a deterministic failure retried every five
minutes would be a storm. On the platform's first live scheduler tick, the deployed champion's
score distribution had drifted and its feature catalogue had changed, so the score-drift and
feature-change rules fired and a retraining run was queued within a second.

**Fig. 17. Autonomous Decision Lifecycle.** *(figures/fig17_decision_lifecycle.drawio)*

## 11.3 How decisions are made

Fig. 17 shows the lifecycle every autonomous decision follows: *observe* a measured signal;
*classify* it against a fixed taxonomy; *decide* by the first matching rule; *record* the decision
with its rule, evidence, and rejected alternatives; *act* within the action's bounds; *verify*
the result against a contract; and, when the bounds or the budget run out, *escalate* to a person
with a ticket that carries the evidence. The same lifecycle governs a routing choice, a hygiene
verdict, a failure diagnosis, a retraining trigger, and a model's acceptance. In every case the
decision is made by code against a written rule. What an agent contributes is the content the rule
is applied to - a cleaning plan, a slot body, a narrative - never the verdict on it, and a person
is always the last step for anything with consequences outside the platform, such as replacing the
production model [@mosqueira2023hitl] [@wu2022hitl].

## 11.4 Resource allocation and adaptive optimization

Resource allocation was described in Section 9.2: the engine for each stage, the container limits
for each job, the dry-run sample, and the in-memory hand-off are all functions of measured sizes,
evaluated by rule. Adaptation happens in four bounded places. A failing job's remedy may change an
allowlisted Spark setting - partitions, memory, skew handling - within the stage's attempt budget.
Between runs, an optimisation loop reads a stage's recorded metrics and doubles its shuffle
partition count after a run in which it spilled, or halves it after one whose reduce tasks took a
median of under 50 ms, always within 2 to 64 (Section 9.5). Hyperparameters of the finalist models
are tuned within the run's optimization budget. And a deployed model is retrained when the
monitoring rules say its world has changed. Each adaptation is bounded in what it may change, how
many times it may try, and how long it may take, and each is recorded - the optimisation loop
twice, with the measurements behind a change and, after the next run, the before-and-after
comparison. Tuning more than one setting, and learning from many runs rather than from the last
one, is future work (Section 21).

---

## Evidence (not part of the manuscript)

- States and terminal sets: `agents/graph/queue.py` (`TERMINAL_RUN_STATES`, `AWAITING_A_PERSON`);
  per-transition commits: FR-073 row; observed live on run-2026-10-03-002.
- Lease 300 s, reapers: `agents/worker.py`, `queue.reap_expired`, `queue.reap_stranded_runs`
  (13 stranded runs failed on 03-10-2026). One run per pipeline: DEC-18.
- LangGraph deviation: DEC-24; smoke test in `docs/implementation/ENVIRONMENT_STATUS.md`.
- Scheduler: `agents/scheduler.py`, `tests/unit/test_triggers.py`; first live tick queued
  run-2026-10-03-001 on RT-3 and RT-7.
- RT-1..RT-9: PRD section 19.1; `agents/retraining.py`. PSI (7): `python_ml/triad_ml/drift.py`
  (`psi`, deciles, null bin, `EPSILON`).
- Retrain on newest window, fresh budget, attempt guard: DEC-22; PRD 19.3.
