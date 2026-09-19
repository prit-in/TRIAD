"""Prometheus metrics for the `/metrics` endpoint (NFR-OBS-02, FR-080).

`backend/app/main.py` has claimed since it was written that "`/metrics` is served for
Prometheus and is reachable on the internal network only". It was not served at all: no route
existed anywhere in the repository, and `prometheus-client` sat in the lockfile unused. Any
scrape config or alert rule pointed at it received a 404, and the documentation said otherwise.

**These are read from the database on scrape, not accumulated in the process.** That is the
opposite of the usual Prometheus advice, and it is deliberate here: the numbers that matter -
runs by state, escalations, retries, LLM spend, drift alerts - are produced by the *orchestrator
worker*, which is a different process from the API. Counters incremented in this process would
describe only the API's own view and would reset whenever it restarted, so a dashboard built on
them would be wrong in a way that looks plausible. The database already holds the truth and is
the only place both processes agree on.

The cost of that choice is honest: a scrape runs a handful of aggregate queries. They are all
indexed counts over tables that hold one row per run, task or report - nothing customer-level -
and the queries are bounded to a recent window where an unbounded scan would otherwise grow
without limit. If this ever became a real load, the answer is a materialised summary table
written by the worker, not in-process counters that would go back to being wrong.

Nothing here exposes a customer identifier, a score or any personal data: every series is a
count, a duration or a cost.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from prometheus_client import CollectorRegistry, Gauge, generate_latest
from sqlalchemy import func, select

from triad_core.models import (
    AgentTask,
    Alert,
    Approval,
    DriftReport,
    Execution,
    PipelineRun,
)

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

# How far back the windowed series look. A dashboard wants "recently", and an unbounded count
# over every run since the beginning grows for ever and answers a question nobody asked.
WINDOW = dt.timedelta(days=7)


def _window_start() -> dt.datetime:
    return dt.datetime.now(dt.UTC) - WINDOW


def collect(session: Any) -> bytes:
    """Build a fresh registry from the database and render it in Prometheus text format.

    A new registry per scrape, rather than module-level metrics: these are gauges read from
    the database, so there is no state to carry between scrapes, and a fresh registry cannot
    accumulate stale label combinations for runs or states that no longer occur.
    """
    registry = CollectorRegistry()
    since = _window_start()

    runs_by_state = Gauge(
        "triad_runs_total",
        "Pipeline runs by state, over the last 7 days.",
        ["state"],
        registry=registry,
    )
    for state, count in session.execute(
        select(PipelineRun.state, func.count())
        .where(PipelineRun.started_at >= since)
        .group_by(PipelineRun.state)
    ).all():
        runs_by_state.labels(state=state).set(count)

    runs_in_flight = Gauge(
        "triad_runs_in_flight",
        "Runs that are neither finished nor failed, at any age.",
        registry=registry,
    )
    runs_in_flight.set(
        session.execute(
            select(func.count()).select_from(PipelineRun).where(
                PipelineRun.state.notin_(
                    ("COMPLETED", "PUBLISHED", "ABORTED", "CANCELLED", "REJECTED_INPUT",
                     "FAILED", "ESCALATED")
                )
            )
        ).scalar_one()
    )

    tasks_by_status = Gauge(
        "triad_agent_tasks_total",
        "Agent tasks by status and stage, over the last 7 days.",
        ["status", "stage"],
        registry=registry,
    )
    for status, stage, count in session.execute(
        select(AgentTask.status, AgentTask.stage, func.count())
        .where(AgentTask.created_at >= since)
        .group_by(AgentTask.status, AgentTask.stage)
    ).all():
        tasks_by_status.labels(status=status, stage=stage or "unknown").set(count)

    # Retries are the attempts beyond the first, which is what a retry *is*. `attempt` defaults
    # to 0 for a task that was never claimed, so the subtraction is floored at zero: a queued
    # task has not retried -1 times.
    retries = Gauge(
        "triad_task_retries_total",
        "Attempts beyond the first, summed over tasks in the last 7 days.",
        ["stage"],
        registry=registry,
    )
    for stage, total in session.execute(
        select(
            AgentTask.stage,
            func.coalesce(func.sum(func.greatest(AgentTask.attempt - 1, 0)), 0),
        )
        .where(AgentTask.created_at >= since)
        .group_by(AgentTask.stage)
    ).all():
        retries.labels(stage=stage or "unknown").set(int(total or 0))

    failures_by_class = Gauge(
        "triad_task_failures_total",
        "Failed or escalated tasks by error class, over the last 7 days.",
        ["error_class"],
        registry=registry,
    )
    for error_class, count in session.execute(
        select(AgentTask.error_class, func.count())
        .where(
            AgentTask.created_at >= since,
            AgentTask.status.in_(("failed", "escalated")),
        )
        .group_by(AgentTask.error_class)
    ).all():
        failures_by_class.labels(error_class=error_class or "unclassified").set(count)

    stage_seconds = Gauge(
        "triad_stage_duration_seconds",
        "Mean execution duration by engine, over the last 7 days.",
        ["engine"],
        registry=registry,
    )
    for engine, mean_ms in session.execute(
        select(Execution.engine, func.avg(Execution.duration_ms))
        .where(Execution.started_at >= since, Execution.duration_ms.isnot(None))
        .group_by(Execution.engine)
    ).all():
        if mean_ms is not None:
            stage_seconds.labels(engine=engine or "unknown").set(float(mean_ms) / 1000.0)

    # Budget: the two numbers a run can exhaust. Summed over the window, because "how much did
    # we spend this week" is the question, and reported as 0 rather than absent when no run
    # made a call - an absent series looks like a broken exporter.
    llm_calls = Gauge(
        "triad_llm_calls_total",
        "LLM calls used by runs started in the last 7 days.",
        registry=registry,
    )
    llm_cost = Gauge(
        "triad_llm_cost_usd_total",
        "Estimated LLM cost of runs started in the last 7 days, in USD.",
        registry=registry,
    )
    calls, cost = session.execute(
        select(
            func.coalesce(func.sum(PipelineRun.llm_calls_used), 0),
            func.coalesce(func.sum(PipelineRun.llm_cost_used), 0.0),
        ).where(PipelineRun.started_at >= since)
    ).one()
    llm_calls.set(int(calls or 0))
    llm_cost.set(float(cost or 0.0))

    approvals = Gauge(
        "triad_approvals_total",
        "Approval requests by status, at any age.",
        ["status"],
        registry=registry,
    )
    for status, count in session.execute(
        select(Approval.status, func.count()).group_by(Approval.status)
    ).all():
        approvals.labels(status=status).set(count)

    alerts = Gauge(
        "triad_alerts_total",
        "Alerts by status and severity, at any age.",
        ["status", "severity"],
        registry=registry,
    )
    for status, severity, count in session.execute(
        select(Alert.status, Alert.severity, func.count())
        .group_by(Alert.status, Alert.severity)
    ).all():
        alerts.labels(status=status, severity=severity).set(count)

    drift = Gauge(
        "triad_drift_reports_total",
        "Drift reports computed in the last 7 days.",
        registry=registry,
    )
    drift.set(
        session.execute(
            select(func.count()).select_from(DriftReport)
            .where(DriftReport.computed_at >= since)
        ).scalar_one()
    )

    window = Gauge(
        "triad_metrics_window_days",
        "The window the windowed series above are computed over.",
        registry=registry,
    )
    window.set(WINDOW.days)

    return generate_latest(registry)
