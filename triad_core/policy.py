"""M18 Policy engine (PRD section 30): deterministic, fail-closed governance decisions.

No LLM is in this path. `check()` returns one of ALLOW / REQUIRE_APPROVAL / DENY with the
policy rule that produced it, and every caller must honour the verdict before acting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

VALID_AUTONOMY = {"forbidden", "advisory", "supervised", "bounded"}


class Verdict(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True)
class PolicyDecision:
    verdict: Verdict
    action: str
    rule_ids: list[str]
    reason: str
    approvers: list[str] = field(default_factory=list)
    approvers_all_of: list[str] = field(default_factory=list)
    separation_of_duties: bool = False
    required_evidence: list[str] = field(default_factory=list)
    limits: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.verdict is Verdict.ALLOW


class PolicyError(Exception):
    """Raised when the policy file itself is unusable. Callers must fail closed."""


class PolicyEngine:
    """Loads and evaluates the governance policy.

    A missing, unparseable or inconsistent file does not raise on every call: it is recorded
    and every gated action is DENIED, which is the behaviour SYS-05 and R-14 require.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.load_error: str | None = None
        self.policy: dict[str, Any] = {}
        self._load()

    # -- loading ---------------------------------------------------------------
    def _load(self) -> None:
        try:
            if not self.path.exists():
                raise PolicyError(f"policy file not found: {self.path}")
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise PolicyError("policy file is not a mapping")
            self._validate(raw)
            self.policy = raw
            self.load_error = None
        except Exception as exc:  # fail closed, never crash the process at import time
            self.policy = {}
            self.load_error = str(exc)

    @staticmethod
    def _validate(raw: dict[str, Any]) -> None:
        if "policy_version" not in raw:
            raise PolicyError("policy_version missing")
        actions = raw.get("actions")
        if not isinstance(actions, dict) or not actions:
            raise PolicyError("actions section missing or empty")
        for name, spec in actions.items():
            if not isinstance(spec, dict):
                raise PolicyError(f"action {name} is not a mapping")
            autonomy = spec.get("autonomy")
            if autonomy not in VALID_AUTONOMY:
                raise PolicyError(f"action {name} has invalid autonomy {autonomy!r}")
            if autonomy == "supervised" and not (
                spec.get("approvers") or spec.get("approvers_all_of")
            ):
                raise PolicyError(f"supervised action {name} names no approvers")

    @property
    def version(self) -> str:
        return str(self.policy.get("policy_version", "invalid"))

    @property
    def valid(self) -> bool:
        return self.load_error is None

    def defaults(self) -> dict[str, Any]:
        return dict(self.policy.get("defaults", {}))

    # -- evaluation ------------------------------------------------------------
    def check(
        self,
        action: str,
        *,
        context: dict[str, Any] | None = None,
        actor_role: str = "agent",
    ) -> PolicyDecision:
        """Decide whether `action` may run with the given context.

        `context` carries the measured facts a limit is compared against, for example
        {"rows_affected_pct": 8.0, "column_drop": False}.
        """
        context = context or {}
        if not self.valid:
            return PolicyDecision(
                Verdict.DENY,
                action,
                ["policy.invalid"],
                f"policy file unusable ({self.load_error}); failing closed",
            )

        spec = self.policy.get("actions", {}).get(action)
        if spec is None:
            # Unknown actions are refused: autonomy never widens by omission.
            return PolicyDecision(
                Verdict.DENY, action, ["policy.unknown_action"], "action is not in the policy file"
            )

        autonomy = spec["autonomy"]
        limits = dict(spec.get("limits", {}))
        rule = f"{action}.{autonomy}"

        if autonomy == "forbidden":
            return PolicyDecision(
                Verdict.DENY, action, [rule], spec.get("reason", "forbidden to agents (L0)")
            )

        if autonomy == "advisory":
            if actor_role in spec.get("performer_roles", []):
                return PolicyDecision(Verdict.ALLOW, action, [rule], "performed by a person")
            return PolicyDecision(
                Verdict.REQUIRE_APPROVAL,
                action,
                [rule],
                "advisory: a person must perform this action",
                approvers=list(spec.get("performer_roles", [])),
                required_evidence=list(spec.get("required_evidence", [])),
            )

        if autonomy == "supervised":
            return PolicyDecision(
                Verdict.REQUIRE_APPROVAL,
                action,
                [rule],
                "supervised action: explicit approval required",
                approvers=list(spec.get("approvers", [])),
                approvers_all_of=list(spec.get("approvers_all_of", [])),
                separation_of_duties=bool(spec.get("separation_of_duties", False)),
                required_evidence=list(spec.get("required_evidence", [])),
                limits=limits,
            )

        # bounded (L3): allowed only while every limit holds
        breaches = self._limit_breaches(limits, spec, context)
        if not breaches:
            return PolicyDecision(
                Verdict.ALLOW, action, [rule], "within bounded-autonomy limits", limits=limits
            )

        on_exceeded = spec.get("on_limit_exceeded", "deny")
        reason = "limit exceeded: " + "; ".join(breaches)
        if on_exceeded == "require_approval":
            return PolicyDecision(
                Verdict.REQUIRE_APPROVAL,
                action,
                [rule, f"{action}.limit"],
                reason,
                approvers=list(spec.get("approvers", [])),
                approvers_all_of=list(spec.get("approvers_all_of", [])),
                separation_of_duties=bool(spec.get("separation_of_duties", False)),
                required_evidence=list(spec.get("required_evidence", [])),
                limits=limits,
            )
        return PolicyDecision(Verdict.DENY, action, [rule, f"{action}.limit"], reason, limits=limits)

    @staticmethod
    def _limit_breaches(
        limits: dict[str, Any], spec: dict[str, Any], context: dict[str, Any]
    ) -> list[str]:
        breaches: list[str] = []

        pct_cap = limits.get("max_rows_affected_pct")
        if pct_cap is not None:
            actual = float(context.get("rows_affected_pct", 0.0))
            if actual > float(pct_cap):
                breaches.append(f"rows_affected_pct {actual} > {pct_cap}")

        if limits.get("allow_column_drop") is False and context.get("column_drop"):
            breaches.append("column drop is not permitted without approval")

        if limits.get("quarantine_required") and context.get("quarantine") is False:
            breaches.append("rule would delete rows instead of quarantining them")

        if limits.get("allow_dependency_change") is False and context.get("dependency_change"):
            breaches.append("generated code may not change dependencies")

        if limits.get("slots_only") and context.get("out_of_slot_change"):
            breaches.append("generated code changed content outside a declared slot")

        if limits.get("registered_families_only") and context.get("unregistered_family"):
            breaches.append(f"model family {context.get('unregistered_family')} is not registered")

        allowlist = spec.get("allowlist")
        if allowlist is not None:
            requested = context.get("settings", {}) or {}
            for key in requested:
                if key not in allowlist:
                    breaches.append(f"setting {key} is not on the allowlist")

        if limits.get("within_run_quota") and context.get("exceeds_quota"):
            breaches.append("requested resources exceed the run quota")

        profiles = limits.get("profiles")
        if profiles is not None and context.get("profile") and context["profile"] not in profiles:
            breaches.append(f"sandbox profile {context['profile']} is not permitted")

        condition = spec.get("condition")
        if condition and not context.get(condition):
            breaches.append(f"condition {condition} is not satisfied")

        target = limits.get("target")
        if target == "previous_recorded_champion_only" and context.get(
            "target_is_previous_champion"
        ) not in (None, True):
            breaches.append("rollback target is not the recorded previous champion")

        return breaches
