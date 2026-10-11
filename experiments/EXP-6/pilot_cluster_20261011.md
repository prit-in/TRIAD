# Pilots of EXP-6's cluster arm, 10 and 11 October 2026

The cluster arm asks the PRD's first EXP-6 question as it is written - do Spark's own task
retries absorb a lost executor? - which local mode cannot show, because there the driver is the
executor. The arm runs the clean stage on the compose cluster (`spark-master` and one
`spark-worker`, `--profile spark`) and kills the job's executor JVM in the worker while the
driver keeps running in its sandbox container. These pilots measured only whether the path works
and whether the fault lands, on the EXP-6 data (400 customers, seed 42), through EXP-6's own
`_arm` (`experiments/_logs/pilot_cluster.py`, not committed: `_logs` is ignored).

| Pilot | What happened | What changed |
| --- | --- | --- |
| 1 (10-10, 20:21 UTC) | The driver was refused: `Failed to connect to spark-master/172.20.0.2:7077`. The master was on `triad-net` and the sandbox network and had bound the address its name resolved to on `triad-net`; the driver has only the sandbox network. | The cluster moved to the sandbox network only (`docker/compose.yaml`, DEC-46). |
| 2 (10-10, 20:34) | The clean job ran on the cluster and committed (content hash `5399c7fd`, one attempt, job 81.6 s). The kill failed with no output: the kill script's own command line holds the marker it searches for, and it killed itself first. | The script skips its own pid; a test runs it against stand-in processes. |
| 3 (11-10, 00:52) | No executor existed 10 s after the job's container appeared: the driver's JVM, the application's registration and the executor's JVM come first. | The script waits for a new executor instead of a fixed time. |
| 4 (11-10, 01:11) | The executor was killed 5 s after it started (exit 137 in the worker's log); Spark launched a replacement and the job finished in one attempt with the same content hash - but the driver's log says `Asked to remove non-existent executor 0`: it had not registered, held no task, and no task was lost. The arm's evidence check (lost-task lines in the driver's log) correctly declined to count it. | The script waits until the executor's own log (`stderr` in its working directory) says `Running task`, then kills it 2 s later. |
| 5 (11-10, 01:25) | The executor was killed 28.8 s into the job, 2 s after its log said `Running task`. The driver's log: `ERROR TaskSchedulerImpl: Lost executor 0 on 172.20.0.3: Command exited with code 137` and `WARN TaskSetManager: Lost task 0.0 in stage 0.0 (TID 0) ... ExecutorLostFailure (executor 0 exited caused by one of the running tasks)`. Spark launched a replacement executor, re-ran the task, and the job finished in one platform attempt with the same content hash as the clean cluster run. | None: this is the fault the arm needs, and EXP-6 runs it. |

What the pilots establish beyond the fault: the cluster path itself works - generated jobs
compile, pass their gates, dry-run and run on the compose cluster with a sandboxed driver, and
the cluster's output is byte-identical across every cluster run, killed or not (`5399c7fd...`). Pilot 5 is a pilot: EXP-6's own run is the measurement.
