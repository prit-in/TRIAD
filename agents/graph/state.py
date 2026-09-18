"""The typed run state carried through the graph (PRD section 12.1).

It holds references, never data: dataset version ids, storage URIs, artifact hashes, metric
ids and decision ids. Raw customer rows never enter the state, and therefore never enter an
LLM prompt (DD-03).
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict


def _merge_dict(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    return {**(left or {}), **(right or {})}


def _append(left: list[Any] | None, right: list[Any] | None) -> list[Any]:
    return [*(left or []), *(right or [])]


def _replace(_: Any, right: Any) -> Any:
    return right


class RunState(TypedDict, total=False):
    """LangGraph state. Reducers make concurrent node writes predictable."""

    run_id: Annotated[str, _replace]
    run_key: Annotated[str, _replace]
    pipeline: Annotated[str, _replace]
    mode: Annotated[str, _replace]          # full | incremental | replay
    state: Annotated[str, _replace]         # the PRD state machine value

    # inputs and artifacts, all by reference
    input_versions: Annotated[dict[str, Any], _merge_dict]
    artifacts: Annotated[dict[str, Any], _merge_dict]
    metrics: Annotated[dict[str, Any], _merge_dict]

    # bookkeeping
    budget: Annotated[dict[str, Any], _replace]
    attempts: Annotated[dict[str, int], _merge_dict]
    fingerprints: Annotated[dict[str, int], _merge_dict]
    decisions: Annotated[list[str], _append]
    errors: Annotated[list[dict[str, Any]], _append]
    routing: Annotated[list[dict[str, Any]], _append]

    # governance
    approval_id: Annotated[str | None, _replace]
    approval_status: Annotated[str | None, _replace]
    policy_version: Annotated[str, _replace]

    # outcome
    escalation: Annotated[dict[str, Any] | None, _replace]
    finished: Annotated[bool, _replace]


TERMINAL_STATES = {"COMPLETED", "ABORTED", "REJECTED_INPUT", "CANCELLED"}

# The transitions the orchestrator will accept, from PRD sections 12.2 and 12.3. A move that
# is not listed is a bug in the orchestrator, not a new behaviour to allow at run time.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "RECEIVED": {"PROFILED", "REJECTED_INPUT", "ESCALATED", "CANCELLED"},
    "PROFILED": {"PROFILED", "PLANNED", "ESCALATED", "APPROVAL_REQUIRED", "CANCELLED"},
    "PLANNED": {"CODE_GENERATED", "ESCALATED", "CANCELLED"},
    "CODE_GENERATED": {"VALIDATED", "FAILED", "REGENERATING", "ESCALATED", "CANCELLED"},
    "VALIDATED": {"EXECUTING", "FAILED", "ESCALATED", "CANCELLED"},
    "EXECUTING": {"FEATURES_READY", "FAILED", "ESCALATED", "CANCELLED"},
    "FEATURES_READY": {"TRAINING", "ESCALATED", "CANCELLED"},
    "TRAINING": {"EVALUATED", "ESCALATED", "CANCELLED"},
    "EVALUATED": {"OPTIMIZING", "SELECTED", "ESCALATED", "CANCELLED"},
    "OPTIMIZING": {"TRAINING", "ESCALATED", "CANCELLED"},
    "SELECTED": {"APPROVAL_REQUIRED", "PUBLISHED", "ESCALATED", "CANCELLED"},
    "APPROVAL_REQUIRED": {"DEPLOYED", "PUBLISHED", "ESCALATED", "CANCELLED"},
    "DEPLOYED": {"PUBLISHED", "ESCALATED", "CANCELLED"},
    "PUBLISHED": {"MONITORING", "COMPLETED", "CANCELLED"},
    "MONITORING": {"COMPLETED", "CANCELLED"},
    "FAILED": {"DIAGNOSING", "ESCALATED", "CANCELLED"},
    "DIAGNOSING": {"REGENERATING", "RETRYING", "ESCALATED", "CANCELLED"},
    "REGENERATING": {"RETRYING", "ESCALATED", "CANCELLED"},
    "RETRYING": {"EXECUTING", "CODE_GENERATED", "ESCALATED", "CANCELLED"},
    "ESCALATED": {"PLANNED", "ABORTED", "CANCELLED"},
}


class IllegalTransition(Exception):
    pass


def check_transition(current: str, nxt: str) -> None:
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if nxt not in allowed:
        raise IllegalTransition(
            f"{current} -> {nxt} is not a permitted transition; allowed: {sorted(allowed)}"
        )


def new_state(run_id: str, run_key: str, *, pipeline: str, mode: str = "full",
              policy_version: str = "unknown") -> RunState:
    return RunState(
        run_id=run_id,
        run_key=run_key,
        pipeline=pipeline,
        mode=mode,
        state="RECEIVED",
        input_versions={},
        artifacts={},
        metrics={},
        budget={},
        attempts={},
        fingerprints={},
        decisions=[],
        errors=[],
        routing=[],
        approval_id=None,
        approval_status=None,
        policy_version=policy_version,
        escalation=None,
        finished=False,
    )
