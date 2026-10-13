"""REST API behaviour (PRD section 25): authentication, roles and the governed write paths.

Almost every test here asserts a *refusal*. That is the point: the API is the surface where a
client could ask for something the policy forbids, and the only useful proof is that it says
no, with the right status code and a Problem Details body that names why.

The app runs against the real PostgreSQL schema through an overridden session, so the rows
these tests read are rows the handlers really wrote, and everything is rolled back afterwards.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from backend.app.api.v1.settings import redact_dsn
from backend.app.deps import get_session
from backend.app.main import app
from backend.app.security import hash_password, issue_token
from triad_core.config import settings
from triad_core.models import User

pytestmark = pytest.mark.postgres

PASSWORD = "correct horse battery staple"
# A `file://` reference is passed through; a bare name is resolved to the
# latest registered version (see the resolution tests below).
SOURCE_URI = "file:///data/triad/raw/transactions/v=1"


def _pipeline() -> str:
    """A pipeline name of this test's own.

    Runs of one pipeline are refused while another is active, on purpose, so
    tests that start several runs would otherwise collide with that rule rather
    than with what they are actually checking.
    """
    return f"churn_v1_{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_session] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def users(db_session):
    """One user per role the tests need, each with a real argon2 hash."""
    made: dict[str, User] = {}
    stamp = uuid.uuid4().hex[:8]
    for role in ("admin", "data_engineer", "ml_engineer", "business_analyst", "viewer"):
        user = User(
            email=f"{role}-{stamp}@triad.test",
            display_name=role,
            role=role,
            password_hash=hash_password(PASSWORD),
            is_active=True,
        )
        db_session.add(user)
        made[role] = user
    db_session.flush()
    return made


def _auth(user: User) -> dict[str, str]:
    token, _ = issue_token(user_id=user.id, email=user.email, role=user.role)
    return {"Authorization": f"Bearer {token}"}


# --- health --------------------------------------------------------------------


def test_health_is_public_and_reports_real_state(client) -> None:
    body = client.get("/health").json()
    assert body["database"] is True
    assert body["policy_loaded"] is True
    assert body["status"] == "ok"


# --- authentication ------------------------------------------------------------


def test_login_returns_a_usable_token(client, users) -> None:
    response = client.post(
        "/api/v1/auth/token",
        json={"email": users["admin"].email, "password": PASSWORD},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer" and body["role"] == "admin"

    me = client.get("/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200 and me.json()["email"] == users["admin"].email


def test_a_wrong_password_and_an_unknown_email_are_indistinguishable(client, users) -> None:
    """Different answers here turn a login form into a staff directory."""
    wrong = client.post("/api/v1/auth/token",
                        json={"email": users["admin"].email, "password": "nope"})
    unknown = client.post("/api/v1/auth/token",
                          json={"email": "ghost@triad.test", "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]
    assert wrong.headers["content-type"].startswith("application/problem+json")


def test_a_deactivated_account_cannot_log_in(client, users, db_session) -> None:
    users["viewer"].is_active = False
    db_session.flush()
    response = client.post("/api/v1/auth/token",
                           json={"email": users["viewer"].email, "password": PASSWORD})
    assert response.status_code == 403
    assert response.json()["type"].endswith("/account-disabled")


def test_a_protected_endpoint_refuses_an_anonymous_caller(client) -> None:
    response = client.get("/api/v1/pipelines")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("header", ["", "Bearer", "Bearer not-a-token", "Basic abc"])
def test_a_malformed_authorization_header_is_refused(client, header: str) -> None:
    response = client.get("/api/v1/pipelines", headers={"Authorization": header})
    assert response.status_code == 401


def test_an_expired_token_is_refused(client, users) -> None:
    token, _ = issue_token(
        user_id=users["admin"].id, email=users["admin"].email,
        role=users["admin"].role, ttl_minutes=-1,
    )
    response = client.get("/api/v1/pipelines", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert "expired" in response.json()["detail"]


def test_a_token_signed_with_another_key_is_refused(client, users) -> None:
    import jwt

    forged = jwt.encode(
        {"sub": str(users["admin"].id), "role": "admin", "iss": "triad",
         "exp": int((dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)).timestamp())},
        "not-the-server-secret-but-as-long-as-hs256-needs", algorithm="HS256",
    )
    response = client.get("/api/v1/pipelines", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


def test_a_token_survives_its_user_being_deactivated_but_the_request_does_not(
    client, users, db_session
) -> None:
    """The token is still cryptographically valid; the database is the authority."""
    headers = _auth(users["viewer"])
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200

    users["viewer"].is_active = False
    db_session.flush()
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 403


# --- roles ---------------------------------------------------------------------


def test_a_viewer_may_not_start_a_run(client, users) -> None:
    response = client.post("/api/v1/pipelines/run", headers=_auth(users["viewer"]),
                           json={"pipeline": _pipeline()})
    assert response.status_code == 403
    assert response.json()["type"].endswith("/role")


def test_only_an_admin_may_read_the_audit_trail(client, users) -> None:
    assert client.get("/api/v1/audit-logs",
                      headers=_auth(users["business_analyst"])).status_code == 403
    assert client.get("/api/v1/audit-logs", headers=_auth(users["admin"])).status_code == 200


def test_an_admin_is_never_locked_out_of_a_role_gated_endpoint(client, users) -> None:
    response = client.post("/api/v1/pipelines/run", headers=_auth(users["admin"]),
                           json={"pipeline": _pipeline()})
    assert response.status_code == 202


# --- starting runs -------------------------------------------------------------


def test_starting_a_run_returns_202_with_a_location(client, users) -> None:
    response = client.post(
        "/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
        json={"pipeline": _pipeline(), "inputs": {"transactions": SOURCE_URI}},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["state"] == "RECEIVED"
    assert response.headers["Location"].endswith(body["run_id"])
    assert body["links"]["events"].endswith("/events")


def test_a_started_run_is_queued_not_executed_in_the_request(client, users, db_session) -> None:
    """The request creates work; the orchestrator does it. An HTTP timeout loses nothing."""
    from sqlalchemy import select

    from triad_core.models import AgentTask, PipelineRun

    body = client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                       json={"pipeline": _pipeline()}).json()
    run = db_session.execute(
        select(PipelineRun).where(PipelineRun.run_key == body["run_id"])
    ).scalar_one()
    tasks = db_session.execute(
        select(AgentTask).where(AgentTask.run_id == run.id)
    ).scalars().all()
    assert [t.status for t in tasks] == ["queued"]


def test_the_same_idempotency_key_returns_the_same_run(client, users) -> None:
    key = str(uuid.uuid4())
    payload = {"pipeline": _pipeline(), "inputs": {"transactions": SOURCE_URI}}
    first = client.post("/api/v1/pipelines/run", headers={**_auth(users["data_engineer"]),
                                                          "Idempotency-Key": key}, json=payload)
    second = client.post("/api/v1/pipelines/run", headers={**_auth(users["data_engineer"]),
                                                           "Idempotency-Key": key}, json=payload)
    assert first.status_code == 202 and second.status_code == 200
    assert first.json()["run_id"] == second.json()["run_id"]


def test_the_same_key_with_a_different_body_is_a_conflict(client, users) -> None:
    """Returning the old run would hide the client's bug."""
    key = str(uuid.uuid4())
    headers = {**_auth(users["data_engineer"]), "Idempotency-Key": key}
    client.post("/api/v1/pipelines/run", headers=headers,
                json={"pipeline": _pipeline(), "inputs": {"transactions": SOURCE_URI}})
    clash = client.post("/api/v1/pipelines/run", headers=headers,
                        json={"pipeline": _pipeline(), "inputs": {"transactions": SOURCE_URI + "-other"}})
    assert clash.status_code == 409
    assert clash.json()["type"].endswith("/idempotency-conflict")


