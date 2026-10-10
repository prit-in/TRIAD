"""The STAGE procedure from PRD section 39, implemented once and reused by every stage.

    for attempt in 1..max:
        CHECK_BUDGETS
        artifact <- GENERATE_OR_REUSE        (memoised by input, code and config hash)
        verdict  <- VALIDATION_GATES
        if passed: EXECUTE_IN_SANDBOX; if contract ok: COMMIT and return
        diagnosis <- CLASSIFY (taxonomy first, LLM only for unmatched errors)
        RECORD_DECISION
        if non-recoverable or repeated fingerprint: break
        input <- APPLY_FIX
    ESCALATE

Everything that bounds the loop — attempts, budgets, the fingerprint circuit breaker — lives
here in the orchestrator, never in an agent (FR-058, NFR-REL-05).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from agents.llm.budget import BudgetExceeded, RunBudget
from triad_core import audit
from triad_core.errors import Diagnosis, ErrorClass, Remedy, classify
from triad_core.ids import sha256_obj, utcnow
from triad_core.models import DecisionRecord, Execution, FixMemory, PipelineRun


@dataclass
class StageOutcome:
    ok: bool
    stage: str
    outputs: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    escalated: bool = False
    reason: str = ""
    diagnoses: list[dict[str, Any]] = field(default_factory=list)
    gate_reports: list[dict[str, Any]] = field(default_factory=list)
    applied_fixes: list[dict[str, Any]] = field(default_factory=list)
    reused: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "stage": self.stage,
            "outputs": self.outputs,
            "attempts": self.attempts,
            "escalated": self.escalated,
            "reason": self.reason,
            "diagnoses": self.diagnoses,
            "gate_reports": self.gate_reports,
            "applied_fixes": self.applied_fixes,
            "reused": self.reused,
        }


@dataclass
class StageRequest:
    """What a stage needs to run. `generate`, `validate` and `execute` are injected so the
    same loop drives a Scala job, a PySpark profiling query or a Python training script."""

    stage: str
    agent_name: str
    inputs: dict[str, Any]
    generate: Callable[[dict[str, Any], list[Diagnosis]], dict[str, Any]]
    validate: Callable[[dict[str, Any]], Any]           # -> GateReport
    execute: Callable[[dict[str, Any]], dict[str, Any]]  # -> {"ok", "outputs", "error", ...}
    contract_check: Callable[[dict[str, Any]], Any] | None = None
    max_attempts: int = 3
    memoisable: bool = True
    engine: str = "python"


class StageRunner:
    def __init__(
        self,
        session: Session,
        run: PipelineRun,
        budget: RunBudget,
        *,
        memo: dict[str, dict[str, Any]] | None = None,
        use_fix_memory: bool = True,
        memory_check: Callable[[str], tuple[bool, str]] | None = None,
    ) -> None:
        self.session = session
        self.run = run
        self.budget = budget
        self.memo = memo if memo is not None else {}
        self.use_fix_memory = use_fix_memory
        # Whether a memory remedy may set this value: a Spark setting change like any other,
        # held against the governance allowlist by the pipeline (LR-10, DEC-20). None permits.
        self.memory_check = memory_check

    # -- memoisation (FR-083) --------------------------------------------------
    def _memo_key(self, request: StageRequest) -> str:
        return sha256_obj(
            {
                "stage": request.stage,
                "inputs": request.inputs,
                "config_hash": self.run.config_hash,
                "pipeline": self.run.pipeline_name,
            }
        )

    # -- decision records (FR-060) --------------------------------------------
    def record_decision(
        self,
        *,
        agent: str,
        decision_type: str,
        decision: str,
        summary: str,
        alternatives: list[Any] | None = None,
        evidence: dict[str, Any] | None = None,
        policy_rules: list[str] | None = None,
        outcome: str = "applied",
    ) -> DecisionRecord:
        record = DecisionRecord(
            run_id=self.run.id,
            agent=agent,
            decision_type=decision_type,
            decision=decision[:2000],
            alternatives=alternatives or [],
            evidence_refs=evidence or {},
            policy_rule_ids=policy_rules or [],
            summary=summary[:500],
            outcome=outcome,
        )
        self.session.add(record)
        self.session.flush()
        return record

    # -- fix memory (FR-062) ---------------------------------------------------
    def lookup_fix(self, fingerprint: str) -> FixMemory | None:
        if not self.use_fix_memory:
            return None
        from sqlalchemy import select

        return self.session.execute(
            select(FixMemory)
            .where(FixMemory.error_fingerprint == fingerprint)
            .order_by(FixMemory.success_count.desc())
            .limit(1)
        ).scalar_one_or_none()

    def remember_fix(self, diagnosis: Diagnosis, fix: dict[str, Any]) -> None:
        if not self.use_fix_memory:
            return
        existing = self.lookup_fix(diagnosis.fingerprint)
        if existing is not None:
            existing.success_count += 1
            existing.last_used_at = utcnow()
        else:
            self.session.add(
                FixMemory(
                    error_fingerprint=diagnosis.fingerprint,
                    error_class=diagnosis.error_class.value,
                    fix_kind="playbook" if diagnosis.remedy is not Remedy.REGENERATE_CODE
                    else "code_diff",
                    fix_payload=fix,
                )
            )
        self.session.flush()

    # -- the loop ---------------------------------------------------------------
    def run_stage(self, request: StageRequest) -> StageOutcome:
        outcome = StageOutcome(ok=False, stage=request.stage)
        history: list[Diagnosis] = []
        inputs = dict(request.inputs)

        key = self._memo_key(request)
        if request.memoisable and key in self.memo:
            cached = self.memo[key]
            outcome.ok = True
            outcome.outputs = cached
            outcome.reused = True
            self.record_decision(
                agent=request.agent_name,
                decision_type="memoisation",
                decision=f"reuse stored output for {request.stage}",
                summary="input, code and configuration hashes match a previous run",
                evidence={"memo_key": key[:16]},
                policy_rules=["FR-083"],
            )
            return outcome

        for attempt in range(1, request.max_attempts + 1):
            outcome.attempts = attempt
            try:
                self.budget.check_all()
            except BudgetExceeded as exc:
                return self._escalate(
                    request, outcome, f"budget exceeded: {exc}", extra=exc.to_dict()
                )

            artifact = request.generate(inputs, history)

            gate_report = request.validate(artifact)
            outcome.gate_reports.append(
                gate_report.to_dict() if hasattr(gate_report, "to_dict") else dict(gate_report)
            )
            if not getattr(gate_report, "passed", False):
                failure = getattr(gate_report, "first_failure", None)
                detail = getattr(failure, "detail", "validation failed")
                hint = getattr(failure, "error_class", None)
                diagnosis = classify(detail, hint=hint)
                should_stop = self._after_failure(request, outcome, diagnosis, history)
                if should_stop:
                    return outcome
                inputs = self._apply_fix(inputs, diagnosis)
                outcome.applied_fixes = list(inputs.get("_applied_fixes") or [])
                continue

            execution = self._execute(request, artifact)
            if execution.get("ok"):
                contract_ok = True
                result = None
                if request.contract_check is not None:
                    result = request.contract_check(execution)
                    contract_ok = bool(getattr(result, "passed", result))
                    if not contract_ok:
                        violations = getattr(result, "violations", [])
                        execution["error"] = "contract check failed: " + "; ".join(
                            v.message for v in violations
                        )
                # Gates 7 and 9 record the two checks just made: the output satisfies its
                # contract, and the full run completed with that contract still passing. Both
                # were already deciding whether the stage succeeded; neither was written down
                # as a gate, which is why every recorded gate report stopped at four.
                self._record_execution_gates(outcome, execution, result)
                if contract_ok:
                    outcome.ok = True
                    outcome.outputs = execution.get("outputs", {})
                    if request.memoisable:
                        self.memo[key] = outcome.outputs
                    return outcome

            diagnosis = classify(
                execution.get("error", "") or execution.get("detail", "unknown failure")
            )
            should_stop = self._after_failure(request, outcome, diagnosis, history)
            if should_stop:
                return outcome
            inputs = self._apply_fix(inputs, diagnosis)
            outcome.applied_fixes = list(inputs.get("_applied_fixes") or [])

        return self._escalate(
            request, outcome, f"attempt budget of {request.max_attempts} exhausted"
        )

    # -- helpers ----------------------------------------------------------------
    def _record_execution_gates(
        self, outcome: StageOutcome, execution: dict[str, Any], contract_result: Any
    ) -> None:
        """Append gates 7 and 9 to this stage's gate reports.

        Gates 5 and 6 are deliberately **not** recorded here, because they do not run: gate 5
        is the Scala fixture suite, which gate 4's compile already shares a toolchain with and
        which would add minutes to every stage, and gate 6 is a sandbox dry run on a sample,
        which this pipeline does not perform - it runs the job once, on the whole input. Both
        absences are stated in the documentation rather than papered over with a gate that
        reports `True` for something nobody checked.
        """
        from agents.codegen import gates as G

        if contract_result is None:
            # A stage with no contract to check gets neither gate. Recording gate 7 against a
            # missing contract result reports a *failed* contract check, which would be a
            # fabricated failure for a stage that was never asked to satisfy one - profiling
            # and the Python hand-off are both in that position.
            return
        report = G.GateReport(
            [G.gate7_contract(contract_result), G.gate9_execute(execution, contract_result)]
        )
        outcome.gate_reports.append(report.to_dict())

    def _execute(self, request: StageRequest, artifact: dict[str, Any]) -> dict[str, Any]:
        execution = Execution(
            run_id=self.run.id,
            stage=request.stage,
            engine=request.engine,
            command=str(artifact.get("command", ""))[:2000],
            resources_json=artifact.get("resources", {}),
            inputs_json={k: str(v)[:500] for k, v in request.inputs.items()},
            status="running",
        )
        self.session.add(execution)
        self.session.flush()

        result = request.execute(artifact)

        execution.status = "succeeded" if result.get("ok") else "failed"
        execution.exit_code = result.get("exit_code")
        execution.finished_at = utcnow()
        execution.duration_ms = result.get("duration_ms")
        execution.metrics_json = result.get("metrics", {})
        execution.outputs_json = {k: str(v)[:500] for k, v in (result.get("outputs") or {}).items()}
        execution.spark_app_id = result.get("spark_app_id")
        execution.image_digest = result.get("image_digest")
        execution.log_uri = result.get("log_uri")
        self.session.flush()
        result["execution_id"] = str(execution.id)
        return result

    def _after_failure(
        self,
        request: StageRequest,
        outcome: StageOutcome,
        diagnosis: Diagnosis,
        history: list[Diagnosis],
    ) -> bool:
        """Record the failure and decide whether the loop stops. True means stop."""
        history.append(diagnosis)
        outcome.diagnoses.append(diagnosis.to_dict())

        remembered = self.lookup_fix(diagnosis.fingerprint)
        self.record_decision(
            agent=request.agent_name,
            decision_type="diagnosis",
            decision=f"{diagnosis.error_class.value}: remedy {diagnosis.remedy.value}",
            summary=(
                f"classified deterministically by the taxonomy; "
                f"fingerprint {diagnosis.fingerprint[:12]}"
                + (" (known fix reused)" if remembered else "")
            ),
            evidence={"fingerprint": diagnosis.fingerprint,
                      "message": diagnosis.message[:1000],
                      "matched_by": diagnosis.matched_by,
                      "known_fix": bool(remembered)},
            policy_rules=["section 20.2"],
            outcome="diagnosed",
        )
        audit.record(
            self.session,
            actor_type="agent",
            actor_id=request.agent_name,
            action="stage.failed",
            resource_type="stage",
            resource_id=request.stage,
            run_id=self.run.id,
            outcome=diagnosis.error_class.value,
            details={"fingerprint": diagnosis.fingerprint, "attempt": outcome.attempts},
        )

        if diagnosis.non_recoverable:
            self._escalate(request, outcome,
                           f"non-recoverable: {diagnosis.error_class.value}")
            return True

        # Circuit breaker: the same normalised error twice in a row stops the loop (ATC-10).
        try:
            self.budget.record_fingerprint(diagnosis.fingerprint)
        except BudgetExceeded as exc:
            self._escalate(
                request, outcome,
                f"circuit breaker: error fingerprint {diagnosis.fingerprint[:12]} repeated",
                extra=exc.to_dict(),
            )
            return True

        try:
            self.budget.next_repair_attempt(request.stage)
        except BudgetExceeded as exc:
            self._escalate(request, outcome, f"repair budget exhausted: {exc}",
                           extra=exc.to_dict())
            return True

        self.remember_fix(diagnosis, {"remedy": diagnosis.remedy.value})
        return False

    # Remedies whose effect actually reaches a stage. A remedy outside this set changes an
    # input that no `generate` reads, so the next attempt submits identical work and fails
    # identically. That used to happen silently: the escalation ticket said "attempt budget
    # exhausted" and gave no hint that the prescribed fix had never been applied to anything.
    # Naming them here lets the ticket say so - and makes the set easy to check against
    # `ChurnPipeline.stage_scala_job`, which is what has to honour them.
    APPLIED_REMEDIES = frozenset({
        Remedy.RAISE_SHUFFLE_PARTITIONS,
        Remedy.RAISE_EXECUTOR_MEMORY,
        Remedy.ENABLE_SKEW_HANDLING,
        Remedy.OPTIMISED_RETRY,
        Remedy.RESUBMIT,
    })

    def _apply_fix(self, inputs: dict[str, Any], diagnosis: Diagnosis) -> dict[str, Any]:
        """Playbook action first; code regeneration is driven by `history` in `generate`."""
        out = dict(inputs)
        fixes = out.setdefault("_applied_fixes", [])
        remedy = diagnosis.remedy

        memory_before = out.get("executor_memory", "1g")
        if remedy is Remedy.RAISE_SHUFFLE_PARTITIONS:
            current = int(out.get("shuffle_partitions", 200))
            out["shuffle_partitions"] = min(current * 2, 2000)
            if diagnosis.error_class is ErrorClass.EXECUTOR_OOM:
                # PRD 20.2: "raise shuffle partitions; raise executor memory within quota".
                # Smaller partitions alone cannot help a heap too small for one task.
                out["executor_memory"] = _bump_memory(memory_before)
        elif remedy is Remedy.RAISE_EXECUTOR_MEMORY:
            out["executor_memory"] = _bump_memory(memory_before)
        elif remedy is Remedy.ENABLE_SKEW_HANDLING:
            out["skew_handling"] = True
        elif remedy is Remedy.TRY_CAST_WITH_QUARANTINE:
            out["use_try_cast"] = True
        elif remedy is Remedy.QUARANTINE_AND_CONTINUE:
            out["quarantine_bad_rows"] = True
        elif remedy is Remedy.DISABLE_DEPENDENT_FEATURES:
            out["disable_missing_feature_groups"] = True
        elif remedy is Remedy.OPTIMISED_RETRY:
            out["shuffle_partitions"] = int(out.get("shuffle_partitions", 200)) * 2

        refused = ""
        if out.get("executor_memory", memory_before) != memory_before and self.memory_check:
            permitted, why = self.memory_check(out["executor_memory"])
            if not permitted:
                refused = f"memory not raised to {out['executor_memory']}: {why}"
                out["executor_memory"] = memory_before

        applied = remedy in self.APPLIED_REMEDIES
        note = "" if applied else (
            "no remedy is prescribed for this error, so the next attempt is the same work"
            if remedy is Remedy.NONE else
            f"{remedy.value} needs the generated code rewritten, which requires an LLM "
            "provider; no input the stage reads was changed, so the next attempt is identical"
        )
        if remedy is Remedy.RAISE_EXECUTOR_MEMORY and out["executor_memory"] == memory_before:
            # PRD 20.2's stop condition: "quota would be exceeded" - or a raise the policy
            # leaves to a person.
            applied = False
            note = refused or (f"executor memory is already {memory_before}, the most the "
                               "sandbox can hold; raising it further needs a person")
        elif "executor_memory" in out and out["executor_memory"] != memory_before:
            note = f"executor memory {memory_before} -> {out['executor_memory']}"
        elif refused:
            note = (f"shuffle partitions {inputs.get('shuffle_partitions', 200)} -> "
                    f"{out.get('shuffle_partitions')}; {refused}")
        fixes.append({
            "remedy": remedy.value,
            "error_class": diagnosis.error_class.value,
            "applied": applied,
            # Said plainly, because an escalation that hides this is the reason a reader
            # concludes the repair loop is broken when the truth is narrower.
            "note": note,
        })
        return out

    def _escalate(
        self,
        request: StageRequest,
        outcome: StageOutcome,
        reason: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> StageOutcome:
        """Build the escalation ticket described in PRD section 20.3."""
        outcome.ok = False
        outcome.escalated = True
        outcome.reason = reason

        ticket = {
            "run_key": self.run.run_key,
            "stage": request.stage,
            "agent": request.agent_name,
            "attempts": outcome.attempts,
            "reason": reason,
            "diagnoses": outcome.diagnoses,
            "gate_reports": outcome.gate_reports[-3:],
            "budget": self.budget.to_dict(),
            "suggested_actions": _suggested_actions(outcome.diagnoses, reason),
            "unapplied_remedies": sorted({
                f["remedy"] for f in (outcome.applied_fixes or []) if not f.get("applied", True)
            }),
            "raised_at": utcnow().isoformat(),
            **(extra or {}),
        }
        self.record_decision(
            agent=request.agent_name,
            decision_type="escalation",
            decision=f"escalate {request.stage}",
            summary=reason[:500],
            evidence=ticket,
            policy_rules=["section 20.3"],
            outcome="escalated",
        )
        audit.record(
            self.session,
            actor_type="system",
            actor_id="orchestrator",
            action="stage.escalated",
            resource_type="stage",
            resource_id=request.stage,
            run_id=self.run.id,
            outcome="escalated",
            details={"reason": reason[:500], "attempts": outcome.attempts},
        )
        return outcome


# The largest heap a sandbox can hold. Its container is clamped to the host's memory limit,
# and a JVM needs room beyond the heap - metaspace, threads, off-heap buffers - so the heap
# stays under 70% of that. A fixed 4g used to be the ceiling, above this host's 3 GB clamp.
def _max_heap_mb() -> int:
    from sandbox.runner import HOST_LIMITS

    return int(HOST_LIMITS["memory_gb"] * 1024 * 0.7)


def _bump_memory(value: str) -> str:
    """Double a JVM memory setting ("512m", "1g"), within what the sandbox can hold.

    It read only gigabytes: "512m" came back unchanged, so a stage started below a gigabyte
    could never be given more.
    """
    match = re.fullmatch(r"(\d+)([mg])", str(value).strip().lower())
    if not match:
        return value
    mb = int(match[1]) * (1024 if match[2] == "g" else 1)
    return f"{max(mb, min(mb * 2, _max_heap_mb()))}m"


def _suggested_actions(diagnoses: list[dict[str, Any]], reason: str) -> list[str]:
    if not diagnoses:
        return [f"Review the stage inputs and rerun; the loop stopped because: {reason}"]
    last = diagnoses[-1]
    cls = last.get("error_class")
    table = {
        ErrorClass.MISSING_REQUIRED_COLUMN.value: [
            "Confirm the upstream schema change with the dataset owner",
            "Approve a column mapping, or fix the source extract",
        ],
        ErrorClass.SCALA_COMPILE_ERROR.value: [
            "Open the generated diff and the compiler errors in the pipeline view",
            "Promote a corrected implementation into the template library if it is general",
        ],
        ErrorClass.EXECUTOR_OOM.value: [
            "Raise the run's executor memory quota, or reduce the input window",
            "Check the skew report for a hot key",
        ],
        ErrorClass.INSUFFICIENT_DATA.value: [
            "Extend the data window: the gap rule cannot be relaxed without risking leakage",
        ],
        ErrorClass.RESOURCE_CONFIG_INVALID.value: [
            "Raise the setting Spark names in the evidence to at least the minimum it states",
            "The agent may not change it: `spark.driver.memory` is outside the "
            "spark.config_change allowlist in agents/policies/governance.yaml",
        ],
        ErrorClass.DEPENDENCY_RESOLUTION.value: [
            "Check the toolchain's dependency cache and its network access to the repository",
            "The generated code was not the cause; do not regenerate it",
        ],
        ErrorClass.LEAKAGE_SUSPECTED.value: [
            "Review the feature catalogue entries flagged in the evidence",
            "Do not promote any model from this run until the leak is explained",
        ],
    }
    return table.get(cls, [
        f"Inspect the {cls} failure evidence in the escalation view",
        "Resume the run after fixing the cause, or abort it",
    ])
