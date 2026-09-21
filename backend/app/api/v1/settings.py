"""The settings page's read model (PRD section 26.1, Settings).

Read-only and admin-only. There is no write here on purpose: the policy file is reviewed and
versioned like source (PRD 30.3), so an endpoint that edited it would put the one document the
whole governance story rests on outside review. Users are created by `scripts/seed_demo.py`,
for the reason given there - an unauthenticated route that mints an administrator survives
into production.

What is served, and why each is safe to serve:

  * the two policy files, as text, with their version and a digest. They are committed source
    and contain rules, not credentials;
  * the budget ceilings, from the code default and from the pipeline configuration, side by
    side, because the two disagreeing is exactly the kind of thing this page should show;
  * connection *targets* with every credential stripped by `redact_dsn`, never the DSN. A
    settings page that prints a password is a settings page that puts it in a screenshot;
  * the accounts and their roles - never `password_hash`, which is why this builds its own
    view rather than dumping the row.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends
from sqlalchemy import select

from backend.app.deps import DbSession, require_roles
from backend.app.security import ADMIN
from triad_core.config import load_yaml, settings
from triad_core.policy import PolicyEngine

router = APIRouter(tags=["settings"], dependencies=[Depends(require_roles(ADMIN))])


def redact_dsn(dsn: str) -> str:
    """A connection string with its user info removed.

    `postgresql+psycopg://triad:pw@postgres:5432/triad` becomes
    `postgresql+psycopg://***@postgres:5432/triad`. Splitting on `urlsplit` rather than a
    regular expression, because a password may legitimately contain `@` and `:` and a
    hand-rolled split gets that wrong in the direction that leaks.
    """
    try:
        parts = urlsplit(dsn)
    except ValueError:
        return "***"
    if not parts.netloc:
        return dsn
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    netloc = f"***@{host}" if (parts.username or parts.password) else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _read_file(path: Path) -> tuple[str, str | None]:
    try:
        raw = path.read_bytes()
        return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()
    except OSError as exc:
        # A policy file that cannot be read is the fail-closed case, and saying so is the
        # whole point of this panel.
        return f"(unreadable: {exc})", None


def _governance_view(path: Path) -> dict[str, Any]:
    engine = PolicyEngine(path)
    text, digest = _read_file(path)
    return {
        "name": "governance",
        "path": str(path),
        "version": engine.version,
        "valid": engine.valid,
        "load_error": engine.load_error,
        "sha256": digest,
        "action_count": len(engine.policy.get("actions", {}) or {}),
        "defaults": engine.defaults(),
        "text": text,
    }


def _routing_view(path: Path) -> dict[str, Any]:
    """The routing policy, read the way `agents.routing` reads it.

    Not through `PolicyEngine`: the two files answer different questions and have different
    shapes. Routing carries `routing_version` and a list of `rules`; governance carries
    `policy_version` and a map of `actions`. Running the governance validator over routing.yaml
    reports it as invalid with version "invalid", which on this page reads as "the policy
    failed to load, every gated action is denied" - a false alarm about the wrong file. That is
    exactly what the first version of this endpoint said when it was called.
    """
    text, digest = _read_file(path)
    try:
        raw = load_yaml(path)
        error = None
    except (OSError, ValueError) as exc:
        raw, error = {}, str(exc)
    rules = raw.get("rules") or []
    thresholds = dict(raw.get("thresholds", {}) or {})
    # Validity is judged on the thresholds, because those are what `RoutingEngine` actually
    # reads - `threshold()` raises for a missing one. The rule list beside them is the record
    # of LR-01..LR-10; the comparisons themselves live in `agents/routing.py`.
    if error is None and not thresholds:
        error = "no routing thresholds configured"
    return {
        "name": "routing",
        "path": str(path),
        "version": str(raw.get("routing_version", "not recorded")),
        "valid": error is None,
        "load_error": error,
        "sha256": digest,
        "action_count": len(rules),
        "defaults": thresholds,
        "text": text,
    }


@router.get("/settings", summary="Policy, budgets, connections and accounts")
def read_settings(session: DbSession) -> dict[str, Any]:
    from agents.llm.budget import DEFAULT_LIMITS
    from triad_core.models import User

    cfg = settings()

    try:
        pipeline_budgets = dict(load_yaml(cfg.pipeline_config_path).get("budgets", {}) or {})
        pipeline_error = None
    except (OSError, ValueError) as exc:
        pipeline_budgets, pipeline_error = {}, str(exc)

    accounts = session.execute(select(User).order_by(User.email)).scalars().all()

    return {
        "environment": cfg.env,
        "policies": [
            _governance_view(cfg.governance_policy_path),
            _routing_view(cfg.routing_policy_path),
        ],
        "budgets": {
            # Every key either side knows about, so a limit defined in one and not the other
            # shows as a blank rather than disappearing.
            "keys": sorted(set(DEFAULT_LIMITS) | set(pipeline_budgets)),
            "code_default": DEFAULT_LIMITS,
            "pipeline_config": pipeline_budgets,
            "pipeline_config_path": str(cfg.pipeline_config_path),
            "pipeline_config_error": pipeline_error,
        },
        "connections": [
            {"name": "PostgreSQL", "target": redact_dsn(cfg.db_url), "detail": "run state, audit trail, registry"},
            {"name": "MLflow", "target": redact_dsn(cfg.mlflow_tracking_uri), "detail": "experiment tracking"},
            {"name": "Object storage", "target": str(cfg.data_root), "detail": "datasets, features, models, reports"},
            {"name": "Spark", "target": cfg.spark_master_url, "detail": f"driver {cfg.spark_driver_memory}, executor {cfg.spark_executor_memory}"},
            {
                "name": "LLM provider",
                "target": cfg.llm_provider,
                # ADR-0003: there is no provider configured here, and the page says so rather
                # than showing a blank that reads like a connection.
                "detail": f"model {cfg.llm_model_id}",
            },
        ],
        "users": [
            {
                "id": str(row.id),
                "email": row.email,
                "display_name": row.display_name,
                "role": row.role,
                "is_active": row.is_active,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "last_login_at": row.last_login_at.isoformat() if row.last_login_at else None,
            }
            for row in accounts
        ],
    }
