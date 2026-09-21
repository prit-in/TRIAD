"""Every script and experiment module must at least import.

This exists because two modules shipped with the same undetected bug: `scripts/replay_run.py`
and `experiments/exp01_small_dataset.py` both did

    from triad_core.models import LLMInvocation

and the class is `LlmInvocation`. Neither had ever been executed, so nothing noticed until a
run of the replay script died on its import line — after the fifteen-minute pipeline run it
was supposed to replay had already finished.

An import is a weak test and it is not pretending otherwise: it proves the module parses, that
its top-level imports resolve, and nothing more. What makes it worth having is the cost. These
are long-running entry points; an import error in one is discovered at the end of a run that
may have taken half an hour, and this finds it in milliseconds.

`ruff` does not catch it: `LLMInvocation` is a real name being imported from a real module, and
only the interpreter knows the module does not define it.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Modules that do real work at import time, or that need an argument to be meaningful, are
# still safe to import: every one of these guards its work behind `if __name__ == "__main__"`
# or a function. If one ever stops doing that, this test will hang or fail, which is the
# correct outcome — an entry point with side effects at import is a bug of its own.
SCRIPTS = sorted(p.stem for p in (REPO_ROOT / "scripts").glob("*.py") if not p.stem.startswith("_"))
EXPERIMENTS = sorted(
    p.stem for p in (REPO_ROOT / "experiments").glob("*.py") if not p.stem.startswith("_")
)


@pytest.mark.parametrize("name", SCRIPTS)
def test_every_script_imports(name: str) -> None:
    import sys

    scripts = str(REPO_ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    importlib.import_module(name)


@pytest.mark.parametrize("name", EXPERIMENTS)
def test_every_experiment_imports(name: str) -> None:
    importlib.import_module(f"experiments.{name}")


def test_the_lists_are_not_empty() -> None:
    """A glob that matched nothing would make every test above pass vacuously."""
    assert len(SCRIPTS) >= 5, SCRIPTS
    assert len(EXPERIMENTS) >= 10, EXPERIMENTS
