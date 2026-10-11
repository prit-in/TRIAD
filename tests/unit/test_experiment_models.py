"""Which models the Models page marks as an experiment's: only one a run named for itself."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from backend.app.api.v1.models import _experiment


def _session(runs: dict) -> SimpleNamespace:
    return SimpleNamespace(get=lambda _cls, key: runs.get(key))


def test_the_platform_model_trained_by_experiments_is_not_an_experiments_model() -> None:
    """EXP-1 and EXP-5 train `churn_classifier` and say so in their run config; counting any
    experiment run hid the platform's champion behind the 'experiment models' switch."""
    platform, named = uuid.uuid4(), uuid.uuid4()
    runs = {
        platform: SimpleNamespace(config_json={"experiment": "EXP-5"}),
        named: SimpleNamespace(config_json={"experiment": "EXP-7",
                                            "model_name": "churn_classifier_exp07_ab12cd"}),
    }
    assert _experiment(_session(runs), SimpleNamespace(name="churn_classifier"),
                       [SimpleNamespace(run_id=platform)]) is None
    assert _experiment(_session(runs), SimpleNamespace(name="churn_classifier_exp07_ab12cd"),
                       [SimpleNamespace(run_id=named)]) == "EXP-7"
