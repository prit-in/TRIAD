"""M17 Audit: append-only, hash-chained audit trail (FR-078, PRD sections 24.4 and 28).

Chain rule: entry_hash = sha256(prev_hash || canonical(entry fields)).
The first entry chains from GENESIS_HASH. Editing or deleting any entry breaks
verification at that point, which `verify_chain` reports with the offending id.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from triad_core.ids import canonical_json, sha256_text
from triad_core.models import AuditLog

GENESIS_HASH = "0" * 64


def _entry_payload(
    ts: dt.datetime,
    actor_type: str,
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    run_id: uuid.UUID | None,
    details: dict[str, Any],
    outcome: str,
) -> str:
    return canonical_json(
        {
            "ts": ts.isoformat(),
            "actor_type": actor_type,
            "actor_id": actor_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "run_id": str(run_id) if run_id else None,
            "details": details,
            "outcome": outcome,
        }
    )


def _last_hash(session: Session) -> str:
    row = session.execute(
        select(AuditLog.entry_hash).order_by(AuditLog.id.desc()).limit(1)
    ).scalar_one_or_none()
    return row or GENESIS_HASH


def record(
    session: Session,
    *,
    actor_type: str,
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    outcome: str = "ok",
    run_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    """Append one audit entry inside the caller's transaction.

    The row is flushed (not committed) so it commits atomically with whatever state change
    it describes; a rolled-back action leaves no audit entry, and vice versa.
    """
    details = details or {}
    ts = dt.datetime.now(dt.UTC)
    prev = _last_hash(session)
    payload = _entry_payload(
        ts, actor_type, actor_id, action, resource_type, resource_id, run_id, details, outcome
    )
    entry = AuditLog(
        ts=ts,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        run_id=run_id,
        details=details,
        outcome=outcome,
        prev_hash=prev,
        entry_hash=sha256_text(prev + payload),
    )
    session.add(entry)
    session.flush()
    return entry


@dataclass(frozen=True)
class ChainResult:
    ok: bool
    entries_checked: int
    broken_at_id: int | None = None
    reason: str | None = None


def verify_chain(session: Session, limit: int | None = None) -> ChainResult:
    """Recompute the whole chain. Any edit, deletion or reorder fails here (TC-078)."""
    stmt = select(AuditLog).order_by(AuditLog.id.asc())
    if limit:
        stmt = stmt.limit(limit)
    prev = GENESIS_HASH
    count = 0
    for entry in session.execute(stmt).scalars():
        count += 1
        if entry.prev_hash != prev:
            return ChainResult(False, count, entry.id, "prev_hash does not match previous entry")
        payload = _entry_payload(
            entry.ts,
            entry.actor_type,
            entry.actor_id,
            entry.action,
            entry.resource_type,
            entry.resource_id,
            entry.run_id,
            entry.details,
            entry.outcome,
        )
        expected = sha256_text(entry.prev_hash + payload)
        if expected != entry.entry_hash:
            return ChainResult(False, count, entry.id, "entry_hash does not match content")
        prev = entry.entry_hash
    return ChainResult(True, count)
