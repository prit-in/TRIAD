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

    # -- invocation ------------------------------------------------------------
    def call(self, call: ToolCall, **context: Any) -> ToolResult:
        """Validate, authorise and run one tool call. Never raises for refusals."""
        started = time.perf_counter()

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
            if self._audit_sink and (spec is None or spec.audit_required or not ok):
                self._audit_sink(call, result)
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

        try:
            value = self._impls[call.tool](**call.arguments, **context)
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
