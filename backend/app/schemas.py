"""Request and response models (PRD section 25).

FastAPI generates the OpenAPI description from these same models, so the documentation
cannot drift from what the endpoints accept. Fields that carry a measurement are typed as
optional rather than defaulted to zero: a metric that was never computed must read as absent,
not as a value someone could quote.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


# --- auth ----------------------------------------------------------------------


class TokenRequest(ApiModel):
    # Deliberately a plain string rather than EmailStr: the lookup is an exact match against
    # a stored address, so RFC-5322 validation would reject nothing a wrong password would
    # not already reject, while pulling in a dependency for one field.
    email: str = Field(min_length=3, max_length=255, pattern=r"^[^@\s]+@[^@\s]+$")
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(ApiModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: dt.datetime
    role: str


class WhoAmI(ApiModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: str


# --- pagination ----------------------------------------------------------------


class Page(ApiModel):
    """Opaque cursor pagination: the client never builds a cursor, it only echoes one."""

    items: list[Any]
    next_cursor: str | None = None
    limit: int


# --- datasets ------------------------------------------------------------------


class DatasetSummary(ApiModel):
    id: uuid.UUID
    name: str
    entity_type: str | None = None
    source_type: str | None = None
    latest_version: int | None = None
    latest_quality_score: float | None = None
    updated_at: dt.datetime | None = None


class RegisterDatasetRequest(ApiModel):
    name: str = Field(min_length=1, max_length=128)
    source_uri: str = Field(min_length=1)
    entity_type: str = Field(min_length=1, max_length=64)
    source_type: Literal["parquet", "csv", "json"] = "parquet"


class DatasetVersionSummary(ApiModel):
    version_no: int
    row_count: int | None = None
    checksum: str | None = None
    quality_score: float | None = None
    created_at: dt.datetime | None = None
    storage_uri: str | None = None


# --- pipelines -----------------------------------------------------------------


class StartRunRequest(ApiModel):
    pipeline: str = Field(default="churn_v1", max_length=64)
    inputs: dict[str, str] = Field(default_factory=dict)
    mode: Literal["full", "incremental", "replay"] = "full"
    config_overrides: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)


class StartRunResponse(ApiModel):
    run_id: str
    state: str
    links: dict[str, str]


class StageView(ApiModel):
    name: str
    status: str
    attempts: int
    duration_s: float | None = None


class PendingApprovalView(ApiModel):
    id: uuid.UUID
    action_type: str
    target_ref: str | None = None
    expires_at: dt.datetime | None = None


class RunSummary(ApiModel):
    run_id: str
    pipeline: str
    state: str
    current_stage: str | None = None
    trigger_type: str
    triggered_by: str | None = None
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    error_summary: str | None = None


class RunDetail(RunSummary):
    mode: str
    config_hash: str | None = None
    stages: list[StageView] = Field(default_factory=list)
    budget_used: dict[str, Any] = Field(default_factory=dict)
    budget_limits: dict[str, Any] = Field(default_factory=dict)
    pending_approval: PendingApprovalView | None = None
    # For an escalated run whose candidate failed only a rule a person may override: the
    # failed rules and the reason, so the page can offer the two-approver path (PRD 30).
    override: dict[str, Any] | None = None


class OverrideRequest(ApiModel):
    """The written justification PRD section 30 requires for a promotion despite a failed check."""

    note: str = Field(min_length=20, max_length=2000)


class DecisionView(ApiModel):
    id: uuid.UUID
    agent: str
    decision_type: str
    decision: str
    summary: str | None = None
    policy_rule_ids: list[Any] = Field(default_factory=list)
    evidence_refs: dict[str, Any] = Field(default_factory=dict)
    # The options that were considered and refused. Empty for a decision with no alternative -
    # a routing rule match has one answer - and populated for an acceptance decision, where
    # each entry carries the family, the rule that rejected it and the evidence.
    alternatives: list[Any] = Field(default_factory=list)
    outcome: str
    created_at: dt.datetime | None = None


class ArtifactView(ApiModel):
    id: uuid.UUID
    language: str
    template_id: str | None = None
    template_version: str | None = None
    slot_id: str | None = None
    content_hash: str
    storage_uri: str | None = None
    static_check_status: str | None = None
    compile_status: str | None = None
    gate_results: dict[str, Any] = Field(default_factory=dict)


class LogLine(ApiModel):
    at: dt.datetime | None = None
    stage: str | None = None
    level: str
    message: str


class ResumeRequest(ApiModel):
    fix_note: str = Field(min_length=10, max_length=2000)


class CancelResponse(ApiModel):
    run_id: str
    state: str


# --- models and approvals ------------------------------------------------------


class ModelVersionView(ApiModel):
    id: uuid.UUID
    algorithm: str
    mlflow_version: str | None = None
    feature_set_version: str | None = None
    governance_status: str
    threshold: float | None = None
    created_at: dt.datetime | None = None
    promoted_at: dt.datetime | None = None
    metrics: dict[str, float] = Field(default_factory=dict)


class ModelSummary(ApiModel):
    id: uuid.UUID
    name: str
    task_type: str
    target_definition: str
    champion: ModelVersionView | None = None
    challenger: ModelVersionView | None = None


class ModelDetail(ModelSummary):
    versions: list[ModelVersionView] = Field(default_factory=list)
    model_card_uri: str | None = None


class DecisionRequest(ApiModel):
    approved: bool
    comment: str = Field(min_length=1, max_length=2000)


class ApprovalView(ApiModel):
    id: uuid.UUID
    run_id: uuid.UUID
    action_type: str
    target_ref: str
    status: str
    requested_by: str
    decided_by: uuid.UUID | None = None
    decision_note: str | None = None
    evidence_uri: str | None = None
    requested_at: dt.datetime | None = None
    expires_at: dt.datetime | None = None
    decided_at: dt.datetime | None = None
    # A share of a decision needing several people: the role that must decide this share, and
    # the group the shares belong to. NULL for an ordinary single approval.
    policy_action: str | None = None
    required_role: str | None = None
    group_id: uuid.UUID | None = None


class RollbackRequest(ApiModel):
    previous_version: str = Field(min_length=1, max_length=32)
    reason: str = Field(min_length=10, max_length=500)


# --- predictions, drift, reports, audit ----------------------------------------


class PredictionView(ApiModel):
    customer_ref: str
    score_date: dt.date
    churn_probability: float
    risk_band: str
    prediction_set: str
    model_version_id: uuid.UUID
    feature_set_version: str | None = None
    top_reasons: list[Any] = Field(default_factory=list)


class PredictionAggregate(ApiModel):
    """What a Viewer sees instead of rows (PRD 25.2: aggregates only)."""

    rows: int
    by_risk_band: dict[str, int]
    mean_probability: float | None = None
    score_dates: list[dt.date] = Field(default_factory=list)


class DriftReportView(ApiModel):
    id: uuid.UUID
    model_version_id: uuid.UUID
    reference_ref: str
    current_ref: str
    flagged_features: list[Any] = Field(default_factory=list)
    max_psi: float | None = None
    score_flagged: bool | None = None
    computed_at: dt.datetime | None = None


class AlertView(ApiModel):
    id: uuid.UUID
    alert_type: str
    severity: str
    message: str
    status: str
    run_id: uuid.UUID | None = None
    created_at: dt.datetime | None = None


class ReportView(ApiModel):
    id: uuid.UUID
    run_id: uuid.UUID
    report_type: str
    format: str
    status: str
    storage_uri: str
    claim_check_status: str | None = None
    claim_check_json: dict[str, Any] = Field(default_factory=dict)
    created_at: dt.datetime | None = None


class AuditActionCount(ApiModel):
    action: str
    count: int


class AuditEntryView(ApiModel):
    id: int
    ts: dt.datetime
    actor_type: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    run_id: uuid.UUID | None = None
    outcome: str
    details: dict[str, Any] = Field(default_factory=dict)
    entry_hash: str


class ChainStatus(ApiModel):
    ok: bool
    entries_checked: int
    chains_checked: int
    broken_at: int | None = None
    broken_scope: str | None = None
    detail: str | None = None


class HealthStatus(ApiModel):
    status: Literal["ok", "degraded"]
    database: bool
    policy_loaded: bool
    version: str
