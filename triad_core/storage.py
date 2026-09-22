"""Storage interface and the versioned-folder layout (PRD sections 14.2, 23).

All pipeline code addresses data through URIs resolved here. The MVP backend is `file://`
on the shared volume; swapping to `s3a://` is a configuration change plus a connector JAR
(CR-17), which is why nothing outside this module builds paths by hand.

Write protocol (PRD section 14.2): write into a run-scoped staging folder, verify, then
atomically promote the folder into place and drop `_SUCCESS` + `_manifest.json`. Readers
never see partial output.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from triad_core.config import settings
from triad_core.ids import (
    canonical_json,
    sha256_data_dir,
    sha256_file,
    sha256_obj,
)

MANIFEST_NAME = "_manifest.json"
SUCCESS_NAME = "_SUCCESS"


class StorageError(Exception):
    pass


@dataclass
class Manifest:
    """Written next to every committed folder so readers can verify what they are reading."""

    kind: str  # raw | clean | features | labels | predictions | training_set | quarantine
    name: str
    version: str
    created_at: str
    run_id: str | None = None
    row_count: int | None = None
    schema: dict[str, str] = field(default_factory=dict)
    schema_hash: str | None = None
    content_hash: str | None = None
    catalog_version: str | None = None
    source_refs: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


def schema_hash(schema: dict[str, str]) -> str:
    """Hash of {column: type}. Python refuses a folder whose hash differs from the catalogue."""
    return sha256_obj(sorted(schema.items()))


class Storage:
    """File-backed storage rooted at `data_root` (default /data/triad)."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or settings().data_root)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- URI handling ----------------------------------------------------------
    def uri(self, path: str | Path) -> str:
        p = Path(path)
        rel = p.relative_to(self.root) if p.is_absolute() else p
        return f"file://{(self.root / rel).as_posix()}"

    def path(self, uri: str | Path) -> Path:
        """Resolve a URI to a path **inside the data root**, or refuse it.

        The containment check is here rather than at the call sites because one of those call
        sites takes the URI from an HTTP request body: `POST /api/v1/datasets` passed
        `source_uri` straight through, and the only check downstream was `exists()`. An
        absolute path, or a relative one containing `..`, therefore named any file the API
        process could read - and `register_version` copied its bytes into the catalogue, where
        the dataset endpoints serve them. Every other caller resolves a URI the platform
        itself wrote, so none of them is affected by refusing the ones it did not.
        """
        s = str(uri)
        raw = Path(s[len("file://") :]) if s.startswith("file://") else Path(s)
        resolved = (raw if raw.is_absolute() else self.root / raw).resolve()
        root = self.root.resolve()
        if resolved != root and not resolved.is_relative_to(root):
            raise StorageError(
                f"{uri} resolves outside the data root; storage URIs address {root} only"
            )
        return resolved

    # -- layout (PRD section 14.2) ---------------------------------------------
    def raw_dir(self, dataset: str, version_no: int) -> Path:
        return self.root / "raw" / dataset / f"v={version_no}"

    def clean_dir(self, dataset: str, version_no: int) -> Path:
        return self.root / "clean" / dataset / f"v={version_no}"

    def quarantine_dir(self, dataset: str, version_no: int, run_id: str) -> Path:
        return self.root / "quarantine" / dataset / f"v={version_no}" / f"run={run_id}"

    def features_dir(self, feature_set: str, fsv: int) -> Path:
        return self.root / "features" / feature_set / f"fsv={fsv}"

    def labels_dir(self, label_def: str, version_no: int) -> Path:
        return self.root / "labels" / label_def / f"v={version_no}"

    def training_sets_dir(self, run_key: str) -> Path:
        return self.root / "training_sets" / run_key

    def predictions_dir(self, model: str, mv: str, score_date: str, pset: str) -> Path:
        return (
            self.root
            / "predictions"
            / model
            / f"mv={mv}"
            / f"score_date={score_date}"
            / f"set={pset}"
        )

    def code_dir(self, content_hash: str) -> Path:
        return self.root / "artifacts" / "code" / content_hash

    def reports_dir(self, run_key: str) -> Path:
        return self.root / "reports" / run_key

    def sandbox_dir(self, run_key: str) -> Path:
        return self.root / "sandbox" / run_key

    def staging_dir(self, run_key: str, stage: str) -> Path:
        return self.root / "sandbox" / run_key / "staging" / stage

    # -- primitive operations --------------------------------------------------
    def list_files(self, directory: str | Path, pattern: str = "*") -> list[Path]:
        d = self.path(directory)
        return sorted(p for p in d.glob(pattern) if p.is_file()) if d.exists() else []

    def stat(self, path: str | Path) -> dict[str, Any]:
        p = self.path(path)
        st = p.stat()
        return {
            "path": str(p),
            "size_bytes": st.st_size,
            "modified_at": dt.datetime.fromtimestamp(st.st_mtime, dt.UTC).isoformat(),
            "is_dir": p.is_dir(),
        }

    def checksum(self, path: str | Path) -> str:
        """Content hash of a file or a data folder, independent of part-file names.

        A folder here is a data output, so the name-independent hash is the right one: see
        `sha256_data_dir`. It has no callers in the project today and is kept as the storage
        layer's general-purpose answer to "what is in there".
        """
        p = self.path(path)
        return sha256_data_dir(p) if p.is_dir() else sha256_file(p)

    def copy_immutable(self, source: str | Path, target_dir: str | Path) -> Path:
        """Write-once copy into a version folder. Refuses to overwrite an existing file."""
        src = Path(source)
        dest_dir = self.path(target_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        if dest.exists():
            raise StorageError(f"immutable target already exists: {dest}")
        shutil.copy2(src, dest)
        os.chmod(dest, 0o444)
        return dest

    # -- staging and atomic commit --------------------------------------------
    def commit(self, staging: str | Path, target: str | Path, manifest: Manifest) -> Path:
        """Promote a staging folder to its final location, atomically where the OS allows.

        A folder rename is atomic on the same filesystem; when the target exists it is
        replaced only after the new content is complete, so a reader sees one or the other.
        """
        stage_path = self.path(staging)
        target_path = self.path(target)
        if not stage_path.exists():
            raise StorageError(f"staging folder missing: {stage_path}")

        # Name-independent: Spark mints a fresh UUID into every part-file name, so a hash
        # that included the names changed on every run of identical work (EXP-6).
        manifest.content_hash = sha256_data_dir(stage_path)
        if manifest.schema and not manifest.schema_hash:
            manifest.schema_hash = schema_hash(manifest.schema)
        (stage_path / MANIFEST_NAME).write_text(manifest.to_json(), encoding="utf-8")
        (stage_path / SUCCESS_NAME).touch()

        target_path.parent.mkdir(parents=True, exist_ok=True)
        if target_path.exists():
            backup = target_path.with_name(target_path.name + ".superseded")
            if backup.exists():
                shutil.rmtree(backup)
            target_path.rename(backup)
            try:
                stage_path.rename(target_path)
            except OSError:
                backup.rename(target_path)
                raise
            shutil.rmtree(backup, ignore_errors=True)
        else:
            stage_path.rename(target_path)
        return target_path

    def read_manifest(self, directory: str | Path) -> Manifest:
        p = self.path(directory) / MANIFEST_NAME
        if not p.exists():
            raise StorageError(f"no manifest in {directory}")
        return Manifest(**json.loads(p.read_text(encoding="utf-8")))

    def verify(self, directory: str | Path) -> tuple[bool, str]:
        """Check a committed folder against its manifest (content hash + success marker)."""
        d = self.path(directory)
        if not (d / SUCCESS_NAME).exists():
            return False, "missing _SUCCESS marker"
        try:
            m = self.read_manifest(d)
        except StorageError as exc:
            return False, str(exc)
        # Must be the same function `commit` used to write it, or every committed folder
        # would fail its own verification.
        actual = sha256_data_dir(d)
        if m.content_hash and m.content_hash != actual:
            return False, f"content hash mismatch: manifest {m.content_hash} != actual {actual}"
        return True, "ok"

    def write_json(self, path: str | Path, obj: Any) -> Path:
        p = self.path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(canonical_json(obj) if not isinstance(obj, str) else obj, encoding="utf-8")
        return p

    def write_text(self, path: str | Path, text: str) -> Path:
        p = self.path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def read_text(self, path: str | Path) -> str:
        return self.path(path).read_text(encoding="utf-8")


_default: Storage | None = None


def storage() -> Storage:
    global _default
    if _default is None:
        _default = Storage()
    return _default
