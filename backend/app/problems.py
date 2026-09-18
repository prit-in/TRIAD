"""RFC 9457 Problem Details (PRD section 25.1, 25.5).

Every error the API returns is `application/problem+json` with a stable `type` URI, so a
client can branch on the kind of failure rather than on a message string. The `detail` line
says what actually happened; it never invents a reason, and it never leaks a stack trace.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

BASE = "https://triad.example/problems"
MEDIA_TYPE = "application/problem+json"


class Problem(Exception):
    """An error with a machine-readable type, raised anywhere in the request path."""

    def __init__(
        self,
        *,
        status: int,
        title: str,
        detail: str,
        problem_type: str,
        extra: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.title = title
        self.detail = detail
        self.problem_type = problem_type
        self.extra = extra or {}
        self.headers = headers or {}

    def to_response(self, instance: str) -> JSONResponse:
        body = {
            "type": f"{BASE}/{self.problem_type}",
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "instance": instance,
            **self.extra,
        }
        return JSONResponse(
            body, status_code=self.status, media_type=MEDIA_TYPE, headers=self.headers
        )


def unauthorised(detail: str) -> Problem:
    return Problem(
        status=401, title="Authentication required", detail=detail,
        problem_type="unauthorised", headers={"WWW-Authenticate": "Bearer"},
    )


def forbidden(detail: str, *, problem_type: str = "forbidden") -> Problem:
    return Problem(status=403, title="Not permitted", detail=detail, problem_type=problem_type)


def not_found(what: str, identifier: str) -> Problem:
    return Problem(
        status=404, title=f"{what} not found",
        detail=f"No {what.lower()} with id {identifier}.", problem_type="not-found",
    )


def conflict(title: str, detail: str, *, problem_type: str = "conflict") -> Problem:
    return Problem(status=409, title=title, detail=detail, problem_type=problem_type)


def bad_request(detail: str) -> Problem:
    return Problem(
        status=400, title="Bad request", detail=detail, problem_type="bad-request",
    )


def unprocessable(detail: str) -> Problem:
    return Problem(
        status=422, title="Unprocessable request", detail=detail,
        problem_type="unprocessable",
    )


async def problem_handler(request: Request, exc: Problem) -> JSONResponse:
    return exc.to_response(request.url.path)


async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's own 422, re-shaped as Problem Details so clients see one error format."""
    return Problem(
        status=422,
        title="Request validation failed",
        detail="The request body or query does not match the schema.",
        problem_type="unprocessable",
        extra={"errors": [
            {"location": list(e.get("loc", [])), "message": e.get("msg", "")}
            for e in exc.errors()
        ]},
    ).to_response(request.url.path)
