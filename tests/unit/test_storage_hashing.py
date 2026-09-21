"""NFR-REP-02 and ATC-18: a committed data folder's hash must survive being rewritten.

EXP-6 found this the hard way. It injected a fault, watched the pipeline recover, and got a
correct output - 8078 rows, row accounting balanced, byte-identical input and code - whose
committed content hash did not match the clean run's. There turned out to be two causes, and
only the first was visible at the time:

1. **File names.** Spark mints a UUID per job and puts it in every part-file name, so
   `part-00000-e7ee49db-….parquet` becomes `part-00000-afd64427-….parquet` next run.
2. **Row-to-file assignment.** `OutputValidator` writes without sorting, so which rows land in
   which part file, and in what order inside it, depends on task scheduling. Fixing (1) alone
   was not enough: a replay of run-2026-09-21-004 reproduced the identical 7815 rows and still
   wrote different bytes.

So a table's content is defined as what a table is - the multiset of its rows - and the hash
is over that. These tests pin both directions: it must ignore layout, and it must not ignore
data. `sha256_dir` keeps its name-sensitive behaviour for code trees, where a generated source
renaming itself *is* a difference.
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


# --- the layout-independent half ---------------------------------------------------
#
# The tests above use plain files, which exercise the fallback path: no parquet, so the
# content is the files. These use real parquet, which is the path that matters, because a
# Spark table's rows move between files from run to run.


def _parquet(root: Path, rows: list[dict], chunks: int) -> Path:
    """Write the same rows split across a given number of part files."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    root.mkdir(parents=True, exist_ok=True)
    size = max(1, len(rows) // chunks)
    for i in range(chunks):
        slice_ = rows[i * size : (i + 1) * size] if i < chunks - 1 else rows[i * size :]
        if not slice_:
            continue
        pq.write_table(
            pa.Table.from_pylist(slice_),
            root / f"part-{i:05d}-{'ab' * 8}-c000.parquet",
        )
    return root


def test_the_same_rows_in_a_different_number_of_files_hash_the_same(tmp_path: Path) -> None:
    """The defect the replay actually hit, once the file names were dealt with.

    `OutputValidator` writes without sorting, so which rows land in which part file - and in
    what order inside it - depends on task scheduling. A replay of run-2026-09-21-004 produced
    the identical 7815 rows with identical accounting and still wrote different bytes.
    """
    rows = [{"customer_id": f"C-{i:05d}", "amount": float(i) * 1.5} for i in range(200)]
    two = _parquet(tmp_path / "two", rows, chunks=2)
    five = _parquet(tmp_path / "five", rows, chunks=5)

    assert len(list(two.glob("*.parquet"))) == 2
    assert len(list(five.glob("*.parquet"))) == 5
    assert sha256_data_dir(two) == sha256_data_dir(five)


def test_the_row_order_does_not_change_the_hash(tmp_path: Path) -> None:
    """A table is a multiset of rows; the order they were written in is not content."""
    rows = [{"customer_id": f"C-{i:05d}", "amount": float(i)} for i in range(50)]
    forwards = _parquet(tmp_path / "f", rows, chunks=1)
    backwards = _parquet(tmp_path / "b", list(reversed(rows)), chunks=1)
    assert sha256_data_dir(forwards) == sha256_data_dir(backwards)


def test_a_changed_value_still_changes_the_hash(tmp_path: Path) -> None:
    """The counterpart. A hash blind to layout must not be blind to the data."""
    rows = [{"customer_id": f"C-{i:05d}", "amount": float(i)} for i in range(50)]
    original = _parquet(tmp_path / "o", rows, chunks=2)
    edited = [*rows[:-1], {**rows[-1], "amount": rows[-1]["amount"] + 0.01}]
    changed = _parquet(tmp_path / "c", edited, chunks=2)
    assert sha256_data_dir(original) != sha256_data_dir(changed)


def test_a_dropped_row_changes_the_hash(tmp_path: Path) -> None:
    rows = [{"customer_id": f"C-{i:05d}", "amount": float(i)} for i in range(50)]
    full = _parquet(tmp_path / "full", rows, chunks=2)
    short = _parquet(tmp_path / "short", rows[:-1], chunks=2)
    assert sha256_data_dir(full) != sha256_data_dir(short)


def test_a_renamed_column_changes_the_hash(tmp_path: Path) -> None:
    """Column names travel with each row, so a schema change is a content change."""
    rows = [{"customer_id": "C-1", "amount": 1.0}]
    original = _parquet(tmp_path / "a", rows, chunks=1)
    renamed = _parquet(tmp_path / "b", [{"customer_id": "C-1", "value": 1.0}], chunks=1)
    assert sha256_data_dir(original) != sha256_data_dir(renamed)


def test_reordering_the_columns_does_not_change_the_hash(tmp_path: Path) -> None:
    """But a schema *reordering* is not: the same columns holding the same values."""
    original = _parquet(tmp_path / "a", [{"customer_id": "C-1", "amount": 1.0}], chunks=1)
    reordered = _parquet(tmp_path / "b", [{"amount": 1.0, "customer_id": "C-1"}], chunks=1)
    assert sha256_data_dir(original) == sha256_data_dir(reordered)
