"""The experiments' isolated runner: a result comes back, an exception is re-raised with its
text, and a child that outlives its ceiling is stopped and reported rather than waited on."""

from __future__ import annotations

import pytest

from experiments.baselines import IsolatedProcessKilled, run_isolated


def test_a_result_comes_back_from_the_child() -> None:
    assert run_isolated("json:dumps", obj={"a": 1}) == '{"a": 1}'


def test_an_exception_in_the_child_is_raised_with_its_text() -> None:
    with pytest.raises(RuntimeError, match="JSONDecodeError"):
        run_isolated("json:loads", s="{not json")


def test_a_child_past_its_ceiling_is_stopped_and_reported() -> None:
    with pytest.raises(IsolatedProcessKilled) as stopped:
        run_isolated("subprocess:run", timeout_s=1, args=["sleep", "30"])
    assert stopped.value.timed_out and stopped.value.elapsed_s < 20
