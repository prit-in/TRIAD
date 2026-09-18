"""Password hashing and bearer tokens (PRD section 25.1, 29.2).

Argon2id for passwords and a short-lived HS256 JWT for sessions. The token carries the role
so a request can be refused without a database round trip, but **the role is re-read from the
database on every request that changes something**, because a token minted before a role was
revoked must not outlive the revocation.

Nothing here is a stand-in: there is no bypass flag, no "dev user", and an unparseable or
expired token is refused rather than downgraded to anonymous.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from triad_core.config import settings

_hasher = PasswordHasher()

# PRD section 9: the roles the policy engine already names.
ADMIN = "admin"
DATA_ENGINEER = "data_engineer"
DATA_SCIENTIST = "data_scientist"
ML_ENGINEER = "ml_engineer"
BUSINESS_ANALYST = "business_analyst"
VIEWER = "viewer"
AGENT = "agent"

ALL_ROLES = (ADMIN, DATA_ENGINEER, DATA_SCIENTIST, ML_ENGINEER, BUSINESS_ANALYST, VIEWER, AGENT)
DATA_ROLES = (DATA_ENGINEER, DATA_SCIENTIST, ML_ENGINEER)
APPROVER_ROLES = (ML_ENGINEER, DATA_ENGINEER, ADMIN)


DEV_SECRET_MARKER = "dev-only-insecure"
MIN_SECRET_BYTES = 32   # RFC 7518 section 3.2: an HS256 key below the digest size is weak


class TokenError(Exception):
    """Raised when a token is missing, malformed, expired or signed with another key."""


class InsecureSecret(Exception):
    """Raised at start-up when the signing key would not actually protect anything."""


def check_jwt_secret() -> str | None:
    """Refuse to run on a key that cannot do its job. Returns a warning for dev, or raises.

    A short or shipped-by-default signing key is the kind of finding that gets written down
    as "known issue" and never fixed, so it is enforced here: outside `dev` the process does
    not start. Inside `dev` it starts and says so on every boot.
    """
    cfg = settings()
    secret = cfg.jwt_secret or ""
    is_default = DEV_SECRET_MARKER in secret
    too_short = len(secret.encode("utf-8")) < MIN_SECRET_BYTES

    if not (is_default or too_short):
        return None

    reason = (
        "TRIAD_JWT_SECRET is still the shipped development value"
        if is_default
        else f"TRIAD_JWT_SECRET is {len(secret.encode('utf-8'))} bytes; "
             f"HS256 needs at least {MIN_SECRET_BYTES}"
    )
    if cfg.env != "dev":
        raise InsecureSecret(
            f"{reason}. Set a random {MIN_SECRET_BYTES}-byte secret before running "
            f"with TRIAD_ENV={cfg.env}."
        )
    return f"{reason}; tokens issued by this process are not safe outside development."


@dataclass(frozen=True)
class TokenClaims:
    user_id: uuid.UUID
    email: str
    role: str
    expires_at: dt.datetime


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Constant-time-ish check that never raises on a malformed stored hash."""
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def issue_token(*, user_id: uuid.UUID, email: str, role: str, ttl_minutes: int | None = None) -> tuple[str, dt.datetime]:
    cfg = settings()
    expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(
        minutes=ttl_minutes if ttl_minutes is not None else cfg.jwt_ttl_minutes
    )
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "iat": int(dt.datetime.now(dt.UTC).timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": "triad",
    }
    token = jwt.encode(payload, cfg.jwt_secret, algorithm=cfg.jwt_algorithm)
    return token, expires_at


def decode_token(token: str) -> TokenClaims:
    cfg = settings()
    try:
        payload = jwt.decode(
            token,
            cfg.jwt_secret,
            algorithms=[cfg.jwt_algorithm],   # a list, so `alg: none` can never be honoured
            issuer="triad",
            options={"require": ["exp", "sub", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("the token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("the token is not valid") from exc

    try:
        user_id = uuid.UUID(str(payload["sub"]))
    except (KeyError, ValueError) as exc:
        raise TokenError("the token subject is not a user id") from exc

    role = str(payload.get("role", ""))
    if role not in ALL_ROLES:
        raise TokenError(f"unknown role in token: {role!r}")

    return TokenClaims(
        user_id=user_id,
        email=str(payload.get("email", "")),
        role=role,
        expires_at=dt.datetime.fromtimestamp(int(payload["exp"]), dt.UTC),
    )
