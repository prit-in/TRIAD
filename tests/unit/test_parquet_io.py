"""Parquet interop: the two ways a Spark/pandas hand-off has silently gone wrong.

Both of these shipped as defects before they were tests:

  1. pandas writes nanosecond timestamps and Spark 4 refuses to read them
     (`[PARQUET_TYPE_ILLEGAL] Illegal Parquet type: INT64 (TIMESTAMP(NANOS,false))`);
  2. a Hive partition path carries no type, so a partitioned `snapshot_date` came back as a
     string while the same column from an unpartitioned set came back as a date. The join
     between them matched nothing while both row counts looked correct.
"""

from __future__ import annotations

import datetime as dt
import json

import pandas as pd
import pyarrow.parquet as pq
import pytest

from triad_core.parquet_io import TIMESTAMP_UNIT, read_parquet, write_parquet

CUTOFFS = [dt.date(2026, 1, 1), dt.date(2026, 2, 1), dt.date(2026, 3, 1)]


def _features() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "customer_id": [f"C-{i:04d}" for i in range(9)],
            "snapshot_date": [c for c in CUTOFFS for _ in range(3)],
            "recency_days": list(range(9)),
        }
    )


def _manifest(directory, schema: dict[str, str]) -> None:
    (directory / "_manifest.json").write_text(
        json.dumps({"kind": "features", "name": "t", "version": "1", "schema": schema}),
        encoding="utf-8",
    )


# --- timestamps ----------------------------------------------------------------


def test_nanosecond_timestamps_are_written_as_microseconds(tmp_path) -> None:
    frame = pd.DataFrame({"t": pd.to_datetime(["2026-01-01 00:00:00.123456789"])})
    assert frame["t"].dtype == "datetime64[ns]"

    write_parquet(frame, tmp_path / "ts")
    schema = pq.read_schema(str(tmp_path / "ts" / "part-00000.parquet"))
    assert schema.field("t").type.unit == TIMESTAMP_UNIT


def test_a_timezone_survives_the_coercion(tmp_path) -> None:
    frame = pd.DataFrame({"t": pd.to_datetime(["2026-01-01T00:00:00Z"])})
    write_parquet(frame, tmp_path / "tz")
    field = pq.read_schema(str(tmp_path / "tz" / "part-00000.parquet")).field("t")
    assert field.type.unit == TIMESTAMP_UNIT and field.type.tz is not None


# --- partition columns ---------------------------------------------------------


def test_a_partitioned_date_column_reads_back_as_a_date(tmp_path) -> None:
    target = tmp_path / "features"
    write_parquet(_features(), target, partition_cols=["snapshot_date"])
    _manifest(target, {"customer_id": "string", "snapshot_date": "date",
                       "recency_days": "int"})

    frame = read_parquet(target)
    assert isinstance(frame["snapshot_date"].iloc[0], dt.date)
    assert sorted(frame["snapshot_date"].unique()) == CUTOFFS


def test_partitioned_and_unpartitioned_reads_agree_on_the_key_type(tmp_path) -> None:
    """The invariant the feature/label join depends on."""
    features = tmp_path / "features"
    labels = tmp_path / "labels"
    write_parquet(_features(), features, partition_cols=["snapshot_date"])
    _manifest(features, {"customer_id": "string", "snapshot_date": "date",
                         "recency_days": "int"})

    label_frame = _features()[["customer_id", "snapshot_date"]].copy()
    label_frame["churned_90d"] = 1
    write_parquet(label_frame, labels)

    left, right = read_parquet(features), read_parquet(labels)
    joined = left.merge(right, on=["customer_id", "snapshot_date"], how="inner")
    assert len(joined) == len(left) == 9


def test_the_unfixed_behaviour_is_what_broke_the_join(tmp_path) -> None:
    """Pinned so the failure mode stays visible: without restoration, nothing matches."""
    features = tmp_path / "features"
    write_parquet(_features(), features, partition_cols=["snapshot_date"])

    raw = read_parquet(features, restore_types=False)
    assert isinstance(raw["snapshot_date"].dtype, pd.CategoricalDtype)
    assert isinstance(raw["snapshot_date"].iloc[0], str)

    labels = _features()[["customer_id", "snapshot_date"]]
    assert len(raw.merge(labels, on=["customer_id", "snapshot_date"], how="inner")) == 0


def test_a_partition_column_is_never_left_categorical(tmp_path) -> None:
    """A Categorical join key is its own trap, so de-categorising happens manifest or not."""
    target = tmp_path / "no_manifest"
    write_parquet(_features(), target, partition_cols=["snapshot_date"])

    frame = read_parquet(target)          # deliberately no manifest written
    assert not isinstance(frame["snapshot_date"].dtype, pd.CategoricalDtype)


def test_an_integer_partition_column_comes_back_as_an_integer(tmp_path) -> None:
    target = tmp_path / "by_month"
    frame = pd.DataFrame({"v": [1.0, 2.0, 3.0], "snapshot_month": [1, 1, 2]})
    write_parquet(frame, target, partition_cols=["snapshot_month"])
    _manifest(target, {"v": "double", "snapshot_month": "int"})

    assert read_parquet(target)["snapshot_month"].dtype == "int64"


def test_an_unpartitioned_read_is_untouched(tmp_path) -> None:
    write_parquet(_features(), tmp_path / "flat")
    frame = read_parquet(tmp_path / "flat")
    assert list(frame.columns) == ["customer_id", "snapshot_date", "recency_days"]
    assert isinstance(frame["snapshot_date"].iloc[0], dt.date)


def test_a_corrupt_manifest_does_not_fail_the_read(tmp_path) -> None:
    target = tmp_path / "features"
    write_parquet(_features(), target, partition_cols=["snapshot_date"])
    (target / "_manifest.json").write_text("{not json", encoding="utf-8")

    frame = read_parquet(target)
    assert len(frame) == 9
    assert not isinstance(frame["snapshot_date"].dtype, pd.CategoricalDtype)


@pytest.mark.parametrize("columns", [None, ["customer_id", "snapshot_date"]])
def test_column_projection_still_restores_types(tmp_path, columns) -> None:
    target = tmp_path / "features"
    write_parquet(_features(), target, partition_cols=["snapshot_date"])
    _manifest(target, {"customer_id": "string", "snapshot_date": "date",
                       "recency_days": "int"})

    frame = read_parquet(target, columns=columns)
    assert isinstance(frame["snapshot_date"].iloc[0], dt.date)
