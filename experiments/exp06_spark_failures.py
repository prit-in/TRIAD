"""EXP-6: Spark failures and recovery.

PRD section 33:

    Setup: kill an executor mid-stage; set executor memory too low; give one customer 20% of
    all lines.
    Hypothesis: Spark's task retries absorb the kill; the playbook fixes out-of-memory within
    2 retries; adaptive execution or salting handles skew.
    Independent variable: fault type.
    Dependent measures: recovery rate, retries, time to recovery, output checksum against a
    clean run.
    Expected observation: recovered outputs identical to the clean run.

This is the experiment the repair loop was built for, and - unlike EXP-8 - it can be run in
full here, because every remedy it needs is deterministic. Raising the executor memory, raising
the shuffle partitions, resubmitting a lost job and enabling adaptive skew handling are all
infrastructure changes that `StageRunner._apply_fix` makes to the stage inputs and
`stage_scala_job` reads on the next attempt. None of them needs a model to rewrite code.

**Every arm checks that its fault happened.** An arm that "recovers" from a fault that never
landed measures an ordinary run. The first version of this experiment did exactly that twice:
its kill thread called a docker CLI the image does not have, and its memory arm set 256m, below
the roughly 450 MB of heap Spark needs to start at all - so Spark refused to start, which is a
configuration error the playbook deliberately leaves to a person (RESOURCE_CONFIG_INVALID), not
an out-of-memory. So the memory arm now starts just above that minimum and counts only if the
first attempt really failed with an executor out-of-memory, and the kill arm counts only if a
running job's container was killed.

**The checksum comparison needs identical inputs, so the data is generated once and shared.**
Three of the four arms - the clean baseline, the low-memory fault and the killed container -
read byte-identical source data, which is what makes "recovered output identical to the clean
run" a meaningful claim. The skew arm necessarily has different input data, so its checksum is
reported but not compared; comparing it would be comparing two different datasets.

**Skew is measured where it can act.** Cleaning de-duplicates on order and line, so one
customer holding 20% of the lines never meets a customer-keyed shuffle there; the feature job
aggregates per customer and does. The baseline and the skew arm therefore also run the feature
job, and the slowest task over the median task of its stages is compared between them.

**The executor kill is real, not simulated, and it is made twice.** In local mode the driver
*is* the executor: a watcher thread waits for the job's own sandbox container, lets it run into
the job, and kills it through the Docker engine, and what absorbs that is the platform
resubmitting the job - local mode retries no task. The PRD's claim is about Spark's own task
retries, so the same data is also run on the compose Spark cluster (`spark-master` and one
`spark-worker`, started for this experiment), where the driver stays in its sandbox container
and the executor JVM inside the worker is killed mid-job. That arm counts only if an executor
died under a running job, the platform made one attempt, and Spark's own log shows the tasks it
lost - and its output is compared with a clean run on the same cluster.
"""

from __future__ import annotations

import textwrap
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from experiments.harness import Experiment

CUSTOMERS = 400
MONTHS = 24
SEED = 42
DATA_START = "2024-01-01"
# The memory arm runs where memory matters. Two pilots on 10-10-2026, which ran only this arm
# and measured only whether its fault lands (experiments/EXP-6/pilot_20261010.md), found that
# at 512m and 460m on 400 customers neither job failed or spilled a byte, and that at 460m on
# 20 000 customers neither failed and the feature job spilled 191 MB. So the arm runs 20 000
# customers at 460m - the least heap Spark starts with (about 450 MiB; below it Spark refuses to
# start, which the playbook leaves to a person, DEC-20) - beside the same data at the
# configured memory, and compares the outputs.
MEMORY_CUSTOMERS = 20_000
OOM_DRIVER_MEMORY = "460m"
# How far into the running job the container is killed: past JVM and Spark start-up, inside
# the work. The baseline's own job duration is recorded beside it, to show the kill landed.
KILL_INTO_JOB_SECONDS = 10.0
# The compose cluster (`--profile spark`). Its worker runs the executors; the driver of a
# cluster job stays in the job's sandbox container, on the internal sandbox network.
CLUSTER_MASTER = "spark://spark-master:7077"
WORKER_CONTAINER = "triad-spark-worker-1"
MASTER_CONTAINER = "triad-spark-master-1"
# Spark's own account of an executor loss, in the driver's log (TaskSchedulerImpl and
# TaskSetManager at Spark's default INFO level).
SPARK_LOSS_MARKERS = ("Lost executor", "Lost task", "ExecutorLostFailure")
# A cluster job's executor starts well after its driver's container - the driver's JVM, the
# application's registration and the executor's own JVM come first - and runs no task until it
# has registered with the driver. So the cluster arm does not kill at a fixed time: it waits in
# the worker until the job's executor has started a task, by its own log, and kills it this
# long after. Two pilots that did not wait for that found, at 10 s after the container, no
# executor yet, and at 5 s after the executor's start, one not yet registered - a loss Spark
# recovered from with no task lost (experiments/EXP-6/pilot_cluster_20261011.md).
EXECUTOR_KILL_INTO_S = 2.0
EXECUTOR_WAIT_S = 180.0


