"""ST-03: secret scanning of the tracked source.

Two things are checked, and the second is the one that matters.

The shipped development JWT secret is *named* after its own danger, and it is allowed to appear
where that naming is the point - the settings default, the check that refuses it outside `dev`,
the deployment runbook that tells an operator to replace it. Anywhere else it would be a real
secret pasted into source, so the allowlist is by file rather than by pattern.

The second check is generic: nothing tracked may contain a private key block or a recognisable
provider key. That is the one that catches a key nobody meant to commit, which is the only kind
that ever gets committed.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

DEV_SECRET = "dev-only-insecure-secret-change-me-before-any-real-deployment"

# Where the dev secret may legitimately appear, because naming it is the safety mechanism.
SECRET_ALLOWED_IN = {
    "triad_core/config.py",            # the default itself, named after its own danger
    "backend/app/security.py",         # the check that refuses it outside dev
    "docker/compose.yaml",             # the override point, with the same name
    "docs/runbooks/deployment.md",     # the instruction to replace it
    # The template an operator copies. It carries the named default deliberately: the value
    # announces what it is, the line above it gives the command to generate a real one, and the
    # API refuses to start on it outside `dev`. A placeholder here would instead produce a key
    # too short for HS256 and break local development at the first `docker compose up`.
    ".env.example",
    "tests/security/test_no_secrets_in_source.py",
    "tests/unit/test_api.py",          # the tests that assert it is refused
}

KEY_PATTERNS = [
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"), "a private key block"),
    (re.compile(r"\bsk-[A-Za-z0-9]{32,}"), "an OpenAI-style key"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{20,}"), "an Anthropic-style key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key id"),
    (re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "a GitHub token"),
]

TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".scala", ".sql", ".yaml", ".yml", ".json", ".md", ".toml",
    ".cfg", ".ini", ".sh", ".ps1", ".txt", ".css", ".html", ".sbt", ".env", ".example",
}


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    return [Path(p) for p in out if Path(p).suffix in TEXT_SUFFIXES]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def test_the_scan_has_files_to_scan() -> None:
    """Without this, an empty file list would make both scans below vacuously green."""
    assert len(_tracked_files()) > 100


def test_the_development_secret_appears_only_where_naming_it_is_the_point() -> None:
    offenders = [
        str(path).replace("\\", "/")
        for path in _tracked_files()
        if DEV_SECRET in _read(path)
        and str(path).replace("\\", "/") not in SECRET_ALLOWED_IN
    ]
    assert offenders == [], f"the development secret appears in {offenders}"


@pytest.mark.parametrize(("pattern", "what"), KEY_PATTERNS)
def test_no_tracked_file_carries_a_credential(pattern: re.Pattern[str], what: str) -> None:
    offenders = [
        str(path) for path in _tracked_files() if pattern.search(_read(path))
    ]
    assert offenders == [], f"{what} appears in {offenders}"
