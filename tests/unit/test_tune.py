"""M11 hyper-parameter search (PRD 18.3, 18.4).

The trials use a stub trainer rather than a real fit: what is under test is the search - its
budget, its determinism and what it refuses to claim - not sklearn. A real fit would make
these tests minutes long and would not check anything extra.
"""

from __future__ import annotations

import time

import optuna
import pytest

from python_ml.triad_ml.candidates import Candidate
from python_ml.triad_ml.tune import (
    DEFAULT_TUNING,
    TUNABLE,
    TuningResult,
    search_space,
    tune_family,
    tune_finalists,
)


class FakeModel:
    """Just enough of a TrainedModel for the search to rank it."""

    def __init__(self, family: str, value: float, *, failed: bool = False, params=None):
        self.candidate = Candidate(family=family, role="tuned", params=params or {})
        self.valid_pr_auc = value
        self.failed = failed
        self.error = "deliberate failure" if failed else None
        self.train_seconds = 1.0
        self.model_size_mb = 1.0

    @property
    def family(self) -> str:
        return self.candidate.family


def scoring_fit(score):
    """A trainer whose validation score is a known function of the parameters."""

    def fit(candidate: Candidate) -> FakeModel:
        return FakeModel(candidate.family, score(candidate.params), params=candidate.params)

    return fit


# --- the search space ----------------------------------------------------------


@pytest.mark.parametrize("family", TUNABLE)
def test_every_tunable_family_has_a_space(family: str) -> None:
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    trial = optuna.create_study().ask()
    assert search_space(family, trial), f"{family} produced no parameters"


def test_an_untunable_family_is_refused_rather_than_silently_skipped() -> None:
    trial = optuna.create_study().ask()
    with pytest.raises(ValueError, match="no search space"):
        search_space("logreg", trial)


def test_logreg_is_deliberately_not_tuned() -> None:
    """AP-2 compares against the same simple model every time; tuning it moves the goalposts."""
    assert "logreg" not in TUNABLE
    assert "dummy" not in TUNABLE


# --- one study -----------------------------------------------------------------


def test_the_search_finds_a_better_point_than_the_baseline() -> None:
    # Score rises with max_depth, so a working search should end up near the top of the range.
    result = tune_family(
        "random_forest", split=None,  # type: ignore[arg-type]
        config={"n_trials": 12, "timeout_seconds": 30},
        baseline=0.10,
        fit=scoring_fit(lambda p: 0.1 + p["max_depth"] / 100.0),
    )
    assert result.trials_run == 12
    assert result.best_value is not None and result.best_value > 0.10
    assert result.improved
    assert result.best_params["max_depth"] >= 10


def test_a_search_that_beats_nothing_says_so() -> None:
    """Reporting a search as an improvement when it found none would falsify the record."""
    result = tune_family(
        "hist_gb", split=None,  # type: ignore[arg-type]
        config={"n_trials": 5, "timeout_seconds": 30},
        baseline=0.99,
        fit=scoring_fit(lambda _: 0.5),
    )
    assert result.best_value == pytest.approx(0.5)
    assert not result.improved


def test_the_same_seed_searches_the_same_points() -> None:
    """PRD 18.3: the same seed, data and versions must reproduce the result."""
    config = {"n_trials": 8, "timeout_seconds": 30, "seed": 7}
    fit = scoring_fit(lambda p: 0.1 + p["max_depth"] / 100.0)
    first = tune_family("random_forest", split=None, config=config, baseline=0.0, fit=fit)  # type: ignore[arg-type]
    second = tune_family("random_forest", split=None, config=config, baseline=0.0, fit=fit)  # type: ignore[arg-type]
    assert first.best_params == second.best_params
    assert first.best_value == second.best_value


def test_a_different_seed_searches_differently() -> None:
    fit = scoring_fit(lambda p: 0.1 + p["max_depth"] / 100.0)
    a = tune_family("random_forest", split=None, fit=fit, baseline=0.0,  # type: ignore[arg-type]
                    config={"n_trials": 8, "timeout_seconds": 30, "seed": 1})
    b = tune_family("random_forest", split=None, fit=fit, baseline=0.0,  # type: ignore[arg-type]
                    config={"n_trials": 8, "timeout_seconds": 30, "seed": 2})
    assert a.best_params != b.best_params or a.best_value != b.best_value


def test_a_failing_candidate_is_pruned_not_fatal() -> None:
    calls = {"n": 0}

    def fit(candidate: Candidate) -> FakeModel:
        calls["n"] += 1
        return FakeModel(candidate.family, 0.4, failed=calls["n"] % 2 == 0)

    result = tune_family("xgboost", split=None, config={"n_trials": 6, "timeout_seconds": 30},  # type: ignore[arg-type]
                         baseline=0.1, fit=fit)
    assert calls["n"] == 6
    assert result.trials_run == 3, "only the successful trials count"
    assert result.best_value == pytest.approx(0.4)


