"""Hit the running API over HTTP and print what it returns for the real demo run."""
import json, os, urllib.request, urllib.error

BASE = os.environ.get("TRIAD_API", "http://api:8000")

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

print("health          ", call("/health")[1])

status, tok = call("/api/v1/auth/token", method="POST",
                   body={"email": "admin@triad.test", "password": "triad-local-demo-password"})
print("login           ", status, tok.get("role"))
t = tok["access_token"]

s, runs = call("/api/v1/pipelines?limit=3", t)
print(f"runs            {s} showing {len(runs['items'])} of the newest")
for r in runs["items"]:
    print(f"   {r['run_id']:<22} {r['state']:<18} {r['pipeline']}")

newest = runs["items"][0]["run_id"]
s, detail = call(f"/api/v1/pipelines/{newest}", t)
print(f"run detail      {s} state={detail['state']} stages={len(detail['stages'])}")

s, models = call("/api/v1/models", t)
print(f"models          {s}")
for m in models:
    champ = m["champion"]["mlflow_version"] if m["champion"] else None
    chall = m["challenger"]["mlflow_version"] if m["challenger"] else None
    print(f"   {m['name']}: champion=v{champ} challenger=v{chall}")
    if m["champion"]:
        print(f"   champion metrics: {m['champion']['metrics']}")

s, preds = call("/api/v1/predictions?limit=3", t)
print(f"predictions     {s} first rows:")
for p in preds[:3]:
    print(f"   {p['customer_ref'][:16]}… {p['churn_probability']:.4f} {p['risk_band']}")

s, drift = call("/api/v1/drift?limit=2", t)
print(f"drift           {s}")
for d in drift:
    print(f"   max_psi={d['max_psi']} flagged={d['flagged_features']} score_flagged={d['score_flagged']}")

s, reports = call("/api/v1/reports?limit=2", t)
print(f"reports         {s}")
for r in reports:
    print(f"   {r['report_type']} {r['status']} claim={r['claim_check_status']}")

s, alerts = call("/api/v1/alerts?limit=3", t)
print(f"alerts          {s} {[a['alert_type'] for a in alerts]}")

s, stats = call("/api/v1/stats", t)
print("stats           ", stats)

s, chain = call("/api/v1/audit-logs/verify", t)
print("audit chain     ", s, chain)

s, viewer_tok = call("/api/v1/auth/token", method="POST",
                     body={"email": "viewer@triad.test", "password": "triad-local-demo-password"})
s, agg = call("/api/v1/predictions", viewer_tok["access_token"])
print("viewer sees     ", s, {k: v for k, v in agg.items() if k != "score_dates"})
