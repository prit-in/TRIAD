"""Tool registry (FR-053): the only way an agent can act.

Every tool declares a name, JSON schemas for input and output, the agent roles permitted to
call it, its side effects and whether it must be audited. Calls to unregistered tools,
calls with invalid arguments and calls by unauthorised agents are refused and logged
(ATC-02). The registry is deterministic; no LLM participates in the decision.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jsonschema import Draft202012Validator, ValidationError


class SideEffect(str, Enum):
    NONE = "none"           # read-only
    WRITE_STAGING = "write_staging"
    WRITE_CATALOG = "write_catalog"
    SUBMIT_JOB = "submit_job"
    REGISTRY_WRITE = "registry_write"
    NOTIFY = "notify"


class ToolError(Exception):
    """Base for refusals. Carrying a code lets the orchestrator classify without parsing text."""

    code = "tool_error"


class UnregisteredToolError(ToolError):
    code = "unregistered_tool"


class PermissionDeniedError(ToolError):
    code = "permission_denied"


class InvalidArgumentsError(ToolError):
    code = "invalid_arguments"


class InvalidResultError(ToolError):
    code = "invalid_result"


class PolicyRefusedError(ToolError):
    """The governance policy refused this action, or requires a person to approve it."""

    code = "policy_refused"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    permitted_agents: frozenset[str]
    owner: str
    side_effect: SideEffect = SideEffect.NONE
    audit_required: bool = True
    policy_action: str | None = None  # governance action consulted before running


@dataclass
class ToolCall:
    tool: str
    agent: str
    arguments: dict[str, Any]
    run_id: str | None = None
    task_id: str | None = None


@dataclass
class ToolResult:
    tool: str
    agent: str
    ok: bool
    value: Any = None
    error_code: str | None = None
    error: str | None = None
    duration_ms: int = 0
    audit: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "agent": self.agent,
            "ok": self.ok,
            "value": self.value,
            "error_code": self.error_code,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


class ToolRegistry:
    """Holds tool specs and their implementations, and mediates every call."""

    def __init__(self) -> None:
        self._policy: Any = None
        self._specs: dict[str, ToolSpec] = {}
        self._impls: dict[str, Callable[..., Any]] = {}
        self._audit_sink: Callable[[ToolCall, ToolResult], None] | None = None

    # -- registration ----------------------------------------------------------
    def register(self, spec: ToolSpec, impl: Callable[..., Any]) -> None:
        if spec.name in self._specs:
            raise ValueError(f"tool already registered: {spec.name}")
        Draft202012Validator.check_schema(spec.input_schema)
        Draft202012Validator.check_schema(spec.output_schema)
        self._specs[spec.name] = spec
        self._impls[spec.name] = impl

    def tool(
        self,
        name: str,
        *,
        description: str,
        input_schema: dict[str, Any],
        output_schema: dict[str, Any],
        permitted_agents: set[str] | frozenset[str],
        owner: str,
        side_effect: SideEffect = SideEffect.NONE,
        audit_required: bool = True,
        policy_action: str | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator form of `register`."""

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.register(
                ToolSpec(
                    name=name,
                    description=description,
                    input_schema=input_schema,
                    output_schema=output_schema,
                    permitted_agents=frozenset(permitted_agents),
                    owner=owner,
                    side_effect=side_effect,
                    audit_required=audit_required,
                    policy_action=policy_action,
                ),
                fn,
            )
            return fn

        return decorator

    def set_audit_sink(self, sink: Callable[[ToolCall, ToolResult], None]) -> None:
        self._audit_sink = sink

    # -- introspection ---------------------------------------------------------
    def spec(self, name: str) -> ToolSpec:
        if name not in self._specs:
            raise UnregisteredToolError(f"tool not registered: {name}")
        return self._specs[name]

    def names(self) -> list[str]:
        return sorted(self._specs)

    def tools_for(self, agent: str) -> list[str]:
        """The allowlist an agent is given; it never sees a tool it may not call."""
        return sorted(n for n, s in self._specs.items() if agent in s.permitted_agents)

    def describe_for(self, agent: str) -> list[dict[str, Any]]:
        """Tool descriptions handed to an agent: only what it is permitted to call."""
        return [
            {"name": s.name, "description": s.description, "input_schema": s.input_schema}
            for _, s in sorted(self._specs.items())
            if agent in s.permitted_agents
        ]

    def set_policy(self, policy: Any) -> None:
        """Give the registry the policy engine to consult for tools that name an action.

        Without one, a tool that declares `policy_action` is **refused**, not allowed. A
        governance check that silently turns itself off when unconfigured is not a check.
        """
        self._policy = policy

    def _policy_verdict(
        self, spec: ToolSpec, call: ToolCall, context: dict[str, Any]
    ) -> str | None:
        """Returns None when the action may proceed, otherwise why it may not."""
        policy = context.get("policy") or self._policy
        if policy is None:
            return (
                f"{call.tool} declares the governed action {spec.policy_action} and no policy "
                "engine is configured; refusing rather than proceeding ungoverned"
            )
        from triad_core.policy import Verdict

        decision = policy.check(
            spec.policy_action,
            context=dict(context.get("policy_context") or {}),
            actor_role="agent",
        )
        if decision.verdict is Verdict.ALLOW:
            return None
        return (
            f"{spec.policy_action} -> {decision.verdict.value}: {decision.reason} "
            f"(rules {', '.join(decision.rule_ids)})"
        )

    # -- invocation ------------------------------------------------------------
    def call(self, call: ToolCall, **context: Any) -> ToolResult:
        """Validate, authorise and run one tool call. Never raises for refusals.

        `context` carries the process services the implementation needs, and three keys the
        registry reads itself rather than passing on: `policy_context` (the measured facts
        the policy compares against), `policy` and `audit_sink`. Passing the last two per
        call, rather than relying on the ones set on this object, matters because the
        registry is a module singleton: two concurrent pipelines would otherwise overwrite
        each other and one run's tool calls would be audited into the other one's session.
        """
        started = time.perf_counter()
        sink = context.get("audit_sink") or self._audit_sink

        def finish(
            ok: bool, value: Any = None, error_code: str | None = None, error: str | None = None
        ) -> ToolResult:
            result = ToolResult(
                tool=call.tool,
                agent=call.agent,
                ok=ok,
                value=value,
                error_code=error_code,
                error=error,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            spec = self._specs.get(call.tool)
            if sink and (spec is None or spec.audit_required or not ok):
                sink(call, result)
            return result

        spec = self._specs.get(call.tool)
        if spec is None:
            return finish(
                False,
                error_code=UnregisteredToolError.code,
                error=f"tool not registered: {call.tool}",
            )

        if call.agent not in spec.permitted_agents:
            return finish(
                False,
                error_code=PermissionDeniedError.code,
                error=(
                    f"agent {call.agent} may not call {call.tool}; "
                    f"permitted: {sorted(spec.permitted_agents)}"
                ),
            )

        try:
            Draft202012Validator(spec.input_schema).validate(call.arguments)
        except ValidationError as exc:
            return finish(
                False,
                error_code=InvalidArgumentsError.code,
                error=f"invalid arguments for {call.tool}: {exc.message}",
            )

        # A tool that names a governance action is held against it here, before the
        # implementation runs. Without this the field was a promise nothing kept: a tool could
        # declare `policy_action` and still run whatever the policy said.
        if spec.policy_action is not None:
            verdict = self._policy_verdict(spec, call, context)
            if verdict is not None:
                return finish(False, error_code=PolicyRefusedError.code, error=verdict)

        try:
            # The registry's own context keys are not arguments to the implementation.
            reserved = {"policy_context", "policy", "audit_sink"}
            passthrough = {k: v for k, v in context.items() if k not in reserved}
            value = self._impls[call.tool](**call.arguments, **passthrough)
        except ToolError as exc:
            return finish(False, error_code=exc.code, error=str(exc))
        except Exception as exc:  # implementation faults surface as structured failures
            return finish(False, error_code="tool_exception", error=f"{type(exc).__name__}: {exc}")

        try:
            Draft202012Validator(spec.output_schema).validate(value)
        except ValidationError as exc:
            return finish(
                False,
                error_code=InvalidResultError.code,
                error=f"tool {call.tool} returned an invalid result: {exc.message}",
            )

        return finish(True, value=value)


REGISTRY = ToolRegistry()
