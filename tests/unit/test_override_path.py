"""PRD section 30, "promotion despite a failed check", and PRD 19.4's segment threshold review.

DEC-27. A candidate that failed only a calibration (AP-5) or segment (AP-6) rule, and would
otherwise have been proposed, can be promoted only with one approval per role in
`approvers_all_of` - an ML Engineer and an Admin - each decided by that role, by a different
person, neither the requester nor whoever started the run, with a written justification.
Every test here asserts a refusal or a gate, because the path exists to be hard to walk.
"""

from __future__ import annotations

import uuid

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agents.graph import queue as q
from backend.app.deps import get_session
from backend.app.main import app
from backend.app.security import hash_password, issue_token
from python_ml.triad_ml.evaluate import segment_threshold_review
from triad_core.config import settings
from triad_core.governance import (
    DuplicateApprover,
    EvidenceBundle,
    GovernanceService,
    NotAuthorised,
    SelfApprovalRefused,
)
from triad_core.models import AgentTask, Approval, PipelineRun, User
from triad_core.policy import PolicyEngine

ACTION = "model.promote_with_failed_check"


# --- fixtures ---------------------------------------------------------------------------------


@pytest.fixture()
def people(db_session) -> dict[str, User]:
    """The requester (a data engineer), the run's starter, and one approver per role."""
    stamp = uuid.uuid4().hex[:8]
    made = {}
    for name, role in (("requester", "data_engineer"), ("starter", "data_scientist"),
                       ("ml", "ml_engineer"), ("ml2", "ml_engineer"), ("admin", "admin"),
                       ("analyst", "business_analyst")):
        user = User(email=f"{name}-{stamp}@triad.test", display_name=name, role=role,
                    password_hash=hash_password("correct horse battery staple"), is_active=True)
        db_session.add(user)
        made[name] = user
    db_session.flush()
    return made


@pytest.fixture()
def service(db_session) -> GovernanceService:
    return GovernanceService(db_session, PolicyEngine(settings().governance_policy_path))


def _escalated_run(db_session, starter: User) -> PipelineRun:
    run = q.create_run(db_session, pipeline_name=f"churn_v1_{uuid.uuid4().hex[:8]}",
                       trigger_type="manual", triggered_by=str(starter.id))
    q.set_state(db_session, run, "ESCALATED", stage="evaluate")
    return run


def _evidence(note: str | None = "Calibration error is inside the measured noise floor.") -> EvidenceBundle:
    return EvidenceBundle(
        action_type="policy_override", target_ref="churn_classifier:9",
        metrics_vs_champion={"mean": 0.02, "lower_95": 0.005},
        model_card_uri="file:///data/triad/reports/card.md",
        decision_records=[{"outcome": "ESCALATE", "failed_rules": ["AP-5"]}],
        override_note=note,
    )


def _shares(service, run, requester) -> list[Approval]:
    return service.request_approvals(
        run, action=ACTION, target_ref="churn_classifier:9", requested_by=str(requester.id),
        evidence=_evidence(), evidence_uri="file:///data/triad/reports/override_evidence.json",
    )


def _share(shares, role) -> Approval:
    return next(s for s in shares if s.required_role == role)


# --- governance -----------------------------------------------------------------------------


def test_an_all_of_action_becomes_one_share_per_role(db_session, service, people) -> None:
    run = _escalated_run(db_session, people["starter"])
    shares = _shares(service, run, people["requester"])
    assert sorted(s.required_role for s in shares) == ["admin", "ml_engineer"]
    assert len({s.group_id for s in shares}) == 1 and shares[0].group_id is not None
    assert {s.policy_action for s in shares} == {ACTION}
    assert {s.action_type for s in shares} == {"policy_override"}


