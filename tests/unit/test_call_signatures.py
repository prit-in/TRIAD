"""Every keyword passed at a call site is one the callee accepts.

This exists because the same mistake stopped two experiments from ever running. EXP-2 and EXP-3
both built a `SplitSpec(valid_cutoffs=...)` and the field is `valid_cutoff`, singular. Python
raises `TypeError` when the line executes, and in an experiment harness that is after the Spark
work rather than at import, so both experiments were reported as run for as long as nobody read
their artifacts. A misspelled keyword is not a typo that shows up in review; it is a runtime
failure hiding behind however long it takes to reach the line.

Neither ruff nor mypy catches this across the project as configured, so it is asserted here.

The check is deliberately narrow, because a broad version is worse than none. It resolves each
callee through the calling file's own imports: matching on the bare name alone makes
`subprocess.run` collide with an experiment's own `run` function, which produced 86KB of false
positives on the first attempt. It skips anything it cannot see the full parameter set of -
`**kwargs`, a call with `**spread`, a class inheriting fields from a base - because a guess in
either direction is a worse outcome than silence.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SKIP_PARTS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "scala_spark", "frontend", "dist", "build", ".mypy_cache", ".ruff_cache",
}


def _python_files() -> list[pathlib.Path]:
    return [
        p for p in sorted(REPO.rglob("*.py"))
        if not any(part in SKIP_PARTS for part in p.relative_to(REPO).parts)
    ]


def _module_name(path: pathlib.Path) -> str:
    return ".".join(path.relative_to(REPO).with_suffix("").parts)


def _accepted_keywords(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str] | None:
    """The keyword names this function accepts, or None when it accepts anything."""
    args = node.args
    if args.kwarg is not None:
        return None
    names = {p.arg for p in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    return names - {"self", "cls"}


def _definitions(tree: ast.Module) -> dict[str, set[str] | None]:
    """Top-level callables in one module, mapped to the keywords they accept.

    Top level only: a nested definition is not what an importer of the module calls.
    """
    out: dict[str, set[str] | None] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            out[node.name] = _accepted_keywords(node)
        elif isinstance(node, ast.ClassDef):
            init = next(
                (b for b in node.body if isinstance(b, ast.FunctionDef) and b.name == "__init__"),
                None,
            )
            if init is not None:
                out[node.name] = _accepted_keywords(init)
                continue
            # A dataclass or a Pydantic model: the annotated class-level names are the fields.
            fields = {
                b.target.id for b in node.body
                if isinstance(b, ast.AnnAssign) and isinstance(b.target, ast.Name)
            }
            bases = {b.id for b in node.bases if isinstance(b, ast.Name)} | {
                b.attr for b in node.bases if isinstance(b, ast.Attribute)
            }
            # Any base may contribute fields this file cannot see, so do not judge those.
            out[node.name] = fields if (fields and not bases) else None
    return out


@pytest.fixture(scope="module")
def parsed() -> dict[pathlib.Path, ast.Module]:
    trees: dict[pathlib.Path, ast.Module] = {}
    for path in _python_files():
        try:
            trees[path] = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:                  # a file that does not parse is its own bug
            pytest.fail(f"{path} does not parse: {exc}")
    return trees


def test_the_scan_actually_covers_the_project(parsed) -> None:
    """A guard on the guard: an empty scan would make the assertion below vacuous."""
    assert len(parsed) >= 100, f"only {len(parsed)} files parsed"


def test_no_call_passes_a_keyword_its_callee_does_not_accept(parsed) -> None:
    modules = {_module_name(p): _definitions(t) for p, t in parsed.items()}
    linenos = {
        _module_name(p): {
            n.name: n.lineno for n in t.body
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        }
        for p, t in parsed.items()
    }

    failures: list[str] = []
    checked = 0

    for path, tree in parsed.items():
        here = _module_name(path)

        # Where each bare name in this file came from, for modules this scan parsed.
        origin: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                if node.module in modules:
                    for alias in node.names:
                        origin[alias.asname or alias.name] = node.module
        for name in modules.get(here, {}):
            origin.setdefault(name, here)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            source = origin.get(node.func.id)
            if source is None:
                continue
            allowed = modules[source].get(node.func.id)
            if allowed is None:
                continue
            if any(kw.arg is None for kw in node.keywords):
                continue                            # **spread: the names are not visible here
            checked += 1
            for kw in node.keywords:
                if kw.arg not in allowed:
                    defined = linenos[source].get(node.func.id)
                    failures.append(
                        f"{path.relative_to(REPO)}:{node.lineno} passes "
                        f"{node.func.id}({kw.arg}=...), but {source}.{node.func.id} at line "
                        f"{defined} accepts {sorted(allowed)}"
                    )

    assert checked >= 500, f"only {checked} call sites were resolvable; the scan has regressed"
    assert not failures, "\n".join(failures)
