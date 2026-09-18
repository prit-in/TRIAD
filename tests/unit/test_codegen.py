"""TC-038, TC-054, TC-055 / ATC-03, ATC-04: templates, slots and the validation gates."""

from __future__ import annotations

import pytest

from agents.codegen.gates import (
    GENERATION_SCHEMA,
    build_file_hashes,
    gate1_structured_output,
    gate2_static_checks,
    gate3_dependencies,
    gate4_byte_compile_python,
    scan_python,
    scan_scala,
)
from agents.codegen.templates import (
    SlotViolation,
    TEMPLATE_ROOT,
    diff_outside_slots,
    load_templates,
    parse_template,
    render,
)


@pytest.fixture(scope="module")
def feature_template():  # noqa: ANN201
    return parse_template(TEMPLATE_ROOT / "FeatureJob.scala")


# --- templates and slots -------------------------------------------------------


def test_every_job_template_declares_slots() -> None:
    templates = load_templates()
    assert {"FeatureJob", "LabelJob", "CleanJob"} <= set(templates)
    for name, t in templates.items():
        assert t.slots, f"{name} declares no slots"
        for slot in t.slots.values():
            assert slot.end_line > slot.start_line
            assert slot.default_body, f"{name}:{slot.slot_id} has an empty default body"


def test_render_substitutes_only_the_slot(feature_template) -> None:
    body = "    in(\"transactions\").limit(1)"
    artifact = render(feature_template, {"transform": body})

    assert "limit(1)" in artifact.source
    assert artifact.object_name.startswith("FeatureJob_")
    assert "package triad.jobs.generated" in artifact.source
    # The fixed half of the template survives untouched.
    assert "override def name: String" in artifact.source
    assert "outputGrain" in artifact.source
    assert diff_outside_slots(feature_template, artifact) == []


def test_unknown_slot_is_refused(feature_template) -> None:
    with pytest.raises(SlotViolation, match="unknown slot"):
        render(feature_template, {"not_a_slot": "x"})


def test_out_of_slot_edit_is_detected(feature_template) -> None:
    """FR-038: any change outside a slot fails validation."""
    artifact = render(feature_template, {"transform": "    in(\"transactions\")"})
    artifact.source = artifact.source.replace(
        'override def name: String = "churn-features"',
        'override def name: String = "hijacked"',
    )
    diffs = diff_outside_slots(feature_template, artifact)
    assert diffs, "an edit outside the slot must be detected"
    assert any("hijacked" in d for d in diffs)


def test_rendering_is_deterministic(feature_template) -> None:
    body = "    in(\"transactions\").select(\"customer_id\")"
    a = render(feature_template, {"transform": body})
    b = render(feature_template, {"transform": body})
    assert a.content_hash == b.content_hash
    assert a.source == b.source


# --- gate 1: structured output -------------------------------------------------


def test_gate1_accepts_a_valid_payload_and_rejects_a_malformed_one() -> None:
    good = {
        "slot_id": "transform",
        "language": "scala",
        "code": "df",
        "declared_imports": [],
        "declared_output_schema": {"customer_id": "string"},
    }
    assert gate1_structured_output(good).passed

    bad = dict(good)
    del bad["code"]
    assert gate1_structured_output(bad).passed is False

    extra = {**good, "run_shell": "rm -rf /"}
    assert gate1_structured_output(extra).passed is False, "additionalProperties must be refused"
    assert GENERATION_SCHEMA["additionalProperties"] is False


# --- gate 2: static checks -----------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        "val x = sys.process.Process(\"ls\").!!",
        "Runtime.getRuntime().exec(\"ls\")",
        "import java.net.URL",
        "System.exit(1)",
        "df.collect()",
        "df.toLocalIterator()",
        "spark.conf.set(\"spark.sql.ansi.enabled\", \"false\")",
        "val s = SparkSession.builder.getOrCreate()",
        "df.write.parquet(\"/data\")",
    ],
)
def test_forbidden_scala_apis_are_caught(code: str) -> None:
    """ATC-04: a forbidden API never reaches execution."""
    assert scan_scala(code), f"not caught: {code}"


def test_allowed_scala_code_passes_the_scan() -> None:
    ok = """
    val daily = ChurnTransforms.customerDaily(in("transactions"), in.get("products"))
    ChurnTransforms.windowFeatures(daily)
    """
    assert scan_scala(ok) == []


@pytest.mark.parametrize(
    "code",
    [
        "import subprocess\nsubprocess.run(['ls'])",
        "import socket\ns = socket.socket()",
        "eval('1+1')",
        "exec('x=1')",
        "import pickle\npickle.load(open('f','rb'))",
        "import os\nos.system('ls')",
        "df.collect()",
        "df.toPandas()",
        "import subprocess as sp\nsp.Popen(['ls'])",
    ],
)
def test_forbidden_python_apis_are_caught(code: str) -> None:
    assert scan_python(code), f"not caught: {code}"


def test_python_scan_sees_through_an_alias() -> None:
    """A text scan misses this; the AST scan does not."""
    assert scan_python("import subprocess as quiet\nquiet.run(['x'])")


def test_gate2_combines_slot_compliance_and_the_api_scan(feature_template) -> None:
    clean = render(feature_template, {"transform": "    in(\"transactions\")"})
    assert gate2_static_checks(clean, feature_template).passed

    sneaky = render(feature_template, {"transform": "    in(\"transactions\").collect()\n    in(\"transactions\")"})
    result = gate2_static_checks(sneaky, feature_template)
    assert result.passed is False
    assert any("collect()" in v for v in result.violations)


# --- gate 3: dependencies ------------------------------------------------------


def test_gate3_detects_a_changed_build_file(feature_template) -> None:
    artifact = render(feature_template, {"transform": "    in(\"transactions\")"})
    baseline = build_file_hashes()
    assert gate3_dependencies(artifact, baseline, []).passed

    tampered = dict(baseline)
    first = next(iter(tampered))
    tampered[first] = "0" * 64
    result = gate3_dependencies(artifact, tampered, [])
    assert result.passed is False
    assert any("build file changed" in v for v in result.violations)


def test_gate3_rejects_an_import_outside_the_allowlist(feature_template) -> None:
    artifact = render(feature_template, {"transform": "    in(\"transactions\")"})
    artifact.source = artifact.source.replace(
        "import org.apache.spark.sql.{DataFrame, SparkSession}",
        "import org.apache.spark.sql.{DataFrame, SparkSession}\nimport scary.library.Thing",
    )
    result = gate3_dependencies(artifact, build_file_hashes(), [])
    assert result.passed is False
    assert any("scary.library.Thing" in v for v in result.violations)


# --- gate 4: byte compile ------------------------------------------------------


def test_gate4_python_byte_compile() -> None:
    assert gate4_byte_compile_python("x = 1 + 1").passed
    bad = gate4_byte_compile_python("def broken(:\n  pass")
    assert bad.passed is False
    assert bad.errors and bad.errors[0]["line"]