def test_the_written_justification_is_required_evidence(db_session, service, people) -> None:
    """It was listed as optional, so the override would have been granted without one."""
    assert "override_note" in _evidence(note=None).missing(
        ["override_note", "metrics_vs_champion", "model_card"])
    run = _escalated_run(db_session, people["starter"])
    from triad_core.governance import GovernanceError

    with pytest.raises(GovernanceError, match="override_note"):
        service.request_approvals(
            run, action=ACTION, target_ref="churn_classifier:9",
            requested_by=str(people["requester"].id), evidence=_evidence(note=None),
            evidence_uri="file:///x",
        )


def test_a_share_is_decided_only_by_its_own_role(db_session, service, people) -> None:
    """An admin may not stand in for the ML Engineer, or two approvers would be one role twice."""
    run = _escalated_run(db_session, people["starter"])
    shares = _shares(service, run, people["requester"])
    with pytest.raises(NotAuthorised):
        service.decide(_share(shares, "ml_engineer").id, approver=people["admin"],
                       approved=True, comment="admin standing in")
    with pytest.raises(NotAuthorised):
        service.decide(_share(shares, "admin").id, approver=people["analyst"],
                       approved=True, comment="wrong role")


def test_neither_the_requester_nor_the_runs_starter_may_approve(db_session, service, people) -> None:
    starter_ml = User(email=f"starter-ml-{uuid.uuid4().hex[:8]}@triad.test", display_name="s",
                      role="ml_engineer", password_hash="x", is_active=True)
    requester_admin = User(email=f"req-admin-{uuid.uuid4().hex[:8]}@triad.test",
                           display_name="r", role="admin", password_hash="x", is_active=True)
    db_session.add_all([starter_ml, requester_admin])
    db_session.flush()
    run = _escalated_run(db_session, starter_ml)
    shares = _shares(service, run, requester_admin)
    with pytest.raises(SelfApprovalRefused):
        service.decide(_share(shares, "ml_engineer").id, approver=starter_ml,
                       approved=True, comment="I started this run")
    with pytest.raises(SelfApprovalRefused):
        service.decide(_share(shares, "admin").id, approver=requester_admin,
                       approved=True, comment="I asked for this override")


def test_one_person_cannot_decide_two_shares(db_session, service, people) -> None:
    """Defence in depth for a group whose shares need the same role."""
    run = _escalated_run(db_session, people["starter"])
    group = uuid.uuid4()
    shares = []
    for _ in range(2):
        share = Approval(run_id=run.id, action_type="policy_override", target_ref="m:1",
                         requested_by=str(people["requester"].id), evidence_uri="file:///x",
                         status="pending", policy_action=ACTION, required_role="ml_engineer",
                         group_id=group, expires_at=pd.Timestamp.utcnow() + pd.Timedelta(days=1))
        db_session.add(share)
        shares.append(share)
    db_session.flush()
    service.decide(shares[0].id, approver=people["ml"], approved=True, comment="first share")
    with pytest.raises(DuplicateApprover):
        service.decide(shares[1].id, approver=people["ml"], approved=True, comment="second share")
    service.decide(shares[1].id, approver=people["ml2"], approved=True, comment="another person")
    assert service.group_status(shares[0]) == "approved"


def test_the_group_is_approved_only_when_every_share_is(db_session, service, people) -> None:
    run = _escalated_run(db_session, people["starter"])
    shares = _shares(service, run, people["requester"])
    assert service.group_status(shares[0]) == "pending"
    service.decide(_share(shares, "ml_engineer").id, approver=people["ml"], approved=True,
                   comment="metrics against the champion reviewed")
    assert service.group_status(shares[0]) == "pending", "one share is not the decision"
    service.decide(_share(shares, "admin").id, approver=people["admin"], approved=True,
                   comment="justification accepted")
    assert service.group_status(shares[0]) == "approved"


def test_one_rejected_share_rejects_the_decision(db_session, service, people) -> None:
    run = _escalated_run(db_session, people["starter"])
    shares = _shares(service, run, people["requester"])
    service.decide(_share(shares, "admin").id, approver=people["admin"], approved=False,
                   comment="not with this disparity")
    assert service.group_status(shares[0]) == "rejected"


