"""The churn pipeline: RUN_PIPELINE from PRD section 39, wired to the real services.

This is the deterministic macro-workflow. Agents act only *inside* a stage; the order of
stages, the guards between them and the approval gate are fixed here (DD-02).

Every stage goes through `StageRunner`, so bounds, diagnosis, repair, memoisation, decision
records and escalation tickets behave identically whether the stage runs a Scala job, a
PySpark query or a Python trainer.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.codegen import gates as G
from agents.codegen.templates import (
    TEMPLATE_ROOT,
    RenderedArtifact,
    clean_generated,
    load_templates,
    render,
    write_artifact,
)
from agents.graph import queue as q
from agents.graph.stages import StageRequest, StageRunner
from agents.llm.budget import RunBudget
from agents.routing import RoutingEngine
from agents.tools import pipeline_tools as _tools  # noqa: F401  registers the tools
from agents.tools.registry import REGISTRY, ToolCall, ToolResult, ToolSpec
from sandbox.runner import get_runner
from triad_core import audit
from triad_core.config import load_yaml, settings
from triad_core.contracts import Contract
from triad_core.governance import EvidenceBundle, GovernanceService
from triad_core.ids import content_hash, utcnow
from triad_core.models import CodeArtifact, Metric, PipelineRun
from triad_core.policy import PolicyEngine, Verdict
from triad_core.storage import Storage, StorageError, storage

SCALA_ROOT = settings().repo_root / "scala_spark"


class ReplayUnavailable(RuntimeError):
    """A replay was asked for and the stored artifacts cannot support it.

    Raised rather than falling back to fresh generation: a "replay" that quietly regenerated
    would pass ATC-18 while proving nothing.
    """


@dataclass
class PipelineResult:
    run_key: str
    state: str
    ok: bool
    reason: str = ""
    artifacts: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    approval_id: str | None = None
    escalation: dict[str, Any] | None = None
    stages: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_key": self.run_key,
            "state": self.state,
            "ok": self.ok,
            "reason": self.reason,
            "artifacts": self.artifacts,
            "metrics": self.metrics,
            "approval_id": self.approval_id,
            "escalation": self.escalation,
            "stages": self.stages,
        }


class ChurnPipeline:
    """One governed run, from registered inputs to a published report."""

    def __init__(
        self,
        session: Session,
        run: PipelineRun,
        *,
        store: Storage | None = None,
        policy: PolicyEngine | None = None,
        config_path: Path | None = None,
        replay_of: PipelineRun | None = None,
    ) -> None:
        self.session = session
        self.run = run
        self.storage = store or storage()
        self.policy = policy or PolicyEngine(settings().governance_policy_path)
        self.config = load_yaml(config_path or settings().pipeline_config_path)
        self.routing = RoutingEngine()
        self.governance = GovernanceService(session, self.policy)
        self.budget = RunBudget.from_dict(run.budget_json or self.config.get("budgets"))
        self.runner = StageRunner(session, run, self.budget)
        self.sandbox = get_runner()
        self.templates = load_templates(TEMPLATE_ROOT)
        # Replay mode (ATC-18, NFR-REP-03): reuse the stored artifacts of a completed run
        # rather than generating fresh ones, and make no LLM calls. `replay` used to be a
        # boolean that was assigned and never read; a flag nothing consults is not a mode.
        self.replay_of = replay_of
        self.replay = replay_of is not None
        self._generated_objects: set[str] = set()
        self._specs_seen: dict[str, ToolSpec] = {}
        self._replayed_from: dict[str, Any] = {}
        self.result = PipelineResult(run_key=run.run_key, state=run.state, ok=False)

    # -- helpers ---------------------------------------------------------------
    def _audit_tool_call(self, call: ToolCall, result: ToolResult) -> None:
        """Append one mediated tool call to this run's hash-chained audit trail.

        The arguments are recorded, the returned value is not: a value can be a whole
        schema or a page of Spark output, and the audit trail is for what was asked and
        whether it was allowed. `error_code` carries which gate refused.
        """
        spec = self._specs_seen.get(call.tool)
        audit.record(
            self.session,
            actor_type="agent",
            actor_id=call.agent,
            action=f"tool.{call.tool}",
            resource_type="tool",
            resource_id=call.tool,
            outcome="ok" if result.ok else "refused",
            run_id=self.run.id,
            details={
                "arguments": call.arguments,
                "side_effect": spec.side_effect.value if spec else None,
                "policy_action": spec.policy_action if spec else None,
                "duration_ms": result.duration_ms,
                "error_code": result.error_code,
                "error": result.error[:500] if result.error else "",
            },
        )

    def _call_tool(self, tool: str, agent: str, arguments: dict[str, Any], **services: Any):
        """Make a mediated tool call, recording the spec so the audit sink can describe it."""
        self._specs_seen[tool] = REGISTRY.spec(tool)
        # This run's policy engine and audit session travel with the call, so the shared
        # registry holds no per-run state. Absent a policy engine a governed tool is
        # refused, not allowed, so this cannot fail open.
        return REGISTRY.call(
            ToolCall(tool=tool, agent=agent, arguments=arguments, run_id=str(self.run.id)),
            policy=self.policy,
            audit_sink=self._audit_tool_call,
            **services,
        )

    def _advance(self, state: str, *, stage: str | None = None, reason: str | None = None) -> None:
        """Move the run to `state` and **commit**, so the transition is a checkpoint (FR-073).

        The worker runs a whole phase inside one `session_scope`, and nothing in the pipeline
        committed, so for the twelve-odd minutes of `churn.prepare` the API and the event
        stream saw the run sitting in RECEIVED and then every stage at once; and a worker that
        died at minute eleven rolled back every audit entry, decision record and execution of
        the attempt while the files it had written stayed on disk. Committing here also
        releases the run chain's advisory lock at each step instead of holding it for the
        whole phase.
        """
        q.set_state(self.session, self.run, state, stage=stage, error_summary=reason)
        self.session.commit()
        self.result.state = state

    def _persist_budget(self) -> None:
        self.run.budget_json = self.budget.to_dict()
        self.run.llm_calls_used = self.budget.llm_calls
        self.run.llm_cost_used = self.budget.llm_cost_usd
        self.session.flush()

    def _record_metric(
        self, name: str, value: float | None, *, scope: str = "run", split: str | None = None,
        segment: str | None = None, ci: tuple[float, float] | None = None,
    ) -> None:
        if value is None:
            return
        self.session.add(
            Metric(
                scope=scope,
                ref_id=str(self.run.id),
                split=split,
                segment=segment,
                metric_name=name,
                metric_value=float(value),
                ci_low=ci[0] if ci else None,
                ci_high=ci[1] if ci else None,
            )
        )

    def _route(self, stage: str, **evidence: Any) -> dict[str, Any]:
        """Every stage records the routing rule it matched (PO-03, SC-02)."""
        decision = self.routing.decide(stage, **evidence)
        self.runner.record_decision(
            agent="spark_planning",
            decision_type="routing",
            decision=f"{stage} -> {decision['engine']}/{decision.get('language', '-')}",
            summary=decision["rationale"][:500],
            evidence=decision["evidence"],
            policy_rules=[decision["rule_id"]],
        )
        return decision

    # -- code generation -------------------------------------------------------
    def _generate_scala(
        self, template_id: str, slot_bodies: dict[str, str] | None = None
    ) -> RenderedArtifact:
        """Template-first generation. With no LLM, the tested default slot body is used
        verbatim and parameters come from the plan (PRD 44 template-only mode)."""
        template = self.templates[template_id]
        if self.replay_of is not None:
            artifact = self._replay_scala(template_id)
        else:
            bodies = slot_bodies or {
                slot_id: template.default_body(slot_id) for slot_id in template.slot_ids()
            }
            artifact = render(template, bodies)
        write_artifact(artifact)
        # Generated sources compile as one module, so an artifact left behind by an earlier
        # run would fail this run's build. Only this run's artifacts stay on disk.
        self._generated_objects.add(artifact.object_name)
        clean_generated(keep=self._generated_objects)

        row = CodeArtifact(
            run_id=self.run.id,
            language="scala",
            template_id=template.template_id,
            template_version=template.version,
            slot_id=artifact.slot_id,
            content_hash=artifact.content_hash,
            storage_uri=str(self.storage.code_dir(artifact.content_hash)),
            static_check_status="pending",
            parent_artifact_id=self._replayed_from.get(template_id),
        )
        self.session.add(row)
        self.session.flush()
        self.storage.write_text(
            self.storage.code_dir(artifact.content_hash) / f"{artifact.object_name}.scala",
            artifact.source,
        )
        artifact_ref = artifact
        artifact_ref.path = artifact.path
        self._last_artifact_row = row
        return artifact

    def _replay_scala(self, template_id: str) -> RenderedArtifact:
        """The exact artifact the replayed run used, read back from storage.

        Not re-rendered from the current template: the whole point of ATC-18 is that a replay
        reproduces what ran, so if the template has changed since, the stored source is what
        must be used. The recorded hash is re-checked against the bytes on disk, because an
        artifact that has been altered underneath us would otherwise replay silently and the
        "identical output hashes" check would be comparing the wrong thing.
        """
        assert self.replay_of is not None
        stored = self.session.execute(
            select(CodeArtifact)
            .where(
                CodeArtifact.run_id == self.replay_of.id,
                CodeArtifact.template_id == template_id,
            )
            .order_by(CodeArtifact.created_at.desc())
        ).scalars().first()
        if stored is None:
            raise ReplayUnavailable(
                f"run {self.replay_of.run_key} recorded no {template_id} artifact, so there is "
                "nothing to replay; replay cannot invent one"
            )

        folder = Path(stored.storage_uri)
        sources = sorted(folder.glob("*.scala"))
        if not sources:
            raise ReplayUnavailable(
                f"the stored source for {template_id} is missing from {folder}; replay cannot "
                "reconstruct it"
            )
        source = sources[0].read_text(encoding="utf-8")
        actual = content_hash(source)
        if actual != stored.content_hash:
            raise ReplayUnavailable(
                f"the stored {template_id} source no longer matches its recorded hash "
                f"({actual[:12]} vs {stored.content_hash[:12]}); refusing to replay altered code"
            )

        self._replayed_from[template_id] = stored.id
        template = self.templates[template_id]
        return RenderedArtifact(
            template_id=template_id,
            template_version=stored.template_version,
            slot_id=stored.slot_id or (template.slot_ids()[0] if template.slot_ids() else ""),
            object_name=sources[0].stem,
            source=source,
            content_hash=stored.content_hash,
            language="scala",
        )

    def _validate_scala(
        self,
        artifact: RenderedArtifact,
        template_id: str,
        *,
        dry_run: Callable[[], dict[str, Any]] | None = None,
    ) -> G.GateReport:
        """Gates 1-6 and 8, in order, each only when every earlier gate passed.

        Gates 5 and 6 ran nowhere until FR-055 was audited: the functions existed, and the
        PRD's own viva answer names all nine gates. Gate 5 runs the generated object through
        hand-checked fixtures; gate 6 submits the same job on a sample, in the sandbox, against
        the same time limit. Gates 7 and 9 are decided by the execution itself (stages.py).
        """
        report = G.GateReport()
        template = self.templates[template_id]
        checks = self.config.get("validation", {})

        report.add(
            G.gate1_structured_output(
                {
                    "slot_id": artifact.slot_id,
                    "language": "scala",
                    "code": artifact.source,
                    "declared_imports": [],
                    "declared_output_schema": {},
                }
            )
        )
        report.add(G.gate2_static_checks(artifact, template))
        report.add(G.gate3_dependencies(artifact, G.build_file_hashes(), []))
        if report.passed:
            report.add(G.gate4_compile())
        if report.passed and checks.get("fixture_tests", True):
            report.add(G.gate5_fixture_tests(
                [f"triad.jobs.generated.{artifact.object_name}"],
                cache_dir=self.storage.root / "cache" / "gate5",
            ))
        if report.passed and dry_run is not None and checks.get("dry_run", True):
            report.add(G.gate6_dry_run(dry_run()))
        # Gate 8 records what `_store_artifact` already did: the source is in content-addressed
        # storage under the hash the row carries, which is what `_replay_scala` checks against
        # before reusing it. The check was being performed and never recorded as a gate, so a
        # reader of `gate_results` saw four gates where the PRD names nine.
        report.add(
            G.gate8_store(
                str(self.storage.code_dir(artifact.content_hash)), artifact.content_hash
            )
        )
        row = getattr(self, "_last_artifact_row", None)
        if row is not None:
            row.static_check_status = "passed" if report.passed else "failed"
            row.compile_status = "passed" if report.passed else "failed"
            row.gate_results = report.to_dict()
            self.session.flush()
        return report

    # -- stages ----------------------------------------------------------------
    def stage_profile(self, spark, dataset: str, uri: str, fmt: str = "parquet") -> dict[str, Any]:
        """P2: profile the registered version on Spark (UC-02)."""
        from dataeng.profile import profile_dataframe, read_for_profiling

        self._route("profile", read_only=True, kind="profiling")

        def generate(inputs, history):
            return {"uri": inputs["uri"], "fmt": inputs.get("fmt", "parquet")}

        def execute(artifact):
            df = read_for_profiling(spark, artifact["uri"], artifact["fmt"])
            profile = profile_dataframe(
                df,
                dataset=dataset,
                key_columns=self._key_columns(dataset),
                optional_columns=self._optional_columns(dataset),
                reference=self._previous_profile(artifact["uri"]),
            )
            path = self.storage.write_json(
                self.storage.root / "profiles" / f"{self.run.run_key}_{dataset}.json",
                profile.to_dict(),
            )
            return {
                "ok": True,
                "outputs": {"profile_uri": self.storage.uri(path),
                            "quality_score": profile.quality_score,
                            "blocking_issues": len(profile.blocking_issues),
                            "row_count": profile.row_count},
                "metrics": {"quality_score": profile.quality_score},
            }

        outcome = self.runner.run_stage(
            StageRequest(
                stage="profile",
                agent_name="data_profiling",
                inputs={"uri": uri, "fmt": fmt},
                generate=generate,
                validate=lambda a: G.GateReport([G.GateResult(0, "n/a", True)]),
                execute=execute,
                engine="spark",
            )
        )
        self.result.stages.append(outcome.to_dict())
        if outcome.ok:
            self._record_metric("quality_score", outcome.outputs.get("quality_score"))
            self._attach_profile_to_version(uri, outcome.outputs)
        return outcome.outputs if outcome.ok else {}

    def _previous_profile(self, uri: str) -> dict[str, Any] | None:
        """The profile of the version before the one at `uri`, for the shift check (FR-022).

        The nearest earlier version of the same dataset that was profiled; `None` for a first
        version, an unregistered path, or a profile that can no longer be read - the check is
        then skipped, which the profile records by having no `population_stability` entries.
        """
        from triad_core.models import DatasetVersion

        version = self.session.execute(
            select(DatasetVersion).where(DatasetVersion.storage_uri == uri)
        ).scalars().first()
        if version is None:
            return None
        previous = self.session.execute(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == version.dataset_id,
                   DatasetVersion.version_no < version.version_no,
                   DatasetVersion.profile_uri.is_not(None))
            .order_by(DatasetVersion.version_no.desc())
        ).scalars().first()
        if previous is None:
            return None
        try:
            document = json.loads(self.storage.path(previous.profile_uri).read_text(encoding="utf-8"))
        except (OSError, ValueError, StorageError):
            return None
        return {**document, "version_no": previous.version_no}

    def _attach_profile_to_version(self, uri: str, outputs: dict[str, Any]) -> None:
        """Write the profile back onto the version row it describes.

        `dataset_versions.profile_uri` and `.quality_score` were two more columns nothing
        filled. The profile document was written to storage and its URI handed back to the
        caller, which used the score for the quality gate and dropped the rest - so across 130
        version rows both columns were NULL, `GET /datasets/{ref}/versions/{n}/profile` could
        only ever answer 404 (it refuses when `profile_uri` is unset), and every dataset in the
        list read "not profiled" whatever its measured score was.

        Matched on `storage_uri` because that is what the caller passes as the profiling input:
        it is the same string `register_version` wrote on the row. A version that cannot be
        found is left alone rather than guessed at - profiling a path that is not a registered
        version is legitimate, and the experiments do it.
        """
        from triad_core.models import DatasetVersion

        version = self.session.execute(
            select(DatasetVersion).where(DatasetVersion.storage_uri == uri)
        ).scalars().first()
        if version is None:
            return
        version.profile_uri = outputs.get("profile_uri")
        version.quality_score = outputs.get("quality_score")
        self.session.flush()

    def _append_execution_gates(self, outcome: Any) -> None:
        """Merge the post-execution gates onto the artifact row this stage generated.

        `StageRunner` records gates 7 and 9 on its outcome, and an outcome is a return value:
        it reaches an escalation ticket and otherwise goes out of scope. `code_artifacts.
        gate_results` is where a gate result is meant to be readable afterwards - it is what
        `GET /pipelines/{id}/artifacts` serves and what the run page draws - so the two gates
        are appended there rather than left in an object nobody stores.
        """
        row = getattr(self, "_last_artifact_row", None)
        if row is None:
            return
        recorded = dict(row.gate_results or {})
        gates = list(recorded.get("gates", []))
        seen = {g.get("gate") for g in gates}
        for report in outcome.gate_reports or []:
            for gate in report.get("gates", []):
                if gate.get("gate") not in seen:
                    gates.append(gate)
                    seen.add(gate.get("gate"))
        recorded["gates"] = sorted(gates, key=lambda g: g.get("gate", 0))
        recorded["passed"] = all(g.get("passed") for g in gates)
        row.gate_results = recorded
        self.session.flush()

    def _contract(self, dataset: str) -> Contract | None:
        path = settings().repo_root / "pipelines" / "contracts" / f"{dataset}.yaml"
        return Contract.from_yaml(path) if path.exists() else None

    def _key_columns(self, dataset: str) -> list[str]:
        contract = self._contract(dataset)
        return contract.grain if contract else []

    def _optional_columns(self, dataset: str) -> frozenset[str]:
        """Columns the contract says may legitimately be absent or null.

        Passed to profiling so a nullable-by-design column does not raise a *blocking* issue
        just for being mostly null, which is what a promo code is.
        """
        contract = self._contract(dataset)
        if contract is None:
            return frozenset()
        return frozenset(c.name for c in contract.columns if not c.required and c.nullable)

    def stage_scala_job(
        self,
        *,
        stage: str,
        template_id: str,
        inputs: dict[str, str],
        main_class: str | None = None,
        output_dir: Path,
        params: dict[str, Any],
        partition_by: list[str] | None = None,
        required_columns: list[str] | None = None,
        quarantine_dir: Path | None = None,
        policy_action: str | None = None,
        input_rows: int = 0,
        input_bytes: int = 0,
    ) -> dict[str, Any]:
        """P3/P4/P6/P7: generate, validate and run a Scala Spark job in the sandbox."""
        routing = self._route(
            stage,
            kind="features" if "feature" in stage else ("labels" if "label" in stage else "cleaning"),
            input_rows=input_rows,
            input_bytes=input_bytes,
        )

        staging = self.storage.staging_dir(self.run.run_key, stage)
        report_path = self.storage.sandbox_dir(self.run.run_key) / f"{stage}_report.json"
        # Rejected rows are written inside the run's own folder, like the output, and moved into
        # `quarantine_dir` by the platform. Spark's overwrite deletes the folder it writes to, and
        # a job in a container cannot delete the folder its writable mount is attached to: given
        # the quarantine partition itself as a mount, the first containerised clean job failed
        # with "Unable to clear output directory" (run-2026-10-04-001).
        job_quarantine = self.storage.sandbox_dir(self.run.run_key) / "quarantine" / stage

        def generate(stage_inputs, history):
            artifact = self._generate_scala(template_id)
            job_args = [
                f"--run-key={self.run.run_key}",
                f"--output=file://{output_dir}",
                f"--staging=file://{staging}",
                f"--report={report_path}",
                f"--shuffle-partitions={stage_inputs.get('shuffle_partitions', 8)}",
            ]
            if quarantine_dir is not None:
                # Without this the job refuses to run rather than drop rejected rows
                # (OutputWriter.writeQuarantine); a stage that rejects rows must say where
                # they go (FR-018).
                job_args.append(f"--quarantine=file://{job_quarantine}")
            for name, uri in inputs.items():
                job_args.append(f"--input={name}:{uri}")
            for key, value in params.items():
                job_args.append(f"--param={key}={value}")
            if partition_by:
                job_args.append(f"--partition-by={','.join(partition_by)}")
            if stage_inputs.get("skew_handling"):
                job_args.append("--conf=spark.sql.adaptive.skewJoin.enabled=true")
            # The out-of-memory remedy has to arrive here to mean anything. `_apply_fix` sets
            # `executor_memory` when it diagnoses an OOM, and for a long while nothing read it:
            # the retry was submitted with exactly the memory that had just failed, so the
            # remedy was computed and thrown away and an OOM could only ever exhaust the
            # attempt budget. In local mode the driver *is* the executor, hence one value.
            memory = stage_inputs.get("executor_memory") or self.config.get(
                "spark", {}
            ).get("driver_memory", "1g")
            return {
                "artifact": artifact,
                "job_args": job_args,
                "command": " ".join(job_args),
                "resources": {
                    # PRD 22.4 assigns "small" to dry runs and tests and "medium" to a Spark
                    # client; the full execution ran as "small", whose 10-minute timeout a full
                    # job at a larger tier would hit. Gate 6's dry run below keeps "small".
                    "profile": stage_inputs.get("resource_profile", "medium"),
                    "master": routing.get("deploy_mode", "local[2]"),
                    "driver_memory": memory,
                },
            }

        def dry_run(artifact):
            """Gate 6: the same job and arguments, on the first N rows of each input, writing
            to a throwaway staging folder, through the same registry call as the real run."""
            cfg = self.config.get("validation", {})
            rows = int(min(
                cfg.get("dry_run_max_rows", 100_000),
                max(cfg.get("dry_run_min_rows", 1_000),
                    math.ceil(cfg.get("dry_run_fraction", 0.01) * max(input_rows, 0))),
            ))
            scratch = self.storage.staging_dir(self.run.run_key, f"{stage}-dryrun")
            dry_report = self.storage.sandbox_dir(self.run.run_key) / f"{stage}_dryrun_report.json"
            replaced = ("--output=", "--staging=", "--report=", "--quarantine=")
            job_args = [a for a in artifact["job_args"] if not a.startswith(replaced)] + [
                f"--output=file://{scratch}",
                f"--staging=file://{scratch}",
                f"--report={dry_report}",
                f"--param=dry_run_rows={rows}",
            ]
            if quarantine_dir is not None:
                job_args.append(f"--quarantine=file://{scratch}-quarantine")
            jar = _newest_jar(SCALA_ROOT / "jobs" / "target" / "scala-2.13")
            core_jar = _newest_jar(SCALA_ROOT / "core" / "target" / "scala-2.13")
            if jar is None or core_jar is None:
                return {"ok": False, "detail": "no packaged JAR to dry-run"}
            call = self._call_tool(
                "spark.submit_job",
                "code_generation",
                {
                    "run_key": self.run.run_key,
                    "stage": f"{stage}-dryrun",
                    "jar": str(jar),
                    "core_jar": str(core_jar),
                    "main_class": main_class
                    or f"triad.jobs.generated.{artifact['artifact'].object_name}",
                    "job_args": job_args,
                    "master": self.config.get("spark_master", "local[2]"),
                    "profile": "small",
                    "driver_memory": artifact["resources"]["driver_memory"],
                },
                sandbox=self.sandbox,
                policy_context={"profile": "small"},
            )
            try:
                if not call.ok:
                    return {"ok": False, "detail": f"{call.error_code}: {call.error}"}
                result = call.value
                status = None
                if dry_report.exists():
                    status = json.loads(dry_report.read_text(encoding="utf-8")).get("status")
                ok = bool(result["ok"]) and status == "succeeded"
                detail = (
                    f"completed on a {rows}-row sample in {result['duration_ms']} ms"
                    if ok
                    else _structured_error(result["stdout"] + result["stderr"])
                    or f"dry run ended with status {status!r}"
                )
                return {"ok": ok, "detail": detail, "duration_ms": result["duration_ms"]}
            finally:
                # The sample's output proves only that the job ran; it is not kept.
                for leftover in (scratch, Path(f"{scratch}-quarantine")):
                    shutil.rmtree(leftover, ignore_errors=True)

        def validate(artifact):
            return self._validate_scala(
                artifact["artifact"], template_id, dry_run=lambda: dry_run(artifact)
            )

        def execute(artifact):
            jar = _newest_jar(SCALA_ROOT / "jobs" / "target" / "scala-2.13")
            core_jar = _newest_jar(SCALA_ROOT / "core" / "target" / "scala-2.13")
            if jar is None or core_jar is None:
                return {"ok": False, "error": "no packaged JAR found; run `sbt core/package jobs/package`"}

            # The class to submit is the object this attempt actually generated, so a
            # repaired artifact is the one that runs.
            generated_class = f"triad.jobs.generated.{artifact['artifact'].object_name}"
            # Through the registry, not straight to the sandbox: the call is checked against
            # this agent's permitted tools and against the `sandbox.run` policy action, and
            # is audited either way. The sandbox itself is handed over as context, so no
            # generated argument can substitute a different runner.
            call = self._call_tool(
                "spark.submit_job",
                "code_generation",
                {
                    "run_key": self.run.run_key,
                    "stage": stage,
                    "jar": str(jar),
                    "core_jar": str(core_jar),
                    "main_class": main_class or generated_class,
                    "job_args": artifact["job_args"],
                    "master": self.config.get("spark_master", "local[2]"),
                    "profile": artifact["resources"]["profile"],
                    "driver_memory": artifact["resources"]["driver_memory"],
                },
                sandbox=self.sandbox,
                # Only the profile varies here. The `internal_only` network limit is
                # enforced by SandboxRequest.validate, which every submission passes
                # through, so there is nothing for the policy to compare.
                policy_context={"profile": artifact["resources"]["profile"]},
            )
            if not call.ok:
                # A refused tool call is a stage failure, not a silent skip.
                return {"ok": False, "error": f"{call.error_code}: {call.error}", "outputs": {}}
            result = call.value
            payload: dict[str, Any] = {
                "ok": result["ok"],
                "exit_code": result["exit_code"],
                "duration_ms": result["duration_ms"],
                "image_digest": result["image_digest"],
                "error": (
                    _structured_error(result["stdout"] + result["stderr"])
                    if not result["ok"]
                    else ""
                ),
                "outputs": {},
            }
            if report_path.exists():
                report = json.loads(report_path.read_text(encoding="utf-8"))
                payload["outputs"] = {
                    "staging": str(staging),
                    "row_count": report.get("row_count"),
                    "schema": report.get("schema", {}),
                    "job_metrics": report.get("metrics", {}),
                }
                payload["metrics"] = {**report.get("metrics", {}),
                                      **spark_stage_summary(report.get("spark_stages"))}
                payload["spark_app_id"] = report.get("spark_app_id")
                if report.get("status") != "succeeded":
                    payload["ok"] = False
                    payload["error"] = json.dumps(report.get("error", {}))
            return payload

        def contract_check(execution):
            from triad_core.contracts import OutputContract

            outputs = execution.get("outputs", {})
            columns = list((outputs.get("schema") or {}).keys())
            rows = int(outputs.get("row_count") or 0)
            contract = OutputContract(
                name=stage,
                grain=["customer_id", "snapshot_date"],
                required_columns=required_columns or [],
            )
            return contract.evaluate(
                total_rows=rows,
                distinct_grain_rows=rows,   # the job already proved grain uniqueness
                null_counts={},
                out_of_range_counts={},
                columns=columns,
            )

        partitions, planned = self._plan_shuffle_partitions(stage)
        outcome = self.runner.run_stage(
            StageRequest(
                stage=stage,
                agent_name="code_generation",
                # The memory the first attempt runs with, so a memory remedy raises it from
                # what actually failed rather than from a default.
                inputs={"shuffle_partitions": partitions,
                        "executor_memory": self.config.get("spark", {}).get("driver_memory",
                                                                            "1g")},
                generate=generate,
                validate=validate,
                execute=execute,
                contract_check=contract_check,
                engine="scala_spark",
                max_attempts=int(self.config.get("budgets", {}).get("max_code_repair_attempts", 3)),
            )
        )
        self.result.stages.append(outcome.to_dict())
        self._append_execution_gates(outcome)

        # Before the policy check, so a stage held for rejecting too many rows leaves those rows
        # where the person deciding will look for them.
        if outcome.ok and quarantine_dir is not None and job_quarantine.exists():
            if quarantine_dir.exists():
                shutil.rmtree(quarantine_dir)
            quarantine_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(job_quarantine), str(quarantine_dir))

        if outcome.ok and policy_action:
            verdict = self._check_stage_policy(policy_action, stage, outcome.outputs,
                                               quarantine_dir)
            if verdict is not None:
                self.result.escalation = verdict
                return {}

        if outcome.ok:
            commit = self._call_tool(
                "storage.commit_output",
                "code_generation",
                {
                    "staging": str(staging),
                    "target": str(output_dir),
                    "manifest": {
                        "kind": stage,
                        "name": stage,
                        "version": self.run.run_key,
                        "created_at": utcnow().isoformat(),
                        "run_id": self.run.run_key,
                        "row_count": outcome.outputs.get("row_count"),
                        "schema": outcome.outputs.get("schema", {}),
                    },
                },
                store=self.storage,
            )
            if not commit.ok:
                self.result.escalation = {
                    "stage": stage,
                    "reason": f"publishing the output was refused: {commit.error}",
                    "diagnoses": [],
                }
                return {}
            outcome.outputs["uri"] = commit.value["uri"]
            self._record_setting_outcome(stage, planned)
            self._persist_budget()
            return outcome.outputs

        self.result.escalation = {"stage": stage, "reason": outcome.reason,
                                  "diagnoses": outcome.diagnoses}
        return {}

    # -- configuration optimisation (FR-037) ------------------------------------------------
    def _plan_shuffle_partitions(self, stage: str) -> tuple[int, dict[str, Any] | None]:
        """This stage's shuffle partitions, and the change behind them if there is one.

        The value the last measured run of this pipeline used for this stage, changed when what
        that run measured calls for it (`agents.optimizer.suggest`). A replay takes the value the
        replayed run used, unchanged: it exists to reproduce a run, not to improve on it.
        """
        from agents.optimizer import DEFAULT_PARTITIONS, measured, suggest
        from triad_core.models import Execution

        query = select(Execution).where(Execution.stage == stage,
                                        Execution.status == "succeeded")
        if self.replay_of is not None:
            query = query.where(Execution.run_id == self.replay_of.id)
        else:
            query = query.join(PipelineRun, PipelineRun.id == Execution.run_id).where(
                PipelineRun.pipeline_name == self.run.pipeline_name,
                Execution.run_id != self.run.id,
                Execution.metrics_json.has_key("spark"),
            )
        previous = self.session.execute(
            query.order_by(Execution.finished_at.desc().nulls_last())
        ).scalars().first()
        if previous is None:
            return DEFAULT_PARTITIONS, None
        before = measured(previous)
        if self.replay_of is not None:
            return before["shuffle_partitions"], None
        change = suggest((previous.metrics_json or {}).get("spark"), before["shuffle_partitions"])
        if change is None:
            return before["shuffle_partitions"], None
        self.runner.record_decision(
            agent="optimization",
            decision_type="spark_setting",
            decision=f"{stage}: {change.setting} {change.before} -> {change.after}",
            summary=change.reason,
            evidence={"stage": stage, "change": change.to_dict(), "before": before},
            policy_rules=[change.rule, "FR-037"],
        )
        return change.after, {"change": change, "before": before}

    def _record_setting_outcome(self, stage: str, planned: dict[str, Any] | None) -> None:
        """The after half of a setting change's before-and-after record (PRD section 30)."""
        if planned is None:
            return
        from agents.optimizer import compare, measured
        from triad_core.models import Execution

        execution = self.session.execute(
            select(Execution).where(Execution.run_id == self.run.id, Execution.stage == stage,
                                    Execution.status == "succeeded")
            .order_by(Execution.finished_at.desc().nulls_last())
        ).scalars().first()
        if execution is None:
            return
        change, after = planned["change"], measured(execution)
        comparison = compare(planned["before"], after)
        self.runner.record_decision(
            agent="optimization",
            decision_type="spark_setting_outcome",
            decision=(f"{stage}: {change.setting} {change.before} -> {change.after}: "
                      f"{comparison['verdict']}"),
            summary=("Before and after, one run each: a difference inside the noise band is "
                     "not evidence that the setting mattered."),
            evidence={"stage": stage, "change": change.to_dict(), "before": planned["before"],
                      "after": after, "comparison": comparison},
            policy_rules=[change.rule, "FR-037"],
            outcome="measured",
        )

    def _check_stage_policy(
        self, action: str, stage: str, outputs: dict[str, Any], quarantine_dir: Path | None
    ) -> dict[str, Any] | None:
        """Hold the executed stage against its bounded-autonomy limits (PRD section 51).

        The job already ran in the sandbox; what is gated here is *committing* its output.
        Returns None when the action is allowed, otherwise the escalation payload. Over a
        limit that needs approval, the stage files the request and the payload carries its
        id: the run pauses rather than fails (PRD US-06), and a run resumed after a person
        approved it finds the approval here and commits.
        """
        has_quarantine = quarantine_dir is not None
        metrics = outputs.get("job_metrics") or {}
        input_rows = int(metrics.get("input_rows") or 0)
        affected = int(metrics.get("quarantined_rows") or 0) + int(
            metrics.get("removed_duplicates") or 0
        )
        context: dict[str, Any] = {"quarantine": has_quarantine}
        if input_rows:
            context["rows_affected_pct"] = round(100.0 * affected / input_rows, 4)

        decision = self.policy.check(action, context=context)
        target = f"{stage}@{self.run.run_key}"
        approved = (
            self._stage_approval(action, target, "approved")
            if decision.verdict is Verdict.REQUIRE_APPROVAL else None
        )
        self.runner.record_decision(
            agent="governance",
            decision_type="policy",
            decision=f"{action} -> {decision.verdict.value}"
                     + (f"; approved by a person ({approved.id})" if approved else ""),
            summary=decision.reason[:500],
            evidence={"stage": stage, **context},
            policy_rules=decision.rule_ids,
        )
        if decision.verdict is Verdict.ALLOW or approved is not None:
            return None
        payload = {
            "stage": stage,
            "reason": f"{action}: {decision.reason}",
            "verdict": decision.verdict.value,
            "approvers": decision.approvers,
            "evidence": context,
        }
        if decision.verdict is Verdict.REQUIRE_APPROVAL:
            payload["approval_id"] = self._request_stage_approval(
                action, target, context, metrics, quarantine_dir
            )
        return payload

    def _stage_approval(self, action: str, target: str, status: str):
        from triad_core.models import Approval

        return self.session.execute(
            select(Approval).where(
                Approval.run_id == self.run.id, Approval.policy_action == action,
                Approval.target_ref == target, Approval.status == status,
            )
        ).scalars().first()

    def _request_stage_approval(
        self, action: str, target: str, context: dict[str, Any], metrics: dict[str, Any],
        quarantine_dir: Path | None,
    ) -> str:
        """File the approval a stage over its limit needs, once per run and stage."""
        pending = self._stage_approval(action, target, "pending")
        if pending is not None:
            return str(pending.id)
        evidence = EvidenceBundle(
            action_type=action,
            target_ref=target,
            impact_estimate={
                **context,
                **{k: metrics.get(k) for k in ("input_rows", "quarantined_rows",
                                               "removed_duplicates")},
                "by_rule": {k.removeprefix("rule_"): v for k, v in metrics.items()
                            if k.startswith("rule_")},
            },
            risk_notes=[
                f"{context.get('rows_affected_pct')}% of rows would leave the "
                f"{target.split('@')[0]} output, above the share the policy allows without "
                "approval. Every one is kept in quarantine with its reason, and nothing is "
                "committed before a decision.",
            ],
        )
        uri = self.storage.write_json(
            self.storage.reports_dir(self.run.run_key)
            / f"{target.split('@')[0]}_approval_evidence_{uuid.uuid4().hex[:12]}.json",
            {**evidence.to_dict(), "sample_rows": _sample_rows(quarantine_dir)},
        )
        approval = self.governance.request_approval(
            self.run, action=action, target_ref=target,
            requested_by="agent:data_engineering", evidence=evidence,
            evidence_uri=self.storage.uri(uri),
        )
        return str(approval.id)

    # -- governance ------------------------------------------------------------
    def request_promotion(
        self, *, model_ref: str, evidence: EvidenceBundle, requested_by: str = "agent:evaluation_agent"
    ) -> str | None:
        uri = self.storage.write_json(
            self.storage.reports_dir(self.run.run_key) / "promotion_evidence.json",
            evidence.to_dict(),
        )
        approval = self.governance.request_approval(
            self.run,
            action="model.promote",
            target_ref=model_ref,
            requested_by=requested_by,
            evidence=evidence,
            evidence_uri=self.storage.uri(uri),
        )
        self._advance("APPROVAL_REQUIRED", stage="approval")
        self.result.approval_id = str(approval.id)
        return str(approval.id)

    def finish(self, state: str, *, ok: bool, reason: str = "") -> PipelineResult:
        self._persist_budget()
        self._advance(state, reason=reason or None)
        self.result.ok = ok
        self.result.reason = reason
        audit.record(
            self.session,
            actor_type="system",
            actor_id="orchestrator",
            action="run.finished",
            resource_type="pipeline_run",
            resource_id=str(self.run.id),
            run_id=self.run.id,
            outcome=state,
            details={"ok": ok, "reason": reason[:500], "budget": self.budget.to_dict()},
        )
        self.session.commit()
        return self.result


