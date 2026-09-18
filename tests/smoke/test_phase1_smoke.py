"""Phase-1 compatibility smoke test (PRD section 36.3) — the hard gate before feature work.

Each test maps to one row of the PRD's smoke-test table. Nothing here is mocked: a failure
means the pinned version set does not actually work together, which must be fixed before
Phase 2 rather than documented as a known issue.
"""

from __future__ import annotations

import datetime as dt
import decimal
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.smoke

REPO = Path(__file__).resolve().parents[2]


# --- 1. PySpark: session, Parquet read, profiling query -----------------------


@pytest.mark.spark
def test_pyspark_session_reads_parquet_and_profiles(spark, tmp_data_root: Path) -> None:
    from pyspark.sql import functions as F

    rows = [(f"C-{i:04d}", i % 7, float(i) * 1.5) for i in range(1000)]
    df = spark.createDataFrame(rows, "customer_id string, bucket int, amount double")
    path = str(tmp_data_root / "profile_input")
    df.write.mode("overwrite").parquet(path)

    back = spark.read.parquet(path)
    assert back.count() == 1000

    profile = back.agg(
        F.count(F.lit(1)).alias("rows"),
        F.approx_count_distinct("customer_id").alias("approx_distinct"),
        F.sum(F.when(F.col("amount").isNull(), 1).otherwise(0)).alias("null_amount"),
        F.min("amount").alias("min_amount"),
        F.max("amount").alias("max_amount"),
        F.avg("amount").alias("mean_amount"),
    ).collect()[0]

    assert profile["rows"] == 1000
    assert profile["null_amount"] == 0
    assert profile["min_amount"] == 0.0
    assert 900 <= profile["approx_distinct"] <= 1100  # within the documented error bound

    quantiles = back.approxQuantile("amount", [0.5], 0.01)
    assert len(quantiles) == 1


# --- 2. Arrow round trip: timestamps, decimals, nullable ints, booleans --------


@pytest.mark.spark
def test_arrow_round_trip_preserves_types(spark) -> None:
    """Spark -> pandas -> Spark must keep the types the PRD's type map promises (13.5)."""
    import pandas as pd
    from pyspark.sql.types import (
        BooleanType,
        DecimalType,
        LongType,
        StructField,
        StructType,
        TimestampType,
    )

    schema = StructType(
        [
            StructField("ts", TimestampType(), True),
            StructField("amount", DecimalType(18, 2), True),
            StructField("count", LongType(), True),
            StructField("flag", BooleanType(), True),
        ]
    )
    data = [
        (dt.datetime(2026, 1, 15, 12, 30, 45, 123456), decimal.Decimal("1234.56"), 42, True),
        (dt.datetime(2026, 6, 1, 0, 0, 0), decimal.Decimal("0.01"), None, None),
    ]
    df = spark.createDataFrame(data, schema)

    pdf = df.toPandas()
    assert len(pdf) == 2
    assert isinstance(pdf["amount"].iloc[0], decimal.Decimal)
    assert pdf["amount"].iloc[0] == decimal.Decimal("1234.56")
    assert pd.isna(pdf["count"].iloc[1])
    # Microsecond precision survives (outputTimestampType is pinned to TIMESTAMP_MICROS).
    assert pdf["ts"].iloc[0].microsecond == 123456

    back = spark.createDataFrame(pdf, schema)
    assert back.count() == 2
    types = dict(back.dtypes)
    assert types["ts"] == "timestamp"
    assert types["amount"] == "decimal(18,2)"
    assert types["count"] == "bigint"
    assert types["flag"] == "boolean"

    first = back.orderBy("ts").first()
    assert first["amount"] == decimal.Decimal("1234.56")
    assert first["count"] == 42


# --- 3. ANSI behaviour: try_cast is safe, plain cast fails ---------------------


