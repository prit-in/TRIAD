"""Shared pytest fixtures.

A single local SparkSession is reused across the session: starting a JVM per test is the
slowest thing in this suite, and Env-A-mini has little memory to spare (ADR-0002).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "smoke: Phase-1 compatibility smoke test (hard gate)")
    config.addinivalue_line("markers", "spark: needs a SparkSession")
    config.addinivalue_line("markers", "postgres: needs the postgres service")
    config.addinivalue_line("markers", "mlflow: needs the mlflow service")
    config.addinivalue_line("markers", "slow: takes more than ~30 seconds")


@pytest.fixture(scope="session")
def spark():
    """Local SparkSession with the PRD's production settings (ANSI on, UTC, micros)."""
    from pyspark.sql import SparkSession

    session = (
        SparkSession.builder.appName("triad-tests")
        .master(os.environ.get("TRIAD_TEST_SPARK_MASTER", "local[2]"))
        .config("spark.sql.ansi.enabled", "true")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.parquet.outputTimestampType", "TIMESTAMP_MICROS")
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.ui.enabled", "false")
        .config("spark.driver.memory", "1g")
        .config("spark.sql.execution.arrow.pyspark.enabled", "true")
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("WARN")
    yield session
    session.stop()


@pytest.fixture()
def tmp_data_root() -> Iterator[Path]:
    """Isolated data root so storage tests never touch the shared volume."""
    d = Path(tempfile.mkdtemp(prefix="triad-test-"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def storage(tmp_data_root: Path):
    from triad_core.storage import Storage

    return Storage(tmp_data_root)


@pytest.fixture()
def db_session():
    """A session whose work is rolled back afterwards, so tests never pollute each other.

    Everything runs inside one outer transaction; `commit()` inside the code under test hits
    a SAVEPOINT, and the outer transaction is rolled back at the end.
    """
    from sqlalchemy import event

    from triad_core.db import engine

    connection = engine().connect()
    transaction = connection.begin()
    from sqlalchemy.orm import Session

    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, trans):
        if trans.nested and not trans._parent.nested:
            sess.begin_nested()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _service_up(host: str, port: int, timeout: float = 1.5) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def postgres_available() -> bool:
    return _service_up(os.environ.get("TRIAD_PG_HOST", "postgres"), 5432)


@pytest.fixture(scope="session")
def mlflow_available() -> bool:
    return _service_up(os.environ.get("TRIAD_MLFLOW_HOST", "mlflow"), 5000)
