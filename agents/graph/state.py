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
#
# Enforced by `queue.set_state`, through which every transition passes. It was defined and never
# called, and had drifted from the runs it described: they go PLANNED -> FEATURES_READY (code
# generation, validation and execution are recorded per stage, in `code_artifacts` and
# `executions`, rather than as run states), a retried or resumed task restarts its phase at
# PROFILED, the stranded-run reaper fails a run in any live state, and a promotion despite a
# failed check (PRD section 30) moves an ESCALATED run to APPROVAL_REQUIRED. Each of those was
# read from the transitions recorded in `audit_logs`, not assumed.
_LIVE = {"ESCALATED", "CANCELLED", "FAILED"}
_RESTART = {"PROFILED"}   # a retried or resumed prepare phase starts again from profiling
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "RECEIVED": {"PROFILED", "REJECTED_INPUT"} | _LIVE,
    "PROFILED": {"PROFILED", "PLANNED", "APPROVAL_REQUIRED"} | _LIVE,
    "PLANNED": {"CODE_GENERATED", "FEATURES_READY"} | _RESTART | _LIVE,
    "CODE_GENERATED": {"VALIDATED", "REGENERATING"} | _LIVE,
    "VALIDATED": {"EXECUTING"} | _LIVE,
    "EXECUTING": {"FEATURES_READY"} | _LIVE,
    "FEATURES_READY": {"TRAINING"} | _RESTART | _LIVE,
    "TRAINING": {"EVALUATED"} | _RESTART | _LIVE,
    "EVALUATED": {"OPTIMIZING", "SELECTED"} | _RESTART | _LIVE,
    "OPTIMIZING": {"TRAINING"} | _LIVE,
    "SELECTED": {"APPROVAL_REQUIRED", "PUBLISHED"} | _RESTART | _LIVE,
    "APPROVAL_REQUIRED": {"DEPLOYED", "PUBLISHED"} | _LIVE,
    "DEPLOYED": {"PUBLISHED"} | _LIVE,
    "PUBLISHED": {"MONITORING", "COMPLETED", "CANCELLED"},
    "MONITORING": {"COMPLETED", "CANCELLED"},
    "FAILED": {"DIAGNOSING", "ESCALATED", "CANCELLED"},
    "DIAGNOSING": {"REGENERATING", "RETRYING"} | _LIVE,
    "REGENERATING": {"RETRYING"} | _LIVE,
    "RETRYING": {"EXECUTING", "CODE_GENERATED"} | _RESTART | _LIVE,
    "ESCALATED": {"PLANNED", "RETRYING", "APPROVAL_REQUIRED", "ABORTED", "CANCELLED", "FAILED"},
}


class IllegalTransition(Exception):
    pass


def check_transition(current: str, nxt: str) -> None:
    """Refuse a move the table does not list. Staying in a state is not a move.

    CANCELLED is reachable from every state, as PRD 12.3 says ("Any state can also move to
    CANCELLED on a user request"); whether a *person* may cancel a finished run is the cancel
    endpoint's rule, not the machine's. The experiments' clean-up relies on it: they cancel the
    run they drove whatever it ended in, REJECTED_INPUT included.
    """
    if current == nxt or nxt == "CANCELLED":
        return
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
