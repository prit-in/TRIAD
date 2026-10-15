# This run wrote no result: the Docker VM froze under P and restarted

Run of 14-10-2026, from 21:45 UTC, on the code after DEC-55 (commit 39625b0), with the
orchestrator stopped. The ladder finished in 63 minutes. P started at 22:48:32
(`run-2026-10-14-005`).

The VM froze from about 01:00 UTC on 15-10 and restarted at about 02:51. The experiment's
container ended with it, so no `result.json` was written. Nothing below is from the harness. It
is read from the platform's records and the files the jobs left in the data volume, and it
says no more than they do.

**P at 200 000 customers:**
- **Profile:** 177 s. **Cleaning:** 378 s, and the run reached PLANNED at 23:03:39. These are the
  last records the database committed. The feature stage's own records were flushed but not
  committed when the process died, so they are gone.
- **Feature attempt 1:** about 23:07 to 00:11 (`logs/features_f4a91eff.log`), the length of the
  medium profile's one-hour limit.
- **Feature attempt 2:** the retry, 00:15 to 00:49, 2 038 s (`features_report.json`,
  `logs/features_f94c1a1f.log`).
  - One shuffle stage completed in 1 394 s, with 579 s of garbage collection and 440 MB spilled.
  - At 00:45:56 the job's executor lost contact with its own driver (heartbeats). The
    SparkContext then shut down, and six stages were cancelled.
- **Attempt 3:** its dry run started at 00:55:39 and logged nothing after 00:59:55. Its
  10-minute limit was never enforced, because the sandbox runner, like the rest of the VM, had
  stopped responding. The container was found exited (255) at the VM's restart.

**What this run shows:** P had run more than four hours (22:48 to about 02:51) without finishing
its feature stage. That is 6.4 times the 632 s B3 took for its whole pipeline at this rung on
14-10, and B3 completed every rung of every ladder.

**What it does not show:** what P would do on a node with more memory than this 5.27 GB VM.
EXP-2 is concluded on this run, the two before it and their ladders. A fifth attempt was not
made, because each of the last three exhausted the VM in the same place.
