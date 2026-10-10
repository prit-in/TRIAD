"""EXP-8: Scala compile failures.

PRD section 33:

    Setup: fault-injection mode mutates generated code with 20 seeded faults (EX): type
    mismatch, missing import, wrong column, forbidden API.
    Hypothesis: at least 70% repaired within 3 attempts; repeated fingerprints escalate;
    forbidden APIs never run.
    Independent variable: fault class.
    Dependent measures: repair rate, attempts, LLM calls, escalations.
    Expected observation: import and syntax faults fixed most often; semantic faults escalate
    more.

**What this experiment can and cannot measure here, stated up front.**

The repair rate cannot be measured in this environment, and the result is recorded as
`partial` because of it. Repairing a *code* fault means rewriting the generated slot, and in
TRIAD that is a model's job: `StageRunner._apply_fix` is an infrastructure playbook - it
raises shuffle partitions, bumps executor memory, enables skew handling, turns on `try_cast`
- and then calls `generate` again with the failure in `history`. With no LLM provider
configured (ADR-0003), `generate` re-renders the same template and returns byte-identical
code, so attempt 2 fails exactly as attempt 1 did. Reporting a repair rate of 0% would be as
misleading as inventing 70%: it measures the absence of a provider, not the repair loop. So it
is recorded as not measured, with the reason.

What *is* measured, and is the substance of the experiment:

* **Detection per fault class, and which gate catches each fault**, gates 1 to 5 - the five
  that run before a job reads any real data. A gate's failure counts as a detection only when
  the gate failed *because of the fault*: a compile error the compiler reported, a fixture test
  that ran and failed. A build that could not fetch a dependency, or a test JVM that died, is a
  toolchain failure, recorded and kept out of the counts.
* **That forbidden APIs never run.** Gate 2 is a static scan, so a forbidden API is refused
  before anything is compiled, let alone submitted.
* **That an unrepairable fault escalates.** For every detected fault the failing gates are run
  a second time on the identical artifact - which is what attempt 2 is with no model - and both
  real results are classified and fingerprinted exactly as `StageRunner` does, then given to
  the real circuit breaker (`RunBudget.record_fingerprint`). The first version of this
  measurement classified one string three times, so it could only ever report a repeat.

The wrong-column faults each change what the job does; a fault that changes nothing is not a
fault (see `agents/codegen/mutations.py`). Where each is expected to be caught was read from
the fixture suite before the run, and one is expected to pass it.
"""

from __future__ import annotations

import time
from typing import Any

from experiments.harness import Experiment

TEMPLATE_ID = "CleanJob"
SLOT_ID = "cleaning_plan"
# A gate failure is the fault's only with these classes: the compiler reported an error, or a
# fixture test ran and failed. Gates 1-3 are static checks of the code itself.
GENUINE = {4: "SCALA_COMPILE_ERROR", 5: "CONTRACT_VIOLATION"}


def _gate_report(artifact: Any, template: Any, *, compile_it: bool) -> dict[str, Any]:
    """Run the static gates, and the compiler only if the static gates let the code through.

    This mirrors `ChurnPipeline._validate_scala`, which also refuses to spend a compile on
    code the static scan has already rejected. Running them in the other order would report a
    forbidden API as a compile failure and lose the distinction the experiment is measuring.
    """
    from agents.codegen import gates as G

    report = G.GateReport()
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
    compiled = None
    if report.passed and compile_it:
        result = G.gate4_compile()
        report.add(result)
        compiled = result.passed
    failure = None if report.passed else report.first_failure
    return {
        "passed": report.passed,
        "compiled": compiled,
        "first_failed_gate": getattr(failure, "gate", None),
        "detail": "" if failure is None else str(getattr(failure, "detail", "")),
        "error_class": (getattr(getattr(failure, "error_class", None), "value", None)
                        if failure is not None else None),
        "violations": list(getattr(failure, "violations", []) or []),
        "report": report.to_dict(),
    }