# --- API ------------------------------------------------------------------------------------


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_session] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def tmp_store(storage, monkeypatch):
    """The endpoints read run_state.json through `storage()`; point it at a temporary root."""
    monkeypatch.setattr("triad_core.storage.storage", lambda: storage)
    return storage


def _auth(user: User) -> dict[str, str]:
    token, _ = issue_token(user_id=user.id, email=user.email, role=user.role)
    return {"Authorization": f"Bearer {token}"}


def _with_state(store, run, *, eligible: bool) -> None:
    from agents.graph.churn import write_state

    state = {"model_name": "churn_classifier", "mlflow_version": "9",
             "selection": {"champion_delta": {"mean": 0.02, "lower_95": 0.005}},
             "selection_outcome": "ESCALATE"}
    if eligible:
        state["override"] = {"eligible": True, "failed_rules": ["AP-6"],
                             "reason": "segment recall gap 0.331 within tenure_band exceeds the limit"}
    write_state(store, run, state)


NOTE = {"note": "The disparity is recorded in the model card; new customers get a separate review."}


def test_only_an_escalated_run_can_be_overridden(client, db_session, people, tmp_store) -> None:
    run = q.create_run(db_session, pipeline_name=f"churn_v1_{uuid.uuid4().hex[:8]}",
                       trigger_type="manual", triggered_by=str(people["starter"].id))
    _with_state(tmp_store, run, eligible=True)
    response = client.post(f"/api/v1/pipelines/{run.run_key}/override",
                           headers=_auth(people["requester"]), json=NOTE)
    assert response.status_code == 409


def test_an_escalation_without_an_eligible_candidate_is_refused(
    client, db_session, people, tmp_store
) -> None:
    run = _escalated_run(db_session, people["starter"])
    _with_state(tmp_store, run, eligible=False)
    response = client.post(f"/api/v1/pipelines/{run.run_key}/override",
                           headers=_auth(people["requester"]), json=NOTE)
    assert response.status_code == 409
    assert response.json()["type"].endswith("/not-overridable")


def test_a_short_justification_is_refused(client, db_session, people, tmp_store) -> None:
    run = _escalated_run(db_session, people["starter"])
    _with_state(tmp_store, run, eligible=True)
    response = client.post(f"/api/v1/pipelines/{run.run_key}/override",
                           headers=_auth(people["requester"]), json={"note": "ok"})
    assert response.status_code == 422


def test_the_override_walks_two_approvals_to_a_publish_task(
    client, db_session, people, tmp_store
) -> None:
    run = _escalated_run(db_session, people["starter"])
    _with_state(tmp_store, run, eligible=True)
    response = client.post(f"/api/v1/pipelines/{run.run_key}/override",
                           headers=_auth(people["requester"]), json=NOTE)
    assert response.status_code == 202, response.text
    shares = response.json()
    assert sorted(s["required_role"] for s in shares) == ["admin", "ml_engineer"]
    db_session.refresh(run)
    assert run.state == "APPROVAL_REQUIRED"

    again = client.post(f"/api/v1/pipelines/{run.run_key}/override",
                        headers=_auth(people["requester"]), json=NOTE)
    assert again.status_code == 409, "a second request while one is pending"

    def publish_tasks() -> int:
        return len(db_session.execute(
            select(AgentTask).where(AgentTask.run_id == run.id, AgentTask.task_type == "publish")
        ).scalars().all())

    ml_share = next(s for s in shares if s["required_role"] == "ml_engineer")
    admin_share = next(s for s in shares if s["required_role"] == "admin")
    first = client.post(f"/api/v1/approvals/{ml_share['id']}/decision", headers=_auth(people["ml"]),
                        json={"approved": True, "comment": "metrics against the champion read"})
    assert first.status_code == 200
    assert publish_tasks() == 0, "one approval of two must not publish"
    second = client.post(f"/api/v1/approvals/{admin_share['id']}/decision",
                         headers=_auth(people["admin"]),
                         json={"approved": True, "comment": "the written justification is adequate"})
    assert second.status_code == 200
    assert publish_tasks() == 1


