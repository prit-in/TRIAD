"""M03 Profiling: Spark-computed statistics, semantic types, issues and a quality score.

Everything here is deterministic and computed by Spark (UC-02, FR-009..FR-015). The LLM may
later summarise a profile, but it can never change a number in it, and it may not downgrade
a blocking issue.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from triad_core.ids import canonical_json, sha256_text

if TYPE_CHECKING:  # pragma: no cover
    from pyspark.sql import DataFrame, SparkSession

# --- Semantic types (FR-009) --------------------------------------------------

SEMANTIC_TYPES = (
    "identifier",
    "timestamp",
    "date",
    "currency",
    "quantity",
    "category",
    "boolean_flag",
    "free_text",
    "email",
    "phone",
    "country_code",
    "unknown",
)

_ID_NAME = re.compile(r"(^|_)(id|key|code|no|number)$|^(id|uuid|guid)$", re.I)
_TS_NAME = re.compile(r"(date|time|_at|_on|timestamp)", re.I)
_MONEY_NAME = re.compile(r"(price|amount|revenue|cost|value|discount|refund|charge|fee)", re.I)
_QTY_NAME = re.compile(r"(quantity|qty|count|units|views|clicks|sessions)", re.I)
_FLAG_NAME = re.compile(r"(^is_|^has_|_flag$|^flag_|opt_in)", re.I)
_EMAIL_NAME = re.compile(r"(email|e_mail)", re.I)
_PHONE_NAME = re.compile(r"(phone|mobile|msisdn)", re.I)
_COUNTRY_NAME = re.compile(r"(country|nation)", re.I)


@dataclass
class ColumnProfile:
    name: str
    physical_type: str
    semantic_type: str
    null_count: int
    null_rate: float
    empty_string_count: int
    approx_distinct: int
    exact_distinct: int | None
    min_value: Any = None
    max_value: Any = None
    mean: float | None = None
    stddev: float | None = None
    quantiles: dict[str, float] = field(default_factory=dict)
    top_values: list[dict[str, Any]] = field(default_factory=list)
    outlier_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Issue:
    id: str
    severity: str           # blocking | warning | info
    column: str | None
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Profile:
    dataset: str
    version_no: int | None
    row_count: int
    column_count: int
    columns: list[ColumnProfile]
    duplicate_rows: int
    key_duplicate_rows: int | None
    key_columns: list[str]
    issues: list[Issue]
    quality_score: float
    quality_dimensions: dict[str, float]
    schema_hash: str
    computed_at: str
    sample_fraction: float = 1.0
    schema_diff: dict[str, Any] = field(default_factory=dict)
    population_stability: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["columns"] = [c.to_dict() for c in self.columns]
        d["issues"] = [i.to_dict() for i in self.issues]
        return d

    @property
    def blocking_issues(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "blocking"]


def infer_semantic_type(name: str, physical_type: str, sample_values: list[Any]) -> str:
    """Name-first, then physical type, then value shape. Deterministic and explainable."""
    pt = (physical_type or "").lower()
    if _EMAIL_NAME.search(name):
        return "email"
    if _PHONE_NAME.search(name):
        return "phone"
    if _COUNTRY_NAME.search(name):
        return "country_code"
    if _TS_NAME.search(name) or pt.startswith("timestamp"):
        return "timestamp" if pt.startswith("timestamp") else ("date" if pt == "date" else "timestamp")
    if pt == "date":
        return "date"
    if _FLAG_NAME.search(name) or pt == "boolean":
        return "boolean_flag"
    if _ID_NAME.search(name):
        return "identifier"
    if _MONEY_NAME.search(name) and (pt.startswith(("decimal", "double", "float")) or pt.startswith("int")):
        return "currency"
    if _QTY_NAME.search(name) and pt.startswith(("int", "long", "bigint", "short")):
        return "quantity"
    if pt in ("string", "varchar"):
        values = [str(v) for v in sample_values if v is not None]
        if values:
            avg_len = sum(len(v) for v in values) / len(values)
            distinct_ratio = len(set(values)) / len(values)
            if avg_len > 40:
                return "free_text"
            if distinct_ratio < 0.3:
                return "category"
        return "category"
    if pt.startswith(("decimal", "double", "float", "int", "long", "bigint", "short")):
        return "quantity"
    return "unknown"


def profile_dataframe(
    df: DataFrame,
    *,
    dataset: str,
    version_no: int | None = None,
    key_columns: list[str] | None = None,
    optional_columns: frozenset[str] | None = None,
    quantiles: tuple[float, ...] = (0.25, 0.5, 0.75, 0.95, 0.99),
    top_k: int = 5,
    sample_fraction: float = 1.0,
    approx_distinct_rsd: float = 0.02,
    exact_distinct_max_rows: int = 2_000_000,
) -> Profile:
    """Compute the full profile in a small number of Spark passes.

    Cheap exact metrics (row count, nulls) always use all data; distinct counts are
    approximate above `exact_distinct_max_rows` (UC-02 alternative flow).
    """
    from pyspark.sql import functions as F

    key_columns = key_columns or []
    fields = [(f.name, f.dataType.simpleString()) for f in df.schema.fields]
    row_count = df.count()

    working = df
    if sample_fraction < 1.0:
        working = df.sample(withReplacement=False, fraction=sample_fraction, seed=42).cache()

    # --- one pass for counts, nulls, min/max/mean/stddev ---------------------
    aggs: list[Any] = [F.count(F.lit(1)).alias("__rows")]
    numeric_cols: list[str] = []
    string_cols: list[str] = []
    for name, ptype in fields:
        col = F.col(f"`{name}`")
        aggs.append(F.sum(col.isNull().cast("long")).alias(f"null__{name}"))
        aggs.append(F.approx_count_distinct(col, rsd=approx_distinct_rsd).alias(f"dist__{name}"))
        if ptype.startswith(("int", "long", "bigint", "short", "double", "float", "decimal")):
            numeric_cols.append(name)
            aggs.append(F.min(col).alias(f"min__{name}"))
            aggs.append(F.max(col).alias(f"max__{name}"))
            aggs.append(F.avg(col.cast("double")).alias(f"mean__{name}"))
            aggs.append(F.stddev_samp(col.cast("double")).alias(f"std__{name}"))
        elif ptype in ("string", "varchar"):
            string_cols.append(name)
            aggs.append(
                F.sum((F.trim(col) == F.lit("")).cast("long")).alias(f"empty__{name}")
            )
            aggs.append(F.min(col).alias(f"min__{name}"))
            aggs.append(F.max(col).alias(f"max__{name}"))
        elif ptype in ("date",) or ptype.startswith("timestamp"):
            aggs.append(F.min(col).alias(f"min__{name}"))
            aggs.append(F.max(col).alias(f"max__{name}"))

    stats = working.agg(*aggs).collect()[0].asDict()
    profiled_rows = int(stats["__rows"]) or 1

    # --- quantiles for numeric columns (one approxQuantile call per column) ---
    quantile_map: dict[str, dict[str, float]] = {}
    if numeric_cols:
        qs = working.approxQuantile(numeric_cols, list(quantiles), 0.01)
        for name, values in zip(numeric_cols, qs, strict=False):
            quantile_map[name] = {
                f"p{int(q * 100)}": v for q, v in zip(quantiles, values, strict=False)
            }

    # --- top values for categorical-ish columns ------------------------------
    top_map: dict[str, list[dict[str, Any]]] = {}
    for name in string_cols:
        approx_distinct = int(stats.get(f"dist__{name}", 0) or 0)
        if approx_distinct and approx_distinct <= 1000:
            rows = (
                working.groupBy(F.col(f"`{name}`").alias("value"))
                .count()
                .orderBy(F.col("count").desc())
                .limit(top_k)
                .collect()
            )
            top_map[name] = [{"value": r["value"], "count": int(r["count"])} for r in rows]

    # --- sample values for semantic typing -----------------------------------
    sample_rows = working.limit(200).collect()

    columns: list[ColumnProfile] = []
    for name, ptype in fields:
        nulls = int(stats.get(f"null__{name}", 0) or 0)
        approx_distinct = int(stats.get(f"dist__{name}", 0) or 0)
        exact_distinct = None
        if row_count <= exact_distinct_max_rows and (name in key_columns or approx_distinct < 1000):
            exact_distinct = working.select(F.col(f"`{name}`")).distinct().count()

        sample_values = [r[name] for r in sample_rows if r[name] is not None][:50]
        mean = stats.get(f"mean__{name}")
        std = stats.get(f"std__{name}")
        outliers = None
        if name in numeric_cols and name in quantile_map:
            q = quantile_map[name]
            p25, p75 = q.get("p25"), q.get("p75")
            if p25 is not None and p75 is not None:
                iqr = p75 - p25
                if iqr > 0:
                    lo, hi = p25 - 1.5 * iqr, p75 + 1.5 * iqr
                    outliers = working.where(
                        (F.col(f"`{name}`") < lo) | (F.col(f"`{name}`") > hi)
                    ).count()

        columns.append(
            ColumnProfile(
                name=name,
                physical_type=ptype,
                semantic_type=infer_semantic_type(name, ptype, sample_values),
                null_count=nulls,
                null_rate=nulls / profiled_rows,
                empty_string_count=int(stats.get(f"empty__{name}", 0) or 0),
                approx_distinct=approx_distinct,
                exact_distinct=exact_distinct,
                min_value=_scalar(stats.get(f"min__{name}")),
                max_value=_scalar(stats.get(f"max__{name}")),
                mean=float(mean) if mean is not None and not _is_nan(mean) else None,
                stddev=float(std) if std is not None and not _is_nan(std) else None,
                quantiles=quantile_map.get(name, {}),
                top_values=top_map.get(name, []),
                outlier_count=outliers,
            )
        )

    # --- duplicates (FR-014) --------------------------------------------------
    distinct_rows = working.distinct().count()
    duplicate_rows = profiled_rows - distinct_rows
    key_duplicate_rows = None
    if key_columns and all(k in dict(fields) for k in key_columns):
        distinct_keys = working.select(*[F.col(f"`{k}`") for k in key_columns]).distinct().count()
        key_duplicate_rows = profiled_rows - distinct_keys

    issues = detect_issues(
        columns, row_count, duplicate_rows, key_duplicate_rows, key_columns,
        optional_columns=optional_columns or frozenset(),
    )
    score, dims = quality_score(columns, row_count, duplicate_rows, key_duplicate_rows, issues)

    if sample_fraction < 1.0:
        working.unpersist()

    return Profile(
        dataset=dataset,
        version_no=version_no,
        row_count=row_count,
        column_count=len(fields),
        columns=columns,
        duplicate_rows=duplicate_rows,
        key_duplicate_rows=key_duplicate_rows,
        key_columns=key_columns,
        issues=issues,
        quality_score=score,
        quality_dimensions=dims,
        schema_hash=sha256_text(canonical_json(sorted(fields))),
        computed_at=dt.datetime.now(dt.UTC).isoformat(),
        sample_fraction=sample_fraction,
    )


def _scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def _is_nan(value: Any) -> bool:
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return False


# --- Issues and quality score -------------------------------------------------

NULL_WARN_RATE = 0.20      # EX, PRD section 11.2
NULL_BLOCK_RATE = 0.50
DOMINANT_VALUE_SHARE = 0.99


def detect_issues(
    columns: list[ColumnProfile],
    row_count: int,
    duplicate_rows: int,
    key_duplicate_rows: int | None,
    key_columns: list[str],
    optional_columns: frozenset[str] = frozenset(),
) -> list[Issue]:
    """Severity comes from rules, never from judgement (PRD section 11.2).

    `optional_columns` are the ones the ingest contract declares `required: false` and
    `nullable: true`. A high null rate on one of those is the data behaving as documented -
    85.7% of orders genuinely carry no promo code - and calling it *blocking* both cries wolf
    and makes the blocking count useless as a signal: every healthy run reported one, so a run
    with a real schema fault looked no different. It is still reported, as a warning.

    The contract is the rule here, so this is not judgement creeping in: a column the contract
    declares required, or non-nullable, is blocking on a high null rate exactly as before.
    """
    issues: list[Issue] = []

    if row_count == 0:
        issues.append(Issue("EMPTY_DATASET", "blocking", None, "the dataset has no rows"))

    for col in columns:
        if col.name in key_columns and col.null_count > 0:
            issues.append(
                Issue(
                    f"NULL_KEY.{col.name}",
                    "blocking",
                    col.name,
                    f"{col.null_count} rows have a null key",
                    {"null_count": col.null_count, "null_rate": round(col.null_rate, 4)},
                )
            )
        elif col.null_rate >= NULL_BLOCK_RATE:
            optional = col.name in optional_columns
            issues.append(
                Issue(
                    f"HIGH_NULL_RATE.{col.name}",
                    "warning" if optional else "blocking",
                    col.name,
                    f"{col.null_rate:.1%} of {col.name} is null"
                    + (" (the contract declares this column optional)" if optional else ""),
                    {"null_rate": round(col.null_rate, 4), "contract_optional": optional},
                )
            )
        elif col.null_rate >= NULL_WARN_RATE:
            issues.append(
                Issue(
                    f"NULL_RATE.{col.name}",
                    "warning",
                    col.name,
                    f"{col.null_rate:.1%} of {col.name} is null",
                    {"null_rate": round(col.null_rate, 4)},
                )
            )
        elif col.semantic_type == "identifier" and col.null_count > 0:
            # An identifier with any nulls costs rows downstream (they are quarantined as
            # NO_CUSTOMER_KEY in P3), so it is always reported even below the warn rate.
            issues.append(
                Issue(
                    f"NULL_IN_IDENTIFIER.{col.name}",
                    "warning",
                    col.name,
                    f"{col.null_count} rows have no {col.name} "
                    f"({col.null_rate:.1%}); these rows cannot be joined to a customer",
                    {"null_count": col.null_count, "null_rate": round(col.null_rate, 4)},
                )
            )

        if col.empty_string_count and row_count:
            share = col.empty_string_count / row_count
            if share >= NULL_WARN_RATE:
                issues.append(
                    Issue(
                        f"EMPTY_STRINGS.{col.name}",
                        "warning",
                        col.name,
                        f"{share:.1%} of {col.name} is an empty string",
                        {"empty_string_count": col.empty_string_count},
                    )
                )

        if col.top_values and row_count:
            top = col.top_values[0]
            share = top["count"] / row_count
            if share >= DOMINANT_VALUE_SHARE:
                issues.append(
                    Issue(
                        f"CONSTANT_COLUMN.{col.name}",
                        "warning",
                        col.name,
                        f"{col.name} is {share:.1%} the single value {top['value']!r}",
                        {"dominant_share": round(share, 4)},
                    )
                )

        if col.outlier_count:
            issues.append(
                Issue(
                    f"OUTLIERS.{col.name}",
                    "info",
                    col.name,
                    f"{col.outlier_count} values outside the 1.5*IQR fence",
                    {"outlier_count": col.outlier_count},
                )
            )

    if duplicate_rows:
        issues.append(
            Issue(
                "DUPLICATE_ROWS",
                "warning",
                None,
                f"{duplicate_rows} fully duplicated rows",
                {"duplicate_rows": duplicate_rows},
            )
        )
    if key_duplicate_rows:
        issues.append(
            Issue(
                "DUPLICATE_KEYS",
                "blocking",
                None,
                f"{key_duplicate_rows} rows share a key in {key_columns}",
                {"key_duplicate_rows": key_duplicate_rows, "key_columns": key_columns},
            )
        )
    return issues


def quality_score(
    columns: list[ColumnProfile],
    row_count: int,
    duplicate_rows: int,
    key_duplicate_rows: int | None,
    issues: list[Issue],
) -> tuple[float, dict[str, float]]:
    """Weighted score over four dimensions (FR-015). Deterministic and reproducible."""
    if not columns or row_count == 0:
        return 0.0, {"completeness": 0.0, "uniqueness": 0.0, "validity": 0.0, "consistency": 0.0}

    completeness = 1.0 - sum(c.null_rate for c in columns) / len(columns)
    uniqueness = 1.0 - min(1.0, (duplicate_rows + (key_duplicate_rows or 0)) / max(1, row_count))

    blocking = sum(1 for i in issues if i.severity == "blocking")
    warnings = sum(1 for i in issues if i.severity == "warning")
    validity = max(0.0, 1.0 - (0.25 * blocking + 0.05 * warnings))

    typed = sum(1 for c in columns if c.semantic_type != "unknown")
    consistency = typed / len(columns)

    dims = {
        "completeness": round(completeness, 4),
        "uniqueness": round(uniqueness, 4),
        "validity": round(validity, 4),
        "consistency": round(consistency, 4),
    }
    score = 0.35 * completeness + 0.25 * uniqueness + 0.30 * validity + 0.10 * consistency
    return round(max(0.0, min(1.0, score)), 4), dims


# --- Version comparison (FR-015) ----------------------------------------------


def compare_profiles(current: Profile, previous: Profile) -> tuple[dict[str, Any], dict[str, float]]:
    """Schema diff plus population stability against the previous version."""
    cur_cols = {c.name: c for c in current.columns}
    prev_cols = {c.name: c for c in previous.columns}

    added = sorted(set(cur_cols) - set(prev_cols))
    removed = sorted(set(prev_cols) - set(cur_cols))
    retyped = [
        {
            "column": name,
            "from": prev_cols[name].physical_type,
            "to": cur_cols[name].physical_type,
        }
        for name in sorted(set(cur_cols) & set(prev_cols))
        if cur_cols[name].physical_type != prev_cols[name].physical_type
    ]
    # A rename shows up as one added and one removed column with the same type and a similar
    # distribution; the pairing is only a proposal and always needs approval (ATC-06).
    rename_candidates = [
        {"from": r, "to": a}
        for r in removed
        for a in added
        if prev_cols[r].physical_type == cur_cols[a].physical_type
    ]

    diff = {
        "added_columns": added,
        "removed_columns": removed,
        "retyped_columns": retyped,
        "rename_candidates": rename_candidates,
        "row_count_delta": current.row_count - previous.row_count,
    }

    psi: dict[str, float] = {}
    for name in set(cur_cols) & set(prev_cols):
        c, p = cur_cols[name], prev_cols[name]
        if c.quantiles and p.quantiles:
            psi[name] = round(_quantile_psi(p.quantiles, c.quantiles), 4)
    return diff, psi


def _quantile_psi(expected: dict[str, float], actual: dict[str, float]) -> float:
    """Cheap stability signal from stored quantiles; the full PSI uses binned counts."""
    keys = sorted(set(expected) & set(actual))
    if not keys:
        return 0.0
    total = 0.0
    for k in keys:
        e, a = expected[k], actual[k]
        if e is None or a is None:
            continue
        denom = abs(e) + 1e-9
        total += abs(a - e) / denom
    return total / len(keys)


def read_for_profiling(spark: SparkSession, uri: str, fmt: str) -> DataFrame:
    """Read a registered version the way profiling needs it: permissive, schema inferred."""
    path = uri[len("file://") :] if uri.startswith("file://") else uri
    reader = spark.read.format("parquet" if fmt == "parquet" else fmt)
    if fmt == "csv":
        reader = (
            reader.option("header", "true")
            .option("inferSchema", "true")
            .option("mode", "PERMISSIVE")
            .option("columnNameOfCorruptRecord", "_corrupt_record")
        )
    elif fmt in ("json", "jsonl"):
        reader = spark.read.format("json").option("mode", "PERMISSIVE")
        if fmt == "json":
            reader = reader.option("multiLine", "true")
    return reader.load(path)
