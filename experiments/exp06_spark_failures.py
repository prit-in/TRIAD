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
full here, because every remedy it needs is deterministic. Raising the driver memory, raising
the shuffle partitions and enabling adaptive skew handling are all infrastructure changes that
`StageRunner._apply_fix` makes to the stage inputs and `stage_scala_job` reads on the next
attempt. None of them needs a model to rewrite code.

That was not true when this experiment was written. `RAISE_EXECUTOR_MEMORY` bumped an input
that nothing read, so an out-of-memory retry was submitted with exactly the memory that had
just failed and the remedy could only ever exhaust the attempt budget. The plumbing is fixed;
this experiment is what proves it, and would catch it breaking again.

**The checksum comparison needs identical inputs, so the data is generated once and shared.**
Three of the four arms - the clean baseline, the low-memory fault and the killed container -
read byte-identical source data, which is what makes "recovered output identical to the clean
run" a meaningful claim. The skew arm necessarily has different input data, so its checksum is
reported but not compared; comparing it would be comparing two different datasets.

**The executor kill is real, not simulated.** A watcher thread waits for the sandbox container
to appear and then `docker kill`s it mid-job. In local mode the driver *is* the executor, so
killing the container is losing the executor - the closest this environment gets to the PRD's
fault, and it is stated as that rather than as a distributed executor loss.
"""

from __future__ import annotations

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
KILL_AFTER_SECONDS = 25.0


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
    """Kills the first sandbox container that appears, once, after a delay.

    Runs in a thread because the stage call is blocking. `killed` records whether it actually
    found and killed something: an arm that reports recovery from a kill that never happened
    would be worthless, so the measurement says which.
    """

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.killed: str | None = None
        self.error = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        if self._stop.wait(self.delay):
            return
        # Through the SDK: the image has no docker CLI, so the `docker ps` this used to run
        # could never find anything, and the arm could never land. The socket is given to the
        # experiment's container for this experiment only; no platform service but
        # sandbox-runner has it (DD-04).
        try:
            import docker

            client = docker.from_env(timeout=30)
            client.ping()
        except Exception as exc:  # no engine reachable is a recorded limitation, not a crash
            self.error = f"{type(exc).__name__}: {exc}"
            return
        deadline = time.perf_counter() + 240
        while time.perf_counter() < deadline and not self._stop.is_set():
            # The job itself, not gate 6's dry run on a sample, which runs first and is named
            # `<stage>-dryrun`: the arm is about losing the executor mid-stage.
            running = [c for c in client.containers.list(filters={"name": "triad-sbx-"})
                       if "dryrun" not in c.name]
            if running:
                running[0].kill()
                self.killed = running[0].name
                return
            self._stop.wait(1.0)


def _clean_stage(
    exp: Experiment,
    name: str,
    source: Path,
    *,
    config_overrides: dict[str, Any] | None = None,
    kill_container: bool = False,
) -> dict[str, Any]:
    """Profile, then run the cleaning stage, and report how the repair loop behaved."""
    from agents.graph import queue as q
    from agents.graph.pipeline import ChurnPipeline
    from dataeng.ingest import DatasetRegistry
    from triad_core.db import session_scope
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
                result = registry.register_version(dataset, files[0], actor="exp06")
                if result.version_id:
                    registered[table] = result.storage_uri or ""

        if "transactions" not in registered:
            trial.ok = False
            trial.detail = "the transactions table did not register; nothing to run"
            trial.record("recovered", False)
            q.set_state(session, run, "CANCELLED", actor="exp06")
            return out

        config_path = None
        if config_overrides:
            config_path = _config_with(
                config_overrides, store.root / "experiments" / "exp06" / f"{tag}.yaml"
            )
            trial.observations["config_overrides"] = config_overrides

        pipeline = ChurnPipeline(session, run, store=store, config_path=config_path)
        output_dir = store.clean_dir("transactions", 1)
        killer = _ContainerKiller(KILL_AFTER_SECONDS) if kill_container else None
        spark = _spark(f"triad-exp06-{name}")
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
        finally:
            if killer is not None:
                killer.stop()
            spark.stop()

        elapsed = time.perf_counter() - started
        trial.record("wall_clock_s", round(elapsed, 1), "s")
        recovered = bool(clean)
        trial.record("recovered", recovered)

        if killer is not None:
            trial.record("container_killed", killer.killed is not None)
            trial.record("killed_container", killer.killed or "")
            if killer.error:
                trial.record("killer_error", killer.error)

        # The stage's own record of how many attempts it took and what it changed between them.
        stage_record = next(
            (s for s in pipeline.result.stages if s.get("stage") == "clean"), {}
        )
        trial.record("attempts", stage_record.get("attempts"))
        trial.record("retries", max(0, int(stage_record.get("attempts") or 1) - 1))
        trial.record("escalated", bool(stage_record.get("escalated")))
        applied = stage_record.get("applied_fixes") or []
        trial.record("remedies_applied", [f["remedy"] for f in applied if f.get("applied")])
        trial.record("remedies_unapplied", [f["remedy"] for f in applied if not f.get("applied")])
        trial.observations["diagnoses"] = stage_record.get("diagnoses")
        trial.observations["applied_fixes"] = applied

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

        q.set_state(session, run, "CANCELLED", actor="exp06")

    trial.ok = recovered
    trial.detail = (
        f"recovered={recovered} after {trial.get('attempts')} attempt(s); remedies "
        f"{trial.get('remedies_applied')}"
    )
    return out


def _identical(trial: Any) -> Any:
    """The `identical_to_clean_run` measurement, or None when it was never recorded."""
    return next(
        (m.value for m in trial.measurements if m.name == "identical_to_clean_run"), None
    )


def run() -> Experiment:
    from datasets.synthetic.generator import GeneratorConfig, SyntheticGenerator
    from triad_core.storage import storage

    store = storage()
    root = store.root / "experiments" / "exp06" / uuid.uuid4().hex[:8]

    with Experiment(
        "EXP-6",
        title="Spark failures and recovery",
        hypothesis=(
            "Spark's task retries absorb an executor loss; the playbook fixes out-of-memory "
            "within 2 retries; adaptive execution handles skew."
        ),
        independent_variable="fault type",
        dependent_measures=[
            "recovered", "attempts", "retries", "wall_clock_s", "content_hash",
            "remedies_applied", "identical_to_clean_run",
        ],
        config={
            "customers": CUSTOMERS, "months": MONTHS, "seed": SEED,
            "kill_after_seconds": KILL_AFTER_SECONDS,
            "arms": {
                "clean_baseline": "no fault; the reference output checksum",
                "low_driver_memory": "first attempt given 256m, so it must be raised to finish",
                "sandbox_container_killed": "the sandbox container is killed mid-job",
                "skewed_one_customer_20pct": "one account holds 20% of all order lines",
            },
            "shared_source_note": (
                "the first three arms read byte-identical source data, which is what makes the "
                "checksum comparison meaningful; the skew arm necessarily differs"
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
        baseline = _clean_stage(exp, "clean_baseline", shared)
        reference_hash = baseline.get("content_hash")

        # --- arm 2: out of memory ------------------------------------------------
        # 256m of JVM heap is not enough for the job; the playbook has to raise it.
        _clean_stage(
            exp, "low_driver_memory", shared,
            config_overrides={"spark.driver_memory": "256m"},
        )

        # --- arm 3: the executor is killed ---------------------------------------
        _clean_stage(exp, "sandbox_container_killed", shared, kill_container=True)

        # --- arm 4: skew ---------------------------------------------------------
        skewed = root / "skewed"
        SyntheticGenerator(
            GeneratorConfig(customers=CUSTOMERS, months=MONTHS, seed=SEED,
                            start_date=DATA_START, skew_customer_share=0.2)
        ).write(skewed)
        _clean_stage(exp, "skewed_one_customer_20pct", skewed)

        # --- read it -------------------------------------------------------------
        by_name = {t.condition: t for t in exp.trials}
        comparable = ["clean_baseline", "low_driver_memory", "sandbox_container_killed"]
        for name in comparable[1:]:
            trial = by_name[name]
            got = next((m.value for m in trial.measurements if m.name == "content_hash"), None)
            trial.record(
                "identical_to_clean_run",
                bool(reference_hash) and got == reference_hash,
                note="the committed output's content hash against the clean run's",
            )
        by_name["skewed_one_customer_20pct"].record(
            "identical_to_clean_run", None,
            note="not compared: the skewed arm reads different source data by construction",
        )

        faults = [by_name[n] for n in comparable[1:]] + [
            by_name["skewed_one_customer_20pct"]
        ]
        recovered = [t for t in faults if t.get("recovered")]
        identical = [
            t for t in faults
            if any(m.name == "identical_to_clean_run" and m.value is True
                   for m in t.measurements)
        ]
        killed_for_real = by_name["sandbox_container_killed"].get("container_killed")

        exp.limitation(
            "Spark runs in local mode inside the sandbox, so the driver is the executor. "
            "Killing the sandbox container is therefore losing the executor, which is the "
            "closest this environment reaches to the PRD's distributed executor loss - not a "
            "measurement of a multi-executor cluster shedding one worker (ADR-0002)."
        )
        if not killed_for_real:
            exp.limitation(
                "The container kill did not land: no sandbox container was seen while the job "
                "ran, so that arm measures an ordinary run and not a recovery. "
                + (by_name["sandbox_container_killed"].get("killer_error") or "")
            )

        exp.conclude(
            status="partial",
            hypothesis_held=(
                bool(reference_hash)
                and len(recovered) == len(faults)
                and len(identical) == len(comparable) - 1
            ),
            conclusion=(
                f"Reference clean run: {baseline.get('row_count')} rows, content hash "
                f"{str(reference_hash)[:16]}. "
                + " ".join(
                    f"{t.condition}: recovered={t.get('recovered')} in {t.get('attempts')} "
                    f"attempt(s) ({t.get('retries')} retries, {t.get('wall_clock_s')}s), "
                    f"remedies {t.get('remedies_applied')}, output identical to the clean "
                    f"run: {_identical(t)}."
                    for t in faults
                )
                + f" {len(recovered)} of {len(faults)} injected faults recovered. The executor "
                f"kill landed on a real container: {killed_for_real}. "
                "The out-of-memory arm is the one that matters most here: it is the test that "
                "RAISE_EXECUTOR_MEMORY reaches the submitted job at all, which it did not until "
                "this experiment was written - the retry used to be given exactly the memory "
                "that had just failed."
            ),
        )
        return exp


if __name__ == "__main__":
    run()
