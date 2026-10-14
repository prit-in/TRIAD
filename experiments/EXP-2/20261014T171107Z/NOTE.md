# P was escalated by defects in the platform, not by its own failure alone

Run of 14-10-2026, 17:11 to 20:14 UTC, on the code after DEC-54 (commit 3442102).

The batch log records `exit=125` at 19:12 for this run. The `docker compose` client lost the
engine then; the experiment's container went on and wrote this result itself.

**The ladder is a measurement.** It matches the run of 13-10-2026:
- B2 completed every rung up to 100 000 customers and was killed by the container's memory limit
  at 200 000. The VM's kernel log shows that kill: a 2.9 GB Python process.
- B3 completed every rung, in 632 s at 200 000.

**P at 200 000 customers (`run-2026-10-14-001`)**, from the platform's records:
- It profiled the data in 178 s and cleaned it in 381 s.
- Its feature job ran in the sandbox with 1 GB of heap and 8 shuffle partitions. It made no
  recorded progress on its last six stages for 50 minutes, and the medium profile's one-hour
  deadline killed it (exit 124). That much is P's own behaviour at this size.

What followed was not P's own behaviour:
- **The timeout was misread.** It was diagnosed from the job's own shutdown message and filed
  `UNKNOWN`, with no remedy.
- **A scheduled retrain overwrote P's build.** At 20:08 the scheduler started a drift-triggered
  retrain (`run-2026-10-14-002`) because the published champion's seven-day cooldown had just run
  out. Its build removed P's generated feature source and repackaged the shared JAR. P's retry,
  whose dry run had passed, then could not load its class (exit 101).
- **The retry was diagnosed from the wrong report.** The retry wrote no report, so it was
  diagnosed from the first attempt's, under the same fingerprint, and the circuit breaker
  escalated P with "feature job failed".
- **P's result was hidden.** By then P's in-process JVM had ended. The kernel recorded no kill of
  it, and it left no crash log. The harness's `spark.stop()` raised `ConnectionRefusedError` over
  P's escalation.

All four are fixed in DEC-55.

**What this run does measure:** P had run 5 493 s when it stopped, 8.7 times B3's 632 s at that
rung.

**What it does not measure:** whether P finishes at 200 000 customers, or what the platform's
timeout remedy does there.

EXP-2 was re-run after DEC-55. This folder is kept so the run history shows the attempt.
