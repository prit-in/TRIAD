# Troubleshooting

For whoever is on the other end of an escalation. It covers how a failure is classified, what
the platform tries on its own, what it hands to a person, and the specific problems this system
is known to produce.

## How a failure is classified

A failing stage produces a `TRIAD_JOB_ERROR` with a structured payload — never a stack trace for
someone to parse. The payload is classified into one of 27 error classes, and the **class**
selects the remedy from a fixed playbook. Nothing in that path reads free text.

Each failure also gets a **fingerprint**. A fingerprint that repeats trips the circuit breaker
(`max_identical_fingerprint`, default 2), so an unrepairable fault stops the loop instead of
spending the whole attempt budget re-running the same failure.

## The playbook

| Error class | Remedy | Retries | Recoverable | Needs a person |
| --- | --- | --- | --- | --- |
| `MALFORMED_FILE` | quarantine and continue | 1 | yes | no |
| `TYPE_CHANGE` | `try_cast` with quarantine | 1 | yes | no |
| `MISSING_OPTIONAL_COLUMN` | disable dependent features | 0 | yes | no |
| `MISSING_REQUIRED_COLUMN` | none | 0 | **no** | no |
| `RENAMED_REQUIRED_COLUMN` | propose a mapping | 0 | yes | **yes** |
| `EXECUTOR_LOST` | resubmit | 2 | yes | no |
| `EXECUTOR_OOM` | raise shuffle partitions | 2 | yes | no |
| `DRIVER_OOM` | rewrite to storage | 1 | yes | no |
| `DATA_SKEW` | enable skew handling | 1 | yes | no |
| `INVALID_CAST_ANSI` | `try_cast` with quarantine | 1 | yes | no |
| `UNRESOLVED_COLUMN` | regenerate code | 3 | yes | no |
| `SCALA_COMPILE_ERROR` | regenerate code | 3 | yes | no |
| `PYTHON_EXCEPTION` | regenerate code | 3 | yes | no |
| `MISSING_PACKAGE` | none | 0 | **no** | no |
| `CONVERGENCE_WARNING` | scale and more iterations | 2 | yes | no |
| `EXTREME_IMBALANCE` | switch metrics and weighting | 0 | yes | no |
| `INSUFFICIENT_DATA` | none | 0 | **no** | no |
| `POLICY_VIOLATION` | regenerate code | 2 | yes | no |
| `TIMEOUT` | optimised retry | 1 | yes | no |
| `LLM_ERROR` | backoff and re-ask | 3 | yes | no |
| `STORAGE_ERROR` | refresh credentials | 1 | yes | no |
| `CONTRACT_VIOLATION` | regenerate code | 2 | yes | no |
| `ROW_ACCOUNTING_MISMATCH` | none | 0 | **no** | no |
| `LEAKAGE_SUSPECTED` | none | 0 | **no** | no |
| `DEPENDENCY_RESOLUTION` | resubmit | 1 | yes | no |
| `RESOURCE_CONFIG_INVALID` | none | 0 | **no** | no |
| `UNKNOWN` | none | 1 | yes | no |

The four with no remedy and no retry are the ones where a retry would be dishonest.
`ROW_ACCOUNTING_MISMATCH` means rows went missing without being accounted for;
`LEAKAGE_SUSPECTED` means the model may have seen the future. Neither is something re-running
fixes, and both stop the run.

The last two were added after EXP-6 measured them going wrong (DEC-20). A dependency the build could not download used to be classified as a Scala compile error, which prescribes regenerating code that was never the problem; it is now resubmitted once. A job Spark refuses to start because a memory setting is below Spark's own minimum used to be `UNKNOWN`. It is now named, and handed to a person rather than fixed, because `spark.driver.memory` is not on the agent's `spark.config_change` allowlist - the escalation says which setting and the minimum Spark asked for.

## Handling an escalation

A run in `ESCALATED` has written a ticket. Read it before anything else:

```bash
curl -s http://localhost:8010/api/v1/pipelines/$RUN/decisions -H "Authorization: Bearer $TOKEN" \
  | jq '.[] | select(.outcome=="escalated") | {decision, policy_rule_ids, evidence_refs}'
```

The ticket carries the rule ids that fired, the evidence they fired on, the remedies the loop
tried, and — where a remedy was computed but could not be applied — an `unapplied_remedies` list,
so you are not left guessing what was considered.

Then:

