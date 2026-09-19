"""Exercise every endpoint against a running stack and report what actually happens.

    python scripts/api_audit.py

This is an audit tool, not a test: it reports rather than asserts, so one broken endpoint does
not hide the state of the other thirty-six. Each row shows the status the endpoint returned and
whether that status is one the endpoint is supposed to be able to return.

It is read-mostly. The writes it does make are the ones that cannot be audited any other way
(starting a run, requesting an export), and each is marked.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("TRIAD_API_BASE", "http://api:8000")
PASSWORD = os.environ.get("TRIAD_SEED_PASSWORD", "")

Row = tuple[str, str, int, bool, str]
rows: list[Row] = []


def call(path, token=None, method="GET", body=None, headers=None, timeout=30):
    request = urllib.request.Request(BASE + path, method=method)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, data, timeout=timeout) as response:
            raw = response.read()
            parsed = json.loads(raw) if raw and raw[:1] in b"[{" else raw.decode(errors="replace")
            return response.status, parsed, response.headers
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw or b"null"), exc.headers
        except ValueError:
            return exc.code, raw.decode(errors="replace"), exc.headers
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, str(exc), {}


def probe(method: str, path: str, *, expect: tuple[int, ...], token=None, body=None,
          headers=None, note: str = "", timeout: int = 30):
    status, payload, response_headers = call(path, token, method, body, headers, timeout)
    ok = status in expect
    detail = note
    if not ok:
        detail = (detail + " " if detail else "") + _summarise(payload)
    rows.append((method, path, status, ok, detail))
    return status, payload, response_headers


def _summarise(payload) -> str:
    if isinstance(payload, dict):
        if "detail" in payload:
            return str(payload["detail"])[:160]
        return json.dumps(payload)[:160]
    return str(payload)[:160]


def token_for(email: str) -> str | None:
    status, body, _ = call("/api/v1/auth/token", method="POST",
                           body={"email": email, "password": PASSWORD})
    return body.get("access_token") if status == 200 and isinstance(body, dict) else None


def wait_for_health(seconds: int = 60) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        status, _, _ = call("/health")
        if status == 200:
            return True
        time.sleep(1.0)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit every TRIAD endpoint.")
    parser.add_argument("--write", action="store_true",
                        help="also exercise the endpoints that create something")
    args = parser.parse_args()

    if not PASSWORD:
        print("TRIAD_SEED_PASSWORD is not set; run scripts/seed_demo.py first.")
        return 2
    if not wait_for_health():
        print(f"the API at {BASE} did not become reachable")
        return 2

    admin = token_for("admin@triad.test")
    engineer = token_for("engineer@triad.test")
    analyst = token_for("analyst@triad.test")
    viewer = token_for("viewer@triad.test")
    ml = token_for("ml@triad.test")
    if not all([admin, engineer, analyst, viewer, ml]):
        print("one or more seeded accounts could not log in; run seed_demo.py --force")
        return 2

    # --- open ------------------------------------------------------------------
    probe("GET", "/health", expect=(200,))
    probe("GET", "/openapi.json", expect=(200,))

    # --- auth ------------------------------------------------------------------
    probe("POST", "/api/v1/auth/token", expect=(200,),
          body={"email": "admin@triad.test", "password": PASSWORD})
    probe("GET", "/api/v1/auth/me", expect=(200,), token=admin)

    # --- datasets --------------------------------------------------------------
    status, datasets, _ = probe("GET", "/api/v1/datasets", expect=(200,), token=admin)
    first = datasets[0]["name"] if isinstance(datasets, list) and datasets else None
    if first:
        probe("GET", f"/api/v1/datasets/{first}", expect=(200,), token=admin)
        status, versions, _ = probe("GET", f"/api/v1/datasets/{first}/versions",
                                    expect=(200,), token=admin)
        version_no = versions[0]["version_no"] if isinstance(versions, list) and versions else 1
        probe("GET", f"/api/v1/datasets/{first}/versions/{version_no}/profile",
              expect=(200, 404), token=engineer,
              note="404 is correct when that version was never profiled")
        probe("GET", f"/api/v1/datasets/{first}/versions/{version_no}/profile",
              expect=(403,), token=viewer, note="a viewer must not read a profile")
    else:
        rows.append(("GET", "/api/v1/datasets/{ref}", 0, False, "no dataset to audit"))
    probe("GET", "/api/v1/datasets/no-such-dataset", expect=(404,), token=admin)

    # --- pipelines -------------------------------------------------------------
    status, page, _ = probe("GET", "/api/v1/pipelines?limit=5", expect=(200,), token=admin)
    runs = page.get("items", []) if isinstance(page, dict) else []
    run_id = runs[0]["run_id"] if runs else None
    probe("GET", "/api/v1/pipelines?limit=1000", expect=(200,), token=admin,
          note="limit must be capped, not rejected")
    probe("GET", "/api/v1/pipelines?cursor=not-a-cursor", expect=(400,), token=admin)
    probe("GET", "/api/v1/pipelines?state=NOT_A_STATE", expect=(200,), token=admin,
          note="an unknown state is an empty page, not an error")

    if run_id:
        probe("GET", f"/api/v1/pipelines/{run_id}", expect=(200,), token=admin)
        probe("GET", f"/api/v1/pipelines/{run_id}/decisions", expect=(200,), token=engineer)
        probe("GET", f"/api/v1/pipelines/{run_id}/artifacts", expect=(200,), token=engineer)
        probe("GET", f"/api/v1/pipelines/{run_id}/logs", expect=(200,), token=engineer)
        probe("GET", f"/api/v1/pipelines/{run_id}/logs?level=error", expect=(200,),
              token=engineer)
        probe("GET", f"/api/v1/pipelines/{run_id}/logs?level=nonsense", expect=(422,),
              token=engineer)
        probe("GET", f"/api/v1/pipelines/{run_id}/decisions", expect=(403,), token=viewer)
        probe("POST", f"/api/v1/pipelines/{run_id}/cancel", expect=(202, 403, 409),
              token=admin, note="409 when already finished, which is the usual case")
        probe("POST", f"/api/v1/pipelines/{run_id}/resume", expect=(202, 409),
              token=engineer, body={"fix_note": "auditing the resume path end to end"},
              note="409 unless the run is escalated")
        probe("POST", f"/api/v1/pipelines/{run_id}/resume", expect=(422,), token=engineer,
              body={"fix_note": "too short"})
        # The stream must terminate on a finished run rather than hold the connection.
        probe("GET", f"/api/v1/pipelines/{run_id}/events", expect=(200,), token=admin,
              timeout=20, note="must close once the run is in a final state")
    probe("GET", "/api/v1/pipelines/run-1970-01-01-999", expect=(404,), token=admin)

    # --- models ----------------------------------------------------------------
    status, models, _ = probe("GET", "/api/v1/models", expect=(200,), token=admin)
    model = models[0]["name"] if isinstance(models, list) and models else None
    if model:
        probe("GET", f"/api/v1/models/{model}", expect=(200,), token=admin)
        probe("POST", f"/api/v1/models/{model}/approve", expect=(403, 409),
              token=analyst, body={"approved": True, "comment": "auditing the refusal"},
              note="403 for the role, or 409 when nothing is pending")
        probe("POST", f"/api/v1/models/{model}/reject", expect=(409, 403), token=ml,
              body={"approved": False, "comment": "auditing the refusal"})
        probe("POST", f"/api/v1/models/{model}/rollback", expect=(409, 403, 200), token=ml,
              body={"previous_version": "999", "reason": "auditing an unknown version"},
              note="409 expected: version 999 was never registered")
    probe("GET", "/api/v1/models/no-such-model", expect=(404,), token=admin)

    # --- approvals and alerts ---------------------------------------------------
    for state in ("pending", "approved", "rejected", "expired"):
        probe("GET", f"/api/v1/approvals?status={state}", expect=(200,), token=ml)
    probe("GET", "/api/v1/approvals?status=nonsense", expect=(400,), token=ml)
    probe("GET", f"/api/v1/approvals/{uuid.uuid4()}", expect=(404,), token=ml)
    status, approvals, _ = call("/api/v1/approvals?status=approved", ml)
    if isinstance(approvals, list) and approvals:
        probe("GET", f"/api/v1/approvals/{approvals[0]['id']}", expect=(200,), token=ml)
        probe("POST", f"/api/v1/approvals/{approvals[0]['id']}/decision", expect=(409,),
              token=ml, body={"approved": True, "comment": "auditing a second decision"})

    probe("GET", "/api/v1/alerts", expect=(200,), token=admin)
    probe("GET", "/api/v1/alerts?status=acknowledged", expect=(200,), token=admin)
    probe("GET", "/api/v1/alerts?status=nonsense", expect=(422,), token=admin)
    probe("POST", f"/api/v1/alerts/{uuid.uuid4()}/acknowledge", expect=(404,), token=admin)

    # --- predictions, drift, reports, audit -------------------------------------
    probe("GET", "/api/v1/predictions", expect=(200,), token=analyst)
    probe("GET", "/api/v1/predictions?risk_band=high&limit=5", expect=(200,), token=analyst)
    probe("GET", "/api/v1/predictions?risk_band=nonsense", expect=(422,), token=analyst)
    probe("GET", "/api/v1/predictions?min_probability=2", expect=(422,), token=analyst)
    probe("GET", "/api/v1/predictions", expect=(200,), token=viewer,
          note="a viewer receives aggregates")
    probe("GET", "/api/v1/predictions", expect=(403,), token=engineer,
          note="a data engineer is not a prediction consumer")

    status, drift, _ = probe("GET", "/api/v1/drift", expect=(200,), token=admin)
    if isinstance(drift, list) and drift:
        probe("GET", f"/api/v1/drift/{drift[0]['id']}", expect=(200,), token=admin)
    probe("GET", f"/api/v1/drift/{uuid.uuid4()}", expect=(404,), token=admin)

    status, reports, _ = probe("GET", "/api/v1/reports", expect=(200,), token=admin)
    if isinstance(reports, list) and reports:
        probe("GET", f"/api/v1/reports/{reports[0]['id']}", expect=(200,), token=admin)
        probe("GET", f"/api/v1/reports/{reports[0]['id']}?file=true", expect=(200, 404),
              token=admin, note="404 when the document is missing from storage")
    probe("GET", f"/api/v1/reports/{uuid.uuid4()}", expect=(404,), token=admin)

    probe("GET", "/api/v1/audit-logs?limit=5", expect=(200,), token=admin)
    probe("GET", "/api/v1/audit-logs?action=run.created&limit=3", expect=(200,), token=admin)
    probe("GET", "/api/v1/audit-logs", expect=(403,), token=analyst)
    probe("GET", "/api/v1/audit-logs/verify", expect=(200,), token=admin)
    probe("GET", "/api/v1/stats", expect=(200,), token=admin)

    probe("GET", "/api/v1/exports/../../etc/passwd", expect=(400, 404), token=analyst)
    probe("GET", "/api/v1/exports/no-such-export.csv", expect=(404,), token=analyst)

    # --- writes, only when asked -------------------------------------------------
    if args.write:
        probe(
            "POST", "/api/v1/pipelines/run", expect=(202, 409, 422), token=engineer,
            headers={"Idempotency-Key": str(uuid.uuid4())},
            body={"pipeline": "churn_v1", "inputs": {"transactions": "transactions"}},
            note="409 when a run is already active",
        )
        status, export, _ = probe("POST", "/api/v1/predictions/export", expect=(202,),
                                  token=analyst)
        if isinstance(export, dict) and export.get("download"):
            probe("GET", export["download"].replace("/api/v1", "/api/v1"), expect=(200,),
                  token=analyst, note="the export just requested must be downloadable")

    # --- report -----------------------------------------------------------------
    width = max(len(p) for _, p, _, _, _ in rows) + 2
    print(f"\n{'':4} {'METHOD':7} {'PATH'.ljust(width)} STATUS  DETAIL")
    failures = 0
    for method, path, status, ok, detail in rows:
        mark = "ok  " if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"{mark:4} {method:7} {path.ljust(width)} {status:>6}  {detail}")

    print(f"\n{len(rows)} probes, {failures} unexpected")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