def _value(trial: Any, name: str) -> Any:
    """A measurement, or None for one this trial never reached (gate 5 runs on some only)."""
    return next((m.value for m in trial.measurements if m.name == name), None)


def _fixture_gate(artifact: Any) -> Any:
    """Gate 5 for code the static gates let through: the generated object on fixtures.

    What the production validator runs next (`ChurnPipeline._validate_scala`). Not cached:
    every seeded fault is new code, and the control must be run, not remembered.
    """
    from agents.codegen import gates as G

    return G.gate5_fixture_tests([f"triad.jobs.generated.{artifact.object_name}"])


def _attempt(artifact: Any, template: Any) -> dict[str, Any]:
    """Gates 1 to 5 on one artifact, in production's order: the first failure, if any."""
    outcome = _gate_report(artifact, template, compile_it=True)
    if not outcome["passed"]:
        return {**outcome, "gate": outcome["first_failed_gate"], "fixture": None}
    fixture = _fixture_gate(artifact)
    return {
        **outcome,
        "gate": None if fixture.passed else 5,
        "detail": "" if fixture.passed else fixture.detail,
        "error_class": None if fixture.passed else getattr(fixture.error_class, "value", None),
        "fixture": fixture,
    }


def _genuine(attempt: dict[str, Any]) -> bool:
    """Whether a gate failed because of the fault rather than the toolchain (module docstring)."""
    gate = attempt["gate"]
    if gate is None:
        return False
    if gate in (1, 2, 3):
        return True
    if gate == 5 and str(attempt["detail"]).startswith("no fixture test ran"):
        return False
    return GENUINE.get(gate) == attempt["error_class"]


def _escalation(first: dict[str, Any], second: dict[str, Any] | None) -> dict[str, Any]:
    """What the stage runner does with these two real failures: its classifier, its breaker."""
    from agents.llm.budget import BudgetExceeded, RunBudget
    from triad_core.errors import ErrorClass, classify

    def diagnose(attempt: dict[str, Any]) -> Any:
        hint = ErrorClass(attempt["error_class"]) if attempt["error_class"] else None
        return classify(str(attempt["detail"]), hint=hint)

    d1 = diagnose(first)
    out: dict[str, Any] = {"error_class": d1.error_class.value, "fingerprints": [d1.fingerprint]}
    if d1.non_recoverable:
        # `StageRunner._after_failure` escalates a non-recoverable class at once.
        return {**out, "escalation": "non_recoverable", "attempts_to_escalation": 1}
    if second is None:
        return {**out, "escalation": "not_measured", "attempts_to_escalation": None}
    budget = RunBudget.from_dict(None)
    budget.record_fingerprint(d1.fingerprint)
    d2 = diagnose(second)
    out["fingerprints"].append(d2.fingerprint)
    try:
        budget.record_fingerprint(d2.fingerprint)
    except BudgetExceeded:
        return {**out, "escalation": "circuit_breaker", "attempts_to_escalation": 2}
    return {**out, "escalation": "none_by_attempt_2", "attempts_to_escalation": None}


def _conclude_failed(exp: Experiment, reason: str) -> Experiment:
    exp.conclude(status="failed", hypothesis_held=None, conclusion=reason)
    return exp