def test_a_search_with_no_usable_trial_reports_it() -> None:
    result = tune_family(
        "xgboost", split=None, config={"n_trials": 3, "timeout_seconds": 30},  # type: ignore[arg-type]
        baseline=0.1, fit=lambda c: FakeModel(c.family, 0.0, failed=True),
    )
    assert result.best_value is None
    assert "no trial produced a value" in result.stopped_because
    assert not result.improved


def test_the_run_wide_deadline_stops_the_search_and_says_why() -> None:
    """The budget belongs to the run, not to each study."""
    result = tune_family(
        "random_forest", split=None,  # type: ignore[arg-type]
        config={"n_trials": 500, "timeout_seconds": 60},
        baseline=0.0, fit=scoring_fit(lambda p: p["max_depth"] / 100.0),
        deadline=time.perf_counter() - 1.0,   # already spent
    )
    assert result.trials_run == 0, "no trial produced a value"
    assert result.trials_attempted == 1, "the first trial is what notices the deadline"
    assert result.best_value is None
    assert "budget" in result.stopped_because


# --- the finalists -------------------------------------------------------------


def test_only_the_top_tunable_families_are_searched() -> None:
    models = [
        FakeModel("logreg", 0.9),          # not tunable, however good it is
        FakeModel("xgboost", 0.8),
        FakeModel("hist_gb", 0.7),
        FakeModel("random_forest", 0.6),   # third, so outside the finalists
    ]
    _, records = tune_finalists(
        models, split=None,  # type: ignore[arg-type]
        config={"finalists": 2, "n_trials": 3, "timeout_seconds": 20},
        fit=scoring_fit(lambda _: 0.5),
    )
    searched = {r["family"] for r in records}
    assert searched == {"xgboost", "hist_gb"}


def test_a_tuned_model_is_returned_only_when_it_actually_improved() -> None:
    models = [FakeModel("xgboost", 0.80), FakeModel("hist_gb", 0.10)]
    tuned, records = tune_finalists(
        models, split=None,  # type: ignore[arg-type]
        config={"finalists": 2, "n_trials": 4, "timeout_seconds": 20},
        fit=scoring_fit(lambda _: 0.50),
    )
    # 0.50 beats hist_gb's 0.10 but not xgboost's 0.80.
    assert [m.family for m in tuned] == ["hist_gb"]
    assert {r["family"]: r["improved"] for r in records} == {"xgboost": False, "hist_gb": True}


def test_tuning_can_be_turned_off_and_says_so() -> None:
    tuned, records = tune_finalists(
        [FakeModel("xgboost", 0.5)], split=None, config={"enabled": False},  # type: ignore[arg-type]
        fit=scoring_fit(lambda _: 0.9),
    )
    assert tuned == []
    assert "disabled" in records[0]["skipped"]


def test_nothing_to_tune_is_reported_rather_than_silently_empty() -> None:
    tuned, records = tune_finalists(
        [FakeModel("logreg", 0.5), FakeModel("dummy", 0.2)], split=None,  # type: ignore[arg-type]
        config={"n_trials": 2}, fit=scoring_fit(lambda _: 0.9),
    )
    assert tuned == []
    assert "no tunable family" in records[0]["skipped"]


def test_a_failed_family_is_not_a_finalist() -> None:
    models = [FakeModel("xgboost", 0.9, failed=True), FakeModel("hist_gb", 0.4)]
    _, records = tune_finalists(
        models, split=None, config={"finalists": 2, "n_trials": 2, "timeout_seconds": 20},  # type: ignore[arg-type]
        fit=scoring_fit(lambda _: 0.5),
    )
    assert [r["family"] for r in records] == ["hist_gb"]


def test_the_record_carries_the_budget_that_was_actually_used() -> None:
    result = TuningResult("xgboost", trials_run=7, trials_requested=30, seconds=12.34,
                          best_value=0.5, baseline_value=0.4,
                          stopped_because="the 900s study timeout was reached")
    payload = result.to_dict()
    assert payload["trials_run"] == 7 and payload["trials_requested"] == 30
    assert "trials_attempted" in payload
    assert payload["improved"] is True
    assert payload["stopped_because"]


def test_the_defaults_match_the_pipeline_configuration() -> None:
    """The config file and the module must not drift apart."""
    from triad_core.config import load_yaml, settings

    configured = load_yaml(settings().pipeline_config_path)["tuning"]
    for key in ("sampler", "seed", "n_trials", "timeout_seconds", "pruner", "objective"):
        assert key in configured, f"{key} is missing from pipelines/churn_v1.yaml"
        assert configured[key] == DEFAULT_TUNING[key], (
            f"{key}: the pipeline says {configured[key]}, the module defaults to "
            f"{DEFAULT_TUNING[key]}"
        )
