"""The tools the pipeline actually calls, registered so every call is mediated (FR-053).

Before this module existed the registry was real code with nothing in it: `REGISTRY` was
empty at runtime, so the permission checks and the audit hook were exercised only by their
own unit tests. The pipeline reached `sandbox.run` and `storage.commit` directly.

Every side-effecting operation an agent stage performs now goes through `REGISTRY.call`, so
four things happen on the live path rather than in a test:

* the calling agent is checked against `permitted_agents` - a stage cannot use a tool
  belonging to another agent;
* the arguments are validated against a JSON schema before the implementation sees them;
* a tool that names a governed action is held against the policy engine first;
* the call is appended to the run's hash-chained audit trail.

**Services are passed through `**context`, never through `arguments`.** The Storage,
sandbox and registry clients are not JSON-serialisable and are not part of any schema: they
come from the process. So the most a model-authored call can ever supply is
schema-validated data, and it cannot reach a service the stage did not already hold. This
is the property that makes the mediation worth having, and it is why the tool signatures
below take their services as keyword-only arguments that no schema mentions.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from agents.tools.registry import REGISTRY, SideEffect
from triad_core.storage import Manifest

_STR = {"type": "string"}


# --- spark ------------------------------------------------------------------------------
@REGISTRY.tool(
    "spark.submit_job",
    description=(
        "Submit one packaged Spark job to the sandbox. The sandbox has no route to the "
        "internet, the database or any secret; it sees only the data volume."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "run_key": _STR,
            "stage": _STR,
            "jar": _STR,
            "core_jar": _STR,
            "main_class": _STR,
            "job_args": {"type": "array", "items": _STR},
            "master": _STR,
            "profile": {"enum": ["small", "medium", "large"]},
            # The repair loop raises this when it diagnoses an out-of-memory failure, so the
            # retry is submitted with more memory than the attempt that failed.
            "driver_memory": {"type": "string", "pattern": "^[0-9]+[mMgG]$"},
        },
        "required": ["run_key", "stage", "jar", "core_jar", "main_class", "job_args"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "exit_code": {"type": "integer"},
            "duration_ms": {"type": "integer"},
            "image_digest": {"type": ["string", "null"]},
            "stdout": _STR,
            "stderr": _STR,
        },
        "required": ["ok", "exit_code"],
    },
    permitted_agents={"code_generation", "spark_optimisation"},
    owner="platform",
    side_effect=SideEffect.SUBMIT_JOB,
    # `sandbox.run` is bounded autonomy with a profile allowlist and internal-only
    # networking; the registry refuses the call if the policy engine does not allow it.
    policy_action="sandbox.run",
)
def _submit_job(*, sandbox: Any, **arguments: Any) -> dict[str, Any]:
    from sandbox.runner import spark_submit_request

    result = sandbox.run(spark_submit_request(**arguments))
    return {
        "ok": result.ok,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "image_digest": result.image_digest,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


# --- storage ----------------------------------------------------------------------------
@REGISTRY.tool(
    "storage.commit_output",
    description=(
        "Atomically publish a staging folder as a catalog output, with its manifest. The "
        "target is derived by the caller; a tool call cannot write outside the data root."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "staging": _STR,
            "target": _STR,
            "manifest": {
                "type": "object",
                "properties": {
                    "kind": _STR,
                    "name": _STR,
                    "version": _STR,
                    "created_at": _STR,
                    "run_id": {"type": ["string", "null"]},
                    "row_count": {"type": ["integer", "null"]},
                    "schema": {"type": "object"},
                },
                "required": ["kind", "name", "version", "created_at"],
            },
        },
        "required": ["staging", "target", "manifest"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {"path": _STR, "uri": _STR},
        "required": ["path", "uri"],
    },
    permitted_agents={"code_generation", "data_engineering"},
    owner="platform",
    side_effect=SideEffect.WRITE_CATALOG,
    # Deliberately no `policy_action`: what governs a commit is the *stage* action
    # (cleaning.execute and friends), checked against the measured row-impact facts in
    # ChurnPipeline._check_stage_policy. Naming a second action here would gate the same
    # commit twice, on a context that has none of those facts in it.
)
def _commit_output(
    *, store: Any, staging: str, target: str, manifest: dict[str, Any]
) -> dict[str, Any]:
    committed = store.commit(Path(staging), Path(target), Manifest(**manifest))
    return {"path": str(committed), "uri": store.uri(committed)}


@REGISTRY.tool(
    "storage.read_manifest",
    description="Read the manifest of a committed folder: row count, schema and hashes.",
    input_schema={
        "type": "object",
        "properties": {"directory": _STR},
        "required": ["directory"],
        "additionalProperties": False,
    },
    output_schema={"type": "object"},
    permitted_agents={"code_generation", "data_engineering", "ml_engineering", "analysis"},
    owner="platform",
    # A read has no side effect and needs no policy action; it is registered so the
    # contrast is visible on the live path, and audited like everything else.
    side_effect=SideEffect.NONE,
)
def _read_manifest(*, store: Any, directory: str) -> dict[str, Any]:
    return asdict(store.read_manifest(Path(directory)))


# --- what is deliberately NOT here ------------------------------------------------------
# No alias-moving tool, no delete, no approval tool. ATC-02 - "agents never move aliases
# directly" - is guaranteed by absence, which is stronger than a refusal: there is nothing
# to call however the policy file is edited. Governance applies an approved promotion or the
# pre-approved rollback through its own service, on a path no agent reaches.
# test_injection.py asserts that property against the whole registry, not against a list.
#
# `policy_action` is not idle for want of this: `spark.submit_job` above names
# `sandbox.run`, so every Spark submission on the live path passes the policy engine.