def test_an_unknown_field_in_the_body_is_refused(client, users) -> None:
    response = client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                           json={"pipeline": _pipeline(), "run_shell": "rm -rf /"})
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


# --- reading and controlling runs ----------------------------------------------


def test_an_unknown_run_is_a_problem_not_a_crash(client, users) -> None:
    response = client.get("/api/v1/pipelines/run-1970-01-01-999",
                          headers=_auth(users["admin"]))
    assert response.status_code == 404
    assert response.json()["type"].endswith("/not-found")


def test_a_run_detail_reports_its_stages_and_budget(client, users) -> None:
    started = client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                          json={"pipeline": _pipeline(),
                                "budget": {"max_llm_calls": 5}}).json()
    detail = client.get(f"/api/v1/pipelines/{started['run_id']}",
                        headers=_auth(users["data_engineer"])).json()
    assert detail["state"] == "RECEIVED"
    # One task for the preparation phase; the worker drives the stages inside it and the
    # publish phase is queued separately once the approval is decided.
    assert [s["name"] for s in detail["stages"]] == ["pipeline"]
    assert detail["stages"][0]["status"] == "queued"
    assert detail["budget_limits"]["max_llm_calls"] == 5
    assert detail["budget_used"]["llm_calls"] == 0
    assert detail["pending_approval"] is None


def test_only_the_starter_or_an_admin_may_cancel(client, users) -> None:
    started = client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                          json={"pipeline": _pipeline()}).json()
    stranger = client.post(f"/api/v1/pipelines/{started['run_id']}/cancel",
                           headers=_auth(users["ml_engineer"]))
    assert stranger.status_code == 403

    owner = client.post(f"/api/v1/pipelines/{started['run_id']}/cancel",
                        headers=_auth(users["data_engineer"]))
    assert owner.status_code == 202 and owner.json()["state"] == "CANCELLED"


def test_cancelling_a_finished_run_is_a_conflict(client, users) -> None:
    started = client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                          json={"pipeline": _pipeline()}).json()
    client.post(f"/api/v1/pipelines/{started['run_id']}/cancel",
                headers=_auth(users["data_engineer"]))
    again = client.post(f"/api/v1/pipelines/{started['run_id']}/cancel",
                        headers=_auth(users["data_engineer"]))
    assert again.status_code == 409


def test_cancelling_also_cancels_the_queued_work(client, users, db_session) -> None:
    from sqlalchemy import select

    from triad_core.models import AgentTask, PipelineRun

    started = client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                          json={"pipeline": _pipeline()}).json()
    client.post(f"/api/v1/pipelines/{started['run_id']}/cancel",
                headers=_auth(users["data_engineer"]))
    run = db_session.execute(
        select(PipelineRun).where(PipelineRun.run_key == started["run_id"])
    ).scalar_one()
    statuses = db_session.execute(
        select(AgentTask.status).where(AgentTask.run_id == run.id)
    ).scalars().all()
    assert statuses == ["cancelled"]


def test_resuming_a_run_that_is_not_escalated_is_a_conflict(client, users) -> None:
    started = client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                          json={"pipeline": _pipeline()}).json()
    response = client.post(f"/api/v1/pipelines/{started['run_id']}/resume",
                           headers=_auth(users["data_engineer"]),
                           json={"fix_note": "widened the contract for the renamed column"})
    assert response.status_code == 409


