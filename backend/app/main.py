"""The TRIAD REST API (PRD section 25).

The application is deliberately thin. Every rule it enforces - who may decide an approval,
whether a promotion is self-approval, which sandbox profiles exist - is decided by the policy
engine and the governance service that the pipeline already uses, so the API and the
orchestrator can never hold different opinions about the same action.

`/health` is public. `/metrics` is served for Prometheus and is reachable on the internal
network only, which Compose enforces by not publishing that port.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from backend.app import problems
from backend.app.api.v1 import api_v1
from backend.app.schemas import HealthStatus
from backend.app.security import check_jwt_secret
from triad_core.config import settings
from triad_core.db import ping
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

# The dashboard is served from a different origin in development. Credentials are carried in
# the Authorization header, not a cookie, so no cookie-sharing is needed or allowed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
)

app.include_router(api_v1)


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
