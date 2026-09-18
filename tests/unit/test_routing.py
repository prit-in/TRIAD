"""TC-052 / ATC-01 / SC-02: every stage gets a routing decision that cites a rule."""

from __future__ import annotations

import pytest

from agents.routing import RoutingEngine, RoutingError


@pytest.fixture(scope="module")
def routing() -> RoutingEngine:
    return RoutingEngine()


def test_profiling_routes_to_pyspark_with_lr04(routing: RoutingEngine) -> None:
    """ATC-01: a profiling request on large event data cites LR-04."""
    d = routing.decide("profile", kind="profiling", read_only=True, input_rows=20_000_000)
    assert d["rule_id"] == "LR-04"
    assert d["engine"] == "spark" and d["language"] == "python"
    assert d["evidence"]["input_rows"] == 20_000_000


def test_reusable_stages_route_to_compiled_scala(routing: RoutingEngine) -> None:
    for stage, kind in (("clean", "cleaning"), ("labels", "labels"), ("features", "features")):
        d = routing.decide(stage, kind=kind, input_rows=1_000_000)
        assert d["rule_id"] == "LR-02", stage
        assert d["language"] == "scala"


def test_custom_row_logic_routes_to_scala_not_a_python_udf(routing: RoutingEngine) -> None:
    d = routing.decide("custom", needs_row_logic=True)
    assert d["rule_id"] == "LR-03"
    assert d["language"] == "scala"
    assert "Python UDFs are blocked" in d["rationale"]


def test_ml_stages_route_to_python(routing: RoutingEngine) -> None:
    for kind in ("training", "tuning", "evaluation", "explanation"):
        d = routing.decide(kind, kind=kind)
        assert d["rule_id"] == "LR-05"
        assert d["engine"] == "python"


def test_the_size_guard_picks_pandas_or_the_distributed_trainer(routing: RoutingEngine) -> None:
    """LR-06: pandas only when the estimate is at most 25% of ML worker RAM."""
    ram = 4 * 1024**3
    small = routing.decide("handoff", kind="handoff",
                           estimated_memory_bytes=int(ram * 0.10), ml_worker_ram_bytes=ram)
    assert small["rule_id"] == "LR-06" and small["target"] == "pandas"

    big = routing.decide("handoff", kind="handoff",
                         estimated_memory_bytes=int(ram * 0.80), ml_worker_ram_bytes=ram)
    assert big["target"] == "distributed"
    assert big["engine"] == "spark"


def test_scoring_routes_by_size(routing: RoutingEngine) -> None:
    small = routing.decide("score", kind="scoring", input_rows=1000)
    big = routing.decide("score", kind="scoring", input_rows=50_000_000)
    assert small["rule_id"] == big["rule_id"] == "LR-08"
    assert small["engine"] == "python"
    assert big["engine"] == "spark"


def test_small_inputs_use_local_mode_rather_than_a_pandas_path(routing: RoutingEngine) -> None:
    """LR-07: the same Spark job runs locally; there is no separate small-data path."""
    d = routing.decide("features", kind="features", input_bytes=10 * 1024**2)
    assert d["deploy_mode"] == "local[2]"
    big = routing.decide("features", kind="features", input_bytes=50 * 1024**3)
    assert big["deploy_mode"] == "cluster"


def test_every_decision_carries_a_rule_and_evidence(routing: RoutingEngine) -> None:
    for stage in ("profile", "clean", "labels", "features", "train", "score", "handoff"):
        d = routing.decide(stage)
        assert d["rule_id"].startswith("LR-")
        assert d["rationale"]
        assert d["evidence"]["stage"] == stage


def test_a_missing_threshold_is_an_error_not_a_guess(tmp_path) -> None:
    bad = tmp_path / "routing.yaml"
    bad.write_text("routing_version: '1'\nthresholds: {}\nrules: []\n", encoding="utf-8")
    engine = RoutingEngine(bad)
    with pytest.raises(RoutingError, match="not configured"):
        engine.threshold("event_rows_for_spark")


def test_spark_setting_allowlist_comes_from_the_governance_policy(routing: RoutingEngine) -> None:
    from triad_core.config import load_yaml, settings

    governance = load_yaml(settings().governance_policy_path)
    allowed = routing.allowed_spark_settings(governance)
    assert "spark.sql.shuffle.partitions" in allowed
    assert "spark.sql.ansi.enabled" not in allowed
