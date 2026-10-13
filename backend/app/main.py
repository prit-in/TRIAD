"""The TRIAD REST API (PRD section 25).

The application is deliberately thin. Every rule it enforces - who may decide an approval,
whether a promotion is self-approval, which sandbox profiles exist - is decided by the policy
engine and the governance service that the pipeline already uses, so the API and the
orchestrator can never hold different opinions about the same action.

`/health` and `/metrics` are public on the internal network; Compose keeps them off the
host by not publishing that port. `/metrics` reads its numbers from the database on each
scrape rather than from in-process counters, because the work it describes is done by the
orchestrator worker and not by this process - see backend/app/metrics.py.
"""

from __future__ import annotations

import logging
import re
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from backend.app import metrics as metrics_collector
from backend.app import problems
from backend.app.api.v1 import api_v1
from backend.app.schemas import HealthStatus
from backend.app.security import check_jwt_secret
from triad_core.config import settings
from triad_core.db import ping, session_scope
from triad_core.policy import PolicyEngine

API_VERSION = "1.0"

DESCRIPTION = """
Governed, agentic churn analytics.

Agents propose; verification decides; a person approves anything irreversible. Every number
this API returns was measured by the pipeline and stored, and every governed action leaves a
hash-chained audit entry that `/api/v1/audit-logs/verify` can recompute.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the policy once at start-up and refuse to serve without it.

    The engine is fail-closed, so a missing policy would deny every governed action while the
    read endpoints kept working - an API that looks healthy and quietly refuses every
    approval. Better to say so at start-up.
    """
    policy = PolicyEngine(settings().governance_policy_path)
    app.state.policy_ok = policy.valid
    app.state.policy_error = policy.load_error

    # A signing key that cannot protect a token is refused outside development, and
    # announced on every boot inside it, so it cannot quietly become the production key.
    warning = check_jwt_secret()
    app.state.jwt_warning = warning
    if warning:
        logging.getLogger("triad.api").warning(warning)
    yield


app = FastAPI(
    title="TRIAD API",
    version=API_VERSION,
    description=DESCRIPTION,
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_exception_handler(problems.Problem, problems.problem_handler)
app.add_exception_handler(RequestValidationError, problems.validation_handler)

# A caller's own id is kept when it is a plain token, so a trace can cross the dashboard and
# the API; anything else - a header carrying a line break, say - is replaced, never logged.
_CALLER_ID = re.compile(r"[A-Za-z0-9-]{8,64}")


@app.middleware("http")
async def correlate(request: Request, call_next):
    """Give every request a correlation id, and every failure a problem that carries it.

    PRD 26.3 asks an error to show "a correlation ID for support". This middleware is added
    before the CORS middleware below, so it runs inside it: an error nothing anticipated is
    answered here as a problem, with its CORS headers. Left to Starlette, it became a plain-text
    500 outside the CORS layer, which the dashboard - on another origin - could not even read,
    and so reported as "could not reach the server" (DEC-53).
    """
    supplied = request.headers.get("X-Request-ID", "")
    request.state.correlation_id = (
        supplied if _CALLER_ID.fullmatch(supplied) else uuid.uuid4().hex)
    try:
        response = await call_next(request)
    except Exception:
        logging.getLogger("triad.api").exception(
            "unhandled error on %s %s, correlation id %s",
            request.method, request.url.path, request.state.correlation_id)
        response = problems.internal_error().to_response(
            request.url.path, request.state.correlation_id)
    response.headers["X-Request-ID"] = request.state.correlation_id
    return response


# The dashboard is served from a different origin in development. Credentials are carried in
# the Authorization header, not a cookie, so no cookie-sharing is needed or allowed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
    # Readable by the dashboard: the correlation id it shows with an error, and where a
    # started run lives.
    expose_headers=["X-Request-ID", "Location"],
)

app.include_router(api_v1)


@app.get("/metrics", tags=["ops"], summary="Prometheus metrics", include_in_schema=False)
def metrics() -> Response:
    """Read from the database on scrape, not accumulated here (see backend/app/metrics.py).

    Unauthenticated, like `/health`, and kept off the public surface by Compose not publishing
    the port - the same arrangement the module docstring has always described. It is
    `include_in_schema=False` because a Prometheus text payload is not a JSON API and putting
    it in the OpenAPI document only misleads a client generator.
    """
    try:
        with session_scope() as session:
            payload = metrics_collector.collect(session)
    except Exception as exc:   # a scrape must not take the service down
        logging.getLogger("triad.api").warning(
            "metrics scrape failed: %s: %s", type(exc).__name__, exc
        )
        # A 503 with an empty body is honest: Prometheus records the target as down rather
        # than recording zeros, which would look like a healthy but idle platform.
        return Response(status_code=503, content="", media_type=metrics_collector.CONTENT_TYPE)
    return Response(content=payload, media_type=metrics_collector.CONTENT_TYPE)


@app.get("/health", response_model=HealthStatus, tags=["ops"], summary="Liveness and readiness")
def health() -> HealthStatus:
    """Reports what is actually reachable, and says `degraded` rather than lying about it."""
    try:
        database = ping()
    except Exception:
        database = False
    policy_ok = bool(getattr(app.state, "policy_ok", False))
    return HealthStatus(
        status="ok" if database and policy_ok else "degraded",
        database=database,
        policy_loaded=policy_ok,
        version=API_VERSION,
    )
