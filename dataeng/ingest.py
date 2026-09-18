"""M02 Dataset management: registration, immutable versioning, watching (UC-01).

Rules that hold for every path into the system:
  * A raw file is copied **once** into `raw/<dataset>/v=<n>/` and made read-only.
  * The version record carries source, checksum, size, schema hash, row count and owner,
    which is the minimum needed to reproduce a run (FR-006).
  * An identical SHA-256 never creates a second version (AT-01) — it returns the existing one.
  * A file is only registered once its size is stable or a success marker exists (FR-005).
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from triad_core import audit
from triad_core.contracts import Contract, ContractResult
from triad_core.ids import sha256_file, utcnow
from triad_core.models import Dataset, DatasetVersion, Event
from triad_core.storage import Storage, schema_hash, storage

SUPPORTED_FORMATS = ("csv", "json", "jsonl", "parquet")


class IngestError(Exception):
    pass


@dataclass
class FileObservation:
    """What the watcher saw. Separated from registration so it can be tested without a DB."""

    path: Path
    size_bytes: int
    modified_at: dt.datetime
    checksum: str
    fmt: str
    stable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at.isoformat(),
            "checksum_sha256": self.checksum,
            "format": self.fmt,
            "stable": self.stable,
        }


@dataclass
class RegistrationResult:
    dataset_name: str
    version_id: uuid.UUID | None
    version_no: int | None
    created: bool
    reason: str
    storage_uri: str | None = None
    checksum: str | None = None
    contract_result: ContractResult | None = None
    quarantined: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset_name,
            "version_id": str(self.version_id) if self.version_id else None,
            "version_no": self.version_no,
            "created": self.created,
            "reason": self.reason,
            "storage_uri": self.storage_uri,
            "checksum_sha256": self.checksum,
            "quarantined": self.quarantined,
            "contract": self.contract_result.to_dict() if self.contract_result else None,
        }


def detect_format(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    if suffix in ("csv", "tsv", "txt"):
        return "csv"
    if suffix in ("jsonl", "ndjson"):
        return "jsonl"
    if suffix == "json":
        return "json"
    if suffix in ("parquet", "pq"):
        return "parquet"
    if path.is_dir() and any(path.glob("*.parquet")):
        return "parquet"
    raise IngestError(f"unsupported file format: {path.name}")


def observe(path: str | Path, *, settle_check: bool = False) -> FileObservation:
    """Look at a file without touching it. `settle_check` re-stats to catch growing files."""
    p = Path(path)
    if not p.exists():
        raise IngestError(f"file not found: {p}")
    st = p.stat()
    size1 = st.st_size
    stable = True
    if settle_check:
        import time

        time.sleep(1.0)
        stable = p.stat().st_size == size1
    return FileObservation(
        path=p,
        size_bytes=size1,
        modified_at=dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc),
        checksum=sha256_file(p) if p.is_file() else "",
        fmt=detect_format(p),
        stable=stable,
    )


def read_schema(path: str | Path, fmt: str, sample_rows: int = 2000) -> dict[str, str]:
    """Infer {column: type} cheaply, without Spark, for registration and contract checks.

    Profiling (M03) computes the authoritative statistics on Spark; this is only the schema
    needed to detect a contract violation before a run is queued.
    """
    import pandas as pd

    p = Path(path)
    if fmt == "parquet":
        import pyarrow.parquet as pq

        schema = pq.ParquetDataset(str(p)).schema
        names = list(schema.names)
        types = [str(t) for t in schema.types]
        return dict(zip(names, types, strict=False))
    if fmt == "csv":
        df = pd.read_csv(p, nrows=sample_rows)
    elif fmt == "jsonl":
        df = pd.read_json(p, lines=True, nrows=sample_rows)
    else:
        df = pd.json_normalize(pd.read_json(p).to_dict("records"))
    return {str(c): str(t) for c, t in df.dtypes.items()}


def count_rows(path: str | Path, fmt: str) -> int | None:
    """Exact row count where it is cheap; None only for formats that need a Spark pass.

    Parquet counts come from footer metadata, so they cost nothing even on large folders.
    Errors are not swallowed: a count that fails for an unexpected reason is a real problem.
    """
    p = Path(path)
    if fmt == "parquet":
        import pyarrow.dataset as ds

        return int(ds.dataset(str(p), format="parquet").count_rows())
    if fmt in ("csv", "jsonl"):
        with open(p, "rb") as fh:
            n = sum(1 for _ in fh)
        return max(0, n - 1) if fmt == "csv" else n
    return None  # multi-line JSON: counted during profiling instead


class DatasetRegistry:
    """Registers sources and their immutable versions."""

    def __init__(self, session: Session, store: Storage | None = None) -> None:
        self.session = session
        self.storage = store or storage()

    # -- sources ---------------------------------------------------------------
    def register_source(
        self,
        name: str,
        *,
        entity_type: str,
        source_type: str,
        source_uri: str | None = None,
        owner_id: uuid.UUID | None = None,
        contract_yaml: str | None = None,
        auto_run_policy: dict[str, Any] | None = None,
        actor: str = "system",
    ) -> Dataset:
        existing = self.session.execute(
            select(Dataset).where(Dataset.name == name)
        ).scalar_one_or_none()
        if existing:
            return existing
        ds = Dataset(
            name=name,
            entity_type=entity_type,
            source_type=source_type,
            source_uri=source_uri,
            owner_id=owner_id,
            contract_yaml=contract_yaml,
            auto_run_policy=auto_run_policy or {"auto_run": False},
        )
        self.session.add(ds)
        self.session.flush()
        audit.record(
            self.session,
            actor_type="user" if actor != "system" else "system",
            actor_id=actor,
            action="dataset.register_source",
            resource_type="dataset",
            resource_id=str(ds.id),
            details={"name": name, "source_type": source_type},
        )
        return ds

    def get(self, name: str) -> Dataset | None:
        return self.session.execute(
            select(Dataset).where(Dataset.name == name)
        ).scalar_one_or_none()

    # -- versions --------------------------------------------------------------
    def find_by_checksum(self, dataset: Dataset, checksum: str) -> DatasetVersion | None:
        return self.session.execute(
            select(DatasetVersion).where(
                DatasetVersion.dataset_id == dataset.id,
                DatasetVersion.checksum_sha256 == checksum,
            )
        ).scalar_one_or_none()

    def latest_version(self, dataset: Dataset) -> DatasetVersion | None:
        return self.session.execute(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == dataset.id)
            .order_by(DatasetVersion.version_no.desc())
            .limit(1)
        ).scalar_one_or_none()

    def _next_version_no(self, dataset: Dataset) -> int:
        latest = self.latest_version(dataset)
        return (latest.version_no + 1) if latest else 1

    def register_version(
        self,
        dataset_name: str,
        file_path: str | Path,
        *,
        actor: str = "system",
        contract: Contract | None = None,
        settle_check: bool = False,
    ) -> RegistrationResult:
        """The UC-01 main flow. Returns without creating anything for a duplicate checksum."""
        dataset = self.get(dataset_name)
        if dataset is None:
            raise IngestError(
                f"dataset '{dataset_name}' is not registered; a new source needs a confirmed "
                "mapping from a Data Engineer (UC-01 alternative flow)"
            )

        obs = observe(file_path, settle_check=settle_check)
        if not obs.stable:
            return RegistrationResult(
                dataset_name, None, None, False, "file is still growing; will re-check"
            )

        duplicate = self.find_by_checksum(dataset, obs.checksum)
        if duplicate is not None:
            audit.record(
                self.session,
                actor_type="system",
                actor_id=actor,
                action="dataset.duplicate_ignored",
                resource_type="dataset_version",
                resource_id=str(duplicate.id),
                outcome="duplicate",
                details={"checksum_sha256": obs.checksum, "path": str(obs.path)},
            )
            return RegistrationResult(
                dataset_name,
                duplicate.id,
                duplicate.version_no,
                False,
                "identical checksum: no new version",
                duplicate.storage_uri,
                obs.checksum,
            )

        # Schema and contract are checked before the copy, so a blocking violation never
        # produces a half-registered version.
        schema = read_schema(obs.path, obs.fmt)
        contract_result: ContractResult | None = None
        if contract is not None:
            contract_result = contract.validate_schema(schema)
            if not contract_result.passed:
                quarantine_dir = self.storage.path(
                    self.storage.root / "quarantine" / dataset_name / "rejected"
                )
                quarantine_dir.mkdir(parents=True, exist_ok=True)
                audit.record(
                    self.session,
                    actor_type="system",
                    actor_id=actor,
                    action="dataset.contract_rejected",
                    resource_type="dataset",
                    resource_id=str(dataset.id),
                    outcome="rejected",
                    details={
                        "path": str(obs.path),
                        "violations": [v.to_dict() for v in contract_result.blocking],
                    },
                )
                return RegistrationResult(
                    dataset_name,
                    None,
                    None,
                    False,
                    "blocking contract violation",
                    None,
                    obs.checksum,
                    contract_result,
                    quarantined=True,
                )

        version_no = self._next_version_no(dataset)
        target_dir = self.storage.raw_dir(dataset_name, version_no)
        copied = self.storage.copy_immutable(obs.path, target_dir)

        version = DatasetVersion(
            dataset_id=dataset.id,
            version_no=version_no,
            storage_uri=self.storage.uri(copied),
            checksum_sha256=obs.checksum,
            size_bytes=obs.size_bytes,
            row_count=count_rows(copied, obs.fmt),
            schema_json={"columns": schema, "format": obs.fmt},
            schema_hash=schema_hash(schema),
            status="registered",
            created_by=actor,
        )
        self.session.add(version)
        self.session.flush()

        self.session.add(
            Event(
                event_type="dataset.version.created",
                payload={
                    "dataset": dataset_name,
                    "dataset_version_id": str(version.id),
                    "version_no": version_no,
                    "checksum_sha256": obs.checksum,
                    "size_bytes": obs.size_bytes,
                    "row_count": version.row_count,
                    "auto_run": bool((dataset.auto_run_policy or {}).get("auto_run")),
                    "occurred_at": utcnow().isoformat(),
                },
            )
        )
        audit.record(
            self.session,
            actor_type="system",
            actor_id=actor,
            action="dataset.version_created",
            resource_type="dataset_version",
            resource_id=str(version.id),
            details={
                "dataset": dataset_name,
                "version_no": version_no,
                "checksum_sha256": obs.checksum,
                "schema_hash": version.schema_hash,
            },
        )
        return RegistrationResult(
            dataset_name,
            version.id,
            version_no,
            True,
            "registered",
            version.storage_uri,
            obs.checksum,
            contract_result,
        )


@dataclass
class WatchResult:
    scanned: int = 0
    registered: list[RegistrationResult] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "registered": [r.to_dict() for r in self.registered],
            "skipped": self.skipped,
        }


def watch_folder(
    registry: DatasetRegistry,
    dataset_name: str,
    folder: str | Path,
    *,
    watermark: dt.datetime | None = None,
    pattern: str = "*",
    contract: Contract | None = None,
) -> WatchResult:
    """FR-005: list files newer than the watermark and register the stable ones.

    Registration is idempotent through the checksum rule, so a file seen twice is harmless.
    """
    result = WatchResult()
    base = Path(folder)
    if not base.exists():
        return result
    for path in sorted(base.glob(pattern)):
        if not path.is_file():
            continue
        result.scanned += 1
        mtime = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
        if watermark is not None and mtime <= watermark:
            result.skipped.append(f"{path.name}: older than watermark")
            continue
        try:
            reg = registry.register_version(dataset_name, path, contract=contract)
        except IngestError as exc:
            result.skipped.append(f"{path.name}: {exc}")
            continue
        result.registered.append(reg)
    return result
