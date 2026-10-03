"""B3: Spark and Python without agents (PRD section 32.1).

Hand-written PySpark for the feature build, scikit-learn for the modelling, run as a fixed DAG
with no repair loop and no governance. This is the closest baseline to TRIAD and therefore the
one that matters most: P and B3 use the same engine for the heavy work, so a wall-clock
difference between them is the cost of the agentic loop rather than the cost of Spark.

"No repair loop" is the defining property and it is honoured literally: every stage either
succeeds or the run stops. There is no retry, no diagnosis, no playbook, no attempt budget. A
stage that fails on a shuffle or a bad cast fails the run, which is exactly what this baseline
is for - it is what the repair loop is being compared against.

Same feature subset as B2 (see `baselines.BASELINE_FEATURES`), same model shortlist, same seed,
same split. The only difference from B2 is the engine, which is the independent variable
between those two configurations.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pandas as pd

from experiments.baselines import (
    BASELINE_FEATURES,
    CATEGORICAL,
    HORIZON_DAYS,
    LABEL,
    WINDOW_DAYS,
    BaselineResult,
    fit_and_select,
    peak_rss_mb,
)


def _read(spark: Any, root: Path, table: str) -> Any:
    path = root / table
    return spark.read.parquet(f"file://{path}") if path.exists() else None


def build_features(spark: Any, root: Path, cutoffs: list[pd.Timestamp]) -> pd.DataFrame:
    """The same twelve features as B2, computed in Spark SQL and collected once at the end.

    The whole cutoff set is computed in one pass by cross-joining customers with the cutoff
    list, which is how a Spark engineer would write it: a per-cutoff Python loop issuing one job
    each would measure the loop rather than the engine.
    """
    from pyspark.sql import functions as F

    customers = _read(spark, root, "customers")
    tx = _read(spark, root, "transactions")
    products = _read(spark, root, "products")
    sessions = _read(spark, root, "web_sessions")
    tickets = _read(spark, root, "support_tickets")
    returns = _read(spark, root, "returns")

    cutoff_df = spark.createDataFrame(
        [(pd.Timestamp(c).to_pydatetime(),) for c in cutoffs], ["snapshot_date"]
    )

    # `try_cast` is a Column method in PySpark, not a function on `pyspark.sql.functions`;
    # `F.try_cast` does not exist and this baseline failed on its first stage with an
    # AttributeError. `try_divide` below *is* a function, which is what made the mistake easy.
    tx = (
        tx.withColumn("price", F.col("price").try_cast("double"))
        .withColumn("quantity", F.col("quantity").try_cast("double"))
        .withColumn("discount", F.coalesce(F.col("discount").try_cast("double"), F.lit(0.0)))
    )
    tx = tx.withColumn("gross", F.col("price") * F.abs(F.col("quantity")))
    tx = tx.withColumn("net", F.col("gross") - F.col("discount"))
    if products is not None:
        tx = tx.join(products.select("product_id", "category_l1"), "product_id", "left")
    else:
        tx = tx.withColumn("category_l1", F.lit(None).cast("string"))

    base = customers.select(
        "customer_id", "signup_date", *CATEGORICAL
    ).crossJoin(cutoff_df)

    # --- transaction windows -------------------------------------------------------------
    joined = base.join(tx.select(
        "customer_id", "order_id", "order_date", "quantity", "net", "gross", "discount",
        "category_l1",
    ), "customer_id", "left").where(
        F.col("order_date").isNull() | (F.col("order_date") < F.col("snapshot_date"))
    )
    in_365 = F.col("order_date") >= F.expr(f"snapshot_date - INTERVAL {WINDOW_DAYS} DAYS")
    in_90 = F.col("order_date") >= F.expr("snapshot_date - INTERVAL 90 DAYS")

    agg = joined.groupBy("customer_id", "snapshot_date", "signup_date", *CATEGORICAL).agg(
        F.max("order_date").alias("last_order"),
        F.countDistinct(F.when(in_90, F.col("order_id"))).alias("frequency_90d"),
        F.countDistinct(F.when(in_365, F.col("order_id"))).alias("frequency_365d"),
        F.sum(F.when(in_365, F.col("net"))).alias("monetary_365d"),
        F.sum(F.when(in_365, F.abs(F.col("quantity")))).alias("units_365d"),
        F.sum(F.when(in_365, F.col("discount"))).alias("discount_365d"),
        F.sum(F.when(in_365, F.col("gross"))).alias("gross_365d"),
        F.countDistinct(F.when(in_365, F.col("category_l1"))).alias("category_count_365d"),
    )
    agg = (
        agg.withColumn("recency_days", F.datediff("snapshot_date", "last_order"))
        .withColumn("tenure_days", F.datediff("snapshot_date", "signup_date"))
        .withColumn("monetary_365d", F.coalesce("monetary_365d", F.lit(0.0)))
        .withColumn(
            "aov_365d",
            F.try_divide(F.col("monetary_365d"), F.col("frequency_365d")),
        )
        .withColumn(
            "items_per_order_365d",
            F.try_divide(F.col("units_365d"), F.col("frequency_365d")),
        )
        .withColumn(
            "discount_share_365d",
            F.try_divide(F.col("discount_365d"), F.col("gross_365d")),
        )
    )

    # --- the other three sources ---------------------------------------------------------
    def window_count(frame: Any, ts_col: str, id_col: str, days: int, name: str) -> Any:
        if frame is None:
            return None
        pair = base.select("customer_id", "snapshot_date").join(
            frame.select("customer_id", F.col(ts_col).alias("ts"), F.col(id_col).alias("k")),
            "customer_id", "left",
        ).where(
            F.col("ts").isNull()
            | (
                (F.col("ts") < F.col("snapshot_date"))
                & (F.col("ts") >= F.expr(f"snapshot_date - INTERVAL {days} DAYS"))
            )
        )
        return pair.groupBy("customer_id", "snapshot_date").agg(
            F.countDistinct("k").alias(name)
        )

    for frame, ts_col, id_col, days, name in (
        (sessions, "session_start", "session_id", 90, "sessions_90d"),
        (tickets, "created_at", "ticket_id", WINDOW_DAYS, "tickets_365d"),
        (returns, "return_date", "return_id", WINDOW_DAYS, "returns_365d"),
    ):
        counted = window_count(frame, ts_col, id_col, days, name)
        if counted is None:
            agg = agg.withColumn(name, F.lit(0.0))
        else:
            agg = agg.join(counted, ["customer_id", "snapshot_date"], "left").withColumn(
                name, F.coalesce(F.col(name), F.lit(0.0))
            )

    # --- the label: the only forward-looking expression in this job -----------------------
    horizon = base.select("customer_id", "snapshot_date").join(
        tx.select("customer_id", F.col("order_date").alias("ts")), "customer_id", "left"
    ).where(
        F.col("ts").isNull()
        | (
            (F.col("ts") >= F.col("snapshot_date"))
            & (F.col("ts") < F.expr(f"snapshot_date + INTERVAL {HORIZON_DAYS} DAYS"))
        )
    ).groupBy("customer_id", "snapshot_date").agg(
        F.count("ts").alias("orders_in_horizon")
    )
    agg = agg.join(horizon, ["customer_id", "snapshot_date"], "left").withColumn(
        LABEL, F.when(F.coalesce(F.col("orders_in_horizon"), F.lit(0)) > 0, 0).otherwise(1)
    )

    # A customer with no pre-cutoff order is not a churn candidate, as in B2.
    agg = agg.where(F.col("recency_days").isNotNull())

    keep = ["customer_id", "snapshot_date", LABEL, *BASELINE_FEATURES, *CATEGORICAL]
    return agg.select(*[c for c in keep if c in agg.columns]).toPandas()


def run(
    spark: Any, root: Path, cutoffs: dict[str, list[pd.Timestamp]], *, seed: int = 42
) -> BaselineResult:
    """A fixed DAG: features, split, screen, score. Any failure stops the run."""
    started = time.perf_counter()
    stages: dict[str, float] = {}
    try:
        t0 = time.perf_counter()
        features = build_features(
            spark, root, sorted({c for group in cutoffs.values() for c in group})
        )
        stages["features_spark"] = round(time.perf_counter() - t0, 2)

        features["snapshot_date"] = pd.to_datetime(features["snapshot_date"])
        feature_cols = [c for c in features.columns
                        if c not in ("customer_id", "snapshot_date", LABEL)]
        parts = {
            name: features[features["snapshot_date"].isin([pd.Timestamp(c) for c in group])]
            for name, group in cutoffs.items()
        }
        if any(len(p) == 0 for p in parts.values()):
            return BaselineResult(
                "B3", False, time.perf_counter() - started,
                detail="a split came out empty: "
                       + ", ".join(f"{k}={len(v)}" for k, v in parts.items()),
                per_stage_s=stages,
            )

        t0 = time.perf_counter()
        outcome = fit_and_select(
            parts["train"], parts["valid"], parts["test"], feature_cols, seed=seed
        )
        stages["train_and_select"] = round(time.perf_counter() - t0, 2)

        return BaselineResult(
            "B3", True, time.perf_counter() - started,
            rows_trained=len(parts["train"]),
            rows_tested=len(parts["test"]),
            test_pr_auc=outcome["test_pr_auc"],
            test_roc_auc=outcome["test_roc_auc"],
            test_brier=outcome["test_brier"],
            selected_family=outcome["selected_family"],
            families_tried=[f["family"] for f in outcome["families_tried"]],
            human_actions=0,
            llm_calls=0,
            peak_rss_mb=peak_rss_mb(),
            detail=f"selected {outcome['selected_family']} on validation PR-AUC "
                   f"{outcome['valid_pr_auc']}",
            per_stage_s=stages,
        )
    except Exception as exc:
        # No repair loop: the stage that failed ends the run, and that is the result.
        return BaselineResult(
            "B3", False, time.perf_counter() - started,
            detail=f"the fixed DAG stopped at the failing stage, with no repair attempted: "
                   f"{type(exc).__name__}: {exc}",
            peak_rss_mb=peak_rss_mb(), per_stage_s=stages,
        )


def source_lines() -> int:
    """Hand-written pipeline lines, excluding comments and blanks (32.2 coding effort)."""
    return sum(
        1 for line in Path(__file__).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    )



def run_with_own_session(
    root: Path,
    cutoffs: dict[str, list[pd.Timestamp]],
    *,
    seed: int = 42,
    cores: int = 2,
    driver_memory: str = "1500m",
) -> BaselineResult:
    """B3 in a SparkSession of its own, for `run_isolated`: start, run, read the JVM's peak, stop."""
    from pyspark.sql import SparkSession

    from experiments.baselines import jvm_peak_rss_mb

    spark = (
        SparkSession.builder.appName("triad-b3-isolated")
        .master(f"local[{cores}]")
        .config("spark.sql.ansi.enabled", "true")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.parquet.outputTimestampType", "TIMESTAMP_MICROS")
        .config("spark.ui.enabled", "false")
        .config("spark.driver.memory", driver_memory)
        .getOrCreate()
    )
    try:
        result = run(spark, root, cutoffs, seed=seed)
        result.jvm_peak_rss_mb = jvm_peak_rss_mb(spark)
        return result
    finally:
        spark.stop()
