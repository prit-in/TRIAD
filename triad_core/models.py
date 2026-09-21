"""SQLAlchemy models for the 19 PRD tables (section 24.1) plus `events` and `fix_memory`.

PostgreSQL is the single source of truth for run state, the queue, approvals and audit
(DD-01). MLflow keeps its own tables; TRIAD stores references plus governance status.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _now() -> Mapped[dt.datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# --- M01 Authentication -------------------------------------------------------

ROLES = (
    "admin",
    "data_engineer",
    "data_scientist",
    "ml_engineer",
    "business_analyst",
    "viewer",
    "agent",
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[dt.datetime] = _now()
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "role IN ('admin','data_engineer','data_scientist','ml_engineer',"
            "'business_analyst','viewer','agent')",
            name="users_role_valid",
        ),
    )


# --- M02 Dataset management ---------------------------------------------------


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)  # csv/json/parquet/jdbc
    source_uri: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    contract_yaml: Mapped[str | None] = mapped_column(Text)
    auto_run_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[dt.datetime] = _now()

    versions: Mapped[list[DatasetVersion]] = relationship(back_populates="dataset")


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    row_count: Mapped[int | None] = mapped_column(BigInteger)
    schema_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    schema_hash: Mapped[str | None] = mapped_column(String(64))
    quality_score: Mapped[float | None] = mapped_column(Float)
    profile_uri: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="registered")
    created_at: Mapped[dt.datetime] = _now()
    created_by: Mapped[str | None] = mapped_column(String(128))

    dataset: Mapped[Dataset] = relationship(back_populates="versions")

    __table_args__ = (
        UniqueConstraint("dataset_id", "checksum_sha256", name="uq_dataset_checksum"),
        UniqueConstraint("dataset_id", "version_no", name="uq_dataset_version_no"),
    )


# --- M07 Orchestration --------------------------------------------------------

RUN_STATES = (
    "RECEIVED",
    "PROFILED",
    "PLANNED",
    "CODE_GENERATED",
    "VALIDATED",
    "EXECUTING",
    "FEATURES_READY",
    "TRAINING",
    "EVALUATED",
    "OPTIMIZING",
    "SELECTED",
    "APPROVAL_REQUIRED",
    "DEPLOYED",
    "PUBLISHED",
    "MONITORING",
    "COMPLETED",
    "FAILED",
    "DIAGNOSING",
    "REGENERATING",
    "RETRYING",
    "ESCALATED",
    "REJECTED_INPUT",
    "ABORTED",
    "CANCELLED",
)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    pipeline_name: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    triggered_by: Mapped[str | None] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="RECEIVED")
    current_stage: Mapped[str | None] = mapped_column(String(64))
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    config_hash: Mapped[str | None] = mapped_column(String(64))
    input_versions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pipeline_runs.id"))
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="full")
    budget_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    llm_calls_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    llm_cost_used: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    policy_version: Mapped[str | None] = mapped_column(String(32))
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    started_at: Mapped[dt.datetime] = _now()
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    error_summary: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("mode IN ('full','incremental','replay')", name="runs_mode_valid"),
        Index("ix_runs_state", "state"),
    )


TASK_STATUSES = ("queued", "leased", "running", "succeeded", "failed", "escalated", "cancelled")


class AgentTask(Base):
    __tablename__ = "agent_tasks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    input_refs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    output_refs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    error_class: Mapped[str | None] = mapped_column(String(64))
    error_fingerprint: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = _now()
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','leased','running','succeeded','failed','escalated','cancelled')",
            name="tasks_status_valid",
        ),
        Index(
            "ix_tasks_queue",
            "priority",
            "created_at",
            postgresql_where=text("status = 'queued'"),
        ),
        Index("ix_tasks_run", "run_id"),
    )


class Event(Base):
    """Trigger bus (PRD section 12.1). Written inside the same transaction as state changes."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pipeline_runs.id"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    consumed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[dt.datetime] = _now()

    __table_args__ = (Index("ix_events_type_created", "event_type", "created_at"),)


# --- M08 Agents: decisions, LLM calls, artifacts, executions -------------------