def test_resuming_requires_a_substantial_fix_note(client, users) -> None:
    started = client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                          json={"pipeline": _pipeline()}).json()
    response = client.post(f"/api/v1/pipelines/{started['run_id']}/resume",
                           headers=_auth(users["data_engineer"]), json={"fix_note": "ok"})
    assert response.status_code == 422


def test_an_escalated_run_resumes_and_is_requeued(client, users, db_session) -> None:
    from sqlalchemy import select

    from triad_core.models import AgentTask, PipelineRun

    started = client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                          json={"pipeline": _pipeline()}).json()
    run = db_session.execute(
        select(PipelineRun).where(PipelineRun.run_key == started["run_id"])
    ).scalar_one()
    run.state = "ESCALATED"
    run.current_stage = "clean"
    db_session.flush()

    response = client.post(f"/api/v1/pipelines/{started['run_id']}/resume",
                           headers=_auth(users["ml_engineer"]),
                           json={"fix_note": "quarantine path added to the cleaning stage"})
    assert response.status_code == 202
    queued = db_session.execute(
        select(AgentTask).where(AgentTask.run_id == run.id, AgentTask.status == "queued")
    ).scalars().all()
    assert any(t.stage == "clean" for t in queued)


# --- pagination ----------------------------------------------------------------


def test_a_cursor_this_api_did_not_issue_is_refused(client, users) -> None:
    response = client.get("/api/v1/pipelines?cursor=not-base64!!",
                          headers=_auth(users["admin"]))
    assert response.status_code == 400


def test_the_page_limit_is_capped(client, users) -> None:
    response = client.get("/api/v1/pipelines?limit=99999", headers=_auth(users["admin"]))
    assert response.status_code == 200 and response.json()["limit"] == 200


# --- approvals and separation of duties ----------------------------------------


@pytest.fixture()
def pending_approval(db_session, users):
    """A real promotion request, made by the ML engineer, through the governance service."""
    from agents.graph import queue as q
    from triad_core.config import settings
    from triad_core.governance import EvidenceBundle, GovernanceService
    from triad_core.policy import PolicyEngine

    run = q.create_run(db_session, pipeline_name="churn_v1", trigger_type="manual",
                       triggered_by=str(users["ml_engineer"].id),
                       idempotency_key=f"api-{uuid.uuid4()}")
    service = GovernanceService(db_session, PolicyEngine(settings().governance_policy_path))
    approval = service.request_approval(
        run,
        action="model.promote",
        target_ref="churn_classifier:1",
        requested_by=str(users["ml_engineer"].id),
        evidence=EvidenceBundle(
            action_type="model_promotion",
            target_ref="churn_classifier:1",
            metrics_vs_champion={"pr_auc_delta": 0.03, "lower_95": 0.01},
            segment_checks=[{"segment": "country=UK", "recall": 0.62}],
            calibration={"ece": 0.02},
            drift_summary={"max_psi": 0.04},
            model_card_uri="file:///data/triad/reports/card.md",
            decision_records=[{"outcome": "PROPOSE_PROMOTION"}],
        ),
        evidence_uri="file:///data/triad/reports/evidence.json",
    )
    db_session.flush()
    return approval


def test_the_requester_cannot_approve_their_own_request(client, users, pending_approval) -> None:
    response = client.post(
        f"/api/v1/approvals/{pending_approval.id}/decision",
        headers=_auth(users["ml_engineer"]),
        json={"approved": True, "comment": "looks good to me"},
    )
    assert response.status_code == 403
    assert response.json()["type"].endswith("/self-approval")


