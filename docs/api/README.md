# API reference

For developers calling TRIAD from something other than its own frontend. The live OpenAPI
document is at <http://localhost:8010/openapi.json> and the browsable version at
<http://localhost:8010/docs>; this page covers what the schema does not say — which roles may
call what, how errors arrive, and what the non-obvious endpoints are for.

Base path: `/api/v1`. Everything except `GET /health` needs a bearer token.

## Authentication

```bash
TOKEN=$(curl -s -X POST http://localhost:8010/api/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@triad.test","password":"..."}' | jq -r .access_token)

curl -s http://localhost:8010/api/v1/pipelines -H "Authorization: Bearer $TOKEN"
```

HS256, one hour by default (`TRIAD_JWT_TTL_MINUTES`). A wrong password and an unknown email are
deliberately indistinguishable. A token stays valid for its lifetime, but a request made with it
fails as soon as the account is deactivated — the check is on the request, not on the token.

## Roles

| Role | Reads | Writes |
| --- | --- | --- |
| `admin` | everything | everything, and is never locked out of a role-gated endpoint |
| `data_engineer` | datasets, profiles, runs | register datasets, upload, start runs, approve non-model actions |
| `data_scientist` | datasets, profiles, models, predictions | start runs |
| `ml_engineer` | everything except the audit trail | start runs, approve and reject promotions, roll back |
| `business_analyst` | models, predictions, reports, approvals | export predictions |
| `viewer` | aggregates only — `GET /predictions` returns counts and a mean, never rows | nothing |

Two gates are worth knowing about because they surprise people:

- **`GET /audit-logs` and `/audit-logs/verify` are admin-only**, and so is `GET /settings`.
- **`GET /predictions` changes shape by role.** A viewer gets a `PredictionAggregate`; everyone
  else gets rows. The endpoint does not 403 — it answers a coarser question.

## Errors

Every error is an RFC 9457 problem document, `application/problem+json`:

```json
{
  "type": "https://triad.example/problems/unauthorised",
  "title": "Authentication required",
  "status": 401,
  "detail": "Those credentials are not valid.",
  "instance": "/api/v1/auth/token"
}
```

`detail` is written to be acted on: "approver must differ from requester", not "something went
wrong". A client should show it rather than replace it.

| Status | When |
| --- | --- |
| 400 | A malformed request — including a pagination cursor this API did not issue |
| 401 | No token, an expired token, or one signed with another key |
| 403 | Authenticated, but the role is not permitted |
| 404 | No such resource — or a resource whose stored document does not exist, such as a model version with no evaluation |
| 409 | A conflict with the current state: a second run of a pipeline already running, a decision on an approval already decided |
| 422 | Schema-valid JSON that the domain rejects |

## Endpoints

### Health and authentication

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/health` | Public. Reports real state, including database reachability |
| POST | `/api/v1/auth/token` | Returns `access_token`, `expires_at`, `role` |
| GET | `/api/v1/auth/me` | The caller's own account |

### Datasets

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/datasets` | Summaries with the latest version and its quality score |
| POST | `/datasets` | Register a source. Data engineer only |
| GET | `/datasets/{ref}` | By name or id |
| POST | `/datasets/{ref}/upload` | Multipart, 200 MB limit. Identical bytes return the existing version rather than creating a second copy |
| GET | `/datasets/{ref}/versions` | Newest first, with checksums |
| GET | `/datasets/{ref}/versions/{n}/profile` | The stored profile document: columns, issues with evidence, quality dimensions, schema diff. Data roles only |

