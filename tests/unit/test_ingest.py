"""TC-001..TC-007 / AT-01: ingestion, immutable versioning and duplicate detection."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from dataeng.ingest import (
    DatasetRegistry,
    IngestError,
    count_rows,
    detect_format,
    observe,
    read_schema,
    watch_folder,
)
from triad_core.contracts import Contract
from triad_core.parquet_io import write_parquet
from triad_core.storage import StorageError


def _sample_frame(rows: int = 100) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": [f"O{i}" for i in range(rows)],
            "line_id": [1] * rows,
            "customer_id": [f"C{i % 10}" for i in range(rows)],
            "order_date": pd.to_datetime(["2026-01-01"] * rows),
            "quantity": [2] * rows,
            "price": [9.99] * rows,
        }
    )


# --- format detection and observation (no database needed) --------------------


def test_detect_format_recognises_the_supported_formats(tmp_path: Path) -> None:
    assert detect_format(tmp_path / "a.csv") == "csv"
    assert detect_format(tmp_path / "a.jsonl") == "jsonl"
    assert detect_format(tmp_path / "a.json") == "json"
    assert detect_format(tmp_path / "a.parquet") == "parquet"
    with pytest.raises(IngestError):
        detect_format(tmp_path / "a.xlsx")


def test_observe_reports_checksum_and_size(tmp_path: Path) -> None:
    f = tmp_path / "t.csv"
    _sample_frame(10).to_csv(f, index=False)
    obs = observe(f)
    assert obs.size_bytes > 0
    assert len(obs.checksum) == 64
    assert obs.fmt == "csv"
    assert obs.stable

    # The same content always hashes the same; different content never does.
    again = observe(f)
    assert again.checksum == obs.checksum
    _sample_frame(11).to_csv(f, index=False)
    assert observe(f).checksum != obs.checksum


def test_read_schema_and_row_count_for_csv_and_parquet(tmp_path: Path) -> None:
    csv_path = tmp_path / "t.csv"
    _sample_frame(50).to_csv(csv_path, index=False)
    schema = read_schema(csv_path, "csv")
    assert "order_id" in schema and "price" in schema
    assert count_rows(csv_path, "csv") == 50

    pq_dir = tmp_path / "pq"
    write_parquet(_sample_frame(37), pq_dir)
    pq_schema = read_schema(pq_dir, "parquet")
    assert "customer_id" in pq_schema
    assert count_rows(pq_dir, "parquet") == 37


# --- registration (needs the database) ----------------------------------------


@pytest.mark.postgres
class TestRegistration:
    @pytest.fixture(autouse=True)
    def _db(self, postgres_available, db_session, storage):
        if not postgres_available:
            pytest.skip("postgres service is not reachable")
        self.session = db_session
        self.storage = storage
        self.registry = DatasetRegistry(db_session, storage)

    def test_register_source_is_idempotent(self) -> None:
        a = self.registry.register_source(
            "tx_unit", entity_type="transactions", source_type="csv"
        )
        b = self.registry.register_source(
            "tx_unit", entity_type="transactions", source_type="csv"
        )
        assert a.id == b.id

    def test_identical_checksum_creates_no_second_version(self, tmp_path: Path) -> None:
        """AT-01: re-uploading the same file must create nothing."""
        self.registry.register_source("tx_dup", entity_type="transactions", source_type="csv")
        f = tmp_path / "day1.csv"
        _sample_frame(20).to_csv(f, index=False)

        first = self.registry.register_version("tx_dup", f)
        assert first.created is True
        assert first.version_no == 1
        assert first.checksum

        # Same bytes, different filename: still a duplicate.
        f2 = tmp_path / "day1_copy.csv"
        f2.write_bytes(f.read_bytes())
        second = self.registry.register_version("tx_dup", f2)
        assert second.created is False
        assert second.version_no == 1
        assert "identical checksum" in second.reason

        # Changed content does create a version.
        f3 = tmp_path / "day2.csv"
        _sample_frame(21).to_csv(f3, index=False)
        third = self.registry.register_version("tx_dup", f3)
        assert third.created is True
        assert third.version_no == 2

    def test_registered_copy_is_immutable(self, tmp_path: Path) -> None:
        self.registry.register_source("tx_immutable", entity_type="transactions", source_type="csv")
        f = tmp_path / "in.csv"
        _sample_frame(5).to_csv(f, index=False)
        result = self.registry.register_version("tx_immutable", f)

        stored = self.storage.path(result.storage_uri)
        assert stored.exists()
        # Re-registering the same path into the same version folder is refused.
        with pytest.raises(StorageError, match="immutable"):
            self.storage.copy_immutable(f, stored.parent)

    def test_version_record_carries_reproduction_metadata(self, tmp_path: Path) -> None:
        self.registry.register_source("tx_meta", entity_type="transactions", source_type="parquet")
        d = tmp_path / "pq"
        write_parquet(_sample_frame(30), d / "part-00000.parquet")
        result = self.registry.register_version("tx_meta", d / "part-00000.parquet")

        from triad_core.models import DatasetVersion

        version = self.session.get(DatasetVersion, result.version_id)
        assert version.checksum_sha256 and len(version.checksum_sha256) == 64
        assert version.size_bytes > 0
        assert version.row_count == 30
        assert version.schema_hash
        assert version.schema_json["format"] == "parquet"
        assert version.created_by

    def test_blocking_contract_violation_stops_registration(self, tmp_path: Path) -> None:
        """FR-007: a missing required column stops the run before anything is copied."""
        self.registry.register_source("tx_contract", entity_type="transactions", source_type="csv")
        contract = Contract.from_dict(
            {
                "contract": "transactions",
                "version": "1.0",
                "columns": [
                    {"name": "order_id", "type": "string", "required": True},
                    {"name": "customer_id", "type": "string", "required": True},
                    {"name": "order_date", "type": "timestamp", "required": True},
                ],
            }
        )
        bad = tmp_path / "bad.csv"
        pd.DataFrame({"order_id": ["O1"], "order_date": ["2026-01-01"]}).to_csv(bad, index=False)

        result = self.registry.register_version("tx_contract", bad, contract=contract)
        assert result.created is False
        assert result.quarantined is True
        assert any("customer_id" in v.message for v in result.contract_result.blocking)

    def test_watch_folder_registers_new_files_only(self, tmp_path: Path) -> None:
        """FR-005: files newer than the watermark are registered; older ones are skipped."""
        self.registry.register_source("tx_watch", entity_type="transactions", source_type="csv")
        folder = tmp_path / "landing"
        folder.mkdir()
        (folder / "a.csv").write_text(_sample_frame(3).to_csv(index=False), encoding="utf-8")
        (folder / "b.csv").write_text(_sample_frame(4).to_csv(index=False), encoding="utf-8")

        result = watch_folder(self.registry, "tx_watch", folder, pattern="*.csv")
        assert result.scanned == 2
        assert sum(1 for r in result.registered if r.created) == 2

        # Second sweep: same files, no new versions.
        again = watch_folder(self.registry, "tx_watch", folder, pattern="*.csv")
        assert all(r.created is False for r in again.registered)

        future = dt.datetime.now(dt.UTC) + dt.timedelta(days=1)
        skipped = watch_folder(self.registry, "tx_watch", folder, watermark=future, pattern="*.csv")
        assert skipped.registered == []
        assert len(skipped.skipped) == 2

    def test_unknown_dataset_is_refused(self, tmp_path: Path) -> None:
        f = tmp_path / "x.csv"
        _sample_frame(2).to_csv(f, index=False)
        with pytest.raises(IngestError, match="not registered"):
            self.registry.register_version("never_registered", f)

    def test_registration_writes_event_and_audit_entries(self, tmp_path: Path) -> None:
        from sqlalchemy import select

        from triad_core.models import AuditLog, Event

        self.registry.register_source("tx_events", entity_type="transactions", source_type="csv")
        f = tmp_path / "e.csv"
        _sample_frame(6).to_csv(f, index=False)
        result = self.registry.register_version("tx_events", f)

        events = self.session.execute(
            select(Event).where(Event.event_type == "dataset.version.created")
        ).scalars().all()
        assert any(e.payload.get("dataset_version_id") == str(result.version_id) for e in events)

        entries = self.session.execute(
            select(AuditLog).where(AuditLog.resource_id == str(result.version_id))
        ).scalars().all()
        assert entries, "no audit entry for the new version"


@pytest.mark.postgres
class TestContractIsEnforcedWithoutBeingAskedTo:
    """FR-007 on the path callers actually use, not only when a contract is handed in.

    `register_version` took an optional `contract` and enforced it when given one. Nothing on
    the real path gave it one - not the API, not the demo, not the experiments - so a renamed
    required column was registered, profiled and cleaned without a word. EXP-4 found it.
    """

    @pytest.fixture(autouse=True)
    def _db(self, postgres_available, db_session, storage):
        if not postgres_available:
            pytest.skip("postgres service is not reachable")
        self.session = db_session
        self.storage = storage
        self.registry = DatasetRegistry(db_session, storage)

    def test_a_renamed_required_column_is_refused_with_no_contract_argument(
        self, tmp_path: Path
    ) -> None:
        # `transactions` is the name the repo ships a contract for, so the registry finds it.
        self.registry.register_source(
            "transactions", entity_type="transactions", source_type="parquet"
        )
        frame = _sample_frame(30).rename(columns={"customer_id": "cust_id"})
        path = tmp_path / "renamed"
        write_parquet(frame, path / "part-00000.parquet")

        result = self.registry.register_version(
            "transactions", path / "part-00000.parquet", actor="test"
        )
        assert result.created is False, "a missing required column must not be registered"
        assert result.contract_result is not None
        assert not result.contract_result.passed
        names = " ".join(v.message for v in result.contract_result.blocking)
        assert "customer_id" in names

    def test_a_conforming_file_still_registers(self, tmp_path: Path) -> None:
        """The counterpart: enforcement that refuses everything would pass the test above."""
        self.registry.register_source(
            "transactions", entity_type="transactions", source_type="parquet"
        )
        path = tmp_path / "ok"
        write_parquet(_sample_frame(31), path / "part-00000.parquet")
        result = self.registry.register_version(
            "transactions", path / "part-00000.parquet", actor="test"
        )
        assert result.created is True, result.reason

    def test_a_dataset_with_no_contract_is_unaffected(self, tmp_path: Path) -> None:
        """Most datasets have no contract file; registration must not start demanding one."""
        self.registry.register_source(
            "tx_no_contract", entity_type="transactions", source_type="parquet"
        )
        path = tmp_path / "free"
        write_parquet(_sample_frame(12).drop(columns=["customer_id"]), path / "part-00000.parquet")
        result = self.registry.register_version(
            "tx_no_contract", path / "part-00000.parquet", actor="test"
        )
        assert result.created is True, result.reason


class TestTheProfileReachesTheVersionItDescribes:
    """`dataset_versions.profile_uri` and `.quality_score` were columns nothing filled.

    The profiling stage wrote the document to storage and returned its URI; the caller used the
    score for the quality gate and dropped the rest. Across 130 version rows both columns were
    NULL, which meant the one endpoint that serves a profile - `GET
    /datasets/{ref}/versions/{n}/profile`, FR-068 - refused every request it could ever be
    given, because it checks `profile_uri` first.
    """

    @pytest.fixture(autouse=True)
    def _db(self, postgres_available, db_session, storage):
        if not postgres_available:
            pytest.skip("postgres service is not reachable")
        self.session = db_session
        self.storage = storage
        self.registry = DatasetRegistry(db_session, storage)

    def _register(self, name: str, tmp_path: Path):
        self.registry.register_source(name, entity_type="transactions", source_type="parquet")
        d = tmp_path / "pq"
        write_parquet(_sample_frame(12), d / "part-00000.parquet")
        return self.registry.register_version(name, d / "part-00000.parquet")

    def _pipeline(self):
        from agents.graph.pipeline import ChurnPipeline
        from triad_core.models import PipelineRun

        run = PipelineRun(
            run_key=f"test-{dt.datetime.now(dt.UTC).timestamp()}",
            pipeline_name="churn_v1", state="RUNNING", mode="full",
            trigger_type="manual",
        )
        self.session.add(run)
        self.session.flush()
        return ChurnPipeline(self.session, run, store=self.storage)

    def test_a_profiled_version_carries_its_score_and_document(self, tmp_path: Path) -> None:
        from triad_core.models import DatasetVersion

        result = self._register("tx_profile_link", tmp_path)
        self._pipeline()._attach_profile_to_version(
            result.storage_uri,
            {"profile_uri": "file:///data/triad/profiles/test_tx.json", "quality_score": 0.91},
        )

        version = self.session.get(DatasetVersion, result.version_id)
        assert version.quality_score == pytest.approx(0.91)
        assert version.profile_uri == "file:///data/triad/profiles/test_tx.json"

    def test_profiling_a_path_that_is_not_a_registered_version_changes_nothing(
        self, tmp_path: Path
    ) -> None:
        """The experiments profile scratch folders; that must not raise or touch a row."""
        from triad_core.models import DatasetVersion

        result = self._register("tx_profile_unrelated", tmp_path)
        self._pipeline()._attach_profile_to_version(
            "file:///data/triad/raw/not_a_registered_version/v=1",
            {"profile_uri": "file:///elsewhere.json", "quality_score": 0.1},
        )

        version = self.session.get(DatasetVersion, result.version_id)
        assert version.quality_score is None
        assert version.profile_uri is None
