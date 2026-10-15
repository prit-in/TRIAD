# 9. Apache Spark Distributed Processing Layer

Spark is TRIAD's engine for every transformation of event-level data: cleaning, customer-day
aggregation, snapshot expansion, labels, window features, and scoring sets. This section
describes how work reaches Spark, how resources are allocated to it, how it tolerates failure,
and which optimization techniques are applied (Fig. 13, Table 6).

**Fig. 13. Distributed Spark Architecture.** *(figures/fig13_spark_architecture.drawio)*

## 9.1 Routing work to engines

No agent chooses where a stage runs. Ten ordered routing rules do, each a condition over the
stage's kind and measured size, and the first rule that matches decides; the decision is
recorded with the rule's identifier and the evidence it was judged on. Event-level
transformations above a size threshold must stay in Spark (LR-01). Reusable pipeline stages -
cleaning, features, labels - are compiled Scala jobs generated from templates (LR-02), and row-level
logic on event data uses typed Scala rather than Python user-defined functions (UDFs, LR-03).
Read-only profiling runs from PySpark (LR-04), and training, tuning, evaluation, and explanation
run in Python (LR-05). Inputs below 1 GB run the same Spark job in local mode rather than through a
separate single-machine code path (LR-07), so that a small run exercises exactly the code a large
run would. Driver-side collection of unbounded data is forbidden and enforced by the static scan
(LR-09), and Spark settings are changed only per job, only from an allowlist (LR-10).

## 9.2 The resource allocation function

Three allocations are made for every job, and each is a function of measured quantities.

**The Spark-to-Python hand-off (LR-06).** After the feature and label jobs, the labelled table
must reach the Python learning stack. It is loaded into memory only if it fits a fraction of the
learning worker's memory budget; otherwise the distributed trainer is used:

$$
\mathrm{target}(s) =
\begin{cases}
\text{in-memory (pandas)} & \text{if } \hat{S}(s) \le \varphi\, R_{\mathrm{ML}} \\
\text{distributed} & \text{otherwise}
\end{cases},
\qquad
\hat{S}(s) = \lambda \sum_{g \in G(s)} \sum_{c \in g} u_{g,c} \tag{3}
$$

where $\hat{S}(s)$ estimates the in-memory size of table $s$ from its Parquet footer alone -
$u_{g,c}$ is the uncompressed size of column chunk $c$ in row group $g$, and $\lambda = 1.5$
allows for the higher in-memory cost of string columns - $R_{\mathrm{ML}}$ is the worker's memory
budget (4 GiB here), and $\varphi = 0.25$. The estimate needs no data to be read, so the decision
costs milliseconds, and it is recorded with its inputs: on an 800-customer run, a 1.2 MB estimate
was routed to pandas.

**The container for each job.** Every job runs under a named resource profile $p$, and the
container's limits are the profile's values clamped to what the host can give:

$$
\mathrm{cpu}(p) = \min(c_p, C_{\mathrm{host}}), \quad
\mathrm{mem}(p) = \min(m_p, M_{\mathrm{host}}), \quad
\mathrm{pids}(p) = \pi_p \tag{4}
$$

with $C_{\mathrm{host}} = 4$ and $M_{\mathrm{host}} = 3$ GB on the evaluation workstation. A
job's full execution runs under the *medium* profile and its dry run under *small*. The process
limit $\pi_p$ was set by measurement: every Spark thread counts against it, the feature job peaked
at 292 threads, and a first limit of 256 made that job fail with an inability to create threads
- or, worse, spend its whole ten-minute timeout retrying - so the limits were doubled to 512,
1 024, and 2 048 for the three profiles.

**The dry-run sample (validation gate 6).** Before a job runs on its full input, it runs on a
sample whose size grows with the input but is bounded at both ends:

$$
r(n) = \min\!\left(r_{\max},\ \max\!\left(r_{\min},\ \lceil f\,n \rceil\right)\right) \tag{5}
$$

with $n$ the input rows, $f = 0.01$, $r_{\min} = 1\,000$, and $r_{\max} = 100\,000$. The lower bound
keeps a small input's dry run meaningful; the upper bound keeps a large input's dry run cheap.

## 9.3 Fault tolerance

Spark's own task retries handle the loss of a task within a job [@zaharia2016spark]. TRIAD adds
three layers above that. A failed job is diagnosed and, if its error class has a remedy that
needs no new code, resubmitted with that remedy within the stage's attempt budget (Section 6.4).
Every job writes only to its run's staging folder and the platform publishes the output
atomically after the contract check, so a job that dies halfway leaves nothing behind that a
later stage could read. And the orchestration task that drives the run holds a lease: when a
worker is killed, the lease expires, the task is re-queued, and the next claim resumes the run,
which was observed when the worker was restarted in the middle of a run.

## 9.4 Measuring what a job did