### Pipelines

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/pipelines` | Cursor-paginated. `?state=`, `?pipeline=`, `?limit=`, `?cursor=` |
| POST | `/pipelines/run` | 202 with a `Location`. The run is **queued**, not executed in the request |
| GET | `/pipelines/{ref}` | State, stages with attempts and durations, budget used against limits, any pending approval |
| GET | `/pipelines/{ref}/events` | Server-sent events. See the note below |
| GET | `/pipelines/{ref}/logs` | Redacted |
| GET | `/pipelines/{ref}/decisions` | Every decision record: routing, policy, acceptance, with rule ids and evidence |
| GET | `/pipelines/{ref}/artifacts` | Generated code with its gate results |
| POST | `/pipelines/{ref}/cancel` | Ownership and state are both checked |
| POST | `/pipelines/{ref}/resume` | After an escalation |

**Pagination is by opaque cursor.** `next_cursor` is the encoded sort key of the last row
returned; pass it back as `?cursor=`. An offset would skip or repeat rows whenever something is
inserted between two pages. A cursor this API did not issue is a 400, not a 500 — which it was,
on the only paginated endpoint, until something finally asked for a second page.

**The event stream is authenticated**, so it cannot be consumed with `EventSource`, which cannot
set a header. Read it with `fetch` and parse the SSE framing, as
`frontend/src/shared.tsx::useRunEvents` does, or put the token in a header from a server-side
client. It is deliberately not accepted in the query string, where it would end up in access logs.

### Models

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/models` | Each model with its champion and challenger |
| GET | `/models/{ref}` | All versions, their metrics, thresholds and governance status |
| GET | `/models/{ref}/versions/{id}/evaluation` | The stored evaluation document: ROC, precision-recall, calibration bins, a lift/gain sweep, bootstrap intervals, per-segment metrics, feature importance with its method named |
| POST | `/models/{ref}/approve` | Requires a comment. The approver must differ from the requester |
| POST | `/models/{ref}/reject` | Same, and the champion is untouched |
| POST | `/models/{ref}/rollback` | Restores a **recorded** previous version only |

The evaluation document is served rather than recomputed because the curves need labels and
scores together, and the predictions table holds no labels. A client that recomputed them would
be inventing them.

### Approvals and alerts

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/approvals` | `?status=pending\|approved\|rejected\|expired`. Approver roles, business analyst and admin |
| GET | `/approvals/{id}` | With the evidence bundle URI |
| POST | `/approvals/{id}/decision` | `{approved, comment}`. The comment is mandatory. Deliberately not role-gated at the route: which roles may decide depends on the action type, and the policy engine is the only thing that should answer that |
| GET | `/alerts` | `?status=open\|acknowledged` |
| POST | `/alerts/{id}/acknowledge` | Recorded in the audit trail |

### Insights

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/stats` | The dashboard tiles, each one a query rather than a stored counter |
| GET | `/predictions` | Filters: `model_version_id`, `score_date`, `risk_band`, `prediction_set`, `min_probability`. Viewers get aggregates |
| POST | `/predictions/export` | Audited |
| GET | `/exports/{name}` | Retrieves an export |
| GET | `/drift`, `/drift/{id}` | PSI per feature, flagged features, score drift |
| GET | `/reports`, `/reports/{id}` | `?file=true` returns the rendered document |
| GET | `/experiments` | The ten case-study experiments as the harness wrote them |

### Audit and settings

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/audit-logs` | Admin. `?action=`, `?actor_id=`, `?run_id=`, `?since=`, `?limit=` |
| GET | `/audit-logs/verify` | Admin. Recomputes every chain and names the entry where it breaks |
| GET | `/settings` | Admin. Policy files with their versions and digests, budget ceilings, connection targets with credentials stripped, accounts and roles |

There is no write endpoint under `/settings`, and that is the design: the policy file is
reviewed and versioned like source, and accounts are created by `scripts/seed_demo.py` by
someone with shell access. An unauthenticated route that mints an administrator survives into
production.

## Prometheus

`GET /metrics` — not under `/api/v1`, no authentication, 22 series. It reads from the database
on each scrape rather than from in-process counters, because the work it describes is done by
the worker in a different process and an in-process counter would be wrong in a way that looks
plausible.

## A worked example: start a run and watch it

```bash
RUN=$(curl -s -X POST http://localhost:8010/api/v1/pipelines/run \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"pipeline":"churn_v1"}' | jq -r .run_id)

# Stage changes as they happen
curl -sN http://localhost:8010/api/v1/pipelines/$RUN/events -H "Authorization: Bearer $TOKEN"

# What it decided, and why
curl -s http://localhost:8010/api/v1/pipelines/$RUN/decisions -H "Authorization: Bearer $TOKEN" | jq '.[] | {decision_type, decision, policy_rule_ids}'
```
