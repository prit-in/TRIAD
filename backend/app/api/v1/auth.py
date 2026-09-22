"""POST /auth/token and GET /auth/me (PRD section 25.2).

A failed login says only that the credentials are wrong. It never distinguishes an unknown
email from a wrong password, and it costs the same either way, because the difference is what
turns a login form into a way to enumerate a company's staff.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from backend.app import problems
from backend.app.deps import CurrentUser, DbSession
from backend.app.schemas import TokenRequest, TokenResponse, WhoAmI
from backend.app.security import hash_password, issue_token, needs_rehash, verify_password
from triad_core import audit
from triad_core.ids import utcnow
from triad_core.models import User

router = APIRouter(tags=["auth"])


@router.post("/auth/token", response_model=TokenResponse, summary="Exchange credentials for a token")
def create_token(payload: TokenRequest, session: DbSession) -> TokenResponse:
    user = session.execute(
        select(User).where(User.email == payload.email.lower())
    ).scalar_one_or_none()

    # The hash is verified even when the user is unknown, so a missing account and a wrong
    # password take the same time and return the same answer.
    stored = user.password_hash if user else None
    password_ok = verify_password(payload.password, stored)

    if user is None or not password_ok:
        audit.record_and_commit(
            session,
            actor_type="user",
            actor_id=payload.email.lower(),
            action="auth.login_failed",
            resource_type="user",
            resource_id=str(user.id) if user else "unknown",
            outcome="refused",
            details={"reason": "invalid credentials"},
        )
        raise problems.unauthorised("Those credentials are not valid.")

    if not user.is_active:
        audit.record_and_commit(
            session,
            actor_type="user",
            actor_id=str(user.id),
            action="auth.login_refused",
            resource_type="user",
            resource_id=str(user.id),
            outcome="refused",
            details={"reason": "account deactivated"},
        )
        raise problems.forbidden(
            "This account is deactivated.", problem_type="account-disabled"
        )

    if user.password_hash and needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)

    token, expires_at = issue_token(user_id=user.id, email=user.email, role=user.role)
    user.last_login_at = utcnow()
    audit.record(
        session,
        actor_type="user",
        actor_id=str(user.id),
        action="auth.login",
        resource_type="user",
        resource_id=str(user.id),
        outcome="allowed",
        details={"role": user.role},
    )
    return TokenResponse(access_token=token, expires_at=expires_at, role=user.role)


@router.get("/auth/me", response_model=WhoAmI, summary="The caller, as the server sees them")
def whoami(user: CurrentUser) -> WhoAmI:
    return WhoAmI.model_validate(user)
