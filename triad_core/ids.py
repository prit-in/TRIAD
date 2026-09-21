"""Identifiers, hashing and canonical serialisation.

Every hash in TRIAD (content hash, config hash, schema hash, audit entry hash, error
fingerprint) goes through here, so replay comparisons are apples to apples.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any


def canonical_json(obj: Any) -> str:
    """Sorted, separator-stable JSON. The only serialisation used for hashing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def sha256_text(text_value: str) -> str:
    return hashlib.sha256(text_value.encode("utf-8")).hexdigest()


def sha256_obj(obj: Any) -> str:
    return sha256_text(canonical_json(obj))


def sha256_file(path: str | Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def _hashable_files(
    root: Path, include_suffixes: tuple[str, ...] | None = None
) -> list[tuple[str, str]]:
    """(relative path, file hash) for every file whose bytes are part of the content.

    Commit markers are excluded, so the hash describes the *content* and stays stable
    whether it is computed before or after the folder is committed: `_SUCCESS` and
    `_manifest.json` are written by the commit itself (the manifest even contains this
    hash), and Spark's `.crc` sidecars are checksums of the same bytes.
    """
    entries: list[tuple[str, str]] = []
    skip = {"_SUCCESS", "_manifest.json"}
    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(root).as_posix()
        if rel.startswith(".") or rel.endswith(".crc") or Path(rel).name in skip:
            continue
        if include_suffixes and not rel.endswith(include_suffixes):
            continue
        entries.append((rel, sha256_file(f)))
    return entries


def sha256_dir(path: str | Path, include_suffixes: tuple[str, ...] | None = None) -> str:
    """Content hash of a directory: hash of (relative path, file hash) pairs, sorted.

    **Names are part of this hash**, so use it only where a file's name is part of what the
    folder means - a generated-code tree, where `CleanJob_ab12cd34.scala` renaming itself
    would be a real difference. For a data folder written by Spark, use `sha256_data_dir`.
    """
    return sha256_obj(_hashable_files(Path(path), include_suffixes))


def sha256_data_dir(path: str | Path, include_suffixes: tuple[str, ...] | None = None) -> str:
    """Content hash of a data folder, independent of the file names inside it.

    Spark names every part file with a UUID minted per job: two runs over identical input
    with identical code write `part-00000-e7ee49db-....parquet` and
    `part-00000-afd64427-....parquet`. The *bytes* are identical - measured, not assumed -
    but `sha256_dir` folds the names in, so its hash differed on every run and
    NFR-REP-02 ("identical content for deterministic stages") and ATC-18's "output hashes
    identical" could never pass. EXP-6 found this by recovering from an injected fault and
    producing a correct output that did not match the clean run.

    So this hashes the sorted multiset of file hashes and nothing else. Sorted, because the
    order Spark happens to emit partitions in is not part of the content; a multiset rather
    than a set, because two files with identical bytes are not the same as one.
    """
    return sha256_obj(sorted(h for _, h in _hashable_files(Path(path), include_suffixes)))


def normalise_source(source: str) -> str:
    """Whitespace-normalised source used for code-artifact content hashes (PRD 21.6)."""
    lines = [line.rstrip() for line in source.replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


def content_hash(source: str) -> str:
    return sha256_text(normalise_source(source))


# --- Error fingerprints (PRD section 20.1) ------------------------------------

_FINGERPRINT_SCRUBBERS = (
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<TS>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<UUID>"),
    (re.compile(r"\b[0-9a-f]{32,64}\b"), "<HASH>"),
    (re.compile(r"(?:[A-Za-z]:)?[\\/][\w.\-\\/]{3,}"), "<PATH>"),
    (re.compile(r"\bline\s+\d+\b", re.I), "line <N>"),
    (re.compile(r":\d+:\d+\b"), ":<N>:<N>"),
    (re.compile(r"\b\d+\b"), "<N>"),
    (re.compile(r"\s+"), " "),
)


def normalise_error(message: str) -> str:
    """Strip line numbers, paths, IDs and timestamps so the same fault maps to one string."""
    out = message or ""
    for pattern, repl in _FINGERPRINT_SCRUBBERS:
        out = pattern.sub(repl, out)
    return out.strip().lower()[:2000]


def error_fingerprint(error_class: str, message: str) -> str:
    return sha256_text(f"{error_class}|{normalise_error(message)}")[:32]


# --- Run keys -----------------------------------------------------------------


def run_key(now: dt.datetime, sequence: int) -> str:
    """Readable run key, e.g. run-2026-10-05-001 (PRD section 24.1)."""
    return f"run-{now.strftime('%Y-%m-%d')}-{sequence:03d}"


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)