def kill_executors_script(wait_s: float, into_s: float) -> str:
    """Python to run inside the worker: wait for a new executor to start a task, then kill it.

    New means not running when the script started, so the executor of a sample run on its way
    out is left alone. An executor has started a task when its log - `stderr` in its working
    directory, which the worker sets per executor - says "Running task". The script skips its
    own pid: its command line holds the marker too, and the first pilot's version killed itself
    before reaching the executor (/proc is not listed in pid order). It prints the pid it killed,
    or nothing.
    """
    return textwrap.dedent(f"""        import os, signal, time
        me = str(os.getpid())

        def executors():
            found = []
            for p in os.listdir('/proc'):
                if not p.isdigit() or p == me:
                    continue
                try:
                    cmd = open('/proc/' + p + '/cmdline', 'rb').read()
                except OSError:
                    continue
                if b'CoarseGrainedExecutorBackend' in cmd:
                    found.append(p)
            return set(found)

        def working(p):
            try:
                log = os.path.join(os.readlink('/proc/' + p + '/cwd'), 'stderr')
                return b'Running task' in open(log, 'rb').read()
            except OSError:
                return False

        before = executors()
        deadline = time.time() + {float(wait_s)}
        target = None
        while target is None and time.time() < deadline:
            target = next((p for p in sorted(executors() - before) if working(p)), None)
            if target is None:
                time.sleep(0.25)
        if target is not None:
            time.sleep({float(into_s)})
            try:
                os.kill(int(target), signal.SIGKILL)
            except ProcessLookupError:
                target = None
        print(target or '')
        """)