def run() -> Experiment:
    from agents.codegen.mutations import FAULT_CLASSES, MUTATIONS, check_anchors, slot_body_of
    from agents.codegen.templates import (
        TEMPLATE_ROOT,
        clean_generated,
        load_templates,
        render,
        write_artifact,
    )

    templates = load_templates(TEMPLATE_ROOT)
    template = templates[TEMPLATE_ID]
    template_source = (TEMPLATE_ROOT / f"{TEMPLATE_ID}.scala").read_text(encoding="utf-8")
    stale = check_anchors(template_source, SLOT_ID)
    baseline_slot = slot_body_of(template_source, SLOT_ID)

    with Experiment(
        "EXP-8",
        title="Scala compile failures",
        hypothesis=(
            "At least 70% of seeded code faults are repaired within 3 attempts; repeated "
            "fingerprints escalate; forbidden APIs never run."
        ),
        independent_variable="fault class",
        dependent_measures=[
            "detected", "first_failed_gate", "error_class", "detected_before_data",
            "reached_execution", "toolchain_failure", "escalation", "attempts_to_escalation",
            "repair_rate",
        ],
        config={
            "template": TEMPLATE_ID,
            "slot": SLOT_ID,
            "faults": len(MUTATIONS),
            "fault_classes": list(FAULT_CLASSES),
            "stale_anchors": stale,
            "gates_run": ["1 structured output", "2 static checks", "3 dependencies",
                          "4 compile (only if 1-3 pass)",
                          "5 fixture tests (only if 1-4 pass; not cached)"],
            "attempts": "each detected fault's gates are run twice on the identical artifact; "
                        "both results go through classify and RunBudget.record_fingerprint",
            "repair_measurement": (
                "not possible here: rewriting a generated slot needs an LLM provider and none "
                "is configured (ADR-0003), so `generate` re-renders identical code on every "
                "attempt. See the module docstring."
            ),
        },
    ) as exp:
        if stale:
            # A stale anchor raises on apply, after minutes of compiling: refuse before any.
            return _conclude_failed(
                exp, f"these mutations no longer match the template exactly once: {stale}; "
                     "re-anchor them (tests/unit/test_mutations.py) before running")

        try:
            # A control first: the unmutated slot must pass every gate. Without it, a template
            # or toolchain broken before the experiment started would read as 20 detections.
            control = exp.trial("control_unmutated")
            started = time.perf_counter()
            clean_generated(keep=set())
            artifact = render(template, {SLOT_ID: baseline_slot}, suffix="exp08ctl")
            write_artifact(artifact)
            first = _attempt(artifact, template)
            control.record("passed_all_gates", first["gate"] is None)
            control.record("compiled", first["compiled"])
            if first["fixture"] is not None:
                control.record("fixture_gate_passed", first["fixture"].passed)
                control.observations["fixture_detail"] = first["fixture"].detail[-1500:]
            control.record("wall_clock_s", round(time.perf_counter() - started, 1), "s")
            control.ok = first["gate"] is None
            if not control.ok:
                return _conclude_failed(
                    exp, "the unmutated control did not pass its own gates (failed at gate "
                         f"{first['gate']}), so no statement about the seeded faults would mean "
                         f"anything: {str(first['detail'])[-400:]}")

            for mutation in MUTATIONS:
                _run_fault(exp, mutation, template, baseline_slot, render, write_artifact,
                           clean_generated)
        finally:
            clean_generated(keep=set())

        _conclude(exp, FAULT_CLASSES)
        return exp


