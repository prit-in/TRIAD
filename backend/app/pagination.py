"""Opaque cursor pagination (PRD section 25.1).

The cursor is a base64 of the last row's sort key **and its id**. It is opaque to the client on
purpose: an offset would skip or repeat rows whenever something is inserted between two pages,
and a raw key in the URL invites clients to build their own.

The id is in there because the sort key alone is not unique. `started_at` defaults to
`server_default=func.now()`, and PostgreSQL's `now()` is the *transaction* timestamp, so every
row a single transaction inserts carries the identical value. Seeking on `started_at < cursor`
alone therefore dropped every row whose timestamp equalled the last row of the previous page -
silently, because a page that is missing rows looks exactly like a page.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import uuid
from typing import Any

from backend.app import problems

MAX_LIMIT = 200
DEFAULT_LIMIT = 50


CURSOR_SEPARATOR = "|"


def encode_cursor(value: Any, tiebreak: Any) -> str:
    """Encode the sort key and the row id that breaks ties between equal sort keys."""
    raw = f"{value}{CURSOR_SEPARATOR}{tiebreak}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str | None) -> tuple[dt.datetime, uuid.UUID] | None:
    """The sort key and tie-breaking id the cursor encodes.

    The timestamp is returned as a datetime, not the decoded string: it used to be the string,
    which the caller compared against a `timestamp with time zone` column, and Postgres has no
    `timestamp < varchar` operator - so every request carrying a cursor returned 500, on the one
    paginated endpoint the API has. Nothing had ever asked for a second page, so nothing found
    out.

    `fromisoformat` accepts what `encode_cursor` writes, because the value encoded is the
    `str()` of a timezone-aware datetime.
    """
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise _not_ours() from exc
    key, separator, tiebreak = raw.rpartition(CURSOR_SEPARATOR)
    if not separator:
        raise _not_ours()
    try:
        return dt.datetime.fromisoformat(key), uuid.UUID(tiebreak)
    except ValueError as exc:
        # Decodable base64 that is not one of our cursors: still the client's problem to fix
        # by dropping it, and still not a 500.
        raise _not_ours() from exc


def _not_ours() -> problems.Problem:
    return problems.bad_request(
        "The cursor is not one this API issued. Omit it to start from the beginning."
    )


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    if limit < 1:
        raise problems.bad_request("limit must be at least 1.")
    return min(limit, MAX_LIMIT)


def page(rows: list[Any], limit: int, key: str, tiebreak: str = "id") -> dict[str, Any]:
    """Build a page from `limit + 1` rows: the extra row only says another page exists."""
    has_more = len(rows) > limit
    visible = rows[:limit]
    next_cursor = (
        encode_cursor(getattr(visible[-1], key), getattr(visible[-1], tiebreak))
        if has_more and visible
        else None
    )
    return {"items": visible, "next_cursor": next_cursor, "limit": limit}