def test_a_second_person_can_approve(client, users, pending_approval, db_session) -> None:
    second = User(email=f"second-{uuid.uuid4().hex[:8]}@triad.test", display_name="second",
                  role="ml_engineer", password_hash=hash_password(PASSWORD), is_active=True)
    db_session.add(second)
    db_session.flush()

    response = client.post(
        f"/api/v1/approvals/{pending_approval.id}/decision",
        headers=_auth(second),
        json={"approved": True, "comment": "metrics and segment checks reviewed"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"


def test_a_decision_without_a_comment_is_refused(client, users, pending_approval) -> None:
    response = client.post(
        f"/api/v1/approvals/{pending_approval.id}/decision",
        headers=_auth(users["admin"]), json={"approved": True, "comment": ""},
    )
    assert response.status_code == 422


def test_deciding_twice_is_a_conflict(client, users, pending_approval, db_session) -> None:
    body = {"approved": False, "comment": "the segment gap is too wide"}
    first = client.post(f"/api/v1/approvals/{pending_approval.id}/decision",
                        headers=_auth(users["admin"]), json=body)
    assert first.status_code == 200
    again = client.post(f"/api/v1/approvals/{pending_approval.id}/decision",
                        headers=_auth(users["admin"]), json=body)
    assert again.status_code == 409


def test_a_business_analyst_may_not_decide_a_promotion(client, users, pending_approval) -> None:
    response = client.post(
        f"/api/v1/approvals/{pending_approval.id}/decision",
        headers=_auth(users["business_analyst"]),
        json={"approved": True, "comment": "seems fine"},
    )
    assert response.status_code == 403
    assert response.json()["type"].endswith("/role")


def test_pending_approvals_are_listed_for_the_run(client, users, pending_approval) -> None:
    listed = client.get("/api/v1/approvals?status=pending",
                        headers=_auth(users["ml_engineer"])).json()
    assert str(pending_approval.id) in {a["id"] for a in listed}


def test_a_run_detail_surfaces_its_pending_approval(client, users, pending_approval, db_session) -> None:
    from triad_core.models import PipelineRun

    run = db_session.get(PipelineRun, pending_approval.run_id)
    detail = client.get(f"/api/v1/pipelines/{run.run_key}",
                        headers=_auth(users["admin"])).json()
    assert detail["pending_approval"]["action_type"] == "model_promotion"


# --- predictions and the audit trail -------------------------------------------


def test_a_viewer_receives_aggregates_rather_than_customer_rows(client, users) -> None:
    response = client.get("/api/v1/predictions", headers=_auth(users["viewer"]))
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"rows", "by_risk_band", "mean_probability", "score_dates"}


def test_an_analyst_receives_rows(client, users) -> None:
    response = client.get("/api/v1/predictions", headers=_auth(users["business_analyst"]))
    assert response.status_code == 200 and isinstance(response.json(), list)


def test_an_export_is_audited_before_the_file_exists(client, users, db_session) -> None:
    from sqlalchemy import select

    from triad_core.models import AuditLog

    response = client.post("/api/v1/predictions/export",
                           headers=_auth(users["business_analyst"]))
    assert response.status_code == 202
    entry = db_session.execute(
        select(AuditLog)
        .where(AuditLog.action == "predictions.exported",
               AuditLog.actor_id == str(users["business_analyst"].id))
        .order_by(AuditLog.id.desc())
    ).scalars().first()
    assert entry is not None and entry.details["rows"] == response.json()["rows"]


def test_a_retried_export_is_the_same_export(client, users, db_session) -> None:
    """PRD 25.1: `Idempotency-Key` on exports - one file and one audit entry per key, and the
    same key with other filters refused, as run creation refuses it (DEC-53)."""
    from sqlalchemy import func, select

    from triad_core.models import AuditLog

    analyst = users["business_analyst"]

    def exported() -> int:
        return db_session.execute(
            select(func.count()).select_from(AuditLog)
            .where(AuditLog.action == "predictions.exported",
                   AuditLog.actor_id == str(analyst.id))).scalar_one()

    key = {"Idempotency-Key": f"export-{uuid.uuid4().hex}"}
    before = exported()
    first = client.post("/api/v1/predictions/export", headers={**_auth(analyst), **key})
    again = client.post("/api/v1/predictions/export", headers={**_auth(analyst), **key})
    assert first.status_code == again.status_code == 202
    assert first.json()["download"] == again.json()["download"]
    assert exported() == before + 1, "the retry wrote no second export and no second entry"

    other = client.post("/api/v1/predictions/export?risk_band=high",
                        headers={**_auth(analyst), **key})
    assert other.status_code == 409 and other.json()["type"].endswith("/idempotency-conflict")


def test_every_problem_carries_the_correlation_id_it_was_answered_with(client, users) -> None:
    """PRD 26.3: an error shows "a correlation ID for support" (DEC-53)."""
    headers = _auth(users["admin"])
    missing = client.get("/api/v1/pipelines/run-that-does-not-exist", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["correlation_id"] == missing.headers["X-Request-ID"]
    assert len(missing.headers["X-Request-ID"]) == 32

    traced = client.get("/api/v1/pipelines/run-that-does-not-exist",
                        headers={**headers, "X-Request-ID": "dashboard-trace-0001"})
    assert traced.json()["correlation_id"] == "dashboard-trace-0001", "a caller's id is kept"
    odd = client.get("/api/v1/pipelines/run-that-does-not-exist",
                     headers={**headers, "X-Request-ID": "short"})
    assert odd.headers["X-Request-ID"] != "short", "only a plain token is kept"


def test_an_unexpected_error_is_a_problem_the_dashboard_can_read(client, users,
                                                                 monkeypatch) -> None:
    """Left to Starlette it was a plain-text 500 outside the CORS layer: unreadable from the
    dashboard's origin, which then said it could not reach the server (DEC-53)."""
    from backend.app.api.v1 import pipelines

    def broken(session, run_ref):
        raise RuntimeError("an internal detail that must not leak")

    monkeypatch.setattr(pipelines, "_find_run", broken)
    response = client.get("/api/v1/pipelines/any",
                          headers={**_auth(users["admin"]), "Origin": "http://localhost:5173"})
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["type"].endswith("/internal")
    assert response.json()["correlation_id"] == response.headers["X-Request-ID"]
    assert "must not leak" not in response.text
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


@pytest.mark.parametrize("name", ["..passwd", "..%5C..%5Cetc%5Cpasswd", "a..b.csv"])
def test_an_export_name_cannot_walk_out_of_its_directory(client, users, name: str) -> None:
    """The handler's own guard refuses it - asserted by its answer, not by any 4xx.

    This used to request `..%2F..%2Fetc%2Fpasswd` and accept 400 *or 404*. Starlette decodes
    `%2F` before routing, so that path had four segments and matched no route: the router
    answered 404 before authentication or the handler ran, and the test passed with the guard
    deleted. These names stay in one path segment, so they reach `download_export`.
    """
    response = client.get(f"/api/v1/exports/{name}", headers=_auth(users["business_analyst"]))
    assert response.status_code == 400, response.text
    assert "not an export name this API issued" in response.json()["detail"]


def test_the_audit_chain_verifies_through_the_api(client, users) -> None:
    body = client.get("/api/v1/audit-logs/verify", headers=_auth(users["admin"])).json()
    assert body["ok"] is True and body["entries_checked"] > 0


def test_api_actions_appear_in_the_audit_trail(client, users, db_session) -> None:
    name = _pipeline()
    started = client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                          json={"pipeline": name}).json()
    entries = client.get(
        "/api/v1/audit-logs?action=run.requested", headers=_auth(users["admin"])
    ).json()
    assert any(e["details"].get("pipeline") == name for e in entries), (
        "the request this test made must be in the trail, not merely some request"
    )
    assert started["run_id"]


def test_dashboard_stats_are_computed_not_stored(client, users) -> None:
    before = client.get("/api/v1/stats", headers=_auth(users["admin"])).json()
    client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                json={"pipeline": _pipeline()})
    after = client.get("/api/v1/stats", headers=_auth(users["admin"])).json()
    assert after["runs_today"] == before["runs_today"] + 1


