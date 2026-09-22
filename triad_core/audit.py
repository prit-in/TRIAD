"""M17 Audit: append-only, hash-chained audit trail (FR-078, PRD sections 24.4 and 28).

Chain rule: `entry_hash = sha256(prev_hash || canonical(entry fields))`, and the first entry
of a chain links from `GENESIS_HASH`.

**There is one chain per run, plus one for entries that belong to no run.** A single global
chain looks simpler and is wrong here: appending means reading the last entry and then
inserting, and two writers doing that at once both read the same last entry and fork the
chain. That is not theoretical - it happened, and `verify_chain` then reported tampering that
had not occurred, which is the worst possible failure for a tamper-evidence mechanism.

Serialising a single global chain would fix the fork but would make every audit write wait
for the longest open transaction: a pipeline run holds its transaction for minutes, so a login
elsewhere would block for minutes. Chaining per run confines the lock to the one chain a
writer is actually appending to, which is the chain nobody else is writing at the time.

What this detects: any edit to an entry, any deletion from the middle of a chain, and any
reordering. What it does not detect on its own: the removal of an entire run's chain, which is
why `pipeline_runs` and the entries reference each other and `verify_chain` reports the number
of chains it checked.
"""

from __future__ import annotations

import datetime as dt
import uuid
import zlib
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from triad_core.ids import canonical_json, sha256_text
from triad_core.models import AuditLog

GENESIS_HASH = "0" * 64
SYSTEM_SCOPE = "system"

# PostgreSQL advisory locks are keyed by a pair of 32-bit integers. The first identifies this
# subsystem so TRIAD's locks cannot collide with anything else using advisory locks.
_LOCK_NAMESPACE = 0x54524144   # "TRAD"


def chain_scope(run_id: uuid.UUID | None) -> str:
    """Which chain an entry belongs to. One per run; everything else shares `system`."""
    return str(run_id) if run_id else SYSTEM_SCOPE


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


def _lock_chain(session: Session, scope: str) -> None:
    """Hold the chain's append lock until this transaction ends.

    Without it the read-then-append below is a race. The lock is per chain, so two runs never
    wait on each other, and a writer only ever waits for another writer on the same run.
    """
    try:
        dialect = session.get_bind().dialect.name
    except Exception:
        return
    if dialect != "postgresql":
        return
    key = zlib.crc32(scope.encode("utf-8")) & 0x7FFFFFFF
    session.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, :key)"),
        {"namespace": _LOCK_NAMESPACE, "key": key},
    )


def _last_hash(session: Session, run_id: uuid.UUID | None) -> str:
    stmt = select(AuditLog.entry_hash).order_by(AuditLog.id.desc()).limit(1)
    stmt = (
        stmt.where(AuditLog.run_id == run_id) if run_id is not None
        else stmt.where(AuditLog.run_id.is_(None))
    )
    return session.execute(stmt).scalar_one_or_none() or GENESIS_HASH


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
    _lock_chain(session, chain_scope(run_id))
    prev = _last_hash(session, run_id)
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


def record_and_commit(session: Session, **fields: Any) -> AuditLog:
    """Append one entry and commit it, for a caller that is about to raise.

    `record` flushes rather than commits so that an entry commits atomically with the state
    change it describes - a rolled-back action leaves no audit entry. That is right for an
    action which either happens or does not, and wrong for a **refusal**, because a refusal is
    delivered *by* raising: the request's transactional scope rolls back on the exception and
    takes the entry documenting the refusal with it.

    Measured on a running instance before this existed: three failed logins against
    `POST /api/v1/auth/token` returned 401 each and left `auth.login_failed` at zero rows, so
    the audit trail held no record of a brute-force attempt. An approval that expired during a
    decision attempt was likewise never marked expired.

    Committing in the caller's own transaction, rather than opening a second one, is deliberate:
    `record` holds `pg_advisory_xact_lock` on the chain until the transaction ends, so a second
    session appending to the same chain would wait for this one - which cannot finish until the
    handler returns. Same transaction, no wait.

    The caller must hold no pending write it would not want committed. At every call site the
    refusal entry, and the state change it describes, are the only pending writes.
    """
    entry = record(session, **fields)
    session.commit()
    return entry


@dataclass(frozen=True)
class ChainResult:
    ok: bool
    entries_checked: int
    broken_at_id: int | None = None
    reason: str | None = None
    chains_checked: int = 0
    broken_scope: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "entries_checked": self.entries_checked,
            "chains_checked": self.chains_checked,
            "broken_at_id": self.broken_at_id,
            "broken_scope": self.broken_scope,
            "reason": self.reason,
        }


@dataclass
class _Chain:
    prev: str = GENESIS_HASH
    seen: int = 0


def content_hash(entry: AuditLog) -> str:
    """What the entry's hash must be, given its stored content and its stored link."""
    return sha256_text(
        entry.prev_hash
        + _entry_payload(
            entry.ts, entry.actor_type, entry.actor_id, entry.action, entry.resource_type,
            entry.resource_id, entry.run_id, entry.details, entry.outcome,
        )
    )


def verify_chain(session: Session, limit: int | None = None) -> ChainResult:
    """Recompute every chain. Any edit, deletion or reorder fails here (TC-078).

    Entries are walked in insertion order and dealt into their own chain, so interleaved
    writes from concurrent runs verify exactly as if each run had run alone.
    """
    stmt = select(AuditLog).order_by(AuditLog.id.asc())
    if limit:
        stmt = stmt.limit(limit)

    chains: dict[str, _Chain] = {}
    count = 0
    for entry in session.execute(stmt).scalars():
        count += 1
        scope = chain_scope(entry.run_id)
        chain = chains.setdefault(scope, _Chain())

        if entry.prev_hash != chain.prev:
            return ChainResult(
                False, count, entry.id, "prev_hash does not match the previous entry "
                f"in chain {scope}", chains_checked=len(chains), broken_scope=scope,
            )
        if content_hash(entry) != entry.entry_hash:
            return ChainResult(
                False, count, entry.id, "entry_hash does not match content",
                chains_checked=len(chains), broken_scope=scope,
            )
        chain.prev = entry.entry_hash
        chain.seen += 1

    return ChainResult(True, count, chains_checked=len(chains))


def chain_summary(session: Session) -> list[dict[str, Any]]:
    """Per-chain counts, for an operator asking what the trail actually contains."""
    from sqlalchemy import func

    rows = session.execute(
        select(AuditLog.run_id, func.count(), func.min(AuditLog.id), func.max(AuditLog.id))
        .group_by(AuditLog.run_id)
        .order_by(func.min(AuditLog.id))
    ).all()
    return [
        {"scope": chain_scope(run_id), "entries": n, "first_id": lo, "last_id": hi}
        for run_id, n, lo, hi in rows
    ]


__all__ = [
    "GENESIS_HASH",
    "SYSTEM_SCOPE",
    "ChainResult",
    "chain_scope",
    "chain_summary",
    "content_hash",
    "record",
    "verify_chain",
]