def test_a_rejected_share_returns_the_run_to_escalated(client, db_session, people, tmp_store) -> None:
    run = _escalated_run(db_session, people["starter"])
    _with_state(tmp_store, run, eligible=True)
    shares = client.post(f"/api/v1/pipelines/{run.run_key}/override",
                         headers=_auth(people["requester"]), json=NOTE).json()
    admin_share = next(s for s in shares if s["required_role"] == "admin")
    response = client.post(f"/api/v1/approvals/{admin_share['id']}/decision",
                           headers=_auth(people["admin"]),
                           json={"approved": False, "comment": "the disparity is too large"})
    assert response.status_code == 200
    db_session.refresh(run)
    assert run.state == "ESCALATED"
    assert not db_session.execute(
        select(AgentTask).where(AgentTask.run_id == run.id, AgentTask.task_type == "publish")
    ).scalars().first()


def test_the_run_detail_offers_the_override(client, db_session, people, tmp_store) -> None:
    run = _escalated_run(db_session, people["starter"])
    _with_state(tmp_store, run, eligible=True)
    detail = client.get(f"/api/v1/pipelines/{run.run_key}", headers=_auth(people["requester"]))
    assert detail.status_code == 200
    assert detail.json()["override"]["failed_rules"] == ["AP-6"]


def _with_measured_state(store, run) -> None:
    """An eligible run whose selection evidence carries the gaps, the review and calibration."""
    from agents.graph.churn import write_state

    review = {"segmentation": "tenure_band", "gap_at_segment_thresholds": 0.04,
              "note": "Analysis only.",
              "segments": [{"segment": "tenure_band=under 6 months", "rows": 900,
                            "positives": 120, "recall_at_global_threshold": 0.16}]}
    write_state(store, run, {
        "model_name": "churn_classifier", "mlflow_version": "9", "selection_outcome": "ESCALATE",
        "selection": {
            "champion_delta": {"mean": 0.08, "lower_95": 0.07, "upper_95": 0.1},
            "evidence": {"segment_recall_gaps": {"tenure_band": 0.331, "country": 0.07},
                         "segment_threshold_review": review,
                         "ece_after_calibration": 0.019, "calibration": "isotonic"},
        },
        "override": {"eligible": True, "failed_rules": ["AP-6"],
                     "reason": "segment recall gap 0.331 within tenure_band exceeds the limit"},
    })


def test_approvers_read_the_measurements_not_a_file_path(
    client, db_session, people, tmp_store
) -> None:
    """PRD 30.5. The bundle said only "AP-6 failed", and the page printed its storage path."""
    run = _escalated_run(db_session, people["starter"])
    _with_measured_state(tmp_store, run)
    shares = client.post(f"/api/v1/pipelines/{run.run_key}/override",
                         headers=_auth(people["requester"]), json=NOTE).json()

    response = client.get(f"/api/v1/approvals/{shares[0]['id']}/evidence",
                          headers=_auth(people["ml"]))
    assert response.status_code == 200, response.text
    bundle = response.json()
    check = bundle["segment_checks"][0]
    assert check["rule"] == "AP-6" and check["recall_gaps"]["tenure_band"] == 0.331
    assert check["threshold_review"]["gap_at_segment_thresholds"] == 0.04
    assert bundle["calibration"] == {"calibration": "isotonic", "ece_after_calibration": 0.019}
    assert bundle["override_note"] == NOTE["note"]
    assert bundle["risk_notes"][0].startswith("segment recall gap 0.331")


def test_evidence_is_read_inside_the_data_root_only(client, db_session, people, tmp_store) -> None:
    run = _escalated_run(db_session, people["starter"])
    share = _shares(GovernanceService(db_session, PolicyEngine(settings().governance_policy_path)),
                    run, people["requester"])[0]
    share.evidence_uri = "file:///etc/passwd"
    db_session.flush()
    response = client.get(f"/api/v1/approvals/{share.id}/evidence", headers=_auth(people["ml"]))
    assert response.status_code == 404


