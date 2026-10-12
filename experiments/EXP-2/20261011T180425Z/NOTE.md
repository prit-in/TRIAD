# Reading this run

- The batch log records `exit=125` for this run: the `docker compose` client lost its connection
  to the engine at 19:24 UTC ("error waiting for container: unexpected EOF") while the Docker VM
  was short of memory. The experiment's container kept running and wrote this result itself;
  the exit code is the client's, not the experiment's.
- P's `per_stage_s` observation lists four of its five jobs at 0.0 s: the in-process profile job
  and the failed feature-job attempts reported no duration, and the harness turned the missing
  value into 0 (DEC-51). Those are "not recorded", not measured zeros. `full_runs_s` (481.6 s) is
  gate 9 of the job that completed.
- P escalated at 200 000 customers with "feature job failed". The cause is to be diagnosed from
  run-2026-10-11-032's executions and decision records before this verdict is taken as P's.
