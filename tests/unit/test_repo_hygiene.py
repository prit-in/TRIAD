"""Nothing is tracked that nobody meant to track.

Twice now a zero-byte file has been committed by a stray shell redirect: first `not`, from
something shaped like `grep ... not`, and then `tok.txt`, from a command that piped a token to a
file inside the repository instead of a temporary directory. Neither broke anything, and that is
why neither was noticed - an empty file is invisible in a diff summary and silent at runtime. A
`git add -A` picks it up and it stays for as long as nobody looks.

An empty `__init__.py` is the real exception rather than a special case: it is how a Python
package is declared, so it is the one empty file with a meaning. Everything else empty is either
an accident or something whose emptiness should be explained by adding it here on purpose.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Files that are meant to be empty, each because emptiness is its content.
ALLOWED_EMPTY = {
    "__init__.py",       # declares a package
    ".gitkeep",          # keeps an otherwise empty directory
}

# Extensions no source tree here has a reason to carry. `.log` and `.out` are command output,
# `.pid` and `.lock` (outside the dependency lock, which is `.txt`) are runtime state, and the
# editor and OS droppings are never deliberate.
UNWANTED_SUFFIXES = {
    ".log", ".out", ".err", ".pid", ".tmp", ".temp", ".bak", ".orig", ".rej",
    ".swp", ".swo", ".pyc", ".pyo", ".tsbuildinfo", ".DS_Store",
}

UNWANTED_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", "nul", "NUL"}


def _tracked() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True, cwd=REPO,
    )
    return [line for line in result.stdout.splitlines() if line]


def test_the_scan_has_files_to_scan() -> None:
    """Without this, an empty listing would make every assertion below vacuously green."""
    assert len(_tracked()) > 100, "git ls-files returned almost nothing"


def test_no_tracked_file_is_unintentionally_empty() -> None:
    empty = [
        path for path in _tracked()
        if Path(path).name not in ALLOWED_EMPTY
        and (REPO / path).is_file()
        and (REPO / path).stat().st_size == 0
    ]
    assert not empty, (
        "these tracked files are empty; if that is deliberate, add the name to ALLOWED_EMPTY "
        f"with the reason: {empty}"
    )


def test_no_tracked_file_is_command_output_or_an_editor_dropping() -> None:
    unwanted = [
        path for path in _tracked()
        if Path(path).suffix in UNWANTED_SUFFIXES or Path(path).name in UNWANTED_NAMES
    ]
    assert not unwanted, f"these should not be tracked: {unwanted}"


def test_no_tracked_directory_is_a_mangled_shell_argument() -> None:
    """A path component that is a shell fragment is a redirect or a quoting accident.

    `frontend;C`, `tests/agent|e2e|integration|performance` and similar have all appeared here,
    created by a command whose separator was taken literally as part of a directory name.
    """
    suspicious = [
        path for path in _tracked()
        if any(ch in path for ch in (";", "|", "&", ">", "<", "$", "`", '"', "'", "*", "\\"))
    ]
    assert not suspicious, f"these paths contain shell metacharacters: {suspicious}"
