"""The tool registry on the runtime path (FR-053).

test_injection.py already covers the registry as a mechanism. What is tested here is that
the mechanism is *load-bearing*: that the pipeline's real Spark submission and real catalog
commit go through `REGISTRY.call`, that a refusal at any of its gates stops the work rather
than being logged and stepped over, and that the call lands in the run's audit chain.

The distinction matters because the registry was previously correct and empty. A passing
test of `ToolRegistry` said nothing about what the pipeline did.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import agents.tools.pipeline_tools  # noqa: F401  registers the tools under test
from agents.tools.registry import (
    REGISTRY,
    PermissionDeniedError,
    PolicyRefusedError,
    SideEffect,
    ToolCall,
)
from triad_core.policy import PolicyEngine
from triad_core.storage import Manifest

SUBMIT_ARGS = {
    "run_key": "churn_v1_test",
    "stage": "clean",
    "jar": "/x/jobs.jar",
    "core_jar": "/x/core.jar",
    "main_class": "triad.jobs.generated.Clean",
    "job_args": ["--run-key=churn_v1_test"],
    "master": "local[2]",
    "profile": "small",
}


@dataclass
class _FakeResult:
    ok: bool = True
    exit_code: int = 0
    duration_ms: int = 12
    image_digest: str | None = "sha256:abc"
    stdout: str = ""
    stderr: str = ""


class _RecordingSandbox:
    """Stands in for the container runner and records whether it was ever reached."""

    def __init__(self) -> None:
        self.requests: list[Any] = []

    def run(self, request: Any) -> _FakeResult:
        self.requests.append(request)
        return _FakeResult()


@pytest.fixture()
def policy() -> PolicyEngine:
    from triad_core.config import settings

    return PolicyEngine(settings().governance_policy_path)


# --- the pipeline really does call through the registry ---------------------------------


def test_the_pipeline_submits_spark_jobs_through_the_registry() -> None:
    """Named call sites, so this fails if a future edit reaches past the registry again."""
    source = Path("agents/graph/pipeline.py").read_text(encoding="utf-8")

    assert 'self._call_tool(\n                "spark.submit_job"' in source
    assert '"storage.commit_output"' in source
    # The bare calls these replaced must not come back.
    assert "self.sandbox.run(" not in source, "a Spark submission bypasses the registry"
    assert "self.storage.commit(" not in source, "a catalog commit bypasses the registry"


def test_every_registered_tool_that_writes_is_audited() -> None:
    """A side effect nobody can see afterwards is the failure this rules out."""
    for name in REGISTRY.names():
        spec = REGISTRY.spec(name)
        if spec.side_effect is not SideEffect.NONE:
            assert spec.audit_required, f"{name} has a side effect but is not audited"


# --- the gates actually stop work -------------------------------------------------------


def test_a_permission_refusal_stops_the_job_before_the_sandbox(policy: PolicyEngine) -> None:
    sandbox = _RecordingSandbox()
    result = REGISTRY.call(
        # `analysis` may read a manifest but may not submit jobs.
        ToolCall(tool="spark.submit_job", agent="analysis", arguments=dict(SUBMIT_ARGS)),
        sandbox=sandbox,
        policy=policy,
        policy_context={"profile": "small"},
    )
    assert not result.ok
    assert result.error_code == PermissionDeniedError.code
    assert sandbox.requests == [], "the sandbox ran despite the call being refused"


def test_a_policy_refusal_stops_the_job_before_the_sandbox(policy: PolicyEngine) -> None:
    """`sandbox.run` is bounded autonomy with a profile allowlist; `huge` is not on it.

    The arguments are valid and the agent is permitted, so the only thing that can refuse
    this is the governance policy - which is the point of the test.
    """
    sandbox = _RecordingSandbox()
    result = REGISTRY.call(
        ToolCall(
            tool="spark.submit_job",
            agent="code_generation",
            arguments=dict(SUBMIT_ARGS),
        ),
        sandbox=sandbox,
        policy=policy,
        policy_context={"profile": "huge"},
    )
    assert not result.ok
    assert result.error_code == PolicyRefusedError.code
    assert "not permitted" in result.error
    assert sandbox.requests == [], "the sandbox ran despite the policy refusing"


def test_a_governed_tool_is_refused_when_no_policy_engine_is_configured() -> None:
    """Fail closed. A governance check that switches itself off is not a check."""
    sandbox = _RecordingSandbox()
    result = REGISTRY.call(
        ToolCall(tool="spark.submit_job", agent="code_generation", arguments=dict(SUBMIT_ARGS)),
        sandbox=sandbox,
    )
    assert not result.ok
    assert result.error_code == PolicyRefusedError.code
    assert "no policy engine" in result.error
    assert sandbox.requests == []


def test_an_allowed_submission_reaches_the_sandbox(policy: PolicyEngine) -> None:
    """The counterpart: mediation must not simply block everything."""
    sandbox = _RecordingSandbox()
    result = REGISTRY.call(
        ToolCall(tool="spark.submit_job", agent="code_generation", arguments=dict(SUBMIT_ARGS)),
        sandbox=sandbox,
        policy=policy,
        policy_context={"profile": "small"},
    )
    assert result.ok, result.error
    assert len(sandbox.requests) == 1
    request = sandbox.requests[0]
    assert request.profile == "small"
    assert request.network.endswith("triad-sandbox-net")


def test_arguments_are_schema_checked_before_the_sandbox_sees_them(policy: PolicyEngine) -> None:
    sandbox = _RecordingSandbox()
    bad = dict(SUBMIT_ARGS, job_args="--run-key=x")  # a string where a list is required
    result = REGISTRY.call(
        ToolCall(tool="spark.submit_job", agent="code_generation", arguments=bad),
        sandbox=sandbox,
        policy=policy,
        policy_context={"profile": "small"},
    )
    assert not result.ok
    assert result.error_code == "invalid_arguments"
    assert sandbox.requests == []


def test_an_unknown_argument_cannot_be_smuggled_in(policy: PolicyEngine) -> None:
    """`additionalProperties: false` is what stops an extra key reaching the service."""
    sandbox = _RecordingSandbox()
    sneaky = dict(SUBMIT_ARGS, image="attacker/image:latest")
    result = REGISTRY.call(
        ToolCall(tool="spark.submit_job", agent="code_generation", arguments=sneaky),
        sandbox=sandbox,
        policy=policy,
        policy_context={"profile": "small"},
    )
    assert not result.ok
    assert result.error_code == "invalid_arguments"
    assert sandbox.requests == []


def test_a_service_cannot_be_supplied_as_an_argument(policy: PolicyEngine) -> None:
    """Services come from the process, never from the call. This is the isolation property.

    If `sandbox` were an accepted argument, a generated call could hand in its own runner
    and escape the container entirely.
    """
    assert "sandbox" not in REGISTRY.spec("spark.submit_job").input_schema["properties"]
    assert "store" not in REGISTRY.spec("storage.commit_output").input_schema["properties"]

    result = REGISTRY.call(
        ToolCall(
            tool="spark.submit_job",
            agent="code_generation",
            arguments=dict(SUBMIT_ARGS, sandbox=_RecordingSandbox()),
        ),
        sandbox=_RecordingSandbox(),
        policy=policy,
        policy_context={"profile": "small"},
    )
    assert not result.ok
    assert result.error_code == "invalid_arguments"


# --- the audit sink ---------------------------------------------------------------------


def test_a_refused_call_is_audited_as_refused(policy: PolicyEngine) -> None:
    entries: list[tuple[ToolCall, Any]] = []
    REGISTRY.call(
        ToolCall(tool="spark.submit_job", agent="analysis", arguments=dict(SUBMIT_ARGS)),
        sandbox=_RecordingSandbox(),
        policy=policy,
        audit_sink=lambda c, r: entries.append((c, r)),
    )
    assert len(entries) == 1
    call, result = entries[0]
    assert call.tool == "spark.submit_job"
    assert not result.ok
    assert result.error_code == PermissionDeniedError.code


def test_the_audit_sink_travels_with_the_call_not_the_registry(policy: PolicyEngine) -> None:
    """Two runs in one process must not audit into each other's session.

    The registry is a module singleton. If the sink lived on it, the second pipeline
    constructed would capture the first one's tool calls - which would put one run's
    entries in another run's hash chain.
    """
    first: list[Any] = []
    second: list[Any] = []
    common = {"sandbox": _RecordingSandbox(), "policy": policy,
              "policy_context": {"profile": "small"}}

    REGISTRY.call(
        ToolCall(tool="spark.submit_job", agent="code_generation", arguments=dict(SUBMIT_ARGS)),
        audit_sink=lambda c, r: first.append(r), **common,
    )
    REGISTRY.call(
        ToolCall(tool="spark.submit_job", agent="code_generation", arguments=dict(SUBMIT_ARGS)),
        audit_sink=lambda c, r: second.append(r), **common,
    )
    assert len(first) == 1 and len(second) == 1


# --- the commit tool --------------------------------------------------------------------


def test_commit_output_publishes_through_the_registry(storage, tmp_path: Path) -> None:
    staging = storage.staging_dir("run-commit-tool", "clean")
    staging.mkdir(parents=True)
    (staging / "part-0.parquet").write_bytes(b"not really parquet")
    target = Path(storage.root) / "clean" / "test_commit"

    result = REGISTRY.call(
        ToolCall(
            tool="storage.commit_output",
            agent="code_generation",
            arguments={
                "staging": str(staging),
                "target": str(target),
                "manifest": {
                    "kind": "clean",
                    "name": "clean",
                    "version": "churn_v1_test",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "row_count": 1,
                    "schema": {"customer_id": "string"},
                },
            },
        ),
        store=storage,
    )
    assert result.ok, result.error
    assert result.value["uri"].startswith("file://")
    assert (target / "_manifest.json").exists()

    # And the read tool sees what the write tool published.
    read = REGISTRY.call(
        ToolCall(
            tool="storage.read_manifest",
            agent="analysis",
            arguments={"directory": str(target)},
        ),
        store=storage,
    )
    assert read.ok, read.error
    assert read.value["row_count"] == 1


def test_commit_output_requires_a_manifest_kind(storage, tmp_path: Path) -> None:
    """An output with no declared kind is refused before anything is published."""
    result = REGISTRY.call(
        ToolCall(
            tool="storage.commit_output",
            agent="code_generation",
            arguments={
                "staging": str(tmp_path),
                "target": str(tmp_path / "out"),
                "manifest": {"name": "clean", "version": "v", "created_at": "2026-01-01"},
            },
        ),
        store=storage,
    )
    assert not result.ok
    assert result.error_code == "invalid_arguments"
    assert not (tmp_path / "out").exists()


def test_a_tool_that_raises_becomes_a_structured_failure(storage, tmp_path: Path) -> None:
    """An implementation fault must not escape as an exception through the stage runner."""
    result = REGISTRY.call(
        ToolCall(
            tool="storage.commit_output",
            agent="code_generation",
            arguments={
                "staging": str(tmp_path / "does-not-exist"),
                "target": str(tmp_path / "out"),
                "manifest": {
                    "kind": "clean",
                    "name": "clean",
                    "version": "v",
                    "created_at": "2026-01-01T00:00:00+00:00",
                },
            },
        ),
        store=storage,
    )
    assert not result.ok
    assert result.error_code in {"tool_exception", "storage_error"}
    assert result.error


def test_the_manifest_written_matches_what_the_tool_was_given(storage, tmp_path: Path) -> None:
    """Guards the dict -> Manifest hop: a dropped field would silently lose provenance."""
    staging = storage.staging_dir("run-manifest-hop", "features")
    staging.mkdir(parents=True)
    (staging / "part-0.parquet").write_bytes(b"x")
    target = Path(storage.root) / "features" / "prov"
    REGISTRY.call(
        ToolCall(
            tool="storage.commit_output",
            agent="code_generation",
            arguments={
                "staging": str(staging),
                "target": str(target),
                "manifest": {
                    "kind": "features",
                    "name": "features",
                    "version": "churn_v1_prov",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "run_id": "churn_v1_prov",
                    "row_count": 7,
                    "schema": {"a": "int"},
                },
            },
        ),
        store=storage,
    )
    written: Manifest = storage.read_manifest(target)
    assert written.kind == "features"
    assert written.run_id == "churn_v1_prov"
    assert written.row_count == 7
    assert written.schema == {"a": "int"}


def test_commit_output_refuses_a_staging_folder_outside_the_data_root(
    storage, tmp_path: Path
) -> None:
    """An agent cannot publish an arbitrary directory into the catalogue.

    `staging` is an argument of an agent's tool call. `Storage.commit` resolves it through
    `Storage.path`, which refuses anything outside the data root, so a tool call naming
    `/etc` - or, as here, any directory the platform did not create under its own root - fails
    before a byte is copied, and the target is never created.
    """
    outside = tmp_path / "not-the-data-root"
    outside.mkdir()
    (outside / "part-0.parquet").write_bytes(b"would be published")
    target = Path(storage.root) / "clean" / "should_not_exist"

    result = REGISTRY.call(
        ToolCall(
            tool="storage.commit_output",
            agent="code_generation",
            arguments={
                "staging": str(outside),
                "target": str(target),
                "manifest": {
                    "kind": "clean", "name": "clean", "version": "churn_v1_escape",
                    "created_at": "2026-01-01T00:00:00+00:00", "row_count": 1,
                    "schema": {"customer_id": "string"},
                },
            },
        ),
        store=storage,
    )

    assert not result.ok
    assert "outside the data root" in (result.error or "")
    assert not target.exists()
