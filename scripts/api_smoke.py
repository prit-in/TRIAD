"""Check a running TRIAD stack over HTTP, the way a client would.

    python scripts/api_smoke.py                       # read-only checks
    python scripts/api_smoke.py --start-run           # also queue a real run

Every assertion here is about the deployment rather than the code: that the API is up, that
the database and policy are loaded, that a token works, that role separation is enforced on
the server, and that the audit chain verifies. It is the check to run after a deploy, and it
is deliberately read-only unless asked otherwise.

Exit code 0 means every check passed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("TRIAD_API_BASE", "http://api:8000")
PASSWORD = os.environ.get("TRIAD_SEED_PASSWORD", "")

failures: list[str] = []


def call(path, token=None, method="GET", body=None, headers=None):
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
        with urllib.request.urlopen(request, data, timeout=30) as response:
            return response.status, json.loads(response.read() or b"null"), response.headers
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null"), exc.headers


def wait_for_health(seconds: int) -> bool:
    """A deploy check runs right after a restart, so it waits rather than racing it."""
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            status, _, _ = call("/health")
            if status == 200:
                return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(1.0)
    return False


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(name)
    return ok


def token_for(email: str) -> str | None:
    status, body, _ = call("/api/v1/auth/token", method="POST",
                           body={"email": email, "password": PASSWORD})
    return body.get("access_token") if status == 200 else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-check a running TRIAD stack.")
    parser.add_argument("--start-run", action="store_true",
                        help="also queue a real pipeline run (needs a registered dataset)")
    parser.add_argument("--dataset", default="transactions",
                        help="dataset name to run on with --start-run")
    parser.add_argument("--wait-seconds", type=int, default=60,
                        help="how long to wait for the API to become reachable")
    args = parser.parse_args()

    if not PASSWORD:
        print("TRIAD_SEED_PASSWORD is not set; run scripts/seed_demo.py first.")
        return 2

    print(f"checking {BASE}")
    if not wait_for_health(args.wait_seconds):
        print(f"  FAIL  the API did not become reachable within {args.wait_seconds}s")
        return 1

    status, health, _ = call("/health")
    check("health responds", status == 200, str(health))
    check("database reachable", bool(health.get("database")))
    check("policy loaded", bool(health.get("policy_loaded")))

    admin = token_for("admin@triad.test")
    if not check("admin can log in", admin is not None):
        return 1

    status, _, _ = call("/api/v1/auth/token", method="POST",
                        body={"email": "admin@triad.test", "password": "wrong"})
    check("a wrong password is refused", status == 401)

    status, _, _ = call("/api/v1/pipelines")
    check("an anonymous request is refused", status == 401)

    analyst = token_for("analyst@triad.test")
    if analyst:
        status, _, _ = call("/api/v1/audit-logs", analyst)
        check("a non-admin cannot read the audit trail", status == 403)

    viewer = token_for("viewer@triad.test")
    if viewer:
        status, body, _ = call("/api/v1/predictions", viewer)
        check("a viewer receives aggregates, not rows",
              status == 200 and isinstance(body, dict) and "by_risk_band" in body)

    status, runs, _ = call("/api/v1/pipelines?limit=5", admin)
    check("runs are listed", status == 200, f"{len(runs.get('items', []))} shown")

    status, chain, _ = call("/api/v1/audit-logs/verify", admin)
    check("the audit chain verifies",
          status == 200 and chain.get("ok") is True,
          f"{chain.get('entries_checked')} entries across "
          f"{chain.get('chains_checked')} chains")

    status, stats, _ = call("/api/v1/stats", admin)
    check("dashboard statistics are computed", status == 200, json.dumps(stats))

    if args.start_run:
        engineer = token_for("engineer@triad.test")
        status, body, headers = call(
            "/api/v1/pipelines/run", engineer, method="POST",
            headers={"Idempotency-Key": str(uuid.uuid4())},
            body={"pipeline": "churn_v1", "inputs": {"transactions": args.dataset}},
        )
        queued = check("a run can be queued", status in (202, 409), json.dumps(body)[:160])
        if queued and status == 202:
            check("the response carries a Location",
                  bool(headers.get("Location")), headers.get("Location") or "")
            print(f"     queued {body['run_id']}; the worker picks it up from the queue")
        elif queued:
            print("     a run of this pipeline is already active, which is the rule working")

    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("every check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