# --- the signing key -----------------------------------------------------------


def test_the_shipped_development_secret_is_refused_outside_dev(monkeypatch) -> None:
    """The kind of finding that otherwise becomes a permanent "known issue"."""
    from backend.app import security
    from triad_core.config import Settings, settings

    settings.cache_clear()
    monkeypatch.setenv("TRIAD_ENV", "lab")
    try:
        with pytest.raises(security.InsecureSecret, match="development value"):
            security.check_jwt_secret()
    finally:
        settings.cache_clear()
    assert Settings  # the import is what makes the cache_clear meaningful


def test_a_short_secret_is_refused_outside_dev(monkeypatch) -> None:
    from backend.app import security
    from triad_core.config import settings

    settings.cache_clear()
    monkeypatch.setenv("TRIAD_ENV", "prod")
    monkeypatch.setenv("TRIAD_JWT_SECRET", "short")
    try:
        with pytest.raises(security.InsecureSecret, match="at least 32"):
            security.check_jwt_secret()
    finally:
        settings.cache_clear()


def test_a_real_secret_passes_silently(monkeypatch) -> None:
    from backend.app import security
    from triad_core.config import settings

    settings.cache_clear()
    monkeypatch.setenv("TRIAD_ENV", "prod")
    monkeypatch.setenv("TRIAD_JWT_SECRET", "x" * 48)
    try:
        assert security.check_jwt_secret() is None
    finally:
        settings.cache_clear()


def test_dev_warns_rather_than_refusing(monkeypatch) -> None:
    from backend.app import security
    from triad_core.config import settings

    settings.cache_clear()
    monkeypatch.setenv("TRIAD_ENV", "dev")
    try:
        assert "not safe outside development" in (security.check_jwt_secret() or "")
    finally:
        settings.cache_clear()


# --- input resolution ----------------------------------------------------------


def test_a_dataset_name_is_resolved_to_its_latest_version(client, users, db_session) -> None:
    from sqlalchemy import select

    from triad_core.models import Dataset, DatasetVersion, PipelineRun

    name = f"tx-{uuid.uuid4().hex[:8]}"
    dataset = Dataset(name=name, entity_type="transactions", source_type="parquet")
    db_session.add(dataset)
    db_session.flush()
    for version_no in (1, 2):
        db_session.add(DatasetVersion(
            dataset_id=dataset.id, version_no=version_no,
            storage_uri=f"file:///data/triad/raw/{name}/v={version_no}",
            checksum_sha256=f"{version_no:064d}", size_bytes=1, row_count=10,
        ))
    db_session.flush()

    body = client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                       json={"pipeline": _pipeline(), "inputs": {"transactions": name}}).json()
    run = db_session.execute(
        select(PipelineRun).where(PipelineRun.run_key == body["run_id"])
    ).scalar_one()
    assert run.input_versions["transactions"].endswith("v=2"), "the newest version wins"


def test_an_unknown_dataset_name_is_refused_at_request_time(client, users) -> None:
    """Better than a worker failure ten minutes later with the same cause."""
    response = client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                           json={"pipeline": _pipeline(),
                                 "inputs": {"transactions": "no-such-dataset"}})
    assert response.status_code == 422
    assert "no-such-dataset" in response.json()["detail"]


def test_a_dataset_with_no_versions_is_refused(client, users, db_session) -> None:
    from triad_core.models import Dataset

    name = f"empty-{uuid.uuid4().hex[:8]}"
    db_session.add(Dataset(name=name, entity_type="transactions", source_type="parquet"))
    db_session.flush()

    response = client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                           json={"pipeline": _pipeline(), "inputs": {"transactions": name}})
    assert response.status_code == 422
    assert "no registered version" in response.json()["detail"]


def test_a_second_run_of_the_same_pipeline_is_refused_while_one_is_active(client, users) -> None:
    """They share their clean, feature and label folders, so the second would overwrite the
    first one's inputs mid-flight. Observed for real before this rule existed."""
    name = _pipeline()
    first = client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                        json={"pipeline": name})
    assert first.status_code == 202

    second = client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                         json={"pipeline": name})
    assert second.status_code == 409
    assert second.json()["type"].endswith("/pipeline-busy")
    assert first.json()["run_id"] in second.json()["detail"]


def test_a_different_pipeline_is_unaffected(client, users) -> None:
    client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                json={"pipeline": _pipeline()})
    other = client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                        json={"pipeline": _pipeline()})
    assert other.status_code == 202


def test_a_cancelled_run_frees_the_pipeline(client, users) -> None:
    name = _pipeline()
    first = client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                        json={"pipeline": name}).json()
    client.post(f"/api/v1/pipelines/{first['run_id']}/cancel",
                headers=_auth(users["data_engineer"]))
    again = client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                        json={"pipeline": name})
    assert again.status_code == 202


def test_a_run_waiting_for_approval_does_not_block_the_next_one(client, users, db_session) -> None:
    """A run can sit at the approval gate for days; blocking the pipeline that long would
    make the gate a denial of service."""
    from sqlalchemy import select

    from triad_core.models import AgentTask, PipelineRun

    name = _pipeline()
    first = client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                        json={"pipeline": name}).json()
    run = db_session.execute(
        select(PipelineRun).where(PipelineRun.run_key == first["run_id"])
    ).scalar_one()
    # At the gate the preparation task has *succeeded*: that is what leaves the run paused
    # with no live work. Leaving the task queued would describe a state that never happens.
    run.state = "APPROVAL_REQUIRED"
    for task in db_session.execute(
        select(AgentTask).where(AgentTask.run_id == run.id)
    ).scalars():
        task.status = "succeeded"
    db_session.flush()

    again = client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                        json={"pipeline": name})
    assert again.status_code == 202, again.json()


