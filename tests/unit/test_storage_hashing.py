"""NFR-REP-02 and ATC-18: a committed data folder's hash must survive being rewritten.

EXP-6 found this the hard way. It injected a fault, watched the pipeline recover, and got a
correct output - 8078 rows, row accounting balanced, byte-identical input and code - whose
committed content hash did not match the clean run's. Spark mints a UUID per job and puts it
in every part-file name, so `part-00000-e7ee49db-....parquet` becomes
`part-00000-afd64427-....parquet` on the next run. The bytes are the same; the names are not.
`sha256_dir` folded the names in, so the hash changed on every run and "identical content for
deterministic stages" could never hold.

These tests pin both halves of the fix: the data hash ignores names, and the code hash still
does not - a generated source file renaming itself *is* a difference and must stay one.
"""

from __future__ import annotations

from pathlib import Path

from triad_core.ids import sha256_data_dir, sha256_dir
from triad_core.storage import Manifest


def _write(root: Path, names_to_bytes: dict[str, bytes]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, payload in names_to_bytes.items():
        (root / name).write_bytes(payload)
    return root


def test_the_data_hash_ignores_the_part_file_names(tmp_path: Path) -> None:
    """The exact shape Spark produces: same bytes, different UUID in every name."""
    first = _write(tmp_path / "first", {
        "part-00000-e7ee49db-8143-4373-a1b2-000000000000-c000.zstd.parquet": b"alpha",
        "part-00001-e7ee49db-8143-4373-a1b2-000000000000-c000.zstd.parquet": b"beta",
    })
    second = _write(tmp_path / "second", {
        "part-00000-afd64427-4053-4e07-c3d4-111111111111-c000.zstd.parquet": b"alpha",
        "part-00001-afd64427-4053-4e07-c3d4-111111111111-c000.zstd.parquet": b"beta",
    })

    assert sha256_data_dir(first) == sha256_data_dir(second)
    # And the old behaviour is what it was, so the reason for two functions stays visible.
    assert sha256_dir(first) != sha256_dir(second)


def test_the_data_hash_still_changes_when_the_bytes_change(tmp_path: Path) -> None:
    """A hash that ignored everything would pass the test above. This is the counterpart."""
    first = _write(tmp_path / "a", {"part-00000-aaa.parquet": b"alpha"})
    second = _write(tmp_path / "b", {"part-00000-aaa.parquet": b"alpha-but-edited"})
    assert sha256_data_dir(first) != sha256_data_dir(second)


def test_two_identical_files_are_not_the_same_as_one(tmp_path: Path) -> None:
    """A multiset, not a set: duplicated content is more content, not the same content."""
    one = _write(tmp_path / "one", {"part-00000-x.parquet": b"same"})
    two = _write(tmp_path / "two", {
        "part-00000-x.parquet": b"same",
        "part-00001-y.parquet": b"same",
    })
    assert sha256_data_dir(one) != sha256_data_dir(two)


def test_the_order_spark_emits_partitions_in_does_not_matter(tmp_path: Path) -> None:
    """Partition N holding what partition M held last time is not a content difference."""
    first = _write(tmp_path / "f", {
        "part-00000-aaa.parquet": b"alpha",
        "part-00001-aaa.parquet": b"beta",
    })
    second = _write(tmp_path / "s", {
        "part-00000-bbb.parquet": b"beta",
        "part-00001-bbb.parquet": b"alpha",
    })
    assert sha256_data_dir(first) == sha256_data_dir(second)


def test_commit_markers_are_excluded_so_the_hash_survives_committing(tmp_path: Path) -> None:
    """The manifest contains the hash, so hashing the manifest would be circular."""
    folder = _write(tmp_path / "c", {"part-00000-aaa.parquet": b"alpha"})
    before = sha256_data_dir(folder)
    (folder / "_SUCCESS").write_bytes(b"")
    (folder / "_manifest.json").write_text('{"content_hash": "whatever"}', encoding="utf-8")
    (folder / ".part-00000-aaa.parquet.crc").write_bytes(b"crc")
    assert sha256_data_dir(folder) == before


def test_a_committed_folder_verifies_against_its_own_manifest(storage, tmp_path: Path) -> None:
    """The end-to-end property: `commit` writes a hash that `verify` recomputes and agrees with.

    This is the one that would have caught the bug if the two had ever used different
    functions - which, for a while after the fix, they did.
    """
    staging = _write(tmp_path / "staging", {
        "part-00000-e7ee49db-c000.zstd.parquet": b"rows",
    })
    target = Path(storage.root) / "clean" / "hash_roundtrip"
    committed = storage.commit(staging, target, Manifest(
        kind="clean", name="clean", version="v1",
        created_at="2026-01-01T00:00:00+00:00", run_id="v1", row_count=1,
    ))

    ok, reason = storage.verify(committed)
    assert ok, reason
    assert storage.read_manifest(committed).content_hash
