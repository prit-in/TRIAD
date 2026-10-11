"""The experiments' isolated runner: a result comes back, an exception is re-raised with its
text, and a child that outlives its ceiling is stopped and reported rather than waited on."""

from __future__ import annotations

import pytest

from experiments.baselines import IsolatedProcessKilled, run_isolated


def test_a_result_comes_back_from_the_child() -> None:
    assert run_isolated("json:dumps", obj={"a": 1}) == '{"a": 1}'


def test_an_exception_in_the_child_is_raised_with_its_text() -> None:
    with pytest.raises(RuntimeError, match="JSONDecodeError"):
        run_isolated("json:loads", s="{not json")


def test_a_child_past_its_ceiling_is_stopped_and_reported() -> None:
    with pytest.raises(IsolatedProcessKilled) as stopped:
        run_isolated("subprocess:run", timeout_s=1, args=["sleep", "30"])
    assert stopped.value.timed_out and stopped.value.elapsed_s < 20


def test_a_baseline_that_fails_is_its_failed_result_not_the_experiments_end() -> None:
    from experiments.baselines import run_baseline

    raised = run_baseline("json:loads", "B3", s="{not json")
    assert not raised.ok and raised.detail.startswith("raised:")
    assert "JSONDecodeError" in raised.detail
    assert raised.wall_clock_s > 0, "the time it took, measured - not a placeholder"
    stopped = run_baseline("subprocess:run", "B2", timeout_s=1, args=["sleep", "30"])
    assert not stopped.ok and stopped.detail.startswith("timed out")


def _jvm_probe() -> dict:
    """In a fresh process: start a session, shut its JVM down as B3 does, report what is left."""
    from pyspark import SparkContext
    from pyspark.sql import SparkSession

    from experiments.baseline_b3 import shut_down_jvm

    spark = (SparkSession.builder.master("local[1]").appName("jvm-probe")
             .config("spark.ui.enabled", "false").getOrCreate())
    proc = spark.sparkContext._gateway.proc
    shut_down_jvm(spark)
    return {"exit_code": proc.poll(), "gateway_cleared": SparkContext._gateway is None}


def test_b3_ends_the_spark_jvm_before_it_models() -> None:
    """`spark.stop()` alone left B3's gateway JVM, 2.2 GB at 100 000 customers, resident beside
    the modelling until the container's OOM killer took it (DEC-44)."""
    probe = run_isolated(f"{__name__}:_jvm_probe", timeout_s=240)
    assert probe["exit_code"] is not None, "the gateway JVM is still running"
    assert probe["gateway_cleared"]
