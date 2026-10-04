# 12. Security, Privacy, and Governance Layer

A platform that lets software agents write and run code against business data must assume that the
agents will sometimes be wrong, that their inputs may be hostile, and that the people using it
will sometimes make mistakes. This layer is designed on those assumptions. Fig. 18 shows its
architecture and Table 7 lists its controls with the test or observation that verifies each.

**Fig. 18. Security and Governance Architecture.** *(figures/fig18_security_governance.drawio)*

## 12.1 Access control

People authenticate with passwords hashed by Argon2id and receive short-lived signed tokens; the
service refuses to start with a signing key too weak to be safe. Six roles - administrator, data
engineer, data scientist, machine learning engineer, business analyst, and viewer - are enforced
on the server for every endpoint, and the role is re-read from the database for every request that
changes something, so a demoted user's existing token cannot be used to act with the old role. A
viewer receives aggregates rather than customer-level predictions. A security test enumerates the
endpoints from the running application, rather than from a maintained list, and checks each
against its role rules, so a new endpoint cannot be added without a role check.

## 12.2 Governance by policy

Every consequential action is named in a policy file and assigned an autonomy level. *Forbidden*
actions - adding a dependency, deleting data, and three others - may not be performed by an agent at
all. *Advisory* actions, such as confirming a mapping for a new source, are performed by a person
on the agent's suggestion. *Bounded* actions - profiling, cleaning, generating and running code,
changing an allowlisted Spark setting, training, tuning, rolling back to the previous model - are
performed autonomously within stated limits. *Supervised* actions - promoting a model, promoting
one that failed a check, changing a schema or the churn definition, raising a quota or an LLM
budget, publishing a report externally - require an approval with named evidence. The policy has
23 actions. The engine that evaluates it is fail-closed: an action it does not know, or a check it
cannot complete, is refused. Run budgets cap the wall-clock time at 240 minutes, code repairs at
three per stage, infrastructure retries at two, and identical failures at two, and a cleaning
stage that would affect more than its allowed share of rows waits for a person.

## 12.3 Separation of duties

A promotion is requested by the pipeline on behalf of the person who started the run, and it may
be approved only by a different person holding an approver role; a request expires after 72 hours
and an expired request counts as a rejection. When the approval is granted, the worker - not the
approver's browser - moves the champion alias, runs a smoke test, and rolls the alias back and
raises an alert if the smoke test fails. Every model artifact's hash is recorded at registration
and verified on every load, and a model whose bytes no longer match is refused [@raji2020accountability].

## 12.4 Secure execution

Generated code runs only in the sandbox, and only one service may start sandbox containers: it
holds the container engine's socket and nothing else of value - no database address, no secret,
no model key - and it accepts a job only from a caller presenting its token, only for an
allowlisted image [@sultan2019container] [@wong2023container] [@jarkas2025container]. Each job runs
as an unprivileged user with every Linux capability dropped, no privilege escalation, a read-only
root filesystem, a size-capped scratch area, limits on processor, memory, and process count, and a
hard timeout. A job in local mode is given no network at all, so it cannot reach the internet, the
database, the model registry, or the sandbox service itself. It is given only its own input
folders and the compiled code, read-only, and only its run's folder writable; nothing outside the
data root and the source tree can be mounted, so the container socket can never be passed to a
job. If the sandbox service cannot be reached, the job does not run - there is no fallback to a
less isolated runner. A security test runs real jobs through the service and observes each of these
properties from inside the container.

## 12.5 Untrusted content and generated code

Data are treated as untrusted input to any prompt: content is fenced, scanned for injection
patterns including chat-template control tokens, and neutralized when it matches
[@greshake2023injection] [@das2025llmsecurity]. Generated code is treated as untrusted input to
the build: its slot is the only part that may differ from a reviewed template, its imports are
allowlisted, its static scan refuses forbidden calls, and it runs in the sandbox only after it has
compiled, passed its fixture tests, and completed a dry run [@pearce2022asleep]
[@tihanyi2024secure].

## 12.6 Audit and privacy

Every entry in the audit trail carries the hash of the previous entry of its run, appended under a
lock on that run's chain so that concurrent writers cannot fork it [@schneier1999auditlogs]. A
database trigger refuses updates and deletions of audit rows unless an archival operation opts in
explicitly, and a verifier walks each chain and reports the first entry that does not match.
Refusals are committed to the trail even when the request that caused them is rolled back. One
residual limit is stated in the design record: deleting an entire run's chain is not detected by
chain verification alone.

Secrets are never placed in source or logs, and a test scans the tracked source for them; logs and
stored prompts are redacted; customer identifiers can be masked before reaching a prompt; and the
settings view strips every credential from the connection details it shows. The data used in this
report are synthetic. The audit trail is deliberately not anonymized - it records who did what - and
is visible only to administrators.

**Table 7. Security Controls and Governance Policies**

| Control | Threat addressed | Mechanism | Verified by |
| --- | --- | --- | --- |
| Authentication | Impersonation | Argon2id; signed, expiring tokens; weak-key refusal | ST-01 tests |
| Authorization | Privilege misuse | Six roles checked server-side on every endpoint | ST-02, routes enumerated from the app |
| Secret hygiene | Credential leakage | No secrets in source or logs; redaction; scrubbed job environment | ST-03 scan; sandbox canary test |
| Run isolation | Cross-run interference | Run-scoped storage; one writable folder per job | ST-04 tests |
| Transport encryption | Eavesdropping | TLS at a reverse proxy | **Not configured** in the local deployment (ST-05) |
| Network isolation | Exfiltration, lateral movement | No network for local-mode jobs | ST-06, observed from inside a job |
| Container limits | Escape, resource abuse | Non-root, no capabilities, read-only root, cgroup limits, timeout | ST-07, observed from inside a job |
| Model integrity | Tampered artifacts | Hash at registration, verified on load, fail-closed | ST-08 tests |
| Policy engine | Ungoverned actions | 23 actions in four autonomy levels; fail-closed | Governance tests; EXP-10 |
| Separation of duties | Self-approval | Requester cannot approve; expiry; smoke-test rollback | ATC-17; AT-14 |
| Tool mediation | Agents exceeding their role | Allowlist, schema, and policy check per tool call | Tool mediation tests |
| Generated-code gates | Unsafe or wrong code | Slots, import allowlist, static scan, compile, tests, dry run | Gate tests; EXP-8 |
| Injection defence | Instructions hidden in data | Fencing and scanning of untrusted content | ATC-13; EXP-10 |
| Tamper-evident audit | Altered history | Per-run hash chain; append-only trigger; verifier | Tamper and concurrency tests |

---

## Evidence (not part of the manuscript)

- Roles: `backend/app/security.py` (`ALL_ROLES`); role re-read: module docstring; viewer aggregates:
  PRD 25.2, `PredictionAggregate`.
- Policy: `agents/policies/governance.yaml` (23 `autonomy:` entries; levels forbidden, advisory,
  bounded, supervised; budgets; `approval_expiry_hours: 72`); engine `triad_core/policy.py`.
- Smoke-test rollback: ATC-12, AT-14. Model integrity: ST-08 row in traceability.
- Sandbox: ADR-0004; `tests/security/test_sandbox_isolation.py`; `tests/unit/test_sandbox_runner.py`.
- Audit: DEC-17; append-only trigger and `triad.allow_audit_archival`; `scripts/reseal_audit.py`.
- Security tests: traceability section "Security tests (ST-01 to ST-08)".
