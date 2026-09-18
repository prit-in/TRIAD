"""Request dependencies: the database session, the caller, and role checks.

Role checks happen here rather than in each handler, so a new endpoint cannot forget one:
every router is mounted with the roles it accepts, and `require_roles` is the only way in.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from backend.app import problems
from backend.app.security import ADMIN, TokenError, decode_token
from triad_core.db import session_scope
from triad_core.models import User


def get_session() -> Iterator[Session]:
    with session_scope() as session:
        yield session


DbSession = Annotated[Session, Depends(get_session)]


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise problems.unauthorised("Send an `Authorization: Bearer <token>` header.")
    return token.strip()


def current_user(request: Request, session: DbSession) -> User:
    """Resolve the caller, re-reading the user row rather than trusting the token alone.

    A token is valid for its whole lifetime, so a user deactivated or demoted five minutes
    ago would still hold a token saying otherwise. The database is the authority.
    """
    try:
        claims = decode_token(_bearer_token(request))
    except TokenError as exc:
        raise problems.unauthorised(str(exc)) from exc

    user = session.get(User, claims.user_id)
    if user is None:
        raise problems.unauthorised("The token names a user that no longer exists.")
    if not user.is_active:
        raise problems.forbidden(
            f"The account {user.email} is deactivated.", problem_type="account-disabled"
        )
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_roles(*roles: str):
    """Allow the named roles, plus admin, which is never locked out of its own platform."""
    allowed = {*roles, ADMIN}

    def check(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise problems.forbidden(
                f"Role {user.role} may not use this endpoint; "
                f"it is limited to {', '.join(sorted(allowed))}.",
                problem_type="role",
            )
        return user

    return check