```bash
# The stage timeline with attempts
curl -s http://localhost:8010/api/v1/pipelines/$RUN -H "Authorization: Bearer $TOKEN" | jq .stages

# Redacted logs
curl -s http://localhost:8010/api/v1/pipelines/$RUN/logs -H "Authorization: Bearer $TOKEN"

# The generated code and every gate it passed or failed
curl -s http://localhost:8010/api/v1/pipelines/$RUN/artifacts -H "Authorization: Bearer $TOKEN" | jq '.[].gate_results'

# Resume once the cause is addressed
curl -s -X POST http://localhost:8010/api/v1/pipelines/$RUN/resume -H "Authorization: Bearer $TOKEN"
```

Resuming does not re-run what succeeded: stages are memoised on stage, inputs and config hash.

## Specific problems this system produces

### "The run escalated on MISCALIBRATED"

The acceptance policy refused a model whose predicted probabilities do not match observed rates,
after trying the whole AP-5 remedy chain: isotonic, then sigmoid, then sigmoid with class
weighting removed. The acceptance decision record carries `ece_after_calibration`,
`brier_skill_after_calibration` and `calibration_remedies_tried`.

This is the correct behaviour for a model that cannot be calibrated, and it is also what EXP-5
ran into at every non-zero missing-value rate. If you are seeing it on data with many nulls,
that is the known case rather than a new one.

### "Every gated action is denied"

Check the Settings page, or `GET /settings`, for whether the governance policy loaded. A
missing, unparseable or internally inconsistent policy file denies everything by design — the
engine fails closed. The page shows the load error; the fix is in the file, not in the engine.

### "The audit chain verification fails"

`GET /api/v1/audit-logs/verify` names the entry and the scope where recomputation diverges.
Causes, in order of likelihood:

1. **A partial restore.** Rows are missing. Restore storage and database together.
2. **Manual editing of `audit_logs`.** Do not; the chain exists to detect exactly this.
3. **A genuine fork.** This happened once, from appending without holding the advisory lock, and
   is fixed — but if it recurs, two writers are chaining from the same head.

The chain cannot detect a whole run's chain being removed, because the design is one chain per
run. That limit is recorded rather than hidden; closing it needs a periodic seal over the chain
heads.

### "A run is in progress and something else is slow"

`audit.record` takes `pg_advisory_xact_lock` per chain, and the lock must be held to commit —
it is what stops two writers forking the chain. A long transaction that writes to the **system**
chain therefore delays other writers of that chain.

The observed case was logins: ingest writes carry no run id, so they land on the system chain,
and a run holding them for its whole length queued three logins for 4:32, 3:08 and 1:47.
`run_demo.py` now commits its ingest before the run begins, and the same situation re-measured
during `run-2026-09-21-020` gave eight logins in 0.18–0.45s against twelve minutes of open
transaction. If you see it again, look for a long transaction rather than for lock contention:

```sql
SELECT pid, state, now() - xact_start AS age, left(query, 80)
FROM pg_stat_activity WHERE datname = 'triad' AND state <> 'idle'
ORDER BY xact_start;
```

### "A second run of the same pipeline will not start"

By design, and it returns 409. Runs of one pipeline share their `clean/`, `features/` and
`labels/` folders, and committing supersedes what is there — which is how one feature job failed
on data that had changed underneath it. An abandoned run does not block the pipeline for ever;
the lease expires.

### "A Spark job is not running in a container"

In this environment `docker` is not on the PATH inside the dev container, so `get_runner()` falls
back to `LocalRunner` and jobs run in-process. Every claim about container isolation describes
`DockerRunner`, which is implemented and has its own tests, but is not the path this machine
takes. EXP-6 found this, and the result records the container kill as `container_killed: false`
rather than reporting a recovery that never happened.

### Two classifications that are still wrong

Both found by EXP-6 and both open:

- **An out-of-memory failure from a 256m driver is classified `UNKNOWN`**, so the playbook
  prescribes nothing and `RAISE_EXECUTOR_MEMORY` never fires. The classifier does not recognise
  the signature this particular failure produces.
- **A coursier dependency-download failure is classified `SCALA_COMPILE_ERROR`**, so the remedy
  is to regenerate code that was never the problem. A network failure is not a compile failure.

If you hit either, the diagnosis is the classifier, not your job.

## Escalations that are not failures

Two outcomes read like problems and are not:

- **`KEEP_CHAMPION`.** The challenger did not beat the champion with confidence, so it is kept
  for shadow scoring and no approval is requested. Nothing is wrong.
- **A rejected promotion.** The champion stays exactly where it was, and the rejection is
  recorded with its mandatory comment. That is the gate working.
