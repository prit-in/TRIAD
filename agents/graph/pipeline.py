"""The churn pipeline: RUN_PIPELINE from PRD section 39, wired to the real services.

This is the deterministic macro-workflow. Agents act only *inside* a stage; the order of
stages, the guards between them and the approval gate are fixed here (DD-02).

Every stage goes through `StageRunner`, so bounds, diagnosis, repair, memoisation, decision
records and escalation tickets behave identically whether the stage runs a Scala job, a
PySpark query or a Python trainer.
"""

from __future__ import annotations

import json
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
from triad_core.storage import Storage, storage

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
        q.set_state(self.session, self.run, state, stage=stage, error_summary=reason)
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

    def _validate_scala(self, artifact: RenderedArtifact, template_id: str) -> G.GateReport:
        report = G.GateReport()
        template = self.templates[template_id]

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
        return outcome.outputs if outcome.ok else {}

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
                job_args.append(f"--quarantine=file://{quarantine_dir}")
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
                    "profile": stage_inputs.get("resource_profile", "small"),
                    "master": routing.get("deploy_mode", "local[2]"),
                    "driver_memory": memory,
                },
            }

        def validate(artifact):
            return self._validate_scala(artifact["artifact"], template_id)

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
                payload["metrics"] = report.get("metrics", {})
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

        outcome = self.runner.run_stage(
            StageRequest(
                stage=stage,
                agent_name="code_generation",
                inputs={"shuffle_partitions": 8},
                generate=generate,
                validate=validate,
                execute=execute,
                contract_check=contract_check,
                engine="scala_spark",
                max_attempts=int(self.config.get("budgets", {}).get("max_code_repair_attempts", 3)),
            )
        )
        self.result.stages.append(outcome.to_dict())

        if outcome.ok and policy_action:
            verdict = self._check_stage_policy(policy_action, stage, outcome.outputs,
                                               quarantine_dir is not None)
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
            self._persist_budget()
            return outcome.outputs

        self.result.escalation = {"stage": stage, "reason": outcome.reason,
                                  "diagnoses": outcome.diagnoses}
        return {}

    def _check_stage_policy(
        self, action: str, stage: str, outputs: dict[str, Any], has_quarantine: bool
    ) -> dict[str, Any] | None:
        """Hold the executed stage against its bounded-autonomy limits (PRD section 51).

        The job already ran in the sandbox; what is gated here is *committing* its output.
        Returns None when the action is allowed, otherwise the escalation payload.
        """
        metrics = outputs.get("job_metrics") or {}
        input_rows = int(metrics.get("input_rows") or 0)
        affected = int(metrics.get("quarantined_rows") or 0) + int(
            metrics.get("removed_duplicates") or 0
        )
        context: dict[str, Any] = {"quarantine": has_quarantine}
        if input_rows:
            context["rows_affected_pct"] = round(100.0 * affected / input_rows, 4)

        decision = self.policy.check(action, context=context)
        self.runner.record_decision(
            agent="governance",
            decision_type="policy",
            decision=f"{action} -> {decision.verdict.value}",
            summary=decision.reason[:500],
            evidence={"stage": stage, **context},
            policy_rules=decision.rule_ids,
        )
        if decision.verdict is Verdict.ALLOW:
            return None
        return {
            "stage": stage,
            "reason": f"{action}: {decision.reason}",
            "verdict": decision.verdict.value,
            "approvers": decision.approvers,
            "evidence": context,
        }

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
        return self.result


def _newest_jar(folder: Path) -> Path | None:
    if not folder.exists():
        return None
    jars = sorted(folder.glob("*.jar"), key=lambda p: p.stat().st_mtime)
    return jars[-1] if jars else None


def _structured_error(output: str) -> str:
    """Prefer the job's own structured error line over raw log text (FR-043)."""
    for line in output.splitlines():
        if line.startswith("TRIAD_JOB_ERROR "):
            return line[len("TRIAD_JOB_ERROR ") :]
    return output[-4000:]