def _sample_rows(folder: Path | None, n: int = 5) -> list[dict[str, Any]]:
    """A few quarantined rows with their reasons, for the approver; empty if unreadable."""
    if folder is None or not folder.exists():
        return []
    try:
        import pyarrow.dataset as ds

        rows = ds.dataset(str(folder), format="parquet").head(n).to_pylist()
    except Exception:  # a sample is a courtesy; the request must still be filed
        return []
    plain = (str, int, float, bool, type(None))
    return [{k: v if isinstance(v, plain) else str(v) for k, v in row.items()} for row in rows]


def _newest_jar(folder: Path) -> Path | None:
    if not folder.exists():
        return None
    jars = sorted(folder.glob("*.jar"), key=lambda p: p.stat().st_mtime)
    return jars[-1] if jars else None


_SUMMED = ("duration_ms", "executor_run_time_ms", "jvm_gc_time_ms", "input_bytes", "output_bytes",
           "shuffle_read_bytes", "shuffle_write_bytes", "memory_spill_bytes", "disk_spill_bytes")


def spark_stage_summary(stages: list[dict[str, Any]] | None) -> dict[str, Any]:
    """FR-036: every stage the job ran, as Spark's listener measured it, plus the totals.

    Nested under one `spark` key so the job's own metrics - row accounting, feature
    availability, read by name elsewhere - stay flat beside it. `max_task_skew` is the worst
    stage's slowest task over its median task; spill of any size means a stage ran out of
    execution memory and wrote to disk.
    """
    if not stages:
        return {}
    totals: dict[str, Any] = {key: sum(int(st.get(key) or 0) for st in stages) for key in _SUMMED}
    totals |= {
        "stages": len(stages),
        "tasks": sum(int(st.get("num_tasks") or 0) for st in stages),
        "failed_stages": sum(1 for st in stages if st.get("failed")),
        "max_task_skew": max(float(st.get("task_skew") or 1.0) for st in stages),
    }
    return {"spark": {"totals": totals, "stages": stages}}


def _structured_error(output: str) -> str:
    """Prefer the job's own structured error line over raw log text (FR-043).

    Without one - the JVM failed before the job could write it - the exception lines go first
    and the tail after them. The tail alone is the bottom of a Java trace: on run-2026-10-04-002
    it held frames and not the `OutOfMemoryError: unable to create native thread` at the top, so
    the failure was classified UNKNOWN.
    """
    lines = output.splitlines()
    for line in lines:
        if line.startswith("TRIAD_JOB_ERROR "):
            return line[len("TRIAD_JOB_ERROR ") :]
    headers = [line for line in lines
               if line.startswith(("Exception in thread", "Caused by:"))
               or re.match(r"[\w.$]+(Exception|Error)(:|$)", line)]
    tail = output[-4000:]
    return "\n".join([*headers[-10:], "...", tail]) if headers else tail