def test_an_abandoned_run_does_not_block_the_pipeline_for_ever(client, users, db_session) -> None:
    """A run whose state is not final but which has no live task is going nowhere.

    Reading the state column alone meant one abandoned row - a crashed process, or a test that
    committed a run and walked away - refused every future run of that pipeline until someone
    cancelled it by hand. That happened, through the real API.
    """
    from sqlalchemy import select

    from triad_core.models import AgentTask, PipelineRun

    name = _pipeline()
    first = client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                        json={"pipeline": name}).json()
    run = db_session.execute(
        select(PipelineRun).where(PipelineRun.run_key == first["run_id"])
    ).scalar_one()

    # The worker dies: the task never runs and the run keeps its non-final state.
    for task in db_session.execute(
        select(AgentTask).where(AgentTask.run_id == run.id)
    ).scalars():
        task.status = "failed"
    db_session.flush()
    assert run.state == "RECEIVED", "the state column still says the run is alive"

    again = client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                        json={"pipeline": name})
    assert again.status_code == 202, again.json()


def test_a_run_with_live_work_still_blocks_the_pipeline(client, users, db_session) -> None:
    """The relaxation must not lose the rule it relaxed."""
    name = _pipeline()
    client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                json={"pipeline": name})
    blocked = client.post("/api/v1/pipelines/run", headers=_auth(users["data_engineer"]),
                          json={"pipeline": name})
    assert blocked.status_code == 409
    assert "still queued" in blocked.json()["detail"]


def test_a_cursor_decodes_to_a_datetime_not_a_string() -> None:
    """The only paginated endpoint returned 500 on every second page.

    `decode_cursor` handed back the decoded string, which the query then compared against a
    `timestamp with time zone` column. Postgres has no `timestamp < varchar` operator. Nothing
    had ever requested a second page, so nothing found out — the frontend typed `next_cursor`
    and never read it.
    """
    import datetime as dt
    import uuid as uuid_mod

    from backend.app.pagination import decode_cursor, encode_cursor

    moment = dt.datetime(2026, 9, 21, 19, 18, 29, 260008, tzinfo=dt.UTC)
    row_id = uuid_mod.uuid4()
    decoded = decode_cursor(encode_cursor(moment, row_id))
    assert decoded is not None
    when, tiebreak = decoded
    assert isinstance(when, dt.datetime)
    assert when == moment
    # The id travels with it, because the timestamp alone is not unique: a row written in SQL
    # takes the transaction's `now()`, and two rows can share a clock tick.
    assert tiebreak == row_id


def test_a_cursor_that_is_not_ours_is_a_400_not_a_500() -> None:
    """Decodable base64 that is not a timestamp is the client's mistake, not a server fault."""
    import base64

    from backend.app import problems
    from backend.app.pagination import decode_cursor

    junk = base64.urlsafe_b64encode(b"not-a-timestamp").decode("ascii")
    with pytest.raises(problems.Problem) as raised:
        decode_cursor(junk)
    assert raised.value.status == 400

    # A cursor of the old single-value shape no longer parses, and must still be a 400.
    legacy = base64.urlsafe_b64encode(b"2026-09-21 19:18:29.260008+00:00").decode("ascii")
    with pytest.raises(problems.Problem) as raised:
        decode_cursor(legacy)
    assert raised.value.status == 400

    # A well-formed timestamp with a tie-break that is not a uuid is equally not ours.
    bad_tiebreak = base64.urlsafe_b64encode(
        b"2026-09-21 19:18:29.260008+00:00|not-a-uuid"
    ).decode("ascii")
    with pytest.raises(problems.Problem) as raised:
        decode_cursor(bad_tiebreak)
    assert raised.value.status == 400

    with pytest.raises(problems.Problem) as raised:
        decode_cursor("!!! not base64 !!!")
    assert raised.value.status == 400


def test_no_cursor_means_start_at_the_beginning() -> None:
    from backend.app.pagination import decode_cursor

    assert decode_cursor(None) is None
    assert decode_cursor("") is None


# --- settings ------------------------------------------------------------------


def test_the_settings_page_is_admin_only(client, users) -> None:
    for role in ("viewer", "business_analyst", "ml_engineer", "data_engineer"):
        assert client.get("/api/v1/settings", headers=_auth(users[role])).status_code == 403
    assert client.get("/api/v1/settings", headers=_auth(users["admin"])).status_code == 200


def test_settings_never_serves_a_credential(client, users) -> None:
    """The page shows connection targets, and a target is not a DSN.

    Asserted against the whole payload rather than the connection list: a password that
    reached the browser through some other field would be just as leaked.
    """
    body = client.get("/api/v1/settings", headers=_auth(users["admin"])).json()
    blob = json.dumps(body)
    assert "password_hash" not in blob
    assert settings().db_url not in blob
    postgres = next(c for c in body["connections"] if c["name"] == "PostgreSQL")
    assert postgres["target"].startswith("postgresql")
    assert "***@" in postgres["target"]


@pytest.mark.parametrize(
    ("dsn", "expected"),
    [
        ("postgresql+psycopg://triad:pw@postgres:5432/triad", "postgresql+psycopg://***@postgres:5432/triad"),
        # A password containing the separators a hand-rolled split would trip over.
        ("postgresql://u:p@ss:word@host:1/db", "postgresql://***@host:1/db"),
        ("http://mlflow:5000", "http://mlflow:5000"),
        ("local[2]", "local[2]"),
    ],
)
def test_a_dsn_is_redacted_without_losing_its_target(dsn: str, expected: str) -> None:
    assert redact_dsn(dsn) == expected


