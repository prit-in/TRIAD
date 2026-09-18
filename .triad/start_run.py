"""Start a run through the API the way a client would, then report what came back."""
import json, os, urllib.request, urllib.error, uuid

BASE = os.environ.get("TRIAD_API", "http://api:8000")

def call(path, token=None, method="GET", body=None, headers=None):
    req = urllib.request.Request(BASE + path, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null"), r.headers
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null"), e.headers

_, tok, _ = call("/api/v1/auth/token", method="POST",
                 body={"email": "engineer@triad.test", "password": "triad-local-demo-password"})
t = tok["access_token"]

status, body, headers = call(
    "/api/v1/pipelines/run", t, method="POST",
    headers={"Idempotency-Key": str(uuid.uuid4())},
    body={"pipeline": "churn_v1", "mode": "full",
          "inputs": {"transactions": "transactions", "products": "products"},
          "budget": {"max_llm_calls": 10, "max_wall_clock_minutes": 60}},
)
print("POST /pipelines/run ->", status)
print("Location:", headers.get("Location"))
print(json.dumps(body, indent=2))

s, detail, _ = call(f"/api/v1/pipelines/{body['run_id']}", t)
print("\nimmediately after:", detail["state"],
      "stages:", [(x["name"], x["status"]) for x in detail["stages"]])