class DecisionRecord(Base):
    __tablename__ = "decision_records"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_tasks.id"))
    agent: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_type: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    alternatives: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    evidence_refs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    policy_rule_ids: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    summary: Mapped[str | None] = mapped_column(String(500))
    llm_invocation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("llm_invocations.id"))
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, default="applied")
    created_at: Mapped[dt.datetime] = _now()

    __table_args__ = (Index("ix_decisions_run", "run_id"),)


class LlmInvocation(Base):
    __tablename__ = "llm_invocations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pipeline_runs.id"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_tasks.id"))
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_template_id: Mapped[str | None] = mapped_column(String(64))
    prompt_template_version: Mapped[str | None] = mapped_column(String(16))
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    redacted_prompt_uri: Mapped[str | None] = mapped_column(Text)
    response_uri: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_estimate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ok")
    created_at: Mapped[dt.datetime] = _now()


class CodeArtifact(Base):
    __tablename__ = "code_artifacts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    template_id: Mapped[str] = mapped_column(String(64), nullable=False)
    template_version: Mapped[str] = mapped_column(String(16), nullable=False)
    slot_id: Mapped[str | None] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    git_commit: Mapped[str | None] = mapped_column(String(64))
    static_check_status: Mapped[str | None] = mapped_column(String(16))
    compile_status: Mapped[str | None] = mapped_column(String(16))
    test_status: Mapped[str | None] = mapped_column(String(16))
    gate_results: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    llm_invocation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("llm_invocations.id"))
    parent_artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("code_artifacts.id"))
    created_at: Mapped[dt.datetime] = _now()

    __table_args__ = (Index("ix_artifacts_run", "run_id"),)


class Execution(Base):
    __tablename__ = "executions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_tasks.id"))
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("code_artifacts.id"))
    # Which pipeline stage this execution belongs to. `task_id` is null whenever the pipeline
    # is driven directly rather than through the queue - which is what `scripts/run_demo.py`
    # does - and the run detail's stage list was built from `agent_tasks` alone, so a demo run
    # showed an empty timeline despite having executed four stages. The stage was recoverable
    # only by scraping `--output=` out of the command string, and the profiling row has no
    # command at all, so it is recorded here instead of inferred later.
    stage: Mapped[str | None] = mapped_column(String(64))
    engine: Mapped[str] = mapped_column(String(32), nullable=False)
    spark_app_id: Mapped[str | None] = mapped_column(String(128))
    image_digest: Mapped[str | None] = mapped_column(String(128))
    command: Mapped[str | None] = mapped_column(Text)
    resources_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    exit_code: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[dt.datetime] = _now()
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    inputs_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    outputs_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    log_uri: Mapped[str | None] = mapped_column(Text)


# --- M09..M13 ML --------------------------------------------------------------


class Experiment(Base):
    __tablename__ = "experiments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False)
    mlflow_experiment_id: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    objective_metric: Mapped[str] = mapped_column(String(32), nullable=False, default="pr_auc")
    split_spec_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    feature_set_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = _now()


class Model(Base):
    __tablename__ = "models"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    mlflow_registered_name: Mapped[str] = mapped_column(String(128), nullable=False)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False, default="binary")
    target_definition: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    champion_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("model_versions.id", use_alter=True, name="fk_models_champion")
    )
    created_at: Mapped[dt.datetime] = _now()


GOVERNANCE_STATUSES = (
    "candidate",
    "challenger",
    "pending_approval",
    "champion",
    "archived",
    "rejected",
)


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("models.id"), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pipeline_runs.id"))
    mlflow_version: Mapped[str | None] = mapped_column(String(32))
    mlflow_run_id: Mapped[str | None] = mapped_column(String(64))
    algorithm: Mapped[str] = mapped_column(String(64), nullable=False)
    params_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    feature_set_version: Mapped[str | None] = mapped_column(String(64))
    training_data_ref: Mapped[str | None] = mapped_column(Text)
    signature_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    threshold: Mapped[float | None] = mapped_column(Float)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    governance_status: Mapped[str] = mapped_column(String(32), nullable=False, default="candidate")
    model_card_uri: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _now()
    promoted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    promoted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    __table_args__ = (
        CheckConstraint(
            "governance_status IN ('candidate','challenger','pending_approval','champion',"
            "'archived','rejected')",
            name="model_versions_status_valid",
        ),
    )


