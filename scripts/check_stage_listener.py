"""Smoke check: PySpark can register the FR-036 listener from the core JAR and read it back.

    python scripts/check_stage_listener.py

EXP-3 measures shuffle bytes, spill and skew this way, so it is checked on its own first: a
missing JAR or a class py4j cannot see fails here in seconds rather than an hour into a run.
"""

from __future__ import annotations

import json

from experiments.exp03_large_dataset import _spark


def main() -> None:
    spark = _spark(2)
    listener = spark._jvm.triad.core.StageMetricsListener()
    spark.sparkContext._jsc.sc().addSparkListener(listener)
    try:
        spark.range(0, 200_000).selectExpr("id % 13 as k", "id as v").groupBy("k").sum("v").collect()
    finally:
        spark.stop()
    stages = json.loads(listener.toJson())
    assert len(stages) >= 2, stages
    assert sum(s["shuffle_write_bytes"] for s in stages) > 0, stages
    print(f"ok: {len(stages)} stages, "
          f"{sum(s['shuffle_write_bytes'] for s in stages)} shuffle bytes written")


if __name__ == "__main__":
    main()
