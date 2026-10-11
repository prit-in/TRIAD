"""Shared scaffolding for the ten experiments of PRD section 33.

Every experiment writes its configuration, raw measurements and environment record to
`experiments/<exp_id>/<started_at>/`, as section 33.1 requires. Nothing here interprets a
result: an experiment records what it measured and whether its hypothesis held, and the
reading happens in docs/implementation/EXPERIMENTS.md.

Two rules this module exists to enforce mechanically rather than by discipline:

* **A measurement is recorded or it is absent.** There is no path through `record` that
  invents a number. An experiment that cannot run writes `status: "not_run"` with the
  limitation that stopped it, and `conclude` refuses a result that claims a
  hypothesis held while carrying no measurements.
* **The environment is recorded with every run**, from what is actually installed, so a
  number read a year later says which machine and which library versions produced it.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS_ROOT = REPO_ROOT / "experiments"

# Status vocabulary. `partial` is for an experiment that ran a reduced but valid workload -
# the honest answer when the PRD asks for 100 GB on a 5 GB machine - and it must carry the
# limitation that reduced it.
STATUSES = ("completed", "partial", "not_run", "failed")


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _container_memory_bytes() -> int | None:
    """The cgroup v2 memory ceiling, which is the real limit inside a container.

    `platform`/`os` report the host's memory, which on this project overstates what a job
    may use by about a factor of two (ADR-0002). A resource measurement that quoted the
    host number would be wrong in the direction that flatters the result.
    """
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            raw = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if raw in ("max", ""):
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        # A cgroup with no limit reports a number near 2^63; treat that as unlimited.
        if value < (1 << 62):
            return value
    return None


def environment_record() -> dict[str, Any]:
    """What produced these numbers: versions from what is installed, plus the hardware."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        from report_versions import collect  # type: ignore[import-not-found]

        versions = collect()
    except Exception as exc:  # a missing reporter must not silently blank the record
        versions = {"error": f"could not collect versions: {type(exc).__name__}: {exc}"}

    return {
        "git_commit": _git_commit(),
        "hostname": platform.node(),
        "cpu_count": os.cpu_count(),
        "container_memory_bytes": _container_memory_bytes(),
        "in_container": Path("/.dockerenv").exists(),
        "versions": versions,
    }


@dataclass
class Measurement:
    """One recorded number or fact, with the unit it is in."""

    name: str
    value: Any
    unit: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "value": self.value, "unit": self.unit, "note": self.note}


@dataclass
class Trial:
    """One repetition inside an experiment: a condition, what it did, what was measured."""

    condition: str
    measurements: list[Measurement] = field(default_factory=list)
    observations: dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    detail: str = ""

    def record(self, name: str, value: Any, unit: str = "", note: str = "") -> None:
        self.measurements.append(Measurement(name, value, unit, note))

    def get(self, name: str) -> Any:
        for m in self.measurements:
            if m.name == name:
                return m.value
        raise KeyError(f"{self.condition} recorded no measurement named {name!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "ok": self.ok,
            "detail": self.detail,
            "measurements": [m.to_dict() for m in self.measurements],
            "observations": self.observations,
        }


