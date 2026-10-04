"""The trust score of Section 10.5, computed from what a run recorded - nothing else.

    python docs/research/analysis/trust_score.py run-2026-10-03-002 run-2026-10-04-006 ...

Equation (6): the mean of the components that apply to the run, each in [0, 1]. A component that
does not apply (no report, no promotion, no calibration measured) is left out of the mean and
reported as "n/a"; it is never scored as 0 or 1 by default. The weights are equal by design and the
manuscript says so. Writes `trust_scores.json` beside this file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import select

from triad_core.audit import verify_chain
from triad_core.db import session_scope
from triad_core.models import (
    Approval,
    CodeArtifact,
    DecisionRecord,
    PipelineRun,
    Report,
)

TAU = 0.05  # AP-5 limit


def _final_artifacts(session, run) -> list:
    """The last artifact of each template: the one whose output the run used or stopped on."""
    rows = session.execute(
        select(CodeArtifact).where(CodeArtifact.run_id == run.id).order_by(CodeArtifact.created_at)
    ).scalars().all()
    last: dict[str, CodeArtifact] = {}
    for a in rows:
        last[a.template_id or a.slot_id or str(a.id)] = a
    return list(last.values())


def _all_gates_passed(artifact) -> bool:
    gates = (artifact.gate_results or {}).get("gates") or []
    return len(gates) == 9 and all(g.get("passed") for g in gates)


def components(session, run, chain_ok_scopes: set[str] | None) -> dict[str, float | None]:
    out: dict[str, float | None] = {}

    # s_chain: the run's own chain verifies (verify_chain walks every chain once).
    out["chain"] = None if chain_ok_scopes is None else (
        0.0 if str(run.id) in chain_ok_scopes else 1.0)

    finals = _final_artifacts(session, run)
    out["gates"] = (sum(_all_gates_passed(a) for a in finals) / len(finals)) if finals else None

    acceptance = session.execute(
        select(DecisionRecord).where(DecisionRecord.run_id == run.id,
                                     DecisionRecord.decision_type == "acceptance")
        .order_by(DecisionRecord.created_at.desc())
    ).scalars().first()
    evidence = (acceptance.evidence_refs or {}) if acceptance else {}
    ece = evidence.get("ece_after_calibration", evidence.get("ece"))
    out["calib"] = None if ece is None else max(0.0, min(1.0, 2.0 - float(ece) / TAU))

    reports = session.execute(select(Report).where(Report.run_id == run.id)).scalars().all()
    checked = [r for r in reports if r.claim_check_status in ("passed", "failed")]
    out["claims"] = (sum(r.claim_check_status == "passed" for r in checked) / len(checked)
                     if checked else None)

    approvals = session.execute(
        select(Approval).where(Approval.run_id == run.id,
                               Approval.action_type == "model_promotion")
    ).scalars().all()
    decided = [a for a in approvals if a.status == "approved"]
    out["approval"] = (
        None if not decided else
        float(all(a.decided_by is not None and str(a.decided_by) != str(a.requested_by)
                  for a in decided))
    )

    stored = all((a.gate_results or {}).get("gates") and any(
        g.get("gate") == 8 and g.get("passed") for g in a.gate_results["gates"]) for a in finals)
    out["lineage"] = float(bool(run.input_versions) and bool(finals) and stored)
    return out


def main(run_keys: list[str]) -> None:
    results = []
    with session_scope() as session:
        chain = verify_chain(session)
        # If anything is broken, name the scope; every other run's chain verified.
        broken = {chain.broken_scope} if (not chain.ok and chain.broken_scope) else set()
        for key in run_keys:
            run = session.execute(select(PipelineRun).where(PipelineRun.run_key == key)).scalar_one()
            comp = components(session, run, broken)
            applied = {k: v for k, v in comp.items() if v is not None}
            score = sum(applied.values()) / len(applied) if applied else None
            results.append({"run": key, "state": run.state, "trust_score": score,
                            "components": comp, "applicable": sorted(applied)})
    print(f"{'run':<22} {'state':<17} {'TS':>5}  " + "  ".join(
        f"{k:>8}" for k in ("chain", "gates", "calib", "claims", "approval", "lineage")))
    for r in results:
        c = r["components"]
        cells = "  ".join(f"{('n/a' if c[k] is None else f'{c[k]:.2f}'):>8}"
                          for k in ("chain", "gates", "calib", "claims", "approval", "lineage"))
        ts = "n/a" if r["trust_score"] is None else f"{r['trust_score']:.2f}"
        print(f"{r['run']:<22} {r['state']:<17} {ts:>5}  {cells}")
    out = Path(__file__).with_name("trust_scores.json")
    out.write_text(json.dumps({"equation": "TS = mean of applicable components (Section 10.5)",
                               "tau": TAU, "chain_verified": chain.ok,
                               "chain_entries": chain.entries_checked,
                               "runs": results}, indent=2), encoding="utf-8")
    print(f"written to {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