def _run_fault(exp: Experiment, mutation: Any, template: Any, baseline_slot: str,
               render: Any, write_artifact: Any, clean_generated: Any) -> None:
    trial = exp.trial(f"{mutation.fault_id}_{mutation.fault_class}")
    trial.observations["fault"] = {
        "id": mutation.fault_id,
        "class": mutation.fault_class,
        "description": mutation.description,
        "expected_gate": mutation.expected_gate,
        "note": mutation.note,
    }
    started = time.perf_counter()

    # One generated file at a time: a broken artifact left behind from the previous fault
    # would fail this fault's compile and be recorded as this fault's detection.
    clean_generated(keep=set())
    mutated = mutation.apply(baseline_slot)
    trial.record("mutation_applied", mutated != baseline_slot)

    # The suffix becomes a Scala object name, so it has to be a valid identifier: "WC-1" once
    # produced `object CleanJob_exp08wc-1`, and 15 "detections" were that parse error.
    suffix = "exp08" + "".join(c for c in mutation.fault_id.lower() if c.isalnum())
    art = render(template, {SLOT_ID: mutated}, suffix=suffix)
    write_artifact(art)

    first = _attempt(art, template)
    # The time to the first verdict, for Fig. 23b; `wall_clock_s` also covers attempt 2.
    trial.record("verdict_s", round(time.perf_counter() - started, 1), "s",
                 note="from the fault's start to the first gate verdict")
    genuine = _genuine(first)
    toolchain = first["gate"] is not None and not genuine
    detected = genuine and first["gate"] in (1, 2, 3, 4)
    trial.record("first_failed_gate", first["gate"])
    trial.record("error_class", first["error_class"])
    trial.record("toolchain_failure", toolchain,
                 note="a gate failed for a reason other than the fault; not counted")
    trial.record("detected", detected, note="stopped by a static gate, 1 to 4")
    trial.record("compiled", first["compiled"])
    trial.record("caught_by_expected_gate", genuine and first["gate"] == mutation.expected_gate)
    if first["fixture"] is not None:
        trial.record("fixture_gate_passed", first["fixture"].passed)
        trial.record("fixture_gate_s", round(first["fixture"].duration_ms / 1000, 1), "s")
        trial.observations["fixture_detail"] = first["fixture"].detail[-1500:]
    trial.record("detected_before_data", genuine)
    # A fault that passes gates 1-5 would be run on a data sample (gate 6) and then on the
    # full input by a real run: that is the honest meaning of "reached execution" here.
    trial.record("reached_execution", first["gate"] is None)
    trial.observations["gate_detail"] = str(first["detail"])[-1500:]
    trial.observations["violations"] = first["violations"]

    if genuine:
        # Attempt 2 with no model is the identical artifact through the same gates - run only
        # when the stage runner would retry, not after a non-recoverable class escalated.
        escalation = _escalation(first, None)
        if escalation["escalation"] != "non_recoverable":
            escalation = _escalation(first, _attempt(art, template))
        trial.record("escalation", escalation["escalation"])
        trial.record("attempts_to_escalation", escalation["attempts_to_escalation"])
        trial.record("fingerprints_identical",
                     len(set(escalation["fingerprints"])) == 1
                     if len(escalation["fingerprints"]) > 1 else None)
        trial.observations["escalation"] = escalation

    trial.record("wall_clock_s", round(time.perf_counter() - started, 1), "s")
    if toolchain:
        trial.ok = False
        trial.detail = (f"toolchain failure at gate {first['gate']} "
                        f"({first['error_class']}), not the fault; not counted")
    elif mutation.fault_class == "forbidden_api":
        # The requirement is absolute: it must never reach execution, and gate 2 must stop it.
        trial.ok = first["gate"] == 2
    elif mutation.fault_class == "wrong_column":
        # No verdict per fault: where a wrong column is stopped, if anywhere, is the measure.
        trial.ok = True
    else:
        trial.ok = detected
    if not trial.detail:
        trial.detail = (
            f"stopped at gate {first['gate']} ({first['error_class']}), expected "
            f"{mutation.expected_gate}; escalation {_value(trial, 'escalation')}"
            if first["gate"] is not None else
            f"passed gates 1 to 5; expected gate {mutation.expected_gate}"
        )


