"""B3 hands its feature table to the modelling through parquet, as P does (DEC-50): the table
must be the one it used to collect to the driver, or the change would move B3's results, not
only its memory."""

from __future__ import annotations

import pandas as pd
import pytest


@pytest.mark.spark
@pytest.mark.slow
def test_b3s_features_are_the_same_handed_over_as_collected(spark, tmp_path) -> None:
    from datasets.synthetic.generator import GeneratorConfig, SyntheticGenerator
    from experiments import baseline_b3
    from experiments.exp02_medium_dataset import month_starts, split_groups

    source = tmp_path / "source"
    SyntheticGenerator(GeneratorConfig(customers=150, months=24, seed=42,
                                       start_date="2024-01-01")).write(source)
    cutoffs = sorted({c for group in split_groups(month_starts("2024-01-01", 24)).values()
                      for c in group})

    collected = baseline_b3.build_features(spark, source, cutoffs)
    assert baseline_b3.build_features(spark, source, cutoffs, handoff=tmp_path / "out") is None
    handed = baseline_b3.read_handoff(tmp_path / "out")

    keys = ["customer_id", "snapshot_date"]
    assert str(handed["snapshot_date"].dtype).startswith("datetime64") and \
        handed["snapshot_date"].dt.tz is None, "naive UTC, as toPandas gave it"
    pd.testing.assert_frame_equal(
        collected.sort_values(keys).reset_index(drop=True),
        handed[list(collected.columns)].sort_values(keys).reset_index(drop=True),
        check_dtype=False,
    )