@pytest.mark.spark
def test_ansi_mode_is_on_and_try_cast_is_the_safe_path(spark) -> None:
    from pyspark.errors import PySparkException

    assert spark.conf.get("spark.sql.ansi.enabled") == "true"

    df = spark.createDataFrame([("12",), ("not-a-number",), (None,)], "raw string")
    df.createOrReplaceTempView("ansi_input")

    safe = spark.sql("SELECT try_cast(raw AS INT) AS v FROM ansi_input").collect()
    assert [r["v"] for r in safe] == [12, None, None]

    with pytest.raises((PySparkException, Exception)) as exc:
        spark.sql("SELECT CAST(raw AS INT) AS v FROM ansi_input").collect()
    assert "CAST_INVALID_INPUT" in str(exc.value) or "cannot be cast" in str(exc.value).lower()

    # try_divide returns null instead of raising on division by zero.
    assert spark.sql("SELECT try_divide(1, 0) AS v").collect()[0]["v"] is None


# --- 4. XGBoost: single node, then on Spark -----------------------------------


def test_xgboost_single_node_trains() -> None:
    import numpy as np
    from sklearn.metrics import average_precision_score
    from xgboost import XGBClassifier

    rng = np.random.default_rng(42)
    n = 800
    x = rng.normal(size=(n, 6))
    y = (x[:, 0] + 0.5 * x[:, 1] + rng.normal(scale=0.4, size=n) > 0).astype(int)

    model = XGBClassifier(
        n_estimators=40, max_depth=3, tree_method="hist", eval_metric="aucpr",
        random_state=42, n_jobs=2,
    )
    model.fit(x, y)
    p = model.predict_proba(x)[:, 1]
    assert average_precision_score(y, p) > 0.8


@pytest.mark.spark
@pytest.mark.slow
def test_xgboost_spark_estimator_trains(spark) -> None:
    """The distributed training fallback (LR-06) must actually work on the pinned stack."""
    pytest.importorskip("xgboost.spark")
    from pyspark.ml.functions import array_to_vector  # noqa: F401  (import check only)
    from pyspark.sql import functions as F
    from xgboost.spark import SparkXGBClassifier

    n = 400
    df = (
        spark.range(n)
        .withColumn("f1", (F.col("id") % 17).cast("double"))
        .withColumn("f2", (F.col("id") % 5).cast("double"))
        .withColumn("label", (F.col("f1") > 8).cast("int"))
    )
    clf = SparkXGBClassifier(
        features_col=["f1", "f2"],
        label_col="label",
        num_workers=1,
        n_estimators=8,
        max_depth=3,
        missing=float("nan"),
    )
    model = clf.fit(df)
    preds = model.transform(df)
    assert preds.count() == n
    assert "prediction" in preds.columns


# --- 5. MLflow: log, register, alias, load by alias, Spark UDF -----------------


@pytest.mark.mlflow
@pytest.mark.slow
def test_mlflow_log_register_alias_load_and_spark_udf(spark, tmp_data_root: Path) -> None:
    import mlflow
    import numpy as np
    import pandas as pd
    from mlflow.tracking import MlflowClient
    from sklearn.linear_model import LogisticRegression

    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", f"file://{tmp_data_root}/mlruns")
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment("triad-smoke")

    rng = np.random.default_rng(0)
    x = pd.DataFrame(rng.normal(size=(200, 3)), columns=["f1", "f2", "f3"])
    y = (x["f1"] > 0).astype(int)
    model = LogisticRegression(max_iter=500).fit(x, y)

    name = "triad_smoke_model"
    with mlflow.start_run() as run:
        info = mlflow.sklearn.log_model(
            model, name="model", input_example=x.head(2), registered_model_name=name
        )
        mlflow.log_metric("pr_auc", 0.9)
    assert run.info.run_id

    client = MlflowClient()
    versions = client.search_model_versions(f"name='{name}'")
    latest = max(versions, key=lambda v: int(v.version))

    # CR-14: aliases, not the deprecated stages.
    client.set_registered_model_alias(name, "champion", latest.version)
    loaded = mlflow.sklearn.load_model(f"models:/{name}@champion")
    assert loaded.predict(x.head(5)).shape == (5,)

    # Alias reassignment is what makes rollback a single call (FR-076).
    alias_mv = client.get_model_version_by_alias(name, "champion")
    assert alias_mv.version == latest.version

    udf = mlflow.pyfunc.spark_udf(spark, info.model_uri, result_type="double", env_manager="local")
    sdf = spark.createDataFrame(x.head(20))
    scored = sdf.withColumn("score", udf("f1", "f2", "f3"))
    assert scored.select("score").count() == 20


