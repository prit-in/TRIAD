"""The business view of one published run, from what it stored - for Fig. 28 and Section 19.

    python docs/research/analysis/business_snapshot.py run-2026-10-07-001

Reads the run's evaluation document and run state, and its official predictions from the
database, and writes `business_snapshot.json` beside this file. Nothing is estimated: there is
no cost or revenue data in this project, so no monetary figure is produced (Section 19 says so).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import func, select

from agents.graph.churn import read_state
from triad_core.db import session_scope
from triad_core.models import ModelVersion, PipelineRun, Prediction
from triad_core.storage import storage


def main(run_key: str) -> None:
    store = storage()
    with session_scope() as session:
        run = session.execute(select(PipelineRun).where(PipelineRun.run_key == run_key)).scalar_one()
        state = read_state(store, run)
        evaluation = json.loads(
            (store.reports_dir(run_key) / "evaluation.json").read_text(encoding="utf-8"))
        bands = dict(session.execute(
            select(Prediction.risk_band, func.count())
            .where(Prediction.run_id == run.id, Prediction.prediction_set == "official")
            .group_by(Prediction.risk_band)
        ).all())
        version = session.get(ModelVersion, state["model_version_id"])
    selection = state.get("selection") or {}
    evidence = selection.get("evidence") or {}
    test = evaluation["test"]
    snapshot = {
        "run": run_key,
        "state": run.state,
        "model": f"{state.get('model_name')} v{state.get('mlflow_version')}",
        "algorithm": version.algorithm if version else None,
        "governance_status": version.governance_status if version else None,
        "test_cutoff_rows": test.get("n_rows"),
        "test_prevalence": test.get("prevalence"),
        "test_values": {k: test["values"].get(k) for k in (
            "pr_auc", "roc_auc", "precision", "recall", "threshold", "lift_at_10pct",
            "lift_at_20pct", "brier_skill", "ece")},
        "cumulative_gain": test["curves"].get("lift", []),
        "risk_bands_official": bands,
        "champion_delta_pr_auc": selection.get("champion_delta"),
        "segment_threshold_review": evidence.get("segment_threshold_review"),
        "segment_recall_gaps": evidence.get("segment_recall_gaps"),
        "sources": [f"reports/{run_key}/evaluation.json", f"reports/{run_key}/run_state.json",
                    "predictions (official set) in the platform database"],
    }
    out = Path(__file__).with_name("business_snapshot.json")
    out.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")
    print(f"written to {out}: bands {bands}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "run-2026-10-07-001")
