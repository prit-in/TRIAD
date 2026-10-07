"""FR-037 / TC-037: Spark setting changes come from measurements, apply to the next run only, and
every change has a before-and-after record (PRD section 30: L3, allowlisted, within quota)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from agents.graph import queue as q
from agents.graph.pipeline import ChurnPipeline
from agents.optimizer import QUOTA, compare, suggest
from triad_core.models import DecisionRecord, Execution


def _spark(*, spill: int = 0, reduce_median_ms: int = 400) -> dict:
    return {
        "totals": {"memory_spill_bytes": spill, "disk_spill_bytes": 0, "tasks": 16,
                   "executor_run_time_ms": 9000},
        "stages": [
            {"stage_id": 0, "shuffle_read_bytes": 0, "median_task_ms": 900},
            {"stage_id": 1, "shuffle_read_bytes": 4096, "median_task_ms": reduce_median_ms},
        ],
    }


def test_spill_doubles_the_partitions_within_quota() -> None:
    change = suggest(_spark(spill=1 << 20), 8)
    assert (change.rule, change.before, change.after) == ("OPT-1", 8, 16)
    assert suggest(_spark(spill=1), 48).after == QUOTA[1]
    assert suggest(_spark(spill=1), QUOTA[1]) is None, "never past the allowlist's bound"


def test_tiny_reduce_tasks_halve_the_partitions_within_quota() -> None:
    change = suggest(_spark(reduce_median_ms=12), 8)
    assert (change.rule, change.after) == ("OPT-2", 4)
    assert suggest(_spark(reduce_median_ms=12), 3).after == QUOTA[0]
    assert suggest(_spark(reduce_median_ms=12), QUOTA[0]) is None


def test_a_healthy_or_unmeasured_stage_is_left_alone() -> None:
    assert suggest(_spark(), 8) is None
    assert suggest(None, 8) is None and suggest({}, 8) is None


@pytest.mark.parametrize(("before", "after", "verdict"), [
    (100_000, 80_000, "faster"), (100_000, 95_000, "no difference beyond noise"),
    (100_000, 130_000, "slower"), (None, 50_000, "not comparable"),
])
def test_the_comparison_reads_only_what_is_outside_the_noise_band(before, after, verdict) -> None:
    result = compare({"duration_ms": before, "totals": {"tasks": 16}},
                     {"duration_ms": after, "totals": {"tasks": 8}})
    assert result["verdict"] == verdict
    assert result["tasks"] == {"before": 16, "after": 8}


def _executed(db_session, pipeline: str, *, spark: dict, partitions: int = 8):
    run = q.create_run(db_session, pipeline_name=pipeline, trigger_type="manual",
                       triggered_by="tester")
    db_session.add(Execution(run_id=run.id, stage="features", engine="scala_spark",
                             status="succeeded", exit_code=0, duration_ms=168_916,
                             inputs_json={"shuffle_partitions": str(partitions)},
                             metrics_json={"spark": spark}))
    db_session.flush()
    return run


def test_the_next_run_applies_the_change_and_records_both_halves(db_session, storage) -> None:
    pipeline = f"opt_{uuid.uuid4().hex[:8]}"
    _executed(db_session, pipeline, spark=_spark(reduce_median_ms=12))
    _executed(db_session, f"other_{uuid.uuid4().hex[:8]}", spark=_spark(spill=1 << 30))
    run = q.create_run(db_session, pipeline_name=pipeline, trigger_type="manual",
                       triggered_by="tester")
    churn = ChurnPipeline(db_session, run, store=storage)

    partitions, planned = churn._plan_shuffle_partitions("features")
    assert partitions == 4, "the previous run of *this* pipeline, not the other one, decides"
    db_session.add(Execution(run_id=run.id, stage="features", engine="scala_spark",
                             status="succeeded", exit_code=0, duration_ms=150_000,
                             inputs_json={"shuffle_partitions": "4"},
                             metrics_json={"spark": _spark(reduce_median_ms=30)}))
    db_session.flush()
    churn._record_setting_outcome("features", planned)

    # Both rows carry the transaction's `now()`, so they are told apart by type, not by time.
    records = {r.decision_type: r for r in db_session.execute(
        select(DecisionRecord).where(DecisionRecord.run_id == run.id,
                                     DecisionRecord.agent == "optimization")
    ).scalars()}
    assert set(records) == {"spark_setting", "spark_setting_outcome"}
    assert records["spark_setting"].decision == "features: spark.sql.shuffle.partitions 8 -> 4"
    comparison = records["spark_setting_outcome"].evidence_refs["comparison"]
    assert comparison["verdict"] == "faster"
    assert comparison["duration_ms"] == {"before": 168_916, "after": 150_000}


def test_a_first_run_keeps_the_default_and_records_nothing(db_session, storage) -> None:
    run = q.create_run(db_session, pipeline_name=f"opt_{uuid.uuid4().hex[:8]}",
                       trigger_type="manual", triggered_by="tester")
    assert ChurnPipeline(db_session, run, store=storage)._plan_shuffle_partitions("features") \
        == (8, None)
