"""Which registered model a run trains: the platform's, unless the run names another."""

from __future__ import annotations

from types import SimpleNamespace

from agents.graph.churn import MODEL_NAME, model_name_for


def test_a_run_trains_the_platform_model_unless_it_names_another() -> None:
    assert model_name_for(SimpleNamespace(config_json=None)) == MODEL_NAME
    assert model_name_for(SimpleNamespace(config_json={})) == MODEL_NAME
    assert model_name_for(SimpleNamespace(config_json={"model_name": "exp07_m"})) == "exp07_m"
