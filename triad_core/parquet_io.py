"""Parquet interop helpers (PRD sections 13.5, 23.2).

One rule, learned the hard way in the Phase-1 smoke test: **pandas/PyArrow write nanosecond
timestamps by default, and Spark 4 refuses to read them**
(`[PARQUET_TYPE_ILLEGAL] Illegal Parquet type: INT64 (TIMESTAMP(NANOS,false))`).

Every Parquet file Python writes for Spark to read goes through `write_parquet` here, which
coerces timestamps to microseconds, matching `spark.sql.parquet.outputTimestampType =
TIMESTAMP_MICROS` on the Spark side. Fixtures, the synthetic generator and the ML layer all
use it, so the mismatch cannot come back through a different door.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd

TIMESTAMP_UNIT = "us"


def write_parquet(
    df: pd.DataFrame,
    path: str | Path,
    *,
    partition_cols: list[str] | None = None,
    compression: str = "zstd",
    **kwargs: Any,
) -> Path:
    """Write a DataFrame as Spark-readable Parquet (microsecond timestamps, zstd)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    target = Path(path)
    table = pa.Table.from_pandas(df, preserve_index=False)
    table = _coerce_timestamps(table)

    if partition_cols:
        target.mkdir(parents=True, exist_ok=True)
        pq.write_to_dataset(
            table,
            root_path=str(target),
            partition_cols=partition_cols,
            compression=compression,
            **kwargs,
        )
    elif target.suffix == ".parquet":
        target.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, str(target), compression=compression, **kwargs)
    else:
        target.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            table, str(target / "part-00000.parquet"), compression=compression, **kwargs
        )
    return target


def _coerce_timestamps(table: Any) -> Any:
    """Rewrite every timestamp column to microseconds, keeping any timezone.

    Nanosecond values that are not whole microseconds are truncated on purpose:
    `spark.sql.parquet.outputTimestampType` is pinned to TIMESTAMP_MICROS, so the extra
    digits could not survive the round trip anyway, and a safe cast would simply refuse.
    """
    import pyarrow as pa
    import pyarrow.compute as pc

    columns = []
    fields = []
    changed = False
    for i, field in enumerate(table.schema):
        column = table.column(i)
        if pa.types.is_timestamp(field.type) and field.type.unit != TIMESTAMP_UNIT:
            target = pa.timestamp(TIMESTAMP_UNIT, field.type.tz)
            # safe=False permits the sub-microsecond truncation; the target type is fixed,
            # so no other lossy conversion can happen here.
            columns.append(pc.cast(column, target, safe=False))
            fields.append(field.with_type(target))
            changed = True
        else:
            columns.append(column)
            fields.append(field)
    return pa.Table.from_arrays(columns, schema=pa.schema(fields)) if changed else table


def read_parquet(
    path: str | Path, columns: list[str] | None = None, *, restore_types: bool = True
) -> pd.DataFrame:
    """Read a Parquet folder or file written by Spark or by `write_parquet`.

    Partition columns are restored to their declared type (see `_restore_partition_types`),
    so a column's pandas dtype never depends on whether it happened to be a partition key.
    """
    import pyarrow.parquet as pq

    frame = pq.read_table(str(path), columns=columns).to_pandas()
    return _restore_partition_types(frame, Path(path)) if restore_types else frame


# Spark's `simpleString` type names, for the columns a Hive path cannot carry a type for.
_PARTITION_CASTS = {
    "boolean": "bool",
    "tinyint": "int64", "smallint": "int64", "int": "int64", "bigint": "int64",
    "float": "float64", "double": "float64",
    "string": "object",
}


def _restore_partition_types(frame: pd.DataFrame, root: Path) -> pd.DataFrame:
    """Give a partition column back the type the writer declared for it.

    A Hive path holds `snapshot_date=2026-04-01`, which is a directory name, so Arrow returns
    the column dictionary-encoded as *strings*. The same column read from an unpartitioned
    dataset comes back as `datetime.date`. Joining the two then matches nothing while both
    row counts look right - which is exactly how the feature/label join silently produced an
    empty training set.

    The manifest Spark wrote next to the data carries the real schema, so nothing is guessed.
    Without a manifest the column is still de-categorised, because a Categorical join key is
    a trap of its own.
    """
    import json

    import pandas as pd

    partition_columns = [
        name for name in frame.columns
        if isinstance(frame[name].dtype, pd.CategoricalDtype)
    ]
    if not partition_columns:
        return frame

    schema: dict[str, str] = {}
    manifest = root / "_manifest.json"
    if manifest.is_file():
        try:
            schema = json.loads(manifest.read_text(encoding="utf-8")).get("schema") or {}
        except (OSError, ValueError):
            schema = {}

    for name in partition_columns:
        column = frame[name].astype(object)
        declared = schema.get(name, "")
        if declared == "date":
            column = pd.to_datetime(column).dt.date
        elif declared.startswith("timestamp"):
            column = pd.to_datetime(column)
        elif declared in _PARTITION_CASTS:
            column = column.astype(_PARTITION_CASTS[declared])
        frame[name] = column
    return frame


def estimate_in_memory_bytes(path: str | Path) -> int:
    """Estimate the pandas footprint of a Parquet folder without loading it (FR-045).

    Uses Parquet footer metadata only. The size guard (LR-06) compares this against the ML
    worker's RAM budget and routes to the distributed trainer when it does not fit.
    """
    import pyarrow.parquet as pq

    dataset = pq.ParquetDataset(str(path))
    total = 0
    for fragment in dataset.fragments:
        md = fragment.metadata
        for i in range(md.num_row_groups):
            rg = md.row_group(i)
            for c in range(rg.num_columns):
                col = rg.column(c)
                # Uncompressed size is the closest footer figure to the in-memory size;
                # object/string columns cost more in pandas, hence the 1.5 factor.
                total += col.total_uncompressed_size
    return int(total * 1.5)
