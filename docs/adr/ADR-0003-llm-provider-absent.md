# ADR-0003: No LLM provider is configured; deterministic paths are first-class

- Status: accepted
- Date: 17-09-2026
- PRD anchors: ASM-04, section 44 ("template-only mode"), section 21 (template-first generation),
  NFR-REP-03, ATC-18, FR-054, FR-059, FR-063

## Context

ASM-04 assumes an LLM with tool calling and JSON output is reachable through an adapter.
The workstation has no API key for any provider (Anthropic, OpenAI, Gemini), no local model
server (Ollama absent), and OQ-02 ("which LLM, what budget") is still open in the PRD.

The PRD already anticipates this: CR-15 makes generation template-first, section 44 says
"if the LLM is unavailable, a template-only mode still runs the pipeline with parameter
filling and playbook repairs", and NFR-REP-03 requires replay mode to make no LLM calls.

## Decision

The LLM adapter is built with four interchangeable providers behind one interface:

| Provider | Behaviour | Used for |
| --- | --- | --- |
| `template_only` (default) | No network. Deterministic slot filling from the plan + template parameters; deterministic playbook repairs; deterministic narrative from the fact sheet. | Normal operation on this machine; PRD section 44 fallback; CI. |
| `recorded` | Replays a stored cassette (prompt hash -> response) captured earlier. Fails closed if a prompt is unseen. | Agent tests (ATC-*), replay mode (ATC-18), deterministic CI. |
| `anthropic` / `openai` / `ollama` | Live calls, schema-validated, budget-metered, redacted. | Enabled the moment a key is present; untested here. |

Budgets, redaction, prompt-template versioning, `llm_invocations` rows and decision records
are enforced **in the adapter and the orchestrator**, not in the provider, so they apply
identically to every provider including `template_only` (which records zero-cost invocations
with `provider="template_only"`).

## Consequences

- Every Must requirement that does not intrinsically require a live model is implementable
  and testable here, including the nine generation gates, the repair loop, budgets,
  injection defences and replay.
- Requirements whose evidence needs a live model (for example: an LLM actually repairing an
  unseen compile error) are marked **untested - no provider** in the traceability matrix,
  with the exact command to run once a key exists. They are never marked passing.
- The demo does not depend on the network (PRD section 55 fallbacks).
