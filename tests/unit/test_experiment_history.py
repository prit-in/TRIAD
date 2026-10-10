"""An experiment's history lists every recorded run, newest first, with the note that explains
a folder holding no result - so a re-run result shows that it was re-run, and why."""

from __future__ import annotations

import json

from experiments.harness import load_history


def test_every_run_is_listed_newest_first_with_its_note(tmp_path) -> None:
    exp = tmp_path / "EXP-7"
    (exp / "20261007T215709Z").mkdir(parents=True)
    (exp / "20261007T215709Z" / "result.json").write_text(json.dumps(
        {"started_at": "2026-10-07T21:57:09", "duration_s": 12.0, "status": "partial",
         "hypothesis_held": True}), encoding="utf-8")
    (exp / "20261010T184800Z").mkdir()
    (exp / "20261010T184800Z" / "NOTE.md").write_text("Not an experiment run.", encoding="utf-8")
    (exp / "pilot_20261010.md").write_text("a record, not a run folder", encoding="utf-8")
    (exp / "empty_folder").mkdir()

    runs = load_history("EXP-7", root=tmp_path)

    assert [r["run_dir"] for r in runs] == ["20261010T184800Z", "20261007T215709Z"]
    assert runs[0]["note"] == "Not an experiment run." and "status" not in runs[0]
    assert (runs[1]["status"], runs[1]["hypothesis_held"]) == ("partial", True)