class Experiment:
    """One experiment's run directory, configuration and measurements.

    Used as a context manager so a crash still leaves a directory with the configuration and
    a `failed` result in it, rather than nothing. An experiment that half-ran and vanished is
    indistinguishable from one that was never attempted, which is the failure mode this
    guards against.
    """

    def __init__(
        self,
        exp_id: str,
        *,
        title: str,
        hypothesis: str,
        independent_variable: str,
        dependent_measures: list[str],
        config: dict[str, Any],
        root: Path | None = None,
    ) -> None:
        self.exp_id = exp_id
        self.title = title
        self.hypothesis = hypothesis
        self.independent_variable = independent_variable
        self.dependent_measures = dependent_measures
        self.config = config
        self.started_at = datetime.now(UTC)
        stamp = self.started_at.strftime("%Y%m%dT%H%M%SZ")
        self.dir = (root or EXPERIMENTS_ROOT) / exp_id / stamp
        self.trials: list[Trial] = []
        self.limitations: list[str] = []
        self.status = "failed"  # until something says otherwise
        self.conclusion = ""
        self.hypothesis_held: bool | None = None
        self._t0 = 0.0

    # -- lifecycle -------------------------------------------------------------
    def __enter__(self) -> Experiment:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "config.json").write_text(
            json.dumps(
                {
                    "exp_id": self.exp_id,
                    "title": self.title,
                    "hypothesis": self.hypothesis,
                    "independent_variable": self.independent_variable,
                    "dependent_measures": self.dependent_measures,
                    "started_at": self.started_at.isoformat(),
                    "config": self.config,
                },
                indent=2, default=str,
            ),
            encoding="utf-8",
        )
        (self.dir / "environment.json").write_text(
            json.dumps(environment_record(), indent=2, default=str), encoding="utf-8"
        )
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self.status = "failed"
            self.conclusion = f"the experiment raised {exc_type.__name__}: {exc}"
            (self.dir / "traceback.txt").write_text(
                "".join(traceback.format_exception(exc_type, exc, tb)), encoding="utf-8"
            )
        self._write_result()
        return False  # never swallow the exception

    # -- recording -------------------------------------------------------------
    def trial(self, condition: str) -> Trial:
        t = Trial(condition=condition)
        self.trials.append(t)
        return t

    def limitation(self, text: str) -> None:
        """Record something the environment would not let this experiment do."""
        self.limitations.append(text)

    def conclude(self, *, status: str, hypothesis_held: bool | None, conclusion: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}, not {status!r}")
        if status == "partial" and not self.limitations:
            raise ValueError(
                "a partial result must record the limitation that reduced it; call limitation()"
            )
        if status == "not_run" and not self.limitations:
            raise ValueError("a not_run result must record why it could not run")
        if hypothesis_held is not None and not any(t.measurements for t in self.trials):
            raise ValueError(
                "a verdict on the hypothesis needs measurements behind it; none were recorded"
            )
        self.status = status
        self.hypothesis_held = hypothesis_held
        self.conclusion = conclusion

    # -- output ----------------------------------------------------------------
    def artifact(self, name: str, payload: Any) -> Path:
        """Write one raw artifact next to the result and return its path."""
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, (dict, list)):
            path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        else:
            path.write_text(str(payload), encoding="utf-8")
        return path

    def to_dict(self) -> dict[str, Any]:
        return {
            "exp_id": self.exp_id,
            "title": self.title,
            "hypothesis": self.hypothesis,
            "independent_variable": self.independent_variable,
            "dependent_measures": self.dependent_measures,
            "started_at": self.started_at.isoformat(),
            "duration_s": round(time.perf_counter() - self._t0, 2),
            "status": self.status,
            "hypothesis_held": self.hypothesis_held,
            "conclusion": self.conclusion,
            "limitations": self.limitations,
            "trials": [t.to_dict() for t in self.trials],
        }

    def _write_result(self) -> None:
        result = self.to_dict()
        _write_atomically(self.dir / "result.json", json.dumps(result, indent=2, default=str))
        # A stable path to the newest run, so docs and the API cite one location.
        payload = json.dumps({**result, "run_dir": self.dir.name}, indent=2, default=str)
        _write_atomically(self.dir.parent / "latest.json", payload)


def _write_atomically(path: Path, text: str) -> None:
    """Write beside the target, then rename over it: the file is whole or untouched.

    Writing in place truncated `result.json` first, so when EXP-5's container ran out of memory
    mid-write on 09-10-2026 the run was left with an empty result - and a good `latest.json`
    could be left empty the same way.
    """
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def load_history(exp_id: str, root: Path | None = None) -> list[dict[str, Any]]:
    """Every recorded run of one experiment, newest first.

    A run with a result gives its status, verdict and timing; a folder holding only a NOTE.md -
    a run that was interrupted, or a write that was not a run at all - gives its note. So the
    history shows every attempt, not only the ones that finished, which is how a reader can see
    that a result was re-run and why.
    """
    base = (root or EXPERIMENTS_ROOT) / exp_id
    runs: list[dict[str, Any]] = []
    for folder in sorted((p for p in base.glob("*") if p.is_dir()), reverse=True):
        entry: dict[str, Any] = {"run_dir": folder.name}
        result, note = folder / "result.json", folder / "NOTE.md"
        if result.exists():
            try:
                data = json.loads(result.read_text(encoding="utf-8"))
                entry.update({k: data.get(k) for k in
                              ("started_at", "duration_s", "status", "hypothesis_held")})
            except (OSError, json.JSONDecodeError):
                entry["status"] = "unreadable"
        if note.exists():
            entry["note"] = note.read_text(encoding="utf-8", errors="replace")[:4000]
        if len(entry) == 1 and (folder / "config.json").exists():
            # Started - the harness writes its config first - and never finished: a run that
            # was killed before it could write a result.
            entry["status"] = "incomplete"
        if len(entry) > 1:
            runs.append(entry)
    return runs


def load_results(root: Path | None = None) -> list[dict[str, Any]]:
    """Every experiment's newest result, for the API and the evaluation report."""
    base = root or EXPERIMENTS_ROOT
    out: list[dict[str, Any]] = []
    for latest in sorted(base.glob("EXP-*/latest.json")):
        try:
            out.append(json.loads(latest.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out
