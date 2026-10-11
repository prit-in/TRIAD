"""EXP-7's end-to-end verdict: a part the chain never reached is untested, not refuted."""

from __future__ import annotations

import datetime as dt
import uuid

from experiments.exp07_drift import CHAIN_PARTS, _acceptance, _conclude_chain
from experiments.harness import Trial
from triad_core.ids import utcnow


def _verdict(**parts) -> object:
    trial = _conclude_chain(Trial(condition="end_to_end"),
                            parts={**dict.fromkeys(CHAIN_PARTS, True), **parts}, why="test")
    return trial.observations["verdict"], trial.ok


def test_all_parts_held_is_held_and_one_failed_part_refutes() -> None:
    assert _verdict() == (True, True)
    assert _verdict(retraining_started=False) == (False, False)


def test_a_part_never_reached_leaves_the_chain_untested() -> None:
    assert _verdict(challenger_compared=None) == (None, False)
    assert _verdict(challenger_compared=None, promotion_waited=False) == (False, False)


def test_the_comparison_is_read_from_the_latest_acceptance_decision(db_session) -> None:
    """A retrain that escalates without being eligible for an override writes no prepare state,
    only its acceptance decision: that decision is where EXP-7 reads the comparison (DEC-49)."""
    from triad_core.models import DecisionRecord, PipelineRun

    def run() -> PipelineRun:
        r = PipelineRun(run_key=f"run-e7-{uuid.uuid4().hex[:10]}", pipeline_name="churn_v1",
                        state="FAILED", trigger_type="drift", mode="full", input_versions={})
        db_session.add(r)
        db_session.flush()
        return r

    def decision(r, *, agent, kind, rules, minutes_ago) -> None:
        db_session.add(DecisionRecord(
            run_id=r.id, agent=agent, decision_type=kind, decision=f"{kind} {minutes_ago}",
            policy_rule_ids=rules, created_at=utcnow() - dt.timedelta(minutes=minutes_ago)))

    retrain, never_selected = run(), run()
    decision(retrain, agent="model_selection", kind="acceptance", rules=["AP-1"], minutes_ago=9)
    decision(retrain, agent="model_selection", kind="acceptance", rules=["AP-1", "AP-8"],
             minutes_ago=5)
    decision(retrain, agent="routing", kind="route", rules=[], minutes_ago=1)
    db_session.flush()

    latest = _acceptance(db_session, retrain.id)
    assert latest.decision == "acceptance 5" and "AP-8" in latest.policy_rule_ids
    assert _acceptance(db_session, never_selected.id) is None
