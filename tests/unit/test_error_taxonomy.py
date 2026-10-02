"""The error taxonomy against failures this project actually recorded.

EXP-6 injected Spark failures and read back what the classifier made of them. Two were wrong:

- A dependency download failing inside sbt was classified `SCALA_COMPILE_ERROR`, whose remedy
  is to regenerate the code. The code was never the problem. Two defects combined: the compile
  pattern `\\[error\\][^\\n]*\\.scala` matched any `[error]`-prefixed stack frame whose source file
  ends in `.scala` - which every frame of an sbt trace does - and gate 4 set
  `error_class=SCALA_COMPILE_ERROR` on every non-zero sbt exit, which the stage runner passes to
  `classify` as a hint that skips pattern matching entirely.
- Spark refusing to start with `[INVALID_DRIVER_MEMORY]` was classified `UNKNOWN`: no remedy,
  no diagnosis, one blind retry with identical settings.

The messages below are excerpts copied verbatim from `experiments/EXP-6/latest.json`, so these
tests hold the classifier to the output Spark and sbt really produce, not to a paraphrase.
"""

from __future__ import annotations

import pytest

from agents.codegen.gates import GateResult
from triad_core.errors import PLAYBOOK, ErrorClass, Remedy, classify

# experiments/EXP-6/latest.json, condition clean_baseline, first attempt (abridged to the frames
# that identify it; the classifier sees the full tail in production).
COURSIER_DOWNLOAD_FAILURE = (
    "[error] \tat lmcoursier.internal.shaded.coursier.cache.CacheUrl$."
    "urlConnectionMaybePartial(CacheUrl.scala:349)\n"
    "[error] \tat lmcoursier.internal.shaded.coursier.cache.ConnectionBuilder."
    "connectionMaybePartial(ConnectionBuilder.scala:50)\n"
    "[error] \tat lmcoursier.internal.shaded.coursier.cache.internal.Downloader$Blocking$."
    "doDownload(Downloader.scala:232)\n"
    "[error] \tat scala.concurrent.Future.$anonfun$map$1(Future.scala:292)\n"
)

# experiments/EXP-6/latest.json, condition low_driver_memory.
INVALID_DRIVER_MEMORY = (
    "26/09/19 06:54:01 INFO SparkContext: Successfully stopped SparkContext (Uptime: 9408 ms)\n"
    'Exception in thread "main" org.apache.spark.SparkIllegalArgumentException: '
    "[INVALID_DRIVER_MEMORY] System memory 268435456 must be at least 471859200.\n"
    'Please increase heap size using the --driver-memory option or "spark.driver.memory" in '
    "Spark configuration. SQLSTATE: F0000\n"
    "\tat org.apache.spark.memory.UnifiedMemoryManager$.getMaxMemory"
    "(UnifiedMemoryManager.scala:473)\n"
)

# What scalac actually prints for a genuine type error, through sbt.
REAL_SCALAC_ERROR = (
    "[info] compiling 1 Scala source to /workspace/scala_spark/jobs/target/scala-2.13/classes\n"
    "[error] /workspace/scala_spark/jobs/src/main/scala/triad/jobs/generated/"
    "CleanJob_c610a2c0.scala:42:17: type mismatch;\n"
    "[error]  found   : String\n"
    "[error]  required: Int\n"
    "[error] one error found\n"
)


def test_a_dependency_download_failure_is_not_a_compile_error() -> None:
    diagnosis = classify(COURSIER_DOWNLOAD_FAILURE)
    assert diagnosis.error_class is ErrorClass.DEPENDENCY_RESOLUTION
    assert diagnosis.remedy is Remedy.RESUBMIT
    assert diagnosis.remedy is not Remedy.REGENERATE_CODE


def test_spark_refusing_to_start_on_low_memory_is_diagnosed_and_escalated() -> None:
    diagnosis = classify(INVALID_DRIVER_MEMORY)
    assert diagnosis.error_class is ErrorClass.RESOURCE_CONFIG_INVALID
    assert not diagnosis.unknown
    # The agent may not raise `spark.driver.memory`: it is not on the spark.config_change
    # allowlist. So there is no automatic remedy, and no retry of identical settings.
    assert diagnosis.remedy is Remedy.NONE
    assert diagnosis.retry_limit == 0
    assert diagnosis.non_recoverable


def test_driver_memory_is_outside_the_agents_allowlist() -> None:
    """The reason the remedy above is a person, checked against the policy itself."""
    from triad_core.config import settings
    from triad_core.policy import PolicyEngine

    engine = PolicyEngine(settings().governance_policy_path)

    refused = engine.check(
        "spark.config_change", context={"settings": {"spark.driver.memory": "1g"}}
    )
    assert not refused.allowed, (
        "if driver memory is ever added to the allowlist, RESOURCE_CONFIG_INVALID should get "
        "an automatic remedy instead of escalating"
    )
    assert "spark.driver.memory is not on the allowlist" in refused.reason

    # And the settings the agent *may* change still pass, so the refusal is specific.
    permitted = engine.check(
        "spark.config_change", context={"settings": {"spark.executor.memory": "2g"}}
    )
    assert permitted.allowed, permitted.reason


def test_a_real_compiler_diagnostic_is_still_a_compile_error() -> None:
    assert classify(REAL_SCALAC_ERROR).error_class is ErrorClass.SCALA_COMPILE_ERROR


@pytest.mark.parametrize(
    "frames",
    [
        "[error] \tat scala.concurrent.Future.map(Future.scala:292)",
        "[error] \tat triad.jobs.generated.CleanJob_x.main(CleanJob_x.scala:10)",
        COURSIER_DOWNLOAD_FAILURE.replace("lmcoursier", "somethingelse"),
    ],
)
def test_a_stack_frame_in_a_scala_file_is_not_a_compile_error(frames: str) -> None:
    """The defect at the root of the coursier misclassification, asserted on its own."""
    assert classify(frames).error_class is not ErrorClass.SCALA_COMPILE_ERROR


def test_every_class_has_a_playbook_entry() -> None:
    """A class with no entry would raise KeyError in `classify`, at the worst possible time."""
    missing = [c for c in ErrorClass if c not in PLAYBOOK]
    assert not missing, missing


def test_gate_4_only_claims_a_compile_error_when_the_compiler_reported_one() -> None:
    """Gate 4's error class is passed to `classify` as a hint that skips pattern matching.

    Setting it for every non-zero sbt exit is what turned a download failure into a compile
    error regardless of the output. It must be set only when diagnostics were parsed.
    """
    from triad_core.errors import parse_scala_errors

    assert parse_scala_errors(REAL_SCALAC_ERROR), "the parser must find the real diagnostic"
    assert not parse_scala_errors(COURSIER_DOWNLOAD_FAILURE), "frames are not diagnostics"

    # The same decision gate 4 makes, on both outputs.
    for output, expected in (
        (REAL_SCALAC_ERROR, ErrorClass.SCALA_COMPILE_ERROR),
        (COURSIER_DOWNLOAD_FAILURE, None),
    ):
        errors = parse_scala_errors(output)
        result = GateResult(
            4, "compile", False, output,
            errors=[e.to_dict() for e in errors],
            error_class=ErrorClass.SCALA_COMPILE_ERROR if errors else None,
        )
        assert result.error_class is expected
        assert classify(result.detail, hint=result.error_class).error_class is (
            expected or ErrorClass.DEPENDENCY_RESOLUTION
        )
