"""Opaque cursor pagination (PRD section 25.1).

The cursor is a base64 of the last row's sort key. It is opaque to the client on purpose: an
offset would skip or repeat rows whenever something is inserted between two pages, and a raw
key in the URL invites clients to build their own.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from backend.app import problems

MAX_LIMIT = 200
DEFAULT_LIMIT = 50


def encode_cursor(value: Any) -> str:
    return base64.urlsafe_b64encode(str(value).encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str | None) -> str | None:
    if not cursor:
        return None
    try:
        return base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise problems.bad_request(
            "The cursor is not one this API issued. Omit it to start from the beginning."
        ) from exc


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    if limit < 1:
        raise problems.bad_request("limit must be at least 1.")
    return min(limit, MAX_LIMIT)


def page(rows: list[Any], limit: int, key: str) -> dict[str, Any]:
    """Build a page from `limit + 1` rows: the extra row only says another page exists."""
    has_more = len(rows) > limit
    visible = rows[:limit]
    next_cursor = encode_cursor(getattr(visible[-1], key)) if has_more and visible else None
    return {"items": visible, "next_cursor": next_cursor, "limit": limit}
