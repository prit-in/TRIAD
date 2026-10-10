"""Which registered model a run trains: the platform's, unless the run names another."""

from __future__ import annotations

from types import SimpleNamespace

from agents.graph.churn import MODEL_NAME, model_name_for


def test_a_run_trains_the_platform_model_unless_it_names_another() -> None:
    assert model_name_for(SimpleNamespace(config_json=None)) == MODEL_NAME
    assert model_name_for(SimpleNamespace(config_json={})) == MODEL_NAME
    assert model_name_for(SimpleNamespace(config_json={"model_name": "exp07_m"})) == "exp07_m"


def test_the_in_process_jvm_is_asked_to_give_its_idle_heap_back() -> None:
    """After the phase's last Spark use the JVM is collected once; a session that cannot be
    asked (or has gone) is no reason to fail the run."""
    from agents.graph.churn import release_jvm_heap

    calls: list[str] = []
    jvm = SimpleNamespace(System=SimpleNamespace(gc=lambda: calls.append("gc")))
    release_jvm_heap(SimpleNamespace(sparkContext=SimpleNamespace(_jvm=jvm)))
    assert calls == ["gc"]
    release_jvm_heap(SimpleNamespace())   # no context at all: nothing raised
