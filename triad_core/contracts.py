"""Data contracts (FR-007, FR-023, P1/P8 checks) — deterministic, no LLM in the path.

A contract is YAML: required columns with types and nullability, plus checks compiled into
Spark SQL aggregate expressions (CR-11). The same object validates an ingest contract and an
output contract, and carries the row-accounting identity used after every cleaning run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    type: str
    required: bool = False
    nullable: bool = True
    note: str = ""
    # Names this column may arrive under that a person has already approved (PRD 20.2: a
    # renamed required column "always needs approval unless in the alias list").
    aliases: tuple[str, ...] = ()


# How alike two names must be before one is proposed as the other (difflib ratio). Against
# customer_id, cust_id scores 0.78, store_id 0.74 and client_id 0.50. The bar is low on
# purpose: a proposal is a question put to a person, and the approval is the filter.
MIN_NAME_SIMILARITY = 0.6


@dataclass(frozen=True)
class CheckSpec:
    id: str
    rule: str                    # a Spark SQL boolean expression that must hold per row
    severity: str = "blocking"   # blocking | warning
    max_violation_pct: float = 0.0
    note: str = ""


@dataclass
class Violation:
    check_id: str
    severity: str
    message: str
    count: int | None = None
    pct: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "severity": self.severity,
            "message": self.message,
            "count": self.count,
            "pct": self.pct,
        }


@dataclass
class ContractResult:
    passed: bool
    violations: list[Violation] = field(default_factory=list)

    @property
    def blocking(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "blocking"]

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "violations": [v.to_dict() for v in self.violations]}


# Type equivalences: the physical type Spark reports vs what the contract asks for.
_TYPE_ALIASES = {
    "string": {"string", "varchar", "text", "utf8"},
    "int": {"int", "integer", "int32", "short", "smallint"},
    "long": {"long", "bigint", "int64"},
    "double": {"double", "float64", "float", "real"},
    "boolean": {"boolean", "bool"},
    "date": {"date", "date32"},
    "timestamp": {"timestamp", "timestamp_ntz", "datetime", "timestamp[us]"},
}


def _type_matches(expected: str, actual: str) -> bool:
    e, a = expected.lower().strip(), actual.lower().strip()
    if e == a:
        return True
    if e.startswith("decimal") and a.startswith("decimal"):
        return True  # precision/scale differences are handled by the cast rules
    # A decimal column read as double (or vice versa) is a real difference worth flagging.
    return any(e in aliases and a in aliases for aliases in _TYPE_ALIASES.values())


@dataclass
class Contract:
    name: str
    version: str
    columns: list[ColumnSpec]
    checks: list[CheckSpec] = field(default_factory=list)
    grain: list[str] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str | Path) -> Contract:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Contract:
        return cls(
            name=raw.get("contract", raw.get("name", "unnamed")),
            version=str(raw.get("version", "1.0")),
            columns=[ColumnSpec(**{**c, "aliases": tuple(c.get("aliases") or ())})
                     for c in raw.get("columns", [])],
            checks=[CheckSpec(**c) for c in raw.get("checks", [])],
            grain=list(raw.get("grain", [])),
        )

    @property
    def required_columns(self) -> list[str]:
        return [c.name for c in self.columns if c.required]

    def validate_schema(self, schema: dict[str, str]) -> ContractResult:
        """Check column presence and types against an observed {column: type} mapping."""
        violations: list[Violation] = []
        lowered = {k.lower(): (k, v) for k, v in schema.items()}
        for col in self.columns:
            found = lowered.get(col.name.lower())
            if found is None:
                if col.required:
                    violations.append(
                        Violation(
                            f"schema.required.{col.name}",
                            "blocking",
                            f"required column {col.name} missing",
                        )
                    )
                else:
                    violations.append(
                        Violation(
                            f"schema.optional.{col.name}",
                            "warning",
                            f"optional column {col.name} missing; dependent features disabled",
                        )
                    )
                continue
            _, actual_type = found
            if not _type_matches(col.type, actual_type):
                # A type difference is a warning, never blocking, and the reason is FR-007 and
                # EXP-4 read together. FR-007 blocks on a *missing required column*. EXP-4
                # expects a price arriving as "INR 871.59" to be "fixed by try_cast and
                # quarantine" - which can only happen if the file is allowed past ingest and
                # reaches the cleaning stage that exists to conform it. Blocking here would
                # make that stage unreachable for the very fault it was built for, and would
                # also refuse the project's own generator, which writes price as a double
                # against a contract that asks for decimal(18,2).
                #
                # The mismatch is still recorded on the registration result and in the audit
                # trail, so it is visible rather than forgiven; what changes is who acts on it.
                violations.append(
                    Violation(
                        f"schema.type.{col.name}",
                        "warning",
                        f"column {col.name} has type {actual_type}, contract expects "
                        f"{col.type}; conforming it is the cleaning stage's job",
                    )
                )
        return ContractResult(passed=not any(v.severity == "blocking" for v in violations),
                              violations=violations)

    def propose_mappings(self, schema: dict[str, str]) -> list[dict[str, Any]]:
        """For each missing required column, the observed column it most likely arrived as.

        Candidates are the observed columns the contract does not name. One listed in the
        column's `aliases` is pre-approved; any other must be at least MIN_NAME_SIMILARITY alike
        by name and is only a proposal, applied after a person approves it (PRD 20.2, ATC-06).
        Type agreement is reported, not required: a CSV's inferred types rarely match a
        contract's, so filtering on them would hide exactly the renames this is for.
        """
        # ponytail: name similarity only; comparing values with the previous version would
        # separate two equally named candidates, add it when a dataset has such a pair.
        present = {k.lower() for k in schema}
        named = {c.name.lower() for c in self.columns}
        free = [k for k in schema if k.lower() not in named]
        proposals: list[dict[str, Any]] = []
        for col in self.columns:
            if not col.required or col.name.lower() in present or not free:
                continue
            aliases = {a.lower() for a in col.aliases}

            def score(name: str, col: ColumnSpec = col, aliases: set[str] = aliases) -> float:
                if name.lower() in aliases:
                    return 1.0
                return SequenceMatcher(None, col.name.lower(), name.lower()).ratio()

            best = max(free, key=score)
            pre_approved = best.lower() in aliases
            if not pre_approved and score(best) < MIN_NAME_SIMILARITY:
                continue
            free.remove(best)
            proposals.append({
                "column": col.name, "source": best, "similarity": round(score(best), 3),
                "type": schema[best], "expected_type": col.type,
                "type_matches": _type_matches(col.type, schema[best]),
                "pre_approved": pre_approved,
            })
        return proposals

    def check_expressions(self) -> list[tuple[str, str]]:
        """Compile checks to (check_id, Spark SQL aggregate) pairs (CR-11).

        Each aggregate counts the rows that *fail* the rule, so one pass over the data
        produces every violation count.
        """
        return [
            (c.id, f"SUM(CASE WHEN NOT ({c.rule}) THEN 1 ELSE 0 END) AS `viol_{c.id}`")
            for c in self.checks
        ]

    def evaluate_counts(self, total_rows: int, counts: dict[str, int]) -> ContractResult:
        """Turn per-check violation counts into a verdict."""
        violations: list[Violation] = []
        for check in self.checks:
            n = int(counts.get(check.id, 0) or 0)
            if n == 0:
                continue
            pct = (n / total_rows * 100.0) if total_rows else 0.0
            severity = check.severity
            if severity == "warning" and check.max_violation_pct and pct > check.max_violation_pct:
                severity = "blocking"
            violations.append(
                Violation(
                    check.id,
                    severity,
                    f"{n} rows fail `{check.rule}` ({pct:.2f}%)",
                    count=n,
                    pct=pct,
                )
            )
        return ContractResult(passed=not any(v.severity == "blocking" for v in violations),
                              violations=violations)


# --- Row accounting (FR-023) --------------------------------------------------


@dataclass(frozen=True)
class RowAccounting:
    """input = output + quarantined + removed_duplicates. Any mismatch fails the stage."""

    input_rows: int
    output_rows: int
    quarantined_rows: int
    removed_duplicates: int

    @property
    def balanced(self) -> bool:
        return self.input_rows == self.output_rows + self.quarantined_rows + self.removed_duplicates

    @property
    def difference(self) -> int:
        return self.input_rows - (
            self.output_rows + self.quarantined_rows + self.removed_duplicates
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_rows": self.input_rows,
            "output_rows": self.output_rows,
            "quarantined_rows": self.quarantined_rows,
            "removed_duplicates": self.removed_duplicates,
            "balanced": self.balanced,
            "difference": self.difference,
        }

    def require_balanced(self) -> None:
        if not self.balanced:
            raise ValueError(
                "row accounting mismatch: "
                f"input {self.input_rows} != output {self.output_rows} + quarantined "
                f"{self.quarantined_rows} + duplicates {self.removed_duplicates} "
                f"(difference {self.difference})"
            )


# --- Output contract for features/labels (P8) ---------------------------------


@dataclass
class OutputContract:
    """Grain uniqueness, value ranges and null limits for a produced table."""

    name: str
    grain: list[str]
    required_columns: list[str] = field(default_factory=list)
    max_null_share: dict[str, float] = field(default_factory=dict)
    ranges: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)

    def evaluate(
        self,
        *,
        total_rows: int,
        distinct_grain_rows: int,
        null_counts: dict[str, int],
        out_of_range_counts: dict[str, int],
        columns: list[str],
    ) -> ContractResult:
        violations: list[Violation] = []
        if total_rows == 0:
            violations.append(Violation("output.empty", "blocking", "output table is empty"))
        if distinct_grain_rows != total_rows:
            violations.append(
                Violation(
                    "output.grain",
                    "blocking",
                    f"grain {self.grain} is not unique: {total_rows} rows, "
                    f"{distinct_grain_rows} distinct",
                    count=total_rows - distinct_grain_rows,
                )
            )
        missing = [c for c in self.required_columns if c not in columns]
        if missing:
            violations.append(
                Violation("output.columns", "blocking", f"missing output columns: {missing}")
            )
        for col, cap in self.max_null_share.items():
            n = int(null_counts.get(col, 0) or 0)
            share = (n / total_rows) if total_rows else 0.0
            if share > cap:
                violations.append(
                    Violation(
                        f"output.null.{col}",
                        "blocking",
                        f"{col} null share {share:.3f} exceeds {cap}",
                        count=n,
                        pct=share * 100,
                    )
                )
        for col, n in out_of_range_counts.items():
            if n:
                lo, hi = self.ranges.get(col, (None, None))
                violations.append(
                    Violation(
                        f"output.range.{col}",
                        "blocking",
                        f"{n} rows of {col} outside [{lo}, {hi}]",
                        count=int(n),
                    )
                )
        return ContractResult(passed=not any(v.severity == "blocking" for v in violations),
                              violations=violations)