def test_both_policy_files_are_read_the_way_their_own_engine_reads_them(client, users) -> None:
    """Routing is not a governance policy, and judging it as one reported a false alarm.

    `PolicyEngine` requires `policy_version` and an `actions` map; routing.yaml has
    `routing_version` and `rules`. The first version of this endpoint ran the governance
    validator over both and told the administrator that the routing policy had been refused -
    on a page whose whole job is saying whether the policy loaded.
    """
    body = client.get("/api/v1/settings", headers=_auth(users["admin"])).json()
    by_name = {p["name"]: p for p in body["policies"]}

    assert by_name["governance"]["valid"] is True
    assert by_name["governance"]["action_count"] > 0
    assert by_name["routing"]["valid"] is True
    assert by_name["routing"]["version"] != "invalid"
    assert by_name["routing"]["action_count"] > 0
    for policy in by_name.values():
        assert policy["sha256"] and len(policy["sha256"]) == 64
        assert policy["text"].strip()


def test_paging_does_not_skip_runs_that_share_a_timestamp(client, users, db_session) -> None:
    """The cursor seeks on (started_at, id), because started_at alone is not unique.

    `started_at` used to come from PostgreSQL's `now()`, the transaction timestamp, so every
    row one transaction inserted carried the identical value. With the cursor encoding only the
    timestamp and the query filtering `started_at < cursor`, every row tied with the previous
    page's last row was dropped - and a page missing rows looks exactly like a page. ORM rows
    now carry their own time (DEC-52), but a row written in SQL still takes the server default,
    and two rows can share a clock tick, so the tie is made here rather than left to chance.

    Six runs share one timestamp, then are walked two at a time. All six must appear once.
    """
    from triad_core.ids import utcnow
    from triad_core.models import PipelineRun

    made, tie = [], utcnow()
    for i in range(6):
        run = PipelineRun(
            run_key=f"run-tie-{i:03d}", pipeline_name="churn",
            state="SUCCEEDED", trigger_type="manual", mode="full", started_at=tie,
        )
        db_session.add(run)
        made.append(run)
    db_session.flush()

    stamps = {r.started_at for r in made}
    assert len(stamps) == 1, f"this test needs the tie it is about, got {stamps}"

    headers = _auth(users["admin"])
    seen: list[str] = []
    cursor = None
    for _ in range(12):   # a bound, so a broken cursor loops finitely rather than forever
        url = "/api/v1/pipelines?limit=2" + (f"&cursor={cursor}" if cursor else "")
        body = client.get(url, headers=headers).json()
        seen.extend(item["run_id"] for item in body["items"])
        cursor = body["next_cursor"]
        if not cursor:
            break

    tied = [key for key in seen if key.startswith("run-tie-")]
    assert sorted(tied) == [f"run-tie-{i:03d}" for i in range(6)], (
        f"paging lost or repeated a tied row: {tied}"
    )
    assert len(seen) == len(set(seen)), f"paging repeated a row: {seen}"


def test_an_upload_cannot_write_outside_its_landing_folder(
    client, users, db_session, tmp_path, monkeypatch
) -> None:
    """ST-04, through the endpoint itself.

    The first tests for this re-implemented the endpoint's basename expression and asserted on
    the copy, so reverting the fix in datasets.py left all of them green. This one posts the
    multipart upload a client would, with the filename that escaped before the fix, and looks
    at where the bytes went.
    """
    import triad_core.storage as storage_module
    from dataeng.ingest import DatasetRegistry

    store = storage_module.Storage(tmp_path / "data")
    monkeypatch.setattr(storage_module, "_default", store)
    DatasetRegistry(db_session, store).register_source(
        "upload_probe", entity_type="customer", source_type="csv"
    )
    db_session.flush()

    # landing is <root>/uploads/upload_probe, so three levels up is tmp_path itself.
    escaped = tmp_path / "escaped.csv"
    response = client.post(
        "/api/v1/datasets/upload_probe/upload",
        headers=_auth(users["data_engineer"]),
        files={"file": ("../../../escaped.csv", b"customer_id,country\nC1,IN\n", "text/csv")},
    )

    assert not escaped.exists(), "the upload was written outside the data root"
    assert response.status_code in (200, 201, 422), response.text
    landing = store.root / "uploads" / "upload_probe"
    if response.status_code in (200, 201):
        assert (landing / "escaped.csv").is_file(), "kept, under its basename, in the landing folder"


def test_executions_serve_their_spark_stage_metrics(client, users, db_session) -> None:
    """FR-036: the per-stage metrics a job recorded are readable per execution; an execution
    that ran no Spark job has none rather than zeros."""
    from agents.graph import queue as q
    from triad_core.models import Execution

    run = q.create_run(db_session, pipeline_name=_pipeline(), trigger_type="manual",
                       triggered_by=str(users["data_engineer"].id))
    spark = {"totals": {"stages": 2, "tasks": 6, "shuffle_read_bytes": 2048},
             "stages": [{"stage_id": 0, "num_tasks": 4}, {"stage_id": 1, "num_tasks": 2}]}
    db_session.add_all([
        Execution(run_id=run.id, stage="features", engine="scala_spark", status="succeeded",
                  exit_code=0, duration_ms=1200, metrics_json={"output_rows": "10", "spark": spark}),
        Execution(run_id=run.id, stage="profile", engine="spark", status="succeeded"),
    ])
    db_session.flush()

    response = client.get(f"/api/v1/pipelines/{run.run_key}/executions",
                          headers=_auth(users["data_engineer"]))
    assert response.status_code == 200, response.text
    by_stage = {e["stage"]: e for e in response.json()}
    assert by_stage["features"]["spark"] == spark
    assert by_stage["profile"]["spark"] is None
    assert client.get(f"/api/v1/pipelines/{run.run_key}/executions",
                      headers=_auth(users["viewer"])).status_code == 403


