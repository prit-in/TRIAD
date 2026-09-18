"""Print the [error] lines from the newest escalation's diagnoses."""

import json
import sys

sys.path.insert(0, "/workspace")

from sqlalchemy import select  # noqa: E402

from triad_core.db import session_scope  # noqa: E402
from triad_core.models import DecisionRecord  # noqa: E402

with session_scope() as s:
    rec = s.execute(
        select(DecisionRecord)
        .where(DecisionRecord.decision_type == "escalation")
        .order_by(DecisionRecord.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if rec is None:
        print("no escalation found")
        raise SystemExit(0)
    print("REASON:", rec.evidence_refs.get("reason"))
    for i, d in enumerate(rec.evidence_refs.get("diagnoses", []), 1):
        print(f"\n--- attempt {i}: {d.get('error_class')} ---")
        for line in (d.get("message") or "").splitlines():
            if "error" in line.lower() and "no longer exists" not in line:
                print(" ", line[:300])
    print("\nGATES:", json.dumps(rec.evidence_refs.get("gate_reports", [])[-1:], indent=1)[:2000])
