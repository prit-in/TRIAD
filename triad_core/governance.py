"""M18 Governance: approval requests, decisions and separation of duties (PRD section 30).

The rules that matter are enforced here, in one place, deterministically:
  * a gated action never runs without a recorded approval (FR-075, UC-17);
  * the approver of a promotion must differ from the requester — self-approval returns 403
    (ATC-17, AT-16);
  * a decision without a comment is refused;
  * a request expires after the policy's window and counts as rejected;
  * an invalid policy file denies every gated action (SYS-05, R-14).
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from triad_core import audit
from triad_core.ids import utcnow
from triad_core.models import Alert, Approval, Event, PipelineRun, User
from triad_core.policy import PolicyEngine, Verdict


class GovernanceError(Exception):
    """Base class. `status` mirrors the HTTP code the API returns for this refusal."""

    status = 400


class SelfApprovalRefused(GovernanceError):
    status = 403


class NotAuthorised(GovernanceError):
    status = 403


class CommentRequired(GovernanceError):
    status = 422


class NothingPending(GovernanceError):
    status = 409


class PolicyDenied(GovernanceError):
    status = 403


@dataclass
class EvidenceBundle:
    """What an approver sees on one page (PRD 30.5, AT-08)."""

    action_type: str
    target_ref: str
    metrics_vs_champion: dict[str, Any] = field(default_factory=dict)
    segment_checks: list[dict[str, Any]] = field(default_factory=list)
    calibration: dict[str, Any] = field(default_factory=dict)
    drift_summary: dict[str, Any] = field(default_factory=dict)
    feature_changes: dict[str, Any] = field(default_factory=dict)
    model_card_uri: str | None = None
    decision_records: list[dict[str, Any]] = field(default_factory=list)
    risk_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "target_ref": self.target_ref,
            "metrics_vs_champion": self.metrics_vs_champion,
            "segment_checks": self.segment_checks,
            "calibration": self.calibration,
            "drift_summary": self.drift_summary,
            "feature_changes": self.feature_changes,
            "model_card_uri": self.model_card_uri,
            "decision_records": self.decision_records,
            "risk_notes": self.risk_notes,
        }

    def missing(self, required: list[str]) -> list[str]:
        """Which required evidence fields are empty, so an incomplete request is refused.

        The policy names evidence by concept ("model_card"); the bundle stores some of it by
        reference ("model_card_uri"), so both spellings satisfy the requirement.
        """
        present = self.to_dict()
        optional = {"override_note", "usage_history", "cost_estimate", "verified_report",
                    "impact_estimate", "sample_rows", "schema_diff", "label_impact_analysis"}
        out: list[str] = []
        for name in required:
            if name in optional:
                continue
            if present.get(name) or present.get(f"{name}_uri"):
                continue
            out.append(name)
        return out


class GovernanceService:
    def __init__(self, session: Session, policy: PolicyEngine) -> None:
        self.session = session
        self.policy = policy

    # -- requesting ------------------------------------------------------------
    def evaluate(
        self, action: str, *, context: dict[str, Any] | None = None, actor_role: str = "agent"
    ):
        return self.policy.check(action, context=context, actor_role=actor_role)

    def request_approval(
        self,
        run: PipelineRun,
        *,
        action: str,
        target_ref: str,
        requested_by: str,
        evidence: EvidenceBundle,
        evidence_uri: str,
        context: dict[str, Any] | None = None,
    ) -> Approval:
        """Create an approval request. Refuses if the policy forbids the action outright."""
        decision = self.policy.check(action, context=context or {})
        if decision.verdict is Verdict.DENY:
            audit.record(
                self.session,
                actor_type="agent",
                actor_id=requested_by,
                action="governance.denied",
                resource_type="approval",
                resource_id=target_ref,
                run_id=run.id,
                outcome="denied",
                details={"action": action, "reason": decision.reason,
                         "policy_rules": decision.rule_ids},
            )
            raise PolicyDenied(f"{action} is denied by policy: {decision.reason}")

        missing = evidence.missing(decision.required_evidence)
        if missing:
            raise GovernanceError(
                f"evidence bundle is incomplete for {action}: missing {missing}"
            )

        hours = float(self.policy.defaults().get("approval_expiry_hours", 72))
        approval = Approval(
            run_id=run.id,
            action_type=_action_type(action),
            target_ref=target_ref,
            requested_by=requested_by,
            evidence_uri=evidence_uri,
            status="pending",
            expires_at=utcnow() + dt.timedelta(hours=hours),
        )
        self.session.add(approval)
        self.session.flush()

        audit.record(
            self.session,
            actor_type="agent",
            actor_id=requested_by,
            action="approval.requested",
            resource_type="approval",
            resource_id=str(approval.id),
            run_id=run.id,
            details={"action": action, "target": target_ref,
                     "approvers": decision.approvers or decision.approvers_all_of,
                     "expires_at": approval.expires_at.isoformat()},
        )
        self.session.add(
            Event(
                event_type="approval.requested",
                run_id=run.id,
                payload={"approval_id": str(approval.id), "action_type": approval.action_type,
                         "target_ref": target_ref},
            )
        )
        return approval

    # -- deciding --------------------------------------------------------------
    def decide(
        self,
        approval_id: uuid.UUID,
        *,
        approver: User,
        approved: bool,
        comment: str,
        action: str | None = None,
    ) -> Approval:
        """Record a decision. Every refusal path raises with the status the API returns."""
        approval = self.session.get(Approval, approval_id)
        if approval is None:
            raise NothingPending(f"approval {approval_id} not found")
        if approval.status != "pending":
            raise NothingPending(
                f"approval {approval_id} is already {approval.status}"
            )

        if not comment or not comment.strip():
            raise CommentRequired("a decision without a comment is refused")

        if self.expire_if_due(approval):
            raise NothingPending("the approval request has expired and counts as rejected")

        policy_action = action or _policy_action(approval.action_type)
        decision = self.policy.check(policy_action)
        allowed_roles = set(decision.approvers) | set(decision.approvers_all_of)
        if allowed_roles and approver.role not in allowed_roles and approver.role != "admin":
            audit.record(
                self.session,
                actor_type="user",
                actor_id=str(approver.id),
                action="approval.refused_role",
                resource_type="approval",
                resource_id=str(approval.id),
                run_id=approval.run_id,
                outcome="refused",
                details={"role": approver.role, "allowed": sorted(allowed_roles)},
            )
            raise NotAuthorised(
                f"role {approver.role} may not decide {approval.action_type}; "
                f"allowed: {sorted(allowed_roles)}"
            )

        # Separation of duties (PRD 30.5): the approver must differ from the requester.
        if decision.separation_of_duties and _same_actor(approval.requested_by, approver):
            audit.record(
                self.session,
                actor_type="user",
                actor_id=str(approver.id),
                action="approval.self_approval_refused",
                resource_type="approval",
                resource_id=str(approval.id),
                run_id=approval.run_id,
                outcome="refused",
                details={"requested_by": approval.requested_by, "approver": str(approver.id)},
            )
            raise SelfApprovalRefused(
                f"user {approver.id} requested {approval.action_type} for "
                f"{approval.target_ref} and cannot approve it"
            )

        approval.status = "approved" if approved else "rejected"
        approval.decided_by = approver.id
        approval.decision_note = comment.strip()
        approval.decided_at = utcnow()
        self.session.flush()

        audit.record(
            self.session,
            actor_type="user",
            actor_id=str(approver.id),
            action=f"approval.{approval.status}",
            resource_type="approval",
            resource_id=str(approval.id),
            run_id=approval.run_id,
            outcome=approval.status,
            details={"action_type": approval.action_type, "target": approval.target_ref,
                     "comment": approval.decision_note[:500]},
        )
        self.session.add(
            Event(
                event_type="approval.decided",
                run_id=approval.run_id,
                payload={"approval_id": str(approval.id), "status": approval.status},
            )
        )
        return approval

    # -- housekeeping ----------------------------------------------------------
    def expire_if_due(self, approval: Approval) -> bool:
        if approval.status == "pending" and approval.expires_at <= utcnow():
            approval.status = "expired"
            approval.decided_at = utcnow()
            approval.decision_note = "expired without a decision; counted as rejected"
            self.session.flush()
            audit.record(
                self.session,
                actor_type="system",
                actor_id="governance",
                action="approval.expired",
                resource_type="approval",
                resource_id=str(approval.id),
                run_id=approval.run_id,
                outcome="expired",
                details={"action_type": approval.action_type},
            )
            return True
        return False

    def expire_due(self) -> int:
        pending = self.session.execute(
            select(Approval).where(Approval.status == "pending")
        ).scalars().all()
        return sum(1 for a in pending if self.expire_if_due(a))

    def pending_for(self, run_id: uuid.UUID) -> list[Approval]:
        return list(
            self.session.execute(
                select(Approval).where(
                    Approval.run_id == run_id, Approval.status == "pending"
                )
            ).scalars().all()
        )

    def alert(
        self, *, alert_type: str, message: str, severity: str = "warning",
        run_id: uuid.UUID | None = None, evidence: dict[str, Any] | None = None,
    ) -> Alert:
        a = Alert(
            alert_type=alert_type, severity=severity, run_id=run_id,
            message=message, evidence=evidence or {},
        )
        self.session.add(a)
        self.session.flush()
        audit.record(
            self.session,
            actor_type="system",
            actor_id="monitoring",
            action="alert.raised",
            resource_type="alert",
            resource_id=str(a.id),
            run_id=run_id,
            outcome=severity,
            details={"alert_type": alert_type, "message": message[:500]},
        )
        return a


def _action_type(policy_action: str) -> str:
    """Map a policy action name onto the approvals table's constrained action_type."""
    return {
        "model.promote": "model_promotion",
        "model.promote_with_failed_check": "model_promotion",
        "cleaning.execute": "destructive_cleaning",
        "schema.change": "schema_change",
        "churn_definition.change": "definition_change",
        "quota.increase": "quota_increase",
        "llm_budget.increase": "quota_increase",
        "report.publish_external": "policy_override",
    }.get(policy_action, "policy_override")


def _policy_action(action_type: str) -> str:
    return {
        "model_promotion": "model.promote",
        "destructive_cleaning": "cleaning.execute",
        "schema_change": "schema.change",
        "definition_change": "churn_definition.change",
        "quota_increase": "quota.increase",
        "policy_override": "report.publish_external",
    }.get(action_type, "model.promote")


def _same_actor(requested_by: str, approver: User) -> bool:
    """`requested_by` is a user id or 'agent:<name>'; only a person can self-approve."""
    if requested_by.startswith("agent:"):
        return False
    return requested_by in (str(approver.id), approver.email)