def test_the_card_records_who_approved_and_what_they_overrode(
    db_session, service, people, tmp_store
) -> None:
    """PRD 17.10 and section 30: the card is written before anyone decides, so the approval
    and the failed rule were never on it."""
    from agents.graph.churn import record_approval_on_card
    from python_ml.triad_ml.registry import ModelCard, write_model_card

    run = _escalated_run(db_session, people["starter"])
    folder = tmp_store.reports_dir(run.run_key)
    write_model_card(ModelCard(
        model_name="churn_classifier", version="9", algorithm="hist_gb", intended_use="ranking",
        limits=[], churn_definition="no order in 90 days", horizon_days=90,
        training_cutoffs=["2026-01-01"], feature_set_version="fsv=2", candidate_comparison=[],
        metrics={}, calibration={}, segment_results=[], top_drivers=[], driver_method="shap",
        known_limitations=["one cutoff is one season"],
    ), folder / "model_card.md")
    bundle = tmp_store.write_json(folder / "override_evidence_test.json",
                                  {"override_note": NOTE["note"]})
    shares = _shares(service, run, people["requester"])
    for share in shares:
        share.evidence_uri = tmp_store.uri(bundle)
    service.decide(_share(shares, "ml_engineer").id, approver=people["ml"], approved=True,
                   comment="metrics against the champion read")
    service.decide(_share(shares, "admin").id, approver=people["admin"], approved=True,
                   comment="justification accepted")

    record_approval_on_card(tmp_store, run, shares, {
        "eligible": True, "failed_rules": ["AP-6"],
        "reason": "segment recall gap 0.331 within tenure_band exceeds the limit"})

    import json as _json

    card = _json.loads((folder / "model_card.json").read_text(encoding="utf-8"))
    assert sorted(card["approval"]["roles"]) == ["admin", "ml_engineer"]
    assert card["approval"]["failed_rules"] == ["AP-6"]
    assert card["approval"]["override_note"] == NOTE["note"]
    assert card["known_limitations"][-1].startswith("Promoted despite failing AP-6")
    markdown = (folder / "model_card.md").read_text(encoding="utf-8")
    assert "## Approval" in markdown and NOTE["note"] in markdown


def test_a_promoted_run_says_what_it_was_promoted_despite(
    client, db_session, people, tmp_store
) -> None:
    run = _escalated_run(db_session, people["starter"])
    _with_state(tmp_store, run, eligible=True)
    for state in ("APPROVAL_REQUIRED", "DEPLOYED", "PUBLISHED"):
        q.set_state(db_session, run, state, stage="deploy")
    detail = client.get(f"/api/v1/pipelines/{run.run_key}", headers=_auth(people["requester"]))
    assert detail.json()["override"]["failed_rules"] == ["AP-6"]


def test_a_policy_escalation_is_recorded_by_its_reason_code() -> None:
    """The error classifier has nothing to match in "segment recall gap ... exceeds the limit",
    and filed all ten such escalations as UNKNOWN."""
    from types import SimpleNamespace

    from agents.graph.churn import _reason_code
    from python_ml.triad_ml.evaluate import AcceptanceVerdict

    selection = SimpleNamespace(
        selected=SimpleNamespace(family="hist_gb"),
        verdicts={"logreg": AcceptanceVerdict(False, ["PR_AUC_BELOW_FLOOR"]),
                  "hist_gb": AcceptanceVerdict(False, ["SEGMENT_RECALL_GAP"])})
    assert _reason_code(selection) == "SEGMENT_RECALL_GAP"
    selection.selected = None
    assert _reason_code(selection) == "PR_AUC_BELOW_FLOOR"


# --- the segment threshold review (PRD 19.4) -----------------------------------------------


