"""Configuration P's gate accounting: each gate's time summed over the run's generated jobs."""

from __future__ import annotations

from types import SimpleNamespace

from experiments.configuration_p import FULL_RUN_GATE, gate_seconds


def test_each_gate_is_summed_over_every_job_of_the_run() -> None:
    clean = SimpleNamespace(gate_results={"gates": [
        {"gate": 4, "duration_ms": 1500}, {"gate": FULL_RUN_GATE, "duration_ms": 1000}]})
    features = SimpleNamespace(gate_results={"gates": [
        {"gate": 4, "duration_ms": 500}, {"gate": "not a gate number", "duration_ms": 9}]})
    never_gated = SimpleNamespace(gate_results=None)
    assert gate_seconds([clean, features, never_gated]) == {4: 2.0, FULL_RUN_GATE: 1.0}
