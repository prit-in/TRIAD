# Demonstration guide

How to show TRIAD working, end to end, on the real stack, without editing any source code during
the demonstration. Every command below exists in this repository; every outcome named is one the
platform has produced on recorded runs (the last section says where).

All commands run from the repository root. `DEV` below means:

```bash
docker compose -f docker/compose.yaml --profile tools run --rm -T dev
```

## 1. Setup (once, before the day)

```bash
make build                      # the toolchain image (Python 3.11, JDK 17, Spark 4.1.3, sbt)
make up                         # postgres, mlflow, api, orchestrator, sandbox-runner, frontend
make migrate                    # database schema
docker compose -f docker/compose.yaml --profile tools run --rm -T \
    -e TRIAD_SEED_PASSWORD=<choose one> dev python scripts/seed_demo.py --force
make scala-package              # the core and jobs JARs the sandbox submits
```

Seven accounts are created, one per role, all with the password you chose:
`engineer@triad.test` (Data Engineer), `scientist@triad.test` (Data Scientist),
`ml@triad.test` and `approver@triad.test` (ML Engineers), `admin@triad.test`,
`analyst@triad.test` (Business Analyst) and `viewer@triad.test`.

Check before going further: `curl http://localhost:8010/health` answers, and the dashboard at
<http://localhost:5173> shows the sign-in page.

## 2. Demonstration data

```bash
$DEV python scripts/prepare_demo_data.py --customers 2000
```

This generates a synthetic retail dataset (customers, transactions, products, returns, web
sessions, support tickets, promotions over 24 months) and registers each table as the next
version of its dataset, through the same registry code `POST /datasets` uses - contracts,
checksums and audit entries apply. Larger values make every stage slower; 400 customers is the
size the scripted demonstration (`make demo`) was timed at, about fourteen minutes end to end.

## 3. The main scenario, in the browser

1. **Start a run.** Sign in as `ml@triad.test`, open **Runs**, and use **Start a run of
   churn_v1**. The dialog names the dataset versions it will read; confirming opens the run page.
   (Whoever starts the run is the requester of any approval it asks for, which is what makes the
   separation-of-duties check in section 4 visible.)
2. **Watch it.** The run page updates live: states (RECEIVED, PROFILED, PLANNED, FEATURES_READY,
   TRAINING, EVALUATED ...), each agent decision with the rule it applied, the generated Scala for
   cleaning, features and labels with its nine gate results, the per-stage **Spark execution**
   metrics (shuffle bytes, spill, task skew), and the redacted timeline.
3. **The acceptance policy decides.** Two outcomes are normal and both are worth showing:
   - **Promotion proposed** - an approval request appears; continue with section 4.
   - **Escalated** on calibration (AP-5) or a segment recall gap (AP-6) - the run page explains
     why; if the candidate beat the champion, a **Promote despite a failed check** panel is
     offered; continue with section 5.
4. **The report.** **Reports** shows the claim-checked business report; **Save as PDF** prints the
   same document.

## 4. Approval scenario

- Still as `ml@triad.test`, try to approve the request on **Approvals**: the server refuses it,
  because `ml@` started the run and so requested the promotion; the refusal is audited.
- Sign in as `approver@triad.test`, the second ML Engineer. The bell shows the request without a
  page refresh. Expand **Evidence**: metrics against the champion with a 95% interval,
  calibration, segment checks. Approve with a comment.
- After approval the worker promotes the model, runs the smoke test, scores customers and
  publishes; the run ends PUBLISHED. **Models** shows the new champion and its model card.

## 5. Override scenario (promotion despite a failed check, PRD section 30)

- Sign in as `scientist@triad.test`, open the escalated run, and write the justification
  (at least 20 characters) in **Promote despite a failed check**.
- Two shares are created: one for an ML Engineer, one for an Admin. Approve the first as
  `approver@triad.test` and the second as `admin@triad.test`. `ml@` is refused on either share,
  having started the run; the same person cannot decide both shares, and the requester can
  decide neither.
