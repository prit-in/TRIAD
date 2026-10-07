"""PRD 12.2-12.4: the run state machine is enforced, and it describes the runs that happen.

`check_transition` and `ALLOWED_TRANSITIONS` were defined and called by nothing, so any move was
accepted, and the table had drifted from the lifecycle: runs go PLANNED -> FEATURES_READY, which
it refused. It is now enforced by `queue.set_state`, through which every transition passes.
"""

from __future__ import annotations

import uuid

import pytest

from agents.graph import queue as q
from agents.graph.state import ALLOWED_TRANSITIONS, IllegalTransition, check_transition

# Every transition recorded in `audit_logs` on 04-10-2026, across all 96 runs in the database.
# Each is a path the platform really takes, so the table must allow every one of them.
OBSERVED = [
    ("APPROVAL_REQUIRED", "DEPLOYED"), ("APPROVAL_REQUIRED", "PUBLISHED"),
    ("DEPLOYED", "PUBLISHED"), ("ESCALATED", "CANCELLED"), ("EVALUATED", "ESCALATED"),
    ("EVALUATED", "PROFILED"), ("EVALUATED", "SELECTED"), ("FEATURES_READY", "TRAINING"),
    ("PLANNED", "ESCALATED"), ("PLANNED", "FEATURES_READY"), ("PLANNED", "PROFILED"),
    ("PROFILED", "ESCALATED"), ("PROFILED", "PLANNED"), ("PUBLISHED", "COMPLETED"),
    ("RECEIVED", "CANCELLED"), ("RECEIVED", "FAILED"), ("RECEIVED", "PROFILED"),
    ("SELECTED", "APPROVAL_REQUIRED"), ("SELECTED", "CANCELLED"), ("SELECTED", "ESCALATED"),
    ("SELECTED", "PUBLISHED"), ("TRAINING", "EVALUATED"),
]


@pytest.mark.parametrize(("current", "nxt"), OBSERVED)
def test_every_observed_transition_is_allowed(current: str, nxt: str) -> None:
    check_transition(current, nxt)


@pytest.mark.parametrize(("current", "nxt"), [
    ("PLANNED", "COMPLETED"),        # skipping training and approval
    ("EVALUATED", "DEPLOYED"),       # deploying without selection or approval
    ("COMPLETED", "PROFILED"),       # a finished run does not restart
    ("REJECTED_INPUT", "PROFILED"),
    ("SELECTED", "DEPLOYED"),        # a promotion always passes APPROVAL_REQUIRED
])
def test_a_move_the_table_does_not_list_is_refused(current: str, nxt: str) -> None:
    with pytest.raises(IllegalTransition):
        check_transition(current, nxt)


def test_an_override_moves_an_escalated_run_to_approval() -> None:
    """PRD section 30: a promotion despite a failed check is requested from ESCALATED."""
    check_transition("ESCALATED", "APPROVAL_REQUIRED")
    check_transition("APPROVAL_REQUIRED", "ESCALATED")   # a rejected override returns it


def test_staying_in_a_state_is_not_a_move() -> None:
    check_transition("SELECTED", "SELECTED")


@pytest.mark.parametrize("current", ["REJECTED_INPUT", "COMPLETED", "FAILED", "PUBLISHED",
                                     "APPROVAL_REQUIRED", "RECEIVED"])
def test_any_state_may_be_cancelled(current: str) -> None:
    """PRD 12.3. The experiments cancel the run they drove, whatever state it ended in - a
    REJECTED_INPUT run included, which the table alone would have refused."""
    check_transition(current, "CANCELLED")


def test_every_target_is_a_state_the_table_knows() -> None:
    known = set(ALLOWED_TRANSITIONS) | {"COMPLETED", "ABORTED", "REJECTED_INPUT", "CANCELLED"}
    for current, targets in ALLOWED_TRANSITIONS.items():
        assert targets <= known, (current, targets - known)


def test_set_state_enforces_the_table(db_session) -> None:
    run = q.create_run(db_session, pipeline_name=f"sm-{uuid.uuid4().hex[:8]}",
                       trigger_type="manual", triggered_by="tester")
    q.set_state(db_session, run, "PROFILED")
    with pytest.raises(IllegalTransition):
        q.set_state(db_session, run, "DEPLOYED")
    assert run.state == "PROFILED", "a refused move leaves the run where it was"
