"""A loaded configuration is each caller's own: the parse is cached, the dict is not shared."""

from __future__ import annotations

from triad_core.config import load_yaml


def test_one_callers_change_never_reaches_the_next(tmp_path) -> None:
    """EXP-7 set its retraining cooldown to 0 on the dict `load_yaml` returned, and because that
    was the cached dict, the next pipeline in the process published with no cooldown and queued
    a retrain no rule allowed."""
    path = tmp_path / "pipeline.yaml"
    path.write_text("retraining:\n  cooldown_days: 7\n", encoding="utf-8")

    first = load_yaml(path)
    first["retraining"]["cooldown_days"] = 0

    assert load_yaml(path)["retraining"]["cooldown_days"] == 7