- The run is promoted and published; the run page then reads *Promoted despite a failed check*,
  and the model card's Approval section records the rule that failed, the note and the approvers.

## 6. Rollback scenario

- As `approver@triad.test` (or `ml@`), open **Models**. Under the champion, **Roll back to vN** offers only the
  previous recorded champion; give a reason (at least 10 characters) and confirm.
- The alias moves, the action is audited, and an alert is raised - visible on the bell and on
  **Monitoring**. Any other version is refused by the server: deploying a model that was never
  approved needs an approval.
- The automatic rollback after a failed post-deployment smoke test is shown by the scripted run:

```bash
$DEV python scripts/run_demo.py --fail-smoke
```

## 7. Failure injection

No code is edited for any of these; each puts a fault in the data or the infrastructure.

| Fault | How | What to show |
| --- | --- | --- |
| Renamed key column | `$DEV python scripts/prepare_demo_data.py --fault renamed-key` | the transactions version is refused at ingest by its contract; nothing downstream runs on it |
| Missing customer keys | `$DEV python scripts/prepare_demo_data.py --fault key-nulls`, then start a run | 3% of lines quarantined with a reason code; row accounting still balances; the profile lists the issue |
| Prices as text | `$DEV python scripts/prepare_demo_data.py --fault price-as-text`, then start a run | `try_cast` plus quarantine; the cleaning policy holds the stage if more than 5% of rows are affected |
| Drift | `$DEV python scripts/prepare_demo_data.py --fault drift`, then start a run | the scoring step's drift report (PSI per feature) and the retraining triggers it fires; EXP-7 measures the same mechanism |
| A Spark job dies | during a run's clean or feature stage: `docker ps --format '{{.Names}}' \| grep triad-sbx- \| grep -v dryrun \| head -1 \| xargs -r docker kill` | the failure is diagnosed and the job resubmitted within its attempt budget; the timeline records both |
| An agent tries to bypass governance | `$DEV python scripts/run_experiment.py EXP-10` | moving the champion alias, self-approval, an injected instruction and an exhausted LLM budget are each refused and audited |

## 8. Replay scenario

```bash
make replay                     # scripts/replay_run.py --latest
```

The last completed run is re-executed from its stored generated code; the output hashes must be
identical and the LLM call count zero, or the command exits non-zero.

## 9. Where the evidence is

| Evidence | Where |
| --- | --- |
| Run states, decisions, gates, Spark stage metrics, timeline | the run page; `GET /api/v1/pipelines/{run}/decisions`, `/artifacts`, `/executions`, `/logs` |
| Approvals and their evidence bundles | **Approvals**; `GET /api/v1/approvals/{id}/evidence` |
| Model versions, cards, rollbacks | **Models**; `reports/<run_key>/model_card.md` in the data volume |
| Claim-checked reports | **Reports**; `reports/<run_key>/business_report.html` |
| Tamper-evident audit trail | **Audit** (admin); `GET /api/v1/audit-logs/verify` |
| Alerts and drift | the bell, **Monitoring** |
| Experiment results | `experiments/EXP-n/latest.json` and `docs/implementation/EXPERIMENTS.md` |
| A recorded override, end to end | run-2026-10-07-001 (AP-6 escalation, two approvals, published) |

## 10. Fallbacks

- **The API stops picking up changes or answers 502s.** Restart it and the worker:
  `docker compose -f docker/compose.yaml restart api orchestrator`. (Its file watcher can die with
  "Cannot allocate memory" under WSL2; nothing else is wrong when that happens.)
- **Docker is short of memory.** The VM is 5.27 GB. Stop what the scenario does not need, for
  example `docker compose -f docker/compose.yaml stop mlflow` while only browsing.
- **The browser path is unavailable.** `make demo` runs the whole governed loop from the command
  line and plays both people at the approval gate; `--reject` shows a refused promotion.
- **A run takes too long on the day.** Prepare data with `--customers 400`; every scenario above
  works at that size.
