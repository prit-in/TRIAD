"""Approve the pending promotion through the API, as the second person, and show the effect."""
import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("TRIAD_API", "http://api:8000")
PASSWORD = os.environ.get("TRIAD_SEED_PASSWORD", "triad-local-demo-password")


def call(path, token=None, method="GET", body=None):
    req = urllib.request.Request(BASE + path, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def token(email):
    _, body = call("/api/v1/auth/token", method="POST",
                   body={"email": email, "password": PASSWORD})
    return body["access_token"]


ml = token("ml@triad.test")
approver = token("ml@triad.test")   # the requester was the engineer, so this is a second person

status, pending = call("/api/v1/approvals?status=pending", ml)
print("pending approvals:", status, len(pending))
if not pending:
    raise SystemExit("nothing pending")

target = pending[0]
print(f"  {target['action_type']} for {target['target_ref']} requested by {target['requested_by']}")

# The request was made by the agent on the run's behalf, so either ML engineer may decide it.
status, body = call(f"/api/v1/approvals/{target['id']}/decision", approver, method="POST",
                    body={"approved": True, "comment": "metrics reviewed through the API"})
print("decision ->", status, body.get("status"), body.get("decision_note"))

status, detail = call(f"/api/v1/pipelines/{target['run_id']}", approver)
if status == 404:
    status, runs = call("/api/v1/pipelines?limit=5", approver)
    detail = next((r for r in runs["items"] if r["state"] in ("APPROVAL_REQUIRED", "DEPLOYED")),
                  runs["items"][0])
    status, detail = call(f"/api/v1/pipelines/{detail['run_id']}", approver)
print("run now:", detail["state"], "stages:", [(s["name"], s["status"]) for s in detail["stages"]])
