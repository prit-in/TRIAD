"""FR-037: Spark setting changes from measured stage metrics, applied to the next run only.

PRD section 30 makes "Spark setting changes" L3: autonomous, allowlisted settings within quota,
with before-and-after metrics as the evidence. One setting is tuned: the shuffle partition
count, the allowlisted knob every TRIAD job takes as `--shuffle-partitions`. A change is computed
from what FR-036 measured on the previous run of the same pipeline and stage, applied to the
stage of the run that starts next, and compared with that previous run once it has executed.
Nothing changes mid-run, and a stage with no measured predecessor keeps the default.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass
from typing import Any

SETTING = "spark.sql.shuffle.partitions"
DEFAULT_PARTITIONS = 8
QUOTA = (2, 64)          # the allowlist's bounds for this setting (PRD 30.3)
TINY_TASK_MS = 50        # a reduce task this short is mostly scheduling overhead
NOISE = 0.10             # wall-clock differences inside +/-10% are not read as an effect


@dataclass(frozen=True)
class SettingChange:
    setting: str
    before: int
    after: int
    rule: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def suggest(spark: dict[str, Any] | None, current: int) -> SettingChange | None:
    """The change the last run's measurements call for, or None.

    OPT-1: a stage spilled execution memory to disk, so its partitions were too large for the
    memory it had; twice as many, half as large. OPT-2: the reduce stages' median task took
    under 50 ms, so the work is spread thinner than the per-task cost; half as many. Adaptive
    execution already coalesces small partitions after a shuffle, so OPT-2 mostly trims what it
    leaves - which is why the outcome is measured and recorded, not assumed.
    """
    if not spark:
        return None
    totals, stages = spark.get("totals") or {}, spark.get("stages") or []
    low, high = QUOTA
    spill = int(totals.get("memory_spill_bytes") or 0) + int(totals.get("disk_spill_bytes") or 0)
    if spill > 0:
        if current >= high:
            return None
        return SettingChange(SETTING, current, min(current * 2, high), "OPT-1",
                             f"{spill} bytes spilled at {current} partitions; smaller partitions "
                             "need less execution memory each")
    reduce_side = [s for s in stages if int(s.get("shuffle_read_bytes") or 0) > 0]
    if reduce_side and current > low:
        median_task = statistics.median(int(s.get("median_task_ms") or 0) for s in reduce_side)
        if median_task < TINY_TASK_MS:
            return SettingChange(SETTING, current, max(current // 2, low), "OPT-2",
                                 f"the reduce stages' median task took {median_task:g} ms at "
                                 f"{current} partitions; fewer, larger tasks carry less overhead")
    return None


def measured(execution: Any) -> dict[str, Any]:
    """What one execution measured, in the shape a before-and-after record keeps."""
    spark = (execution.metrics_json or {}).get("spark") or {}
    return {"execution_id": str(execution.id), "duration_ms": execution.duration_ms,
            "shuffle_partitions": int((execution.inputs_json or {}).get("shuffle_partitions")
                                      or DEFAULT_PARTITIONS),
            "totals": spark.get("totals") or {}}


def compare(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """The before-and-after record (PRD 30: "before-and-after metrics").

    One run on each side, so the verdict says only whether the wall clock moved by more than
    the noise band - not that the setting caused it.
    """
    b, a = before.get("duration_ms"), after.get("duration_ms")
    if not b or a is None:
        verdict = "not comparable"
    elif a < b * (1 - NOISE):
        verdict = "faster"
    elif a > b * (1 + NOISE):
        verdict = "slower"
    else:
        verdict = "no difference beyond noise"
    keys = ("executor_run_time_ms", "tasks", "shuffle_read_bytes", "shuffle_write_bytes",
            "memory_spill_bytes", "disk_spill_bytes")
    return {
        "verdict": verdict,
        "duration_ms": {"before": b, "after": a},
        **{k: {"before": before["totals"].get(k), "after": after["totals"].get(k)} for k in keys},
    }