def test_the_review_finds_each_segments_threshold_and_never_applies_it() -> None:
    # Segment "new": churners score lower, so at the global threshold its recall is low.
    rng = np.random.default_rng(3)
    n = 400
    seg = np.where(np.arange(n) < n // 2, "old", "new")
    y = (rng.random(n) < 0.4).astype(int)
    score = np.where(y == 1, rng.uniform(0.6, 1.0, n), rng.uniform(0.0, 0.5, n))
    score = np.where(seg == "new", score * 0.6, score)
    review = segment_threshold_review(pd.DataFrame({"tenure": seg}), y, score,
                                      segment_col="tenure", threshold=0.55)
    assert review["applied"] is False
    by_name = {s["segment"]: s for s in review["segments"]}
    assert by_name["tenure=new"]["recall_at_global_threshold"] < by_name["tenure=old"]["recall_at_global_threshold"]
    for s in review["segments"]:
        assert s["recall_at_segment_threshold"] >= review["target_recall"] - 1e-9
    assert by_name["tenure=new"]["segment_threshold"] < by_name["tenure=old"]["segment_threshold"]
    assert review["gap_at_segment_thresholds"] < 0.10


def test_a_thin_segment_is_marked_not_reviewed() -> None:
    y = np.array([1] * 5 + [0] * 60 + [1] * 40 + [0] * 60)
    score = np.linspace(0, 1, len(y))
    seg = np.array(["thin"] * 65 + ["big"] * 100)
    review = segment_threshold_review(pd.DataFrame({"s": seg}), y, score, segment_col="s",
                                      threshold=0.5)
    thin = next(s for s in review["segments"] if s["segment"] == "s=thin")
    assert thin.get("insufficient_data") is True and "segment_threshold" not in thin


# --- selection continues past a failed AP-6 --------------------------------------------------


def _segment_failing_features(n_per_cutoff: int = 800, seed: int = 9) -> pd.DataFrame:
    """Group B's churn ignores the features, so no model can find B's churners.

    B is a quarter of the rows: large enough to pass DEC-02's 50 rows and 30 churners on the
    validation cutoff, small enough that the model as a whole still clears AP-1 and AP-3 -
    with half the labels pure noise, every family failed the ROC-AUC screen first.
    """
    from tests.unit.test_ml import make_features

    df = make_features(n_per_cutoff, seed=seed)
    rng = np.random.default_rng(seed + 1)
    df["grp"] = np.where(rng.random(len(df)) < 0.75, "A", "B")
    noise = rng.binomial(1, df["churned_90d"].mean(), size=len(df))
    df.loc[df["grp"] == "B", "churned_90d"] = noise[df["grp"] == "B"]
    return df


@pytest.mark.slow
def test_selection_continues_past_a_failed_segment_rule() -> None:
    from python_ml.triad_ml.candidates import shortlist
    from python_ml.triad_ml.splits import apply_split, plan_split
    from python_ml.triad_ml.train import select_model, train_all
    from tests.unit.test_ml import POLICY

    df = _segment_failing_features()
    split = apply_split(df, plan_split(sorted(df.snapshot_date.unique()), horizon_days=90))
    split.evaluation_only_cols = ("grp",)
    models = train_all(shortlist(n_rows=len(split.train), n_features=4), split, seed=42)

    no_champion = select_model(models, split, POLICY, segment_cols=["grp"])
    assert no_champion.outcome == "ESCALATE", no_champion.reason
    assert "AP-6" in no_champion.failed_rules
    assert no_champion.test_metrics is not None, "the candidate is still scored on the test cutoff"
    assert no_champion.override_eligible, "no champion: it would have been proposed"
    review = no_champion.evidence["segment_threshold_review"]
    assert review["segmentation"] == "grp" and review["applied"] is False

    # Fresh models: selection calibrates and tunes the ones it is given (test_ml's
    # reproducibility test retrains for the same reason).
    perfect = split.test["churned_90d"].to_numpy().astype(float)
    loses = select_model(train_all(shortlist(n_rows=len(split.train), n_features=4), split,
                                   seed=42),
                         split, POLICY, segment_cols=["grp"], champion_scores=perfect)
    assert loses.outcome == "ESCALATE"
    assert not loses.override_eligible, "a candidate that loses to the champion is not overridable"
    assert loses.champion_delta is not None and loses.champion_delta["lower_95"] <= 0