# --- 6. LangGraph: interrupt, PostgreSQL checkpoint, resume -------------------


@pytest.mark.postgres
@pytest.mark.slow
def test_langgraph_interrupt_and_resume_with_postgres_checkpointer(postgres_available) -> None:
    """UC-17's pause/resume mechanism: the run must survive leaving the process."""
    if not postgres_available:
        pytest.skip("postgres service is not reachable")

    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt
    from typing_extensions import TypedDict

    dsn = os.environ.get(
        "TRIAD_DB_DSN", "postgresql://triad:triad_dev_pw@postgres:5432/triad"
    )

    class State(TypedDict, total=False):
        steps: list[str]
        decision: str

    def plan(state: State) -> State:
        return {"steps": [*state.get("steps", []), "planned"]}

    def approval(state: State) -> State:
        # The interrupt must be the first thing the node does: LangGraph restarts the node
        # from its beginning on resume (PRD section 12.1).
        decision = interrupt({"question": "promote?"})
        return {"decision": decision}

    def apply(state: State) -> State:
        return {"steps": [*state.get("steps", []), f"applied:{state.get('decision')}"]}

    with PostgresSaver.from_conn_string(dsn) as saver:
        saver.setup()
        graph = StateGraph(State)
        graph.add_node("plan", plan)
        graph.add_node("approval", approval)
        graph.add_node("apply", apply)
        graph.add_edge(START, "plan")
        graph.add_edge("plan", "approval")
        graph.add_edge("approval", "apply")
        graph.add_edge("apply", END)
        app = graph.compile(checkpointer=saver)

        thread = {"configurable": {"thread_id": "smoke-thread-1"}}
        result = app.invoke({"steps": []}, thread)
        assert "__interrupt__" in result, "graph did not pause at the approval interrupt"

        state = app.get_state(thread)
        assert state.next, "checkpoint did not record a pending node"

    # A fresh saver proves the checkpoint really is in PostgreSQL, not process memory.
    with PostgresSaver.from_conn_string(dsn) as saver2:
        graph2 = StateGraph(State)
        graph2.add_node("plan", plan)
        graph2.add_node("approval", approval)
        graph2.add_node("apply", apply)
        graph2.add_edge(START, "plan")
        graph2.add_edge("plan", "approval")
        graph2.add_edge("approval", "apply")
        graph2.add_edge("apply", END)
        app2 = graph2.compile(checkpointer=saver2)

        thread = {"configurable": {"thread_id": "smoke-thread-1"}}
        final = app2.invoke(Command(resume="approved"), thread)
        assert final["decision"] == "approved"
        assert final["steps"] == ["planned", "applied:approved"]


# --- 7. Storage: shared volume write, cross-container read, permissions -------


def test_shared_storage_write_and_manifest(tmp_data_root: Path) -> None:
    from triad_core.storage import Manifest, Storage

    st = Storage(tmp_data_root)
    staging = st.staging_dir("run-smoke-001", "features")
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "part-0000.json").write_text('{"customer_id": "c1"}', encoding="utf-8")

    target = st.features_dir("churn_features", 1)
    manifest = Manifest(
        kind="features",
        name="churn_features",
        version="fsv=1",
        created_at="2026-09-18T00:00:00Z",
        run_id="run-smoke-001",
        row_count=1,
        schema={"customer_id": "string"},
    )
    committed = st.commit(staging, target, manifest)
    assert (committed / "_SUCCESS").exists()
    assert (committed / "_manifest.json").exists()

    ok, reason = st.verify(committed)
    assert ok, reason

    read_back = st.read_manifest(committed)
    assert read_back.row_count == 1
    assert read_back.content_hash


