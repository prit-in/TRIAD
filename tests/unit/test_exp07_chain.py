"""EXP-7's end-to-end verdict: a part the chain never reached is untested, not refuted."""

from __future__ import annotations

from experiments.exp07_drift import CHAIN_PARTS, _conclude_chain
from experiments.harness import Trial


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
