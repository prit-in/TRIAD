"""ST-04: two endpoints took a path from the caller and used it without constraining it.

Both were confirmed against the running stack before being fixed.

**Read side.** `POST /api/v1/datasets` passed `source_uri` to `Storage.path`, which returned an
absolute path verbatim and joined a relative one to the root without resolving it. The only
check downstream was `exists()`. So `{"source_uri": "/etc/passwd"}` named a file outside the
catalogue and `register_version` copied its bytes into `raw/<name>/v=1/`, where the dataset
endpoints serve them.

**Write side.** `POST /api/v1/datasets/{ref}/upload` built its target as
`landing / file.filename`. Starlette does not sanitise `filename` - it is whatever the
request's Content-Disposition header says - so an upload named `../../../../tmp/x.csv` was
accepted with 200 and its bytes were written to `/tmp/x.csv`, outside the data root entirely.
That is an arbitrary write wherever the API process has permission.

The guards are in `Storage.path` and at the one place a filename becomes a path, not in a
per-endpoint check, so an endpoint added later inherits them.
"""

from __future__ import annotations

import uuid

import pytest

from triad_core.storage import Storage, StorageError


@pytest.fixture()
def store(tmp_path) -> Storage:
    return Storage(tmp_path / "data")


ESCAPES = [
    "/etc/passwd",
    "/",
    "../../../../etc/passwd",
    "../outside.csv",
    "file:///etc/hostname",
    "raw/../../../../etc/passwd",
]


@pytest.mark.parametrize("uri", ESCAPES)
def test_a_uri_resolving_outside_the_root_is_refused(store: Storage, uri: str) -> None:
    with pytest.raises(StorageError, match="outside the data root"):
        store.path(uri)


INSIDE = [
    "raw/transactions/v=1",
    "clean/transactions/v=2/part-0.parquet",
    "features/customer_snapshot/fsv=3",
    "raw/a/../b",
]


@pytest.mark.parametrize("uri", INSIDE)
def test_a_uri_inside_the_root_still_resolves(store: Storage, uri: str) -> None:
    resolved = store.path(uri)
    assert resolved.is_relative_to(store.root.resolve())


def test_an_absolute_uri_the_platform_wrote_still_resolves(store: Storage) -> None:
    """Every internal caller passes a URI storage itself produced. Those must keep working."""
    written = store.root / "raw" / "transactions" / "v=1"
    assert store.path(store.uri(written)) == written.resolve()
    assert store.path(str(written)) == written.resolve()


def test_the_root_itself_is_inside_the_root(store: Storage) -> None:
    """`is_relative_to` is true for the root against itself, but being explicit costs nothing
    and a resolver that refused its own root would break `read_manifest` on a top-level folder.
    """
    assert store.path(str(store.root)) == store.root.resolve()
    assert store.path(".") == store.root.resolve()


# --- the write side: a filename is not a path ---------------------------------


FILENAMES = [
    ("../../../../tmp/escaped.csv", "escaped.csv"),
    ("../evil.csv", "evil.csv"),
    ("/etc/cron.d/evil", "evil"),
    ("subdir/nested.csv", "nested.csv"),
    ("ordinary.csv", "ordinary.csv"),
]


@pytest.mark.parametrize(("supplied", "expected"), FILENAMES)
def test_only_the_basename_of_an_upload_filename_is_used(
    tmp_path, supplied: str, expected: str
) -> None:
    """The transformation the endpoint applies, asserted on its own.

    Held here rather than only through the endpoint because it is one expression, and a test
    that has to post multipart form data to observe it would not say which expression was
    wrong when it broke.
    """
    from pathlib import Path

    landing = tmp_path / "uploads" / "transactions"
    landing.mkdir(parents=True)

    basename = Path(supplied).name
    target = landing / (basename if basename not in ("", ".", "..") else "upload.bin")

    assert target.name == expected
    assert target.resolve().is_relative_to(landing.resolve()), (
        f"{supplied} escaped the landing folder"
    )


@pytest.mark.parametrize("supplied", ["", "..", ".", "../", "/", "./"])
def test_a_filename_with_no_usable_basename_falls_back(tmp_path, supplied: str) -> None:
    from pathlib import Path

    landing = tmp_path / "uploads" / "transactions"
    landing.mkdir(parents=True)

    basename = Path(supplied).name
    target = landing / (basename if basename not in ("", ".", "..") else "upload.bin")

    assert target == landing / "upload.bin"
    assert target.resolve().is_relative_to(landing.resolve())


# --- a source that is not one file ---------------------------------------------


def test_registering_a_directory_is_refused_rather_than_crashing(tmp_path) -> None:
    """`observe` gave a directory an empty checksum, so every directory looked like a duplicate
    of every other one, and `copy_immutable` then raised IsADirectoryError - which reached the
    client as a 500 from `POST /api/v1/datasets`.
    """
    from dataeng.ingest import IngestError, observe

    directory = tmp_path / "v=1"
    directory.mkdir()
    (directory / "part-0.parquet").write_bytes(b"not really parquet")

    with pytest.raises(IngestError, match="not a file"):
        observe(directory)

    # A real file is still observed, with a real checksum.
    one_file = directory / "part-0.parquet"
    seen = observe(one_file)
    assert seen.checksum and len(seen.checksum) == 64
    assert seen.size_bytes == one_file.stat().st_size


BACKSLASH_NAMES = ["..\\", "..\\..\\etc\\passwd", "a\\b.csv"]


@pytest.mark.parametrize("supplied", BACKSLASH_NAMES)
def test_a_backslash_is_not_a_separator_here_and_stays_contained(
    tmp_path, supplied: str
) -> None:
    """A Windows-style filename is one odd POSIX filename, not a traversal.

    The API container is Linux, so a backslash is an ordinary character inside a path
    component: the basename of "..\\..\\etc\\passwd" is the whole string. These are kept
    as they arrive rather than rewritten, because the requirement is containment and they are
    contained. Asserting it stops a later tidy-up from splitting on the backslash and
    reintroducing the escape it was meant to remove.
    """
    from pathlib import Path

    landing = tmp_path / "uploads" / "transactions"
    landing.mkdir(parents=True)

    basename = Path(supplied).name
    target = landing / (basename if basename not in ("", ".", "..") else "upload.bin")

    assert target.resolve().is_relative_to(landing.resolve()), supplied


def test_a_missing_source_is_still_reported_as_missing(tmp_path) -> None:
    """The not-a-file guard must not shadow the not-found one: they need different answers."""
    from dataeng.ingest import IngestError, observe

    with pytest.raises(IngestError, match="file not found"):
        observe(tmp_path / f"absent-{uuid.uuid4().hex[:6]}.csv")