@pytest.mark.slow
def test_shared_volume_is_visible_across_containers() -> None:
    """Two containers on the same named volume must see each other's writes (PRD 36.3)."""
    if not _docker_available():
        pytest.skip("docker CLI is not available inside this container")

    volume = "triad-smoke-vol"
    subprocess.run(["docker", "volume", "create", volume], check=True, capture_output=True)
    try:
        subprocess.run(
            ["docker", "run", "--rm", "-v", f"{volume}:/data", "triad-base:0.1",
             "bash", "-c", "echo cross-container > /data/probe.txt"],
            check=True, capture_output=True, timeout=300,
        )
        out = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{volume}:/data", "triad-base:0.1",
             "bash", "-c", "cat /data/probe.txt"],
            check=True, capture_output=True, text=True, timeout=300,
        )
        assert out.stdout.strip() == "cross-container"
    finally:
        subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True)


def _docker_available() -> bool:
    try:
        subprocess.run(["docker", "version"], capture_output=True, timeout=20)
        return True
    except Exception:
        return False


# --- 8. Scala: compile, package, spark-submit --------------------------------


@pytest.mark.slow
def test_scala_job_compiles_packages_and_runs_on_spark(tmp_data_root: Path) -> None:
    """The full Scala path: sbt package, then spark-submit the thin JAR (PRD 36.3)."""
    sbt = Path(os.environ.get("SBT_HOME", "/opt/sbt")) / "bin" / "sbt"
    spark_submit = Path(os.environ.get("SPARK_HOME", "/opt/spark")) / "bin" / "spark-submit"
    if not sbt.exists() or not spark_submit.exists():
        pytest.skip("sbt or spark-submit not present (run inside triad-base)")

    scala_dir = REPO / "scala_spark"
    build = subprocess.run(
        [str(sbt), "-batch", "-Dsbt.server.forcestart=true", "core/package", "jobs/package"],
        cwd=scala_dir, capture_output=True, text=True, timeout=3600,
    )
    assert build.returncode == 0, f"sbt package failed:\n{build.stdout[-4000:]}\n{build.stderr[-2000:]}"

    jars = sorted((scala_dir / "jobs" / "target").glob("scala-2.13/*.jar"))
    assert jars, "no JAR produced by sbt package"
    jar = jars[-1]
    core_jars = sorted((scala_dir / "core" / "target").glob("scala-2.13/*.jar"))
    assert core_jars, "no core JAR produced by sbt package"
    core_jar = core_jars[-1]

    # Build a tiny Parquet input for the feature job. write_parquet coerces timestamps to
    # microseconds; Spark 4 rejects the nanosecond default (see triad_core/parquet_io.py).
    import pandas as pd

    from triad_core.parquet_io import write_parquet

    src = tmp_data_root / "transactions"
    write_parquet(
        pd.DataFrame(
            {
                "order_id": ["O1", "O2", "O3"],
                "line_id": [1, 1, 1],
                "customer_id": ["C1", "C1", "C2"],
                "order_date": pd.to_datetime(["2026-01-05", "2026-02-10", "2026-01-20"]),
                "product_id": ["P1", "P2", "P1"],
                "quantity": [2, 1, 5],
                "price": [10.0, 20.0, 4.0],
                "discount": [0.0, 5.0, 0.0],
                "promo_code": [None, "X", None],
            }
        ),
        src,
    )

    staging = tmp_data_root / "staging_features"
    out = tmp_data_root / "features"
    report = tmp_data_root / "report.json"

    run = subprocess.run(
        [
            str(spark_submit),
            "--master", "local[2]",
            "--class", "triad.jobs.FeatureJob",
            "--jars", str(core_jar),   # thin JARs: core travels alongside, Spark stays provided
            "--conf", "spark.ui.enabled=false",
            "--conf", "spark.driver.memory=1g",
            str(jar),
            "--run-key=run-smoke-001",
            f"--input=transactions:file://{src}",
            f"--output=file://{out}",
            f"--staging=file://{staging}",
            f"--report={report}",
            "--param=max_window_days=365",
            "--param=first_cutoff=2026-01-01",
            "--param=last_cutoff=2026-06-01",
            "--shuffle-partitions=4",
        ],
        capture_output=True, text=True, timeout=1800,
    )
    assert run.returncode == 0, f"spark-submit failed:\n{run.stdout[-4000:]}\n{run.stderr[-3000:]}"
    assert report.exists(), "job did not write its structured report"

    import json

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "succeeded"
    assert payload["row_count"] > 0
    assert "recency_days" in payload["schema"]
    assert (staging / "_manifest.json").exists()