def _model_with_history(db_session):
    """A model whose v1 and v2 were champions in turn, v3 is champion now, v4 was never promoted."""
    from triad_core.models import Model, ModelVersion

    name = f"m-{uuid.uuid4().hex[:6]}"
    model = Model(name=name, mlflow_registered_name=name, target_definition="t")
    db_session.add(model)
    db_session.flush()
    now = dt.datetime.now(dt.UTC)
    rows = {}
    for number, status, promoted in (("1", "archived", now - dt.timedelta(days=3)),
                                     ("2", "archived", now - dt.timedelta(days=2)),
                                     ("3", "champion", now - dt.timedelta(days=1)),
                                     ("4", "challenger", None)):
        rows[number] = ModelVersion(model_id=model.id, mlflow_version=number, algorithm="logreg",
                                    params_json={}, governance_status=status, promoted_at=promoted)
        db_session.add(rows[number])
    db_session.flush()
    model.champion_version_id = rows["3"].id
    db_session.flush()
    return model, rows


@pytest.mark.parametrize("version", ["4", "1"])
def test_a_person_rolls_back_only_to_the_previous_champion(client, users, db_session,
                                                           version) -> None:
    """PRD 30: "previous recorded champion only". v4 was never approved; v1 is not the previous."""
    model, _ = _model_with_history(db_session)
    response = client.post(f"/api/v1/models/{model.name}/rollback",
                           headers=_auth(users["ml_engineer"]),
                           json={"previous_version": version, "reason": "smoke test regression"})
    assert response.status_code == 409
    assert response.json()["type"].endswith("/not-previous-champion")
    assert "v2" in response.json()["detail"]


def test_rolling_back_to_the_previous_champion_moves_it_and_raises_an_alert(
    client, users, db_session, monkeypatch
) -> None:
    from sqlalchemy import select

    from triad_core.models import Alert

    monkeypatch.setattr("python_ml.triad_ml.registry.ModelRegistry.rollback",
                        lambda self, name, previous_version, reason: {"to": previous_version})
    model, rows = _model_with_history(db_session)
    response = client.post(f"/api/v1/models/{model.name}/rollback",
                           headers=_auth(users["ml_engineer"]),
                           json={"previous_version": "2", "reason": "smoke test regression"})
    assert response.status_code == 200, response.text
    assert (rows["2"].governance_status, rows["3"].governance_status) == ("champion", "archived")
    # Re-stamped: left at its first promotion, a later rollback's "previous champion" would be
    # computed from a stale order and name v3 instead of v2.
    assert rows["2"].promoted_at > rows["3"].promoted_at
    alert = db_session.execute(
        select(Alert).where(Alert.model_id == model.id, Alert.alert_type == "model_rolled_back")
    ).scalar_one()
    assert "from v3" in alert.message and alert.evidence["to_version"] == "2"


def test_a_renamed_column_upload_is_proposed_then_applied_once_approved(
    client, users, db_session, tmp_path, monkeypatch
) -> None:
    """ATC-06 through the endpoints. A refused upload used to come back as 200 and the
    dataset summary, which read as success; nothing proposed a mapping and nothing applied one."""
    import triad_core.storage as storage_module
    from dataeng.ingest import DatasetRegistry

    store = storage_module.Storage(tmp_path / "data")
    monkeypatch.setattr(storage_module, "_default", store)
    registry = DatasetRegistry(db_session, store)
    registry.register_source(
        "mapping_probe", entity_type="transactions", source_type="csv",
        contract_yaml=(settings().repo_root / "pipelines/contracts/transactions.yaml")
        .read_text(encoding="utf-8"),
    )
    db_session.flush()

    body = b"order_id,line_id,cust_id,order_date,quantity,price\nO1,1,C1,2026-01-01,2,9.99\n"
    response = client.post(
        "/api/v1/datasets/mapping_probe/upload", headers=_auth(users["data_engineer"]),
        files={"file": ("day1.csv", body, "text/csv")},
    )
    assert response.status_code == 422, response.text
    problem = response.json()
    assert problem["proposed_mapping"] == {"cust_id": "customer_id"}
    assert registry.latest_version(registry.get("mapping_probe")) is None

    decided = client.post(
        f"/api/v1/approvals/{problem['approval_id']}/decision",
        headers=_auth(users["data_engineer"]),
        json={"approved": True, "comment": "cust_id is the customer key"},
    )
    assert decided.status_code == 200, decided.text
    version = registry.latest_version(registry.get("mapping_probe"))
    assert version is not None, "the approval registered nothing"
    assert "customer_id" in version.schema_json["columns"]
    assert version.schema_json["mapped_from"]["mapping"] == {"cust_id": "customer_id"}


def test_an_experiments_history_is_served_and_only_for_the_ten(client, users) -> None:
    """`exp_id` names a folder, so anything but EXP-1..EXP-10 is a 404, not a path."""
    headers = _auth(users["viewer"])
    history = client.get("/api/v1/experiments/EXP-7/runs", headers=headers)
    assert history.status_code == 200 and isinstance(history.json(), list)
    for bad in ("EXP-11", "EXP-0", "..%2F..%2Fetc", "exp-7"):
        assert client.get(f"/api/v1/experiments/{bad}/runs", headers=headers).status_code == 404
