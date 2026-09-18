"""Re-link audit entries written before the per-run chain rule (one-off migration).

    python scripts/reseal_audit.py --check
    python scripts/reseal_audit.py --apply --i-understand-this-rewrites-the-audit-log

Rewriting an append-only log is exactly what must not happen, so this tool is deliberately
awkward and deliberately narrow:

  * it **refuses** unless every entry's content still hashes to its stored `entry_hash` given
    its stored `prev_hash`. That check is independent of the linking, so it can prove the
    *content* was never edited. If it fails, something really was tampered with and re-linking
    would destroy the evidence;
  * it changes only `prev_hash` and `entry_hash` - never a timestamp, actor, action or detail;
  * it appends an entry recording that it ran, with the counts, so the rewrite is itself in
    the trail.

It exists because entries written before the fix used one global chain and concurrent writers
forked it. Those entries are intact; only their links are wrong.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from triad_core import audit
from triad_core.db import session_scope
from triad_core.models import AuditLog


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-link audit entries onto per-run chains.")
    parser.add_argument("--apply", action="store_true", help="write the corrected links")
    parser.add_argument("--i-understand-this-rewrites-the-audit-log", action="store_true",
                        dest="confirmed")
    args = parser.parse_args()

    with session_scope() as session:
        entries = list(session.execute(
            select(AuditLog).order_by(AuditLog.id.asc())
        ).scalars())
        print(f"{len(entries)} entries in the trail")

        # 1. Content first. A mismatch here is tampering, not a linking artefact.
        damaged = [e.id for e in entries if audit.content_hash(e) != e.entry_hash]
        if damaged:
            print(f"REFUSING: {len(damaged)} entr{'y' if len(damaged) == 1 else 'ies'} do not "
                  f"hash to their stored content (first: {damaged[0]}).")
            print("That is an edit, not a link problem. Re-linking would destroy the evidence.")
            return 2
        print("content check passed: every entry still hashes to its stored content")

        before = audit.verify_chain(session)
        print(f"before: {before.to_dict()}")
        if before.ok:
            print("the chains already verify; nothing to do")
            return 0

        # 2. Recompute the links, per chain, in insertion order.
        heads: dict[str, str] = {}
        relinked = 0
        for entry in entries:
            scope = audit.chain_scope(entry.run_id)
            prev = heads.get(scope, audit.GENESIS_HASH)
            if entry.prev_hash != prev:
                relinked += 1
            entry.prev_hash = prev
            entry.entry_hash = audit.content_hash(entry)
            heads[scope] = entry.entry_hash

        print(f"{relinked} entr{'y' if relinked == 1 else 'ies'} need a corrected link "
              f"across {len(heads)} chain(s)")

        if not args.apply:
            print("\ndry run: nothing written. Re-run with --apply and the confirmation flag.")
            session.rollback()
            return 1
        if not args.confirmed:
            print("\nREFUSING: --apply also needs "
                  "--i-understand-this-rewrites-the-audit-log")
            session.rollback()
            return 3

        session.flush()
        audit.record(
            session,
            actor_type="system",
            actor_id="reseal_audit",
            action="audit.relinked",
            resource_type="audit_log",
            resource_id="all",
            outcome="relinked",
            details={"entries": len(entries), "relinked": relinked,
                     "chains": len(heads),
                     "reason": "entries written before the per-run chain rule; content "
                               "verified intact before re-linking"},
        )
        after = audit.verify_chain(session)
        print(f"after:  {after.to_dict()}")
        if not after.ok:
            print("REFUSING to commit: the chains still do not verify")
            session.rollback()
            return 4
        print("re-linked and verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
