"""DEC-55: a run's generated Scala is built, submitted and diagnosed as its own.

EXP-2 at 200 000 customers on 14-10-2026, with a scheduled retrain running beside it: the
retrain's build removed P's generated source and repackaged the shared JAR, so P's retry could
not load its class; that retry was then diagnosed from the earlier attempt's report; and the
earlier attempt, killed at its deadline, had been diagnosed from its own shutdown message.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from agents.graph import pipeline
from experiments.baselines import run_isolated
from triad_core.errors import ErrorClass, classify

# experiments/EXP-2/20261014T171107Z: the report the feature job wrote as it was shut down.
SHUTDOWN_REPORT = {
    "status": "failed",
    "error": {
        "error_class": "UNKNOWN",
        "exception_type": "org.apache.spark.SparkException",
        "message": "Job 18 cancelled because SparkContext was shut down",
        "stack_head": "org.apache.spark.scheduler.DAGScheduler.$anonfun$cleanUpAfterSchedulerStop$1",
    },
}


def test_a_job_killed_at_its_deadline_is_a_timeout_whatever_it_reported() -> None:
    timed_out = classify(pipeline._job_failure("hard timeout reached", SHUTDOWN_REPORT))
    assert timed_out.error_class is ErrorClass.TIMEOUT
    # Without the runner's account the same report says nothing a remedy can act on.
    assert classify(pipeline._job_failure(None, SHUTDOWN_REPORT)).error_class is ErrorClass.UNKNOWN


def test_a_snapshot_is_this_builds_jar_whatever_is_built_after(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "scala_spark"
    for project, name in (("jobs", "triad-jobs_2.13-0.1.0.jar"), ("core", "triad-core_2.13-0.1.0.jar")):
        folder = root / project / "target" / "scala-2.13"
        folder.mkdir(parents=True)
        (folder / name).write_bytes(f"{project}: this attempt".encode())
    monkeypatch.setattr(pipeline, "SCALA_ROOT", root)

    jars = pipeline._snapshot_jars(tmp_path / "artifacts" / "jars" / "run-1" / "FeatureJob_1")
    # Another run builds afterwards and overwrites the workspace's JAR.
    (root / "jobs" / "target" / "scala-2.13" / "triad-jobs_2.13-0.1.0.jar").write_bytes(b"another run")

    assert Path(jars["jar"]).read_bytes() == b"jobs: this attempt"
    assert Path(jars["core_jar"]).read_bytes() == b"core: this attempt"
    assert not Path(jars["jar"]).is_relative_to(root), "the copy must leave the shared workspace"

    (root / "core" / "target" / "scala-2.13" / "triad-core_2.13-0.1.0.jar").unlink()
    assert pipeline._snapshot_jars(tmp_path / "other") is None, "no copy without both JARs"


def test_every_submission_uses_the_attempts_own_jars_and_report() -> None:
    """The dry run and the full run each clear their report before submitting, and submit the
    JARs copied out under the build lock - never whatever the workspace holds by then."""
    source = Path("agents/graph/pipeline.py").read_text(encoding="utf-8")
    source = source.split("    def stage_scala_job(", 1)[1].split("\n    def ", 1)[0]
    for closure, report in (("def dry_run(", "dry_report"), ("def execute(", "report_path")):
        body = source.split(closure, 1)[1].split("\n        def ", 1)[0]
        assert f"{report}.unlink(missing_ok=True)" in body
        assert body.index(f"{report}.unlink(") < body.index('"spark.submit_job"')
        assert "_newest_jar(" not in body
        assert 'jars["jar"]' in body and 'jars["core_jar"]' in body


@pytest.mark.postgres
def test_two_builds_never_overlap(postgres_available) -> None:
    if not postgres_available:
        pytest.skip("postgres service is not reachable")
    from triad_core.db import session_factory

    spans: list[tuple[float, float]] = []

    def build() -> None:
        session = session_factory()()
        try:
            with pipeline._scala_build_lock(session):
                entered = time.perf_counter()
                time.sleep(0.5)
                spans.append((entered, time.perf_counter()))
        finally:
            session.close()

    threads = [threading.Thread(target=build) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert len(spans) == 2
    first, second = sorted(spans)
    assert second[0] >= first[1], "a second build entered while the first was still inside"


def _killed_jvm_probe() -> dict:
    """In a fresh process: a session whose JVM is killed under it, as the OOM killer would."""
    from pyspark.sql import SparkSession

    from experiments.configuration_p import _end_session

    spark = (SparkSession.builder.master("local[1]").appName("dead-jvm-probe")
             .config("spark.ui.enabled", "false").getOrCreate())
    proc = spark.sparkContext._gateway.proc
    proc.kill()
    proc.wait(timeout=60)
    return {"exit_code": _end_session(spark)}


def test_a_dead_jvm_does_not_hide_ps_result() -> None:
    """Stopping a session whose JVM was killed raised ConnectionRefusedError over P's own result
    (EXP-2, 14-10-2026); ending it now reports how the JVM ended instead (DEC-55)."""
    probe = run_isolated(f"{__name__}:_killed_jvm_probe", timeout_s=240)
    assert probe["exit_code"] is not None and probe["exit_code"] != 0
