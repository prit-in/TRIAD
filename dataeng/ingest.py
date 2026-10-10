"""M02 Dataset management: registration, immutable versioning, watching (UC-01).

Rules that hold for every path into the system:
  * A raw file is copied **once** into `raw/<dataset>/v=<n>/` and made read-only.
  * The version record carries source, checksum, size, schema hash, row count and owner,
    which is the minimum needed to reproduce a run (FR-006).
  * An identical SHA-256 never creates a second version (AT-01) — it returns the existing one.
  * A file is only registered once its size is stable or a success marker exists (FR-005).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import shutil
import tempfile
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from triad_core import audit
from triad_core.config import settings
from triad_core.contracts import Contract, ContractResult
from triad_core.ids import sha256_file, utcnow
from triad_core.models import Approval, Dataset, DatasetVersion, Event, PipelineRun
from triad_core.storage import Storage, schema_hash, storage

SUPPORTED_FORMATS = ("csv", "json", "jsonl", "parquet")


class IngestError(Exception):
    pass


# The formats `DatasetRegistry.apply_mapping` can rewrite with only the header changed.
MAPPABLE_FORMATS = ("csv", "parquet")


def _require_columns(mapping: dict[str, str], columns: list[str]) -> None:
    absent = sorted(set(mapping) - set(columns))
    if absent:
        raise IngestError(f"the file has no column(s) {absent} to rename")


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
    # A renamed required column: {observed name: contract name}, and the approval it awaits.
    proposed_mapping: dict[str, str] | None = None
    approval_id: uuid.UUID | None = None
    approval_status: str | None = None

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
            "proposed_mapping": self.proposed_mapping,
            "approval_id": str(self.approval_id) if self.approval_id else None,
            "approval_status": self.approval_status,
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
    if not p.is_file():
        # A directory used to get this far: `checksum` fell back to "" - so every directory
        # looked like a duplicate of every other - and `copy_immutable` then raised
        # IsADirectoryError, which reached the client as a 500. Every caller of
        # `register_version` passes one file; the parameter is named `file_path`.
        raise IngestError(f"not a file: {p}; a dataset version is registered from one file")
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
        modified_at=dt.datetime.fromtimestamp(st.st_mtime, dt.UTC),
        checksum=sha256_file(p),
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
        """The version holding these bytes - or made from them by an approved mapping.

        A mapped version is stored under its renamed copy's checksum, so matching that alone
        let the original file, still in a watched folder, be refused and asked about again on
        every tick after its mapping was approved (AT-01: the same bytes create nothing).
        """
        return self.session.execute(
            select(DatasetVersion).where(
                DatasetVersion.dataset_id == dataset.id,
                (DatasetVersion.checksum_sha256 == checksum)
                | (DatasetVersion.schema_json["mapped_from"]["checksum_sha256"].astext
                   == checksum),
            ).order_by(DatasetVersion.version_no).limit(1)
        ).scalar_one_or_none()

    def latest_version(self, dataset: Dataset) -> DatasetVersion | None:
        return self.session.execute(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == dataset.id)
            .order_by(DatasetVersion.version_no.desc())
            .limit(1)
        ).scalar_one_or_none()

    def latest_uris(self, names: Iterable[str]) -> dict[str, str]:
        """Storage URI of the latest version of each named dataset; unregistered names are left out."""
        uris: dict[str, str] = {}
        for name in names:
            dataset = self.get(name)
            version = self.latest_version(dataset) if dataset is not None else None
            if version is not None and version.storage_uri:
                uris[name] = version.storage_uri
        return uris

    def _next_version_no(self, dataset: Dataset) -> int:
        """Next free version number, considering both the catalogue and the storage layout.

        A run whose transaction rolled back can leave a `v=<n>` folder on disk with no row to
        match it. Allocating purely from the catalogue would then collide with that orphan and
        the write-once copy would refuse, so the filesystem is consulted too.
        """
        latest = self.latest_version(dataset)
        from_db = latest.version_no if latest else 0

        from_disk = 0
        base = self.storage.root / "raw" / dataset.name
        if base.exists():
            for child in base.iterdir():
                if child.is_dir() and child.name.startswith("v="):
                    try:
                        from_disk = max(from_disk, int(child.name[2:]))
                    except ValueError:
                        continue
        return max(from_db, from_disk) + 1

    def contract_for(self, dataset: Dataset) -> Contract | None:
        """The dataset's ingest contract: its own stored YAML, or the repo's file for it.

        `pipelines/contracts/<name>.yaml` is the versioned source of truth in this project, so
        it is the fallback when a dataset row carries no contract of its own.
        """
        if dataset.contract_yaml:
            import yaml

            try:
                return Contract.from_dict(yaml.safe_load(dataset.contract_yaml))
            except Exception as exc:  # a malformed stored contract must not pass silently
                raise IngestError(
                    f"dataset '{dataset.name}' has an unusable stored contract: {exc}"
                ) from exc
        path = settings().repo_root / "pipelines" / "contracts" / f"{dataset.name}.yaml"
        return Contract.from_yaml(path) if path.exists() else None

    def register_version(
        self,
        dataset_name: str,
        file_path: str | Path,
        *,
        actor: str = "system",
        contract: Contract | None = None,
        settle_check: bool = False,
        run: PipelineRun | None = None,
        propose: bool = True,
    ) -> RegistrationResult:
        """The UC-01 main flow. Returns without creating anything for a duplicate checksum.

        `contract` defaults to the dataset's own contract. It used to default to *nothing*,
        and the consequence was that FR-007 - "a missing required column stops the run with a
        reason code and never reaches profiling" - was implemented here and unreachable: the
        API, the demo script and the experiments all called this without a contract, so the
        only caller that ever passed one was `watch_folder`. EXP-4 found it by renaming
        `customer_id` and watching the run profile and clean the data without complaint.

        A refused file is kept in quarantine. When every missing required column has a likely
        source under another name, the mapping is applied at once if the contract lists those
        names as aliases, and otherwise proposed: a `schema.change` approval is requested on
        `run` (or on an ingestion run made for it) and nothing is registered before a decision
        (PRD 20.2, ATC-06). A mapping is proposed only for a format `apply_mapping` can rewrite,
        and a copy made by a mapping (`propose=False`) is never mapped again.
        """
        dataset = self.get(dataset_name)
        if dataset is None:
            raise IngestError(
                f"dataset '{dataset_name}' is not registered; a new source needs a confirmed "
                "mapping from a Data Engineer (UC-01 alternative flow)"
            )
        if contract is None:
            contract = self.contract_for(dataset)

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
                # The result has always said `quarantined`; the file is now actually kept, so
                # an approved mapping has the bytes it was decided on.
                kept = self._keep_refused(dataset_name, obs)
                proposals = contract.propose_mappings(schema)
                missing = {v.check_id.removeprefix("schema.required.")
                           for v in contract_result.blocking}
                renamed = bool(proposals) and {p["column"] for p in proposals} == missing
                # Only a format apply_mapping can rewrite gets a proposal: an approval nobody
                # can carry out would 422 on every attempt and stay pending until it expired.
                mappable = propose and obs.fmt in MAPPABLE_FORMATS
                mapping = ({p["source"]: p["column"] for p in proposals}
                           if renamed and mappable else None)
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
                        "kept_as": self.storage.uri(kept),
                        "violations": [v.to_dict() for v in contract_result.blocking],
                        "proposed_mapping": mapping,
                        "candidates": proposals if renamed and not mapping else None,
                    },
                )
                if mapping and all(p["pre_approved"] for p in proposals):
                    return self.apply_mapping(dataset_name, kept, mapping, actor=actor,
                                              basis="pre-approved alias",
                                              original_checksum=obs.checksum)
                approval = self._request_mapping_approval(
                    dataset, obs, kept, schema, proposals, actor=actor, run=run
                ) if mapping else None
                if approval is None:
                    reason = "blocking contract violation"
                elif approval.status == "pending":
                    reason = "required column renamed: mapping proposed, awaiting approval"
                else:
                    # Decided already, and not approved: a rejection sticks for these bytes.
                    reason = f"required column renamed: the proposed mapping was {approval.status}"
                return RegistrationResult(
                    dataset_name,
                    None,
                    None,
                    False,
                    reason,
                    None,
                    obs.checksum,
                    contract_result,
                    quarantined=True,
                    proposed_mapping=mapping,
                    approval_id=approval.id if approval else None,
                    approval_status=approval.status if approval else None,
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

    # -- renamed columns (PRD 20.2) ----------------------------------------------
    def _keep_refused(self, dataset_name: str, obs: FileObservation) -> Path:
        target = self.storage.root / "quarantine" / dataset_name / "rejected" / obs.checksum[:16]
        kept = self.storage.path(target) / obs.path.name
        return kept if kept.exists() else self.storage.copy_immutable(obs.path, target)

    def _request_mapping_approval(
        self, dataset: Dataset, obs: FileObservation, kept: Path, schema: dict[str, str],
        proposals: list[dict[str, Any]], *, actor: str, run: PipelineRun | None,
    ) -> Approval:
        from agents.graph import queue as q
        from triad_core.governance import EvidenceBundle, GovernanceService
        from triad_core.policy import PolicyEngine

        target = f"{dataset.name}@sha256:{obs.checksum[:16]}"
        # One question per file. A pending request is the same question; a rejected or expired
        # one is its answer, which the next tick of a watched folder must not ask again. (An
        # approved one has registered a version, which find_by_checksum has already returned.)
        asked = self.session.execute(
            select(Approval).where(Approval.target_ref == target,
                                   Approval.status.in_(("pending", "rejected", "expired")))
            .order_by(Approval.requested_at.desc())
        ).scalars().first()
        if asked is not None:
            return asked
        if run is None:
            run = q.create_run(
                self.session, pipeline_name=f"ingest:{dataset.name}"[:64], trigger_type="data",
                triggered_by=actor, config={"dataset": dataset.name, "file": obs.path.name},
            )
            q.set_state(self.session, run, "REJECTED_INPUT", stage="ingest", actor=actor,
                        error_summary="required column renamed: mapping proposed, "
                                      "awaiting approval")
        mapping = {p["source"]: p["column"] for p in proposals}
        contract = self.contract_for(dataset)
        named = {c.name.lower() for c in contract.columns} if contract else set()
        evidence = EvidenceBundle(
            action_type="schema.change",
            target_ref=target,
            schema_diff={
                "missing_required": sorted(mapping.values()),
                "not_in_contract": sorted(c for c in schema if c.lower() not in named),
                "proposals": proposals,
            },
            impact_estimate={
                "rows": count_rows(kept, obs.fmt),
                "size_bytes": obs.size_bytes,
                "effect": "registers a copy of the file with the columns renamed; the file "
                          "as it arrived stays in quarantine and is not changed",
            },
            risk_notes=[
                f"{p['source']} -> {p['column']}: name similarity {p['similarity']}, "
                f"type {p['type']} against {p['expected_type']}"
                for p in proposals
            ],
        )
        # The apply step reads the file and the mapping from here, so what is applied is
        # exactly what was approved. One file per request: a decided bundle is never rewritten.
        uri = self.storage.uri(self.storage.write_json(
            kept.parent / f"mapping_evidence_{uuid.uuid4().hex[:12]}.json",
            {**evidence.to_dict(), "dataset": dataset.name, "kept_uri": self.storage.uri(kept),
             "checksum_sha256": obs.checksum, "mapping": mapping},
        ))
        service = GovernanceService(self.session, PolicyEngine(settings().governance_policy_path))
        return service.request_approval(
            run, action="schema.change", target_ref=target, requested_by="agent:ingest",
            evidence=evidence, evidence_uri=uri,
        )

    def apply_mapping(
        self, dataset_name: str, source: Path, mapping: dict[str, str], *, actor: str,
        basis: str, original_checksum: str, approval_id: uuid.UUID | None = None,
    ) -> RegistrationResult:
        """Register a copy of `source` with its columns renamed; the source is not changed.

        The version records what it was mapped from, so the rename is part of its lineage.
        """
        fmt = detect_format(source)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / source.name
            if fmt == "parquet":
                import pyarrow.parquet as pq

                table = pq.read_table(source)
                _require_columns(mapping, table.column_names)
                pq.write_table(
                    table.rename_columns([mapping.get(n, n) for n in table.column_names]), out
                )
            elif fmt == "csv":
                # Only the header changes: every byte after it is copied as it is, so a value
                # in another encoding later in the file cannot break the copy. The header is
                # read as pandas reads it, without a byte-order mark, or a renamed first column
                # of an Excel export would never match its proposal.
                with open(source, "rb") as src, open(out, "wb") as dst:
                    line = src.readline().decode("utf-8-sig")
                    ending = "\r\n" if line.endswith("\r\n") else "\n"
                    header = next(csv.reader([line]))
                    _require_columns(mapping, header)
                    text = io.StringIO()
                    csv.writer(text, lineterminator=ending).writerow(
                        [mapping.get(n, n) for n in header]
                    )
                    dst.write(text.getvalue().encode("utf-8"))
                    shutil.copyfileobj(src, dst)
            else:
                raise IngestError(f"a {fmt} file cannot be mapped here; re-export it with the "
                                  "contract's column names")
            result = self.register_version(dataset_name, out, actor=actor, propose=False)
        if result.created:
            version = self.session.get(DatasetVersion, result.version_id)
            lineage = {"checksum_sha256": original_checksum, "mapping": mapping, "basis": basis,
                       "approval_id": str(approval_id) if approval_id else None}
            version.schema_json = {**(version.schema_json or {}), "mapped_from": lineage}
            self.session.flush()
            audit.record(
                self.session,
                actor_type="system",
                actor_id=actor,
                action="dataset.mapping_applied",
                resource_type="dataset_version",
                resource_id=str(version.id),
                details={"dataset": dataset_name, **lineage},
            )
        result.proposed_mapping = mapping
        result.approval_id = approval_id
        return result

    def apply_approved_mapping(
        self, approval: Approval, *, actor: str
    ) -> RegistrationResult | None:
        """Carry out an approved mapping. None when the approval holds no mapping."""
        if approval.action_type != "schema_change" or not approval.evidence_uri:
            return None
        doc = json.loads(self.storage.path(approval.evidence_uri).read_text(encoding="utf-8"))
        if not doc.get("mapping"):
            return None
        if approval.status != "approved":
            raise IngestError(f"approval {approval.id} is {approval.status}; a mapping is "
                              "applied only after it is approved")
        result = self.apply_mapping(
            doc["dataset"], self.storage.path(doc["kept_uri"]), doc["mapping"], actor=actor,
            basis="approved", original_checksum=doc["checksum_sha256"], approval_id=approval.id,
        )
        if result.version_id is None:
            # An approval that registers nothing must not read as done: the decision is rolled
            # back with this error rather than answered 200 with nothing behind it.
            raise IngestError(f"the approved mapping did not produce a registrable file: "
                              f"{result.reason}")
        return result


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
        mtime = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.UTC)
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