Every job records each Spark stage it ran: its duration, task count, bytes read and written by
the shuffle, bytes spilled from execution memory to disk, garbage-collection time, and task skew,
defined as the slowest task's duration over the median task's. The measurements come from a Spark
listener that the job's fixed half registers before its first read, not from Spark's monitoring
endpoint, because a sandboxed job has no network and no user interface to ask. Listener events
are delivered asynchronously, so the job stops Spark - which drains them - before it writes its
report; written earlier, the last stage could be missing. The stages are stored on the job's
execution record and drawn on the run page, so a slow stage can be told apart from a skewed or
spilling one without opening a log.

## 9.5 Optimization techniques

Table 6 lists the Spark optimization techniques the platform uses or exposes, and states plainly
which are not implemented. The literature on Spark tuning [@herodotou2020tuning]
[@cheng2021sparkcloud] [@lin2023sparktuning] shows that configuration matters a great deal; TRIAD
lets an agent change settings in two bounded ways. Within a run, a diagnosed failure may trigger
an allowlisted remedy (Section 6.4). Between runs, an optimisation loop reads the stage
measurements of the previous run of the same pipeline and stage and changes one setting, the
shuffle partition count, inside a quota of 2 to 64: it doubles the count when a stage spilled,
and halves it when the reduce stages' median task finished in under 50 ms, where per-task
overhead outweighs the work. The change applies to the next run only and is recorded twice -
once with the measurements that motivated it, and again after the run with a before-and-after
comparison. Because each side of that comparison is a single run, a wall-clock difference
within ten percent is recorded as no difference rather than as an improvement.

**Table 6. Spark Optimization Techniques**

| Technique | How TRIAD uses it | Status |
| --- | --- | --- |
| Local mode for small inputs | LR-07 routes inputs below 1 GB to `local[N]`, same job, same code | Applied on every run here |
| Adaptive query execution | Spark 4 default; skew-join handling switched on by the skew remedy | Applied; remedy allowlisted |
| Shuffle partition count | Set per stage by the optimisation loop; raised by the shuffle remedy | Applied within 2-64 (`spark.sql.shuffle.partitions`) |
| Broadcast joins | Threshold changeable per job | Allowlisted (`spark.sql.autoBroadcastJoinThreshold`) |
| Executor memory | Raised by the out-of-memory remedy | Allowlisted; driver memory deliberately not |
| Columnar storage | Parquet with microsecond timestamps and a manifest per output | Applied |
| Partitioned outputs | Feature table partitioned by snapshot date | Applied |
| No Python UDFs on event data | LR-03; typed Scala instead | Enforced by routing |
| No unbounded driver collection | LR-09; the static scan of gate 2 refuses it | Enforced |
| Thin Java archives (JARs) | Jobs packaged without Spark; the core library passed with `--jars` | Applied |
| Per-stage execution metrics | Shuffle bytes, spill and task skew for every stage (FR-036) | Applied on every job |
| Configuration optimization loop | One setting, from the previous run's measurements (FR-037) | Applied; before-and-after recorded |
| Caching decisions | Deciding what to persist between actions (FR-035) | Not implemented |

## 9.6 Cluster deployment

The same jobs run against a standalone Spark cluster when the routing configuration names one,
and the compose definition includes an optional Spark master and worker for that purpose. The
experiments in this report did not use it: on one eight-core workstation, a cluster of containers
would measure scheduling overhead rather than scaling, so the experiments that vary parallelism
do so with `local[N]` instead (Section 13).

---

## Evidence (not part of the manuscript)

- Routing rules: `agents/policies/routing.yaml`, `agents/routing.py` (LR-06 branch).
- Equation (3): `triad_core/parquet_io.estimate_in_memory_bytes` (factor 1.5); threshold
  `pandas_max_fraction_of_ram: 0.25`, `ml_worker_ram_bytes: 4294967296`; "LR-06 -> pandas (1.2 MB
  estimated)" logged on run-2026-10-04-004.
- Equation (4): `sandbox/runner.py` `RESOURCE_PROFILES`, `HOST_LIMITS`; DEC-25 (292 threads
  measured via `pids.peak`); medium for full execution, small for dry runs: `agents/graph/pipeline.py`.
- Equation (5): `agents/graph/pipeline.py` `dry_run`; `pipelines/churn_v1.yaml` `validation:`.
- Allowlist: `agents/policies/governance.yaml` `spark.config_change`. Thin JARs: DEC-04.
- Lease recovery: run-2026-10-03-002 (worker restarted mid-run). Cluster profile: `docker/compose.yaml`.
- Section 9.4: `scala_spark/core/.../StageMetrics.scala`, `TriadJob.scala` (stop before report),
  `StageMetricsSpec` (TC-036), `GET /pipelines/{id}/executions`; DEC-29.
- Section 9.5 loop: `agents/optimizer.py` (OPT-1, OPT-2, quota, 10% noise band),
  `ChurnPipeline._plan_shuffle_partitions`, `tests/unit/test_optimizer.py`; DEC-31.