def _conclude(exp: Experiment, fault_classes: tuple[str, ...]) -> None:
    faults = [t for t in exp.trials if t.condition != "control_unmutated"]
    valid = [t for t in faults if not _value(t, "toolchain_failure")]
    by_class: dict[str, list[Any]] = {c: [] for c in fault_classes}
    for t in valid:
        by_class[t.observations["fault"]["class"]].append(t)

    per_class = {
        cls: {
            "faults": len(ts),
            "detected_static": sum(bool(_value(t, "detected")) for t in ts),
            "stopped_by_fixtures": sum(_value(t, "first_failed_gate") == 5 for t in ts),
            "detected_before_data": sum(bool(_value(t, "detected_before_data")) for t in ts),
            "reached_execution": sum(bool(_value(t, "reached_execution")) for t in ts),
            "gates": sorted({_value(t, "first_failed_gate") for t in ts}, key=str),
            "escalations": sorted({str(_value(t, "escalation")) for t in ts
                                   if _value(t, "escalation") is not None}),
        }
        for cls, ts in by_class.items()
    }
    exp.artifact("raw/per_class.json", per_class)

    toolchain = [t for t in faults if _value(t, "toolchain_failure")]
    forbidden = by_class["forbidden_api"]
    forbidden_at_2 = [t for t in forbidden if _value(t, "first_failed_gate") == 2]
    detected = [t for t in valid if _value(t, "detected_before_data")]
    # Repeated fingerprints escalate: every detected fault that was not escalated at once as
    # non-recoverable must trip the breaker on its second identical failure.
    recoverable = [t for t in detected if _value(t, "escalation") != "non_recoverable"]
    breaker = [t for t in recoverable if _value(t, "escalation") == "circuit_breaker"]
    repeats_escalate = (len(breaker) == len(recoverable)) if recoverable else None
    forbidden_never_run = (len(forbidden_at_2) == len(forbidden)
                           and not any(_value(t, "reached_execution") for t in forbidden)
                           ) if forbidden else None
    parts = {"repaired_within_3": None, "repeated_fingerprints_escalate": repeats_escalate,
             "forbidden_apis_never_run": forbidden_never_run}
    held = (False if any(v is False for v in parts.values())
            else None if any(v is None for v in parts.values()) else True)

    exp.limitation(
        "Repair rate is not measured. Rewriting a generated slot requires an LLM provider "
        "and none is configured (ADR-0003); `StageRunner._apply_fix` adjusts infrastructure "
        "inputs only, so `generate` returns identical code on every attempt. A measured 0% "
        "would describe the missing provider, not the repair loop, so it is recorded as not "
        "measured rather than reported as a number."
    )
    exp.limitation(
        "`reached_execution` means gates 1 to 5 passed and a real run would have submitted the "
        "job to the dry run on a sample (gate 6) and then to the full input. Gate 6 is not "
        "performed here, so for a fault the fixtures did not stop, what the dry run would "
        "report is not measured."
    )
    if toolchain:
        exp.limitation(
            f"{len(toolchain)} fault(s) met a toolchain failure rather than a gate's verdict and "
            "are left out of every count: "
            + "; ".join(f"{t.condition}: {t.detail}" for t in toolchain)
        )

    passed_static = [t for t in valid if not _value(t, "detected")]
    stopped_fx = [t for t in passed_static if _value(t, "first_failed_gate") == 5]
    fx_secs = [s for t in passed_static if (s := _value(t, "fixture_gate_s")) is not None]
    escaped = [t.condition for t in valid if _value(t, "reached_execution")]
    exp.conclude(
        status="partial",
        hypothesis_held=held,
        conclusion=(
            f"{sum(bool(_value(t, 'detected')) for t in valid)} of {len(valid)} seeded faults "
            f"were stopped by the static gates (1 to 4), and {len(detected)} of {len(valid)} "
            f"before the job read any data once the fixture tests (gate 5) are counted. "
            f"Forbidden APIs: {len(forbidden_at_2)} of {len(forbidden)} refused by gate 2, the "
            "static scan, before any compile. Per class: "
            + "; ".join(
                f"{cls} {v['detected_before_data']}/{v['faults']} stopped (gates {v['gates']})"
                for cls, v in per_class.items()
            )
            + f". Of the {len(passed_static)} faults the static gates let through, the fixture "
            f"tests stopped {len(stopped_fx)}"
            + (f", in {min(fx_secs):.0f} to {max(fx_secs):.0f} s each" if fx_secs else "")
            + (f"; reached execution: {escaped}" if escaped else "; none reached execution")
            + f". Escalation, from two real attempts through the stage runner's classifier and "
            f"circuit breaker: {len(breaker)} of {len(recoverable)} recoverable detections "
            "tripped the breaker on the second identical failure, and "
            f"{len(detected) - len(recoverable)} were escalated at once as non-recoverable. "
            "Repair rate is not measured - see the limitations. Clauses: "
            + ", ".join(f"{k}={v}" for k, v in parts.items()) + "."
        ),
    )


if __name__ == "__main__":
    run()
