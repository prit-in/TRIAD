# P did not finish: the host share refused a write

Run of 13-10-2026, 13:43 to 15:23 UTC, on the code after DEC-53 (commit 4ddde4f).

The ladder is complete and is a measurement. B2 completed every rung up to 100 000 customers and
was killed by the container's memory limit at 200 000 (the cgroup recorded the kill); B3 completed
every rung, in 592 s at 200 000.

P ran once, at 200 000 customers (`run-2026-10-13-002`). From the platform's own records: it
profiled the data (14:56:57 to 14:59:19 UTC), passed its cleaning job's gates and ran it (15:02:50
to 15:14:50), committed the clean table (PLANNED at 15:20:47) and computed its cutoffs. Writing the
feature job's source, it raised `OSError: [Errno 12] Cannot allocate memory:
'/workspace/scala_spark/jobs/src/main/scala/triad/jobs/generated'`: the host file share refused a
directory call under the VM's memory pressure. The stage runner did not handle an error raised
while generating, so the phase ended unclassified, and the harness cancelled the run (15:23:14),
whose own record therefore says nothing about why. Fixed in DEC-54.

What this run does not measure: whether P finishes at 200 000 customers. What it does show: P's
run had existed for 1 604 s when it was cancelled, seconds after it raised - 2.7 times B3's 592 s
for its whole pipeline at that rung - so P could not have stayed within 20% of B3 there. EXP-2 was
re-run after DEC-54; this folder is kept so the run history shows the attempt.