class Metric(Base):
    __tablename__ = "metrics"

    id: Mapped[uuid.UUID] = _uuid_pk()
    scope: Mapped[str] = mapped_column(String(32), nullable=False)  # run | model_version | dataset
    ref_id: Mapped[str] = mapped_column(String(64), nullable=False)
    split: Mapped[str | None] = mapped_column(String(16))
    segment: Mapped[str | None] = mapped_column(String(64))
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_value: Mapped[float | None] = mapped_column(Float)
    ci_low: Mapped[float | None] = mapped_column(Float)
    ci_high: Mapped[float | None] = mapped_column(Float)
    computed_at: Mapped[dt.datetime] = _now()

    __table_args__ = (Index("ix_metrics_lookup", "ref_id", "metric_name", "split", "segment"),)


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_versions.id"), nullable=False
    )
    prediction_set: Mapped[str] = mapped_column(String(16), nullable=False)  # official | shadow
    customer_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    score_date: Mapped[dt.date] = mapped_column(nullable=False)
    churn_probability: Mapped[float] = mapped_column(Float, nullable=False)
    risk_band: Mapped[str] = mapped_column(String(16), nullable=False)
    top_reasons: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    feature_set_version: Mapped[str | None] = mapped_column(String(64))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pipeline_runs.id"))
    created_at: Mapped[dt.datetime] = _now()

    __table_args__ = (
        CheckConstraint("prediction_set IN ('official','shadow')", name="pred_set_valid"),
        Index("ix_predictions_lookup", "model_version_id", "score_date", "risk_band"),
    )


# --- M14 Reporting, M18 Approvals, M17 Audit, M16 Monitoring ------------------


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False)
    report_type: Mapped[str] = mapped_column(String(32), nullable=False)  # technical | business
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    format: Mapped[str] = mapped_column(String(8), nullable=False, default="html")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    claim_check_status: Mapped[str | None] = mapped_column(String(16))
    claim_check_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[dt.datetime] = _now()


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_ref: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_uri: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decision_note: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[dt.datetime] = _now()
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "action_type IN ('model_promotion','destructive_cleaning','schema_change',"
            "'definition_change','policy_override','quota_increase')",
            name="approvals_action_valid",
        ),
        CheckConstraint(
            "status IN ('pending','approved','rejected','expired')", name="approvals_status_valid"
        ),
        CheckConstraint(
            "status = 'pending' OR (decided_at IS NOT NULL AND decision_note IS NOT NULL)",
            name="approvals_decided_needs_note",
        ),
    )


class AuditLog(Base):
    """Append-only, hash-chained (PRD section 24.4). The app role may SELECT and INSERT only."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = _now()
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pipeline_runs.id"))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    __table_args__ = (
        CheckConstraint("actor_type IN ('user','agent','system')", name="audit_actor_valid"),
        Index("ix_audit_run", "run_id"),
    )


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    alert_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="warning")
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pipeline_runs.id"))
    model_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("models.id"))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    created_at: Mapped[dt.datetime] = _now()
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class DriftReport(Base):
    __tablename__ = "drift_reports"

    id: Mapped[uuid.UUID] = _uuid_pk()
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_versions.id"), nullable=False
    )
    reference_ref: Mapped[str] = mapped_column(Text, nullable=False)
    current_ref: Mapped[str] = mapped_column(Text, nullable=False)
    psi_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    flagged_features: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    computed_at: Mapped[dt.datetime] = _now()


class FixMemory(Base):
    """FR-062 (Could): error fingerprint -> fix that worked. Lookup needs no embeddings."""

    __tablename__ = "fix_memory"

    id: Mapped[uuid.UUID] = _uuid_pk()
    error_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    error_class: Mapped[str] = mapped_column(String(64), nullable=False)
    fix_kind: Mapped[str] = mapped_column(String(32), nullable=False)  # playbook | code_diff
    fix_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_used_at: Mapped[dt.datetime] = _now()