def _spark(app: str):
    from pyspark.sql import SparkSession

    from triad_core.config import settings

    session = (
        SparkSession.builder.appName(app)
        .master(settings().spark_master_url)
        .config("spark.sql.ansi.enabled", "true")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.parquet.outputTimestampType", "TIMESTAMP_MICROS")
        .config("spark.sql.shuffle.partitions", "8")
        .config("spark.ui.enabled", "false")
        .config("spark.driver.memory", "1g")
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    return session


def _config_with(overrides: dict[str, Any], scratch: Path) -> Path:
    """The real pipeline config with a few keys overridden, written to a temp file.

    The point is to change one thing - the driver memory the first attempt is given - and leave
    every other threshold, budget and policy exactly as production has them. A bespoke config
    would make the arm measure a different pipeline.
    """
    import yaml

    from triad_core.config import settings

    base = yaml.safe_load(settings().pipeline_config_path.read_text(encoding="utf-8"))
    for key, value in overrides.items():
        cursor = base
        parts = key.split(".")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = value
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_text(yaml.safe_dump(base, sort_keys=False), encoding="utf-8")
    return scratch


class _ContainerKiller:
    """Kills the first job container that appears, once, a set time into its run.

    Runs in a thread because the stage call is blocking. `killed` records whether it actually
    found and killed something: an arm that reports recovery from a kill that never happened
    would be worthless, so the measurement says which.
    """

    def __init__(self, into_job: float, prefix: str) -> None:
        self.into_job = into_job
        # The arm's own job only: `triad-sbx-<run_key>-<stage>-`. A bare `triad-sbx-` matched
        # any run's sandbox job, so a kill elsewhere could be recorded as this arm's.
        self.prefix = prefix
        self.killed: str | None = None
        self.seconds_into_job: float | None = None
        self.error = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        # Never started when profiling failed first; joining it then raised RuntimeError.
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    def _run(self) -> None:
        # Through the SDK: the image has no docker CLI. The socket is given to the experiment's
        # container for this experiment only; no platform service but sandbox-runner has it
        # (DD-04).
        try:
            import docker

            client = docker.from_env(timeout=30)
            client.ping()
        except Exception as exc:  # no engine reachable is a recorded limitation, not a crash
            self.error = f"{type(exc).__name__}: {exc}"
            return
        from docker.errors import APIError

        # Long enough for the gates before the job: compiling, the fixture tests when their
        # cache is cold, and gate 6's dry run.
        deadline = time.perf_counter() + 900
        while time.perf_counter() < deadline and not self._stop.is_set():
            try:
                # The job itself, not gate 6's dry run on a sample, which runs first and is
                # named `<stage>-dryrun`: the arm is about losing the executor mid-stage.
                running = [c for c in client.containers.list(filters={"name": self.prefix},
                                                             ignore_removed=True)
                           if c.name.startswith(self.prefix) and "dryrun" not in c.name]
                if running:
                    target, seen = running[0], time.perf_counter()
                    if self._stop.wait(self.into_job):
                        self.error = "the stage ended before the kill was due"
                        return
                    target.reload()
                    if target.status != "running":
                        self.error = f"the job's container was {target.status} before the kill"
                        return
                    self._kill(client, target)
                    if self.killed:
                        self.seconds_into_job = round(time.perf_counter() - seen, 1)
                    return
            except APIError as exc:  # the container went between listing and killing it
                self.error = f"{type(exc).__name__}: {exc}"[:300]
                return
            self._stop.wait(0.5)
        if not self.killed and not self.error:
            self.error = ("the stage ended before its job's container appeared"
                          if self._stop.is_set() else "no job container appeared in 900 s")

    def _kill(self, client: Any, target: Any) -> None:
        target.kill()
        self.killed = target.name


class _ExecutorKiller(_ContainerKiller):
    """Kills the arm's executor JVM in the cluster's worker, once, while it works.

    The job's container is the driver here, and it is left alone: the fault is the executor
    dying under it, which is the PRD's "kill an executor mid-stage". The kill starts when the
    job's container appears (`into_job` 0) and waits in the worker for the job's own executor
    (`kill_executors_script`), since nothing else in this environment submits to the cluster.
    """

    def __init__(self, into_job: float, prefix: str, worker: str) -> None:
        super().__init__(into_job, prefix)
        self.worker = worker

    def _kill(self, client: Any, target: Any) -> None:
        script = kill_executors_script(EXECUTOR_WAIT_S, EXECUTOR_KILL_INTO_S)
        result = client.containers.get(self.worker).exec_run(["python3", "-c", script])
        output = (result.output or b"").decode("utf-8", "replace").strip()
        if result.exit_code != 0:
            self.error = f"the kill inside {self.worker} failed: {output[:300]}"
        elif not output:
            self.error = (f"no new executor started a task in {self.worker} within "
                          f"{EXECUTOR_WAIT_S:.0f} s of the job's container")
        else:
            self.killed = f"executor pid(s) {output} in {self.worker}"


def _execution_metric(session: Any, run_id: Any, stage: str) -> dict[str, Any]:
    """The succeeded execution of a stage: its duration and Spark's own measurements."""
    from sqlalchemy import select

    from triad_core.models import Execution

    row = session.execute(
        select(Execution).where(Execution.run_id == run_id, Execution.stage == stage,
                                Execution.status == "succeeded")
        .order_by(Execution.started_at.desc())
    ).scalars().first()
    if row is None:
        return {}
    totals = ((row.metrics_json or {}).get("spark") or {}).get("totals") or {}
    return {"duration_s": round((row.duration_ms or 0) / 1000, 1),
            "max_task_skew": totals.get("max_task_skew"),
            "disk_spill_bytes": totals.get("disk_spill_bytes")}


def _spark_saw_loss(session: Any, run_id: Any, stage: str) -> dict[str, Any]:
    """Spark's own lost-executor and lost-task lines in every attempt of the stage, and whether
    a job that lost a task still succeeded - Spark's retries absorbing the loss.

    Every attempt, not only the one that succeeded: if the kill failed a job and the platform's
    resubmission then succeeded, the loss is in the failed attempt's log, and reading only the
    successful one would turn a refutation into "the fault did not land".
    """
    from sqlalchemy import select

    from triad_core.models import Execution
    from triad_core.storage import storage

    store = storage()
    rows = session.execute(
        select(Execution).where(Execution.run_id == run_id, Execution.stage == stage)
        .order_by(Execution.started_at)
    ).scalars().all()
    seen: dict[str, Any] = {"log_kept": False, "loss_lines": 0, "lost_task_lines": 0,
                            "absorbed": False, "examples": []}
    for row in rows:
        if not row.log_uri:
            continue
        try:
            counted = count_loss_lines(store.read_text(store.path(row.log_uri)))
        except OSError:
            continue
        seen["log_kept"] = True
        seen["loss_lines"] += counted["loss_lines"]
        seen["lost_task_lines"] += counted["lost_task_lines"]
        seen["examples"] = (seen["examples"] + counted["examples"])[:3]
        if counted["lost_task_lines"] and row.status == "succeeded":
            seen["absorbed"] = True
    return seen


def count_loss_lines(text: str) -> dict[str, Any]:
    """Spark's own lines about a lost executor or lost tasks, counted, with a few examples."""
    lines = [line for line in text.splitlines() if any(m in line for m in SPARK_LOSS_MARKERS)]
    return {"loss_lines": len(lines),
            "lost_task_lines": sum("Lost task" in line for line in lines),
            "examples": [line[-300:] for line in lines[:3]]}


def _cluster_containers(action: str) -> bool:
    """Start or stop the compose cluster's containers through the engine (the batch runner
    creates them); False when they do not exist or the engine is out of reach."""
    try:
        import docker

        client = docker.from_env(timeout=60)
        names = (MASTER_CONTAINER, WORKER_CONTAINER)
        for name in names if action == "start" else tuple(reversed(names)):
            getattr(client.containers.get(name), action)()
        return True
    except Exception:
        return False


def _start_cluster() -> bool:
    """Start the cluster and wait until the master answers and the worker has registered.

    The registration is read from the worker's log since this start only: a container keeps
    its log across a stop and a start, and an earlier start's line would read as ready.
    """
    begun = int(time.time()) - 1
    if not _cluster_containers("start"):
        return False
    import docker

    deadline = time.perf_counter() + 120
    worker = docker.from_env(timeout=60).containers.get(WORKER_CONTAINER)
    while time.perf_counter() < deadline:
        registered = b"Successfully registered with master" in worker.logs(since=begun)
        if _cluster_reachable() and registered:
            return True
        time.sleep(2)
    return False


def _stop_cluster() -> None:
    _cluster_containers("stop")


def _cluster_reachable() -> bool:
    import socket

    host, port = CLUSTER_MASTER.removeprefix("spark://").split(":")
    try:
        with socket.create_connection((host, int(port)), timeout=5):
            return True
    except OSError:
        return False


def _arm(
    exp: Experiment,
    name: str,
    source: Path,
    *,
    config_overrides: dict[str, Any] | None = None,
    kill_container: bool = False,
    kill_executor: bool = False,
    features: bool = False,
) -> dict[str, Any]:
    """Profile and clean - and, for the skew comparison, build features - and report how the
    repair loop behaved."""
    from agents.graph import queue as q
    from agents.graph.pipeline import ChurnPipeline
    from dataeng.ingest import DatasetRegistry
    from triad_core.db import session_scope
    from triad_core.models import PipelineRun
    from triad_core.storage import storage

    trial = exp.trial(name)
    store = storage()
    started = time.perf_counter()
    out: dict[str, Any] = {}

    with session_scope() as session:
        tag = f"exp06_{name}_{uuid.uuid4().hex[:6]}"
        run = q.create_run(
            session, pipeline_name=tag, trigger_type="manual",
            triggered_by="exp06", config={"experiment": "EXP-6", "arm": name},
        )
        session.flush()

        registry = DatasetRegistry(session, store)
        registered: dict[str, str] = {}
        for table in sorted(p.name for p in source.iterdir() if p.is_dir()):
            dataset = f"{table}__{tag}"
            contract = Path("pipelines") / "contracts" / f"{table}.yaml"
            registry.register_source(
                dataset, entity_type=table, source_type="parquet",
                contract_yaml=contract.read_text(encoding="utf-8") if contract.exists() else None,
            )
            files = sorted((source / table).glob("*.parquet"))
            if files:
                result = registry.register_version(dataset, files[0], actor="exp06", run=run)
                if result.version_id:
                    registered[table] = result.storage_uri or ""

        if "transactions" not in registered:
            trial.ok = False
            trial.detail = "the transactions table did not register; nothing to run"
            trial.record("recovered", False)
            q.set_state(session, run, "CANCELLED", actor="exp06")
            return out
        run_id = run.id

    # Committed before the stages run, as EXP-1 and configuration P do: the registration writes
    # land in the audit's system chain, whose lock a transaction spanning the arm held for all
    # of it, and an arm stopped part-way lost its run and datasets with it.
    with session_scope() as session:
        run = session.get(PipelineRun, run_id)

        config_path = None
        if config_overrides:
            config_path = _config_with(
                config_overrides, store.root / "experiments" / "exp06" / f"{tag}.yaml"
            )
            trial.observations["config_overrides"] = config_overrides

        pipeline = ChurnPipeline(session, run, store=store, config_path=config_path)
        output_dir = store.clean_dir("transactions", 1)
        prefix = f"triad-sbx-{run.run_key}-clean-"
        killer = (_ExecutorKiller(0.0, prefix, WORKER_CONTAINER)
                  if kill_executor
                  else _ContainerKiller(KILL_INTO_JOB_SECONDS, prefix) if kill_container
                  else None)
        spark = _spark(f"triad-exp06-{name}")
        feature_out: dict[str, Any] = {}
        try:
            profile = pipeline.stage_profile(spark, "transactions", registered["transactions"])
            if not profile:
                trial.ok = False
                trial.detail = "profiling failed, so the cleaning stage never ran"
                trial.record("recovered", False)
                q.set_state(session, run, "CANCELLED", actor="exp06")
                return out
            trial.record("input_rows", int(profile["row_count"]))

            if killer is not None:
                killer.start()
            stage_started = time.perf_counter()
            clean = pipeline.stage_scala_job(
                stage="clean",
                template_id="CleanJob",
                inputs={"source": registered["transactions"]},
                output_dir=output_dir,
                params={"dedup_keys": "order_id,line_id"},
                quarantine_dir=store.quarantine_dir("transactions", 1, run.run_key),
                policy_action="cleaning.execute",
                input_rows=profile.get("row_count", 0),
            )
            trial.record("stage_wall_clock_s", round(time.perf_counter() - stage_started, 1),
                         "s", note="the cleaning stage, gates and every attempt included")
            if clean and features:
                from agents.graph.churn import FEATURE_SOURCES, cutoff_window

                window = cutoff_window(spark, clean["uri"])
                inputs = {"transactions": clean["uri"]}
                inputs.update({s: registered[s] for s in FEATURE_SOURCES if registered.get(s)})
                feature_out = pipeline.stage_scala_job(
                    stage="features",
                    template_id="FeatureJob",
                    inputs=inputs,
                    output_dir=store.features_dir("churn_features", 1),
                    params={"max_window_days": 365, **window},
                    partition_by=["snapshot_date"],
                    required_columns=["customer_id", "snapshot_date", "recency_days"],
                    input_rows=clean.get("row_count", 0),
                )
        finally:
            if killer is not None:
                killer.stop()
            spark.stop()

        elapsed = time.perf_counter() - started
        trial.record("wall_clock_s", round(elapsed, 1), "s")
        recovered = bool(clean)
        trial.record("recovered", recovered)

        if killer is not None:
            trial.record("executor_killed" if kill_executor else "container_killed",
                         killer.killed is not None)
            trial.record("killed_container", killer.killed or "")
            trial.record("killed_seconds_into_job", killer.seconds_into_job, "s")
            if killer.error:
                trial.record("killer_error", killer.error)
        if kill_executor:
            seen = _spark_saw_loss(session, run.id, "clean")
            trial.record("spark_log_kept", seen.get("log_kept"))
            trial.record("spark_loss_lines", seen.get("loss_lines"),
                         note="driver log lines naming a lost executor or a lost task")
            trial.record("spark_lost_task_lines", seen.get("lost_task_lines"),
                         note="over every attempt of the stage")
            trial.record("spark_absorbed_in_the_same_job", seen.get("absorbed"),
                         note="the job that lost a task still succeeded")
            trial.observations["spark_loss_examples"] = seen.get("examples") or []

        # The stage's own record of how many attempts it took and what it changed between them.
        stage_record = next(
            (s for s in pipeline.result.stages if s.get("stage") == "clean"), {}
        )
        diagnoses = stage_record.get("diagnoses") or []
        trial.record("attempts", stage_record.get("attempts"))
        trial.record("retries", max(0, int(stage_record.get("attempts") or 1) - 1))
        trial.record("escalated", bool(stage_record.get("escalated")))
        trial.record("first_error_class", diagnoses[0].get("error_class") if diagnoses else None,
                     note="how the first failed attempt was classified; None if none failed")
        applied = stage_record.get("applied_fixes") or []
        trial.record("remedies_applied", [f["remedy"] for f in applied if f.get("applied")])
        trial.record("remedies_unapplied", [f["remedy"] for f in applied if not f.get("applied")])
        trial.observations["diagnoses"] = diagnoses
        trial.observations["applied_fixes"] = applied

        job = _execution_metric(session, run.id, "clean")
        trial.record("job_duration_s", job.get("duration_s"), "s",
                     note="the successful attempt's sandboxed job alone")

        if recovered:
            trial.record("clean_rows", clean.get("row_count"))
            metrics = clean.get("job_metrics") or {}
            trial.record("row_accounting_balanced", metrics.get("row_accounting_balanced"))
            try:
                manifest = store.read_manifest(output_dir)
                trial.record("content_hash", manifest.content_hash)
                trial.record("schema_hash", manifest.schema_hash)
                out["content_hash"] = manifest.content_hash
                out["row_count"] = clean.get("row_count")
            except Exception as exc:
                trial.record("content_hash", None,
                             note=f"manifest unreadable: {type(exc).__name__}: {exc}")
        else:
            trial.observations["escalation"] = pipeline.result.escalation
            trial.record("content_hash", None, note="the stage did not produce an output")

        if features:
            feature_record = next(
                (s for s in pipeline.result.stages if s.get("stage") == "features"), {}
            )
            trial.record("features_succeeded", bool(feature_out))
            trial.record("feature_attempts", feature_record.get("attempts"))
            measured = _execution_metric(session, run.id, "features")
            trial.record("feature_job_duration_s", measured.get("duration_s"), "s")
            trial.record("feature_max_task_skew", measured.get("max_task_skew"),
                         note="the worst stage's slowest task over its median task")
            trial.record("feature_disk_spill_bytes", measured.get("disk_spill_bytes"))
            trial.record("feature_rows", feature_out.get("row_count") if feature_out else None)
            feature_diagnoses = feature_record.get("diagnoses") or []
            trial.record("feature_first_error_class",
                         feature_diagnoses[0].get("error_class") if feature_diagnoses else None)
            trial.record("feature_retries", max(0, int(feature_record.get("attempts") or 1) - 1))
            trial.observations["feature_diagnoses"] = feature_diagnoses
            trial.observations["feature_applied_fixes"] = feature_record.get("applied_fixes")
            if feature_out:
                try:
                    manifest = store.read_manifest(store.features_dir("churn_features", 1))
                    trial.record("feature_content_hash", manifest.content_hash)
                    out["feature_content_hash"] = manifest.content_hash
                except Exception as exc:
                    trial.record("feature_content_hash", None,
                                 note=f"manifest unreadable: {type(exc).__name__}: {exc}")

        q.set_state(session, run, "CANCELLED", actor="exp06")

    trial.ok = recovered and (not features or bool(feature_out))
    trial.detail = (
        f"recovered={recovered} after {trial.get('attempts')} attempt(s); first failure "
        f"{trial.get('first_error_class')}; remedies {trial.get('remedies_applied')}"
    )
    return out


def _value(trial: Any, name: str) -> Any:
    """A measurement's value, or None when it was never recorded."""
    return next((m.value for m in trial.measurements if m.name == name), None)


def run() -> Experiment:
    from datasets.synthetic.generator import GeneratorConfig, SyntheticGenerator
    from triad_core.storage import storage

    store = storage()
    root = store.root / "experiments" / "exp06" / uuid.uuid4().hex[:8]

    with Experiment(
        "EXP-6",
        title="Spark failures and recovery",
        hypothesis=(
            "An executor loss is absorbed by retrying; the playbook fixes out-of-memory "
            "within 2 retries; adaptive execution handles skew; recovered outputs are "
            "identical to the clean run."
        ),
        independent_variable="fault type",
        dependent_measures=[
            "recovered", "first_error_class", "attempts", "retries", "wall_clock_s",
            "content_hash", "remedies_applied", "identical_to_clean_run",
            "feature_max_task_skew",
        ],
        config={
            "customers": CUSTOMERS, "months": MONTHS, "seed": SEED,
            "oom_driver_memory": OOM_DRIVER_MEMORY,
            "memory_arm_customers": MEMORY_CUSTOMERS,
            "kill_into_job_seconds": KILL_INTO_JOB_SECONDS,
            "executor_kill_into_s": EXECUTOR_KILL_INTO_S,
            "arms": {
                "clean_baseline": "no fault; the reference output checksum, and the reference "
                                  "feature-job skew",
                "memory_reference": f"{MEMORY_CUSTOMERS} customers at the configured memory; "
                                    "the memory arm's reference",
                "low_executor_memory": f"the same {MEMORY_CUSTOMERS} customers given "
                                       f"{OOM_DRIVER_MEMORY}, the least heap Spark starts with",
                "sandbox_container_killed": "the job's container is killed "
                                            f"{KILL_INTO_JOB_SECONDS:.0f} s into its run",
                "skewed_one_customer_20pct": "one account holds 20% of all order lines; "
                                             "cleaning and the feature job",
                "cluster_baseline": f"the shared data on the compose cluster ({CLUSTER_MASTER}), "
                                    "no fault",
                "executor_killed_on_cluster": "the same, with the job's executor JVM in the "
                                              f"worker killed {EXECUTOR_KILL_INTO_S:.0f} s after "
                                              "it began its first task",
            },
            "shared_source_note": (
                "the clean baseline and the killed arm read byte-identical source data, and so do "
                "the memory reference and the memory arm, which is what makes each checksum "
                "comparison meaningful; the skew arm necessarily differs"
            ),
        },
    ) as exp:
        # One dataset for the three comparable arms.
        shared = root / "shared"
        SyntheticGenerator(
            GeneratorConfig(customers=CUSTOMERS, months=MONTHS, seed=SEED,
                            start_date=DATA_START)
        ).write(shared)

        # --- arm 1: the reference -------------------------------------------------
        baseline = _arm(exp, "clean_baseline", shared, features=True)
        reference_hash = baseline.get("content_hash")

        # --- arm 2: memory too low, at a size where memory matters ---------------
        memory_data = root / "memory"
        SyntheticGenerator(
            GeneratorConfig(customers=MEMORY_CUSTOMERS, months=MONTHS, seed=SEED,
                            start_date=DATA_START)
        ).write(memory_data)
        memory_reference = _arm(exp, "memory_reference", memory_data, features=True)
        _arm(exp, "low_executor_memory", memory_data, features=True,
             config_overrides={"spark.driver_memory": OOM_DRIVER_MEMORY})

        # --- arm 3: the executor is killed ---------------------------------------
        _arm(exp, "sandbox_container_killed", shared, kill_container=True)

        # --- arm 4: skew ---------------------------------------------------------
        skewed = root / "skewed"
        SyntheticGenerator(
            GeneratorConfig(customers=CUSTOMERS, months=MONTHS, seed=SEED,
                            start_date=DATA_START, skew_customer_share=0.2)
        ).write(skewed)
        _arm(exp, "skewed_one_customer_20pct", skewed, features=True)

        # --- arm 5: an executor of a cluster job is killed --------------------------
        # The cluster runs only for these two arms: started here and stopped as soon as they
        # end, so the local arms above share the VM with nothing they did not start themselves.
        cluster = {"spark_master": CLUSTER_MASTER}
        cluster_up = _start_cluster()
        cluster_reference: dict[str, Any] = {}
        try:
            if cluster_up:
                cluster_reference = _arm(exp, "cluster_baseline", shared,
                                         config_overrides=cluster)
                _arm(exp, "executor_killed_on_cluster", shared, config_overrides=cluster,
                     kill_executor=True)
        finally:
            _stop_cluster()

        # --- read it -------------------------------------------------------------
        by_name = {t.condition: t for t in exp.trials}
        base, oom = by_name["clean_baseline"], by_name["low_executor_memory"]
        mref = by_name["memory_reference"]
        kill, skew = by_name["sandbox_container_killed"], by_name["skewed_one_customer_20pct"]
        kill.record(
            "identical_to_clean_run",
            bool(reference_hash) and _value(kill, "content_hash") == reference_hash,
            note="the committed output's content hash against the clean run's",
        )
        for name, key in (("identical_to_clean_run", "content_hash"),
                          ("features_identical_to_clean_run", "feature_content_hash")):
            oom.record(
                name,
                bool(memory_reference.get(key)) and _value(oom, key) == memory_reference.get(key),
                note="against the same data at the configured memory",
            )
        skew.record("identical_to_clean_run", None,
                    note="not compared: the skewed arm reads different source data by design")
        executor_kill = by_name.get("executor_killed_on_cluster")
        cbase = by_name.get("cluster_baseline")
        if executor_kill is not None and cbase is not None:
            cluster_hash = cluster_reference.get("content_hash")
            cbase.record("identical_to_clean_run",
                         (cluster_hash == reference_hash) if cluster_hash and reference_hash
                         else None,
                         note="the cluster's clean output against local mode's, same data")
            # Against the clean cluster run, or local mode's if that one produced nothing; with
            # neither, nothing was compared, which is not a difference.
            compared_with = cluster_hash or reference_hash
            killed_hash = _value(executor_kill, "content_hash")
            executor_kill.record(
                "identical_to_clean_run",
                (killed_hash == compared_with) if compared_with and killed_hash else None,
                note="against the clean run on the cluster" if cluster_hash
                else "against the clean run in local mode")

        # Each arm counts only if its fault landed (module docstring); a claim whose fault did
        # not land is untested (None), never refuted.
        oom_classes = (_value(oom, "first_error_class"), _value(oom, "feature_first_error_class"))
        oom_landed = "EXECUTOR_OOM" in oom_classes
        kill_landed = (bool(_value(kill, "container_killed"))
                       and _value(kill, "first_error_class") == "EXECUTOR_LOST")
        oom_ok = (
            oom.ok and max(_value(oom, "retries") or 0, _value(oom, "feature_retries") or 0) <= 2
            and _value(oom, "identical_to_clean_run") is True
            and _value(oom, "features_identical_to_clean_run") is True
        ) if oom_landed else None
        kill_ok = (kill.ok and _value(kill, "identical_to_clean_run") is True
                   if kill_landed else None)
        # The fault landed if an executor died under the running job and a driver's log names a
        # task it lost; Spark absorbed it if that same job still succeeded, with no platform
        # resubmission for it, and the output matched. An unmade comparison leaves it untested.
        spark_landed = (executor_kill is not None
                        and bool(_value(executor_kill, "executor_killed"))
                        and bool(_value(executor_kill, "spark_lost_task_lines")))
        spark_ok = None
        if spark_landed:
            identical = _value(executor_kill, "identical_to_clean_run")
            absorbed = bool(_value(executor_kill, "spark_absorbed_in_the_same_job"))
            spark_ok = (False if not absorbed or identical is False
                        else None if identical is None else True)
        # At this size no partition approaches AQE's skew thresholds (256 MB), so completing
        # says nothing about skew handling; the measured task skew is reported instead.
        skew_ok = None
        parts = {"baseline_ran": bool(reference_hash) and base.ok and mref.ok,
                 "oom_fixed": oom_ok,
                 "spark_retries_absorbed_executor_loss": spark_ok,
                 "platform_resubmission_absorbed_container_loss": kill_ok,
                 "skew_handled": skew_ok}
        verdict = (False if any(v is False for v in parts.values())
                   else None if any(v is None for v in parts.values()) else True)

        if cluster_up:
            exp.limitation(
                "The cluster is the compose one: a master and one worker on the same 5.27 GB "
                "VM, so an executor loss is recovered by the same worker launching a new "
                "executor, not by a different machine taking the tasks (ADR-0002)."
            )
        else:
            exp.limitation(
                f"The compose Spark cluster ({CLUSTER_MASTER}) was not reachable, so Spark's own "
                "task retries were not tested; in local mode the driver is the executor and no "
                "task is retried by Spark itself."
            )
        if executor_kill is not None and not spark_landed:
            exp.limitation(
                "The executor kill on the cluster did not land as a loss Spark recorded "
                f"(executor killed: {_value(executor_kill, 'executor_killed')}, lost-task lines "
                f"in the driver's log: {_value(executor_kill, 'spark_lost_task_lines')}). "
                + (_value(executor_kill, "killer_error") or "")
            )
        exp.limitation(
            f"At {CUSTOMERS} customers one account's 20% of the lines is a small partition, so "
            "the skew arm shows whether the feature job completes and how uneven its tasks "
            "were, not how skew handling behaves at the sizes where it decides the run time."
        )
        if not oom_landed:
            exp.limitation(
                f"The memory fault did not land as an out-of-memory: at {OOM_DRIVER_MEMORY} on "
                f"{MEMORY_CUSTOMERS} customers the first failures were {oom_classes} (None means "
                f"no failure). The feature job spilled {_value(oom, 'feature_disk_spill_bytes')} "
                f"bytes to disk against {_value(mref, 'feature_disk_spill_bytes')} at the "
                "configured memory: Spark degraded rather than failed, so the playbook's "
                "out-of-memory remedy was not exercised and that claim is untested here. The "
                "pilots behind the setting are in experiments/EXP-6/pilot_20261010.md."
            )
        if not kill_landed:
            exp.limitation(
                "The container kill did not land as a lost executor (killed: "
                f"{_value(kill, 'container_killed')}, first failure "
                f"{_value(kill, 'first_error_class')}), so that arm measures no recovery. "
                + (_value(kill, "killer_error") or "")
            )
        exp.limitation(
            "The memory remedy is held against the spark.config_change allowlist like any Spark "
            "setting (DEC-20, DEC-41): in local mode the heap is spark.driver.memory, which the "
            "allowlist leaves to a person, so the permitted part of the out-of-memory remedy "
            "here is the shuffle-partition raise."
        )

        exp.conclude(
            status="partial",
            hypothesis_held=verdict,
            conclusion=(
                f"Reference clean run: {baseline.get('row_count')} rows, content hash "
                f"{str(reference_hash)[:16]}, job {_value(base, 'job_duration_s')} s. "
                + " ".join(
                    f"{t.condition}: recovered={_value(t, 'recovered')} in "
                    f"{_value(t, 'attempts')} attempt(s), first failure "
                    f"{_value(t, 'first_error_class')}, remedies "
                    f"{_value(t, 'remedies_applied')}, output identical to the clean run: "
                    f"{_value(t, 'identical_to_clean_run')}."
                    for t in (oom, kill, skew)
                )
                + f" Memory at {OOM_DRIVER_MEMORY} on {MEMORY_CUSTOMERS} customers: out-of-memory "
                f"landed {oom_landed} (first failures {oom_classes}); feature-job spill "
                f"{_value(oom, 'feature_disk_spill_bytes')} bytes against "
                f"{_value(mref, 'feature_disk_spill_bytes')} at the configured memory; feature "
                f"job {_value(oom, 'feature_job_duration_s')} s against "
                f"{_value(mref, 'feature_job_duration_s')} s; outputs identical to the reference: "
                f"clean {_value(oom, 'identical_to_clean_run')}, features "
                f"{_value(oom, 'features_identical_to_clean_run')}; fixed within 2 retries: "
                f"{oom_ok}. The kill landed "
                f"{_value(kill, 'killed_seconds_into_job')} s into the job as a lost executor: "
                f"{kill_landed}; recovered identically: {kill_ok}. "
                + (f"On the cluster the executor was killed "
                   f"{_value(executor_kill, 'killed_seconds_into_job')} s into the job "
                   f"({_value(executor_kill, 'killed_container')}); Spark's log named "
                   f"{_value(executor_kill, 'spark_lost_task_lines')} lost task(s); the job "
                   f"finished in {_value(executor_kill, 'attempts')} platform attempt(s), output "
                   f"identical to the clean cluster run: "
                   f"{_value(executor_kill, 'identical_to_clean_run')} (the clean cluster run "
                   f"against local mode: {_value(cbase, 'identical_to_clean_run')}). "
                   if executor_kill is not None else "")
                + "Feature job "
                f"task skew (slowest over median task): baseline "
                f"{_value(base, 'feature_max_task_skew')}, skewed "
                f"{_value(skew, 'feature_max_task_skew')}; the skewed feature job completed: "
                f"{_value(skew, 'features_succeeded')} in {_value(skew, 'feature_attempts')} "
                "attempt(s); skew handling not assessable at this size. Clauses: "
                + ", ".join(f"{k}={v}" for k, v in parts.items()) + "."
            ),
        )
        return exp


if __name__ == "__main__":
    run()
