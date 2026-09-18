"""Budget enforcement (FR-058, NFR-CST-01, ATC-14).

Ceilings live in the orchestrator, never in the agent: an agent cannot decide to keep going.
Every check is a pure function of counters that are persisted with the run, so a restarted
orchestrator resumes with the budget already spent.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class BudgetKind(str, Enum):
    LLM_CALLS = "max_llm_calls"
    LLM_COST = "max_llm_cost_usd"
    WALL_CLOCK = "max_wall_clock_minutes"
    REPAIR_ATTEMPTS = "max_code_repair_attempts"
    INFRA_RETRIES = "max_infra_retries"
    OPTIMISATION_CYCLES = "max_optimisation_cycles"
    FINGERPRINT_REPEATS = "max_identical_fingerprint"


class BudgetExceeded(Exception):
    """Raised when a ceiling is reached. The orchestrator turns this into ESCALATED."""

    def __init__(self, kind: BudgetKind, used: float, limit: float) -> None:
        super().__init__(f"budget exceeded: {kind.value} used {used} of {limit}")
        self.kind = kind
        self.used = used
        self.limit = limit

    def to_dict(self) -> dict[str, Any]:
        return {"budget": self.kind.value, "used": self.used, "limit": self.limit}


DEFAULT_LIMITS: dict[str, float] = {
    BudgetKind.LLM_CALLS.value: 60,
    BudgetKind.LLM_COST.value: 3.0,
    BudgetKind.WALL_CLOCK.value: 240,
    BudgetKind.REPAIR_ATTEMPTS.value: 3,
    BudgetKind.INFRA_RETRIES.value: 2,
    BudgetKind.OPTIMISATION_CYCLES.value: 2,
    BudgetKind.FINGERPRINT_REPEATS.value: 2,
}


@dataclass
class RunBudget:
    """Mutable counters for one run. Persisted to `pipeline_runs.budget_json`."""

    limits: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    llm_calls: int = 0
    llm_cost_usd: float = 0.0
    started_monotonic: float = field(default_factory=time.monotonic)
    elapsed_minutes_at_resume: float = 0.0
    repair_attempts: dict[str, int] = field(default_factory=dict)
    infra_retries: dict[str, int] = field(default_factory=dict)
    optimisation_cycles: int = 0
    fingerprint_counts: dict[str, int] = field(default_factory=dict)

    # -- serialisation ---------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "limits": self.limits,
            "llm_calls": self.llm_calls,
            "llm_cost_usd": round(self.llm_cost_usd, 6),
            "elapsed_minutes": round(self.elapsed_minutes, 3),
            "repair_attempts": self.repair_attempts,
            "infra_retries": self.infra_retries,
            "optimisation_cycles": self.optimisation_cycles,
            "fingerprint_counts": self.fingerprint_counts,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RunBudget:
        data = data or {}
        return cls(
            limits={**DEFAULT_LIMITS, **(data.get("limits") or {})},
            llm_calls=int(data.get("llm_calls", 0)),
            llm_cost_usd=float(data.get("llm_cost_usd", 0.0)),
            elapsed_minutes_at_resume=float(data.get("elapsed_minutes", 0.0)),
            repair_attempts=dict(data.get("repair_attempts") or {}),
            infra_retries=dict(data.get("infra_retries") or {}),
            optimisation_cycles=int(data.get("optimisation_cycles", 0)),
            fingerprint_counts=dict(data.get("fingerprint_counts") or {}),
        )

    @property
    def elapsed_minutes(self) -> float:
        return self.elapsed_minutes_at_resume + (time.monotonic() - self.started_monotonic) / 60.0

    # -- checks ----------------------------------------------------------------
    def check_all(self) -> None:
        """Called before every stage attempt and before every LLM call."""
        if self.llm_calls >= self.limits[BudgetKind.LLM_CALLS.value]:
            raise BudgetExceeded(
                BudgetKind.LLM_CALLS, self.llm_calls, self.limits[BudgetKind.LLM_CALLS.value]
            )
        if self.llm_cost_usd >= self.limits[BudgetKind.LLM_COST.value]:
            raise BudgetExceeded(
                BudgetKind.LLM_COST, self.llm_cost_usd, self.limits[BudgetKind.LLM_COST.value]
            )
        elapsed = self.elapsed_minutes
        if elapsed >= self.limits[BudgetKind.WALL_CLOCK.value]:
            raise BudgetExceeded(
                BudgetKind.WALL_CLOCK, elapsed, self.limits[BudgetKind.WALL_CLOCK.value]
            )

    def spend_llm(self, cost_usd: float = 0.0) -> None:
        self.check_all()
        self.llm_calls += 1
        self.llm_cost_usd += max(0.0, cost_usd)

    def next_repair_attempt(self, stage: str) -> int:
        used = self.repair_attempts.get(stage, 0)
        limit = self.limits[BudgetKind.REPAIR_ATTEMPTS.value]
        if used >= limit:
            raise BudgetExceeded(BudgetKind.REPAIR_ATTEMPTS, used, limit)
        self.repair_attempts[stage] = used + 1
        return self.repair_attempts[stage]

    def next_infra_retry(self, stage: str) -> int:
        used = self.infra_retries.get(stage, 0)
        limit = self.limits[BudgetKind.INFRA_RETRIES.value]
        if used >= limit:
            raise BudgetExceeded(BudgetKind.INFRA_RETRIES, used, limit)
        self.infra_retries[stage] = used + 1
        return self.infra_retries[stage]

    def next_optimisation_cycle(self) -> int:
        limit = self.limits[BudgetKind.OPTIMISATION_CYCLES.value]
        if self.optimisation_cycles >= limit:
            raise BudgetExceeded(BudgetKind.OPTIMISATION_CYCLES, self.optimisation_cycles, limit)
        self.optimisation_cycles += 1
        return self.optimisation_cycles

    def record_fingerprint(self, fingerprint: str) -> int:
        """Circuit breaker: the same normalised error twice in a row stops the loop (ATC-10)."""
        count = self.fingerprint_counts.get(fingerprint, 0) + 1
        self.fingerprint_counts[fingerprint] = count
        limit = self.limits[BudgetKind.FINGERPRINT_REPEATS.value]
        if count >= limit:
            raise BudgetExceeded(BudgetKind.FINGERPRINT_REPEATS, count, limit)
        return count

    def remaining(self) -> dict[str, float]:
        return {
            "llm_calls": self.limits[BudgetKind.LLM_CALLS.value] - self.llm_calls,
            "llm_cost_usd": self.limits[BudgetKind.LLM_COST.value] - self.llm_cost_usd,
            "wall_clock_minutes": self.limits[BudgetKind.WALL_CLOCK.value] - self.elapsed_minutes,
        }
