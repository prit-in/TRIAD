"""The nine validation gates (PRD section 21.1, FR-055).

    1 structured-output validation   2 static checks        3 dependency validation
    4 compile / byte-compile / EXPLAIN                      5 fixture tests
    6 sandbox dry run                7 contract validation  8 store and version
    9 full execution + second contract check

No artifact runs at full scale without passing every gate, and every gate returns a
structured verdict, so a failure feeds the repair loop instead of a log grep.
"""

from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents.codegen.templates import RenderedArtifact, Template, diff_outside_slots
from triad_core.config import REPO_ROOT
from triad_core.errors import CompileError, ErrorClass, parse_scala_errors
from triad_core.ids import sha256_file

SCALA_ROOT = REPO_ROOT / "scala_spark"

# PRD section 21.5. Matched on source text for Scala and on the AST for Python, so a
# rename or an aliased import cannot slip past the Python scan.
FORBIDDEN_SCALA = (
    (r"\bsys\.process\b", "sys.process: arbitrary execution"),
    (r"\bRuntime\s*\.\s*getRuntime\s*\(\s*\)\s*\.\s*exec\b", "Runtime.exec: arbitrary execution"),
    (r"\bjava\.net\b", "java.net: network access"),
    (r"\bSystem\s*\.\s*exit\b", "System.exit: kills the driver"),
    (r"\.collect\s*\(\s*\)", "collect() on event data: driver memory (LR-09)"),
    (r"\.toLocalIterator\s*\(\s*\)", "toLocalIterator(): driver memory"),
    (r"spark\.sql\.ansi\.enabled", "protected Spark option (CR-19)"),
    (r"\bSparkSession\s*\.\s*builder\b", "the job template owns the SparkSession"),
    (r"\.write\s*\.", "output writing belongs to the template, never to a slot"),
)

FORBIDDEN_PYTHON_CALLS = {
    "eval", "exec", "compile", "__import__",
    "os.system", "os.popen", "os.execv", "os.fork",
    "subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_output",
    "socket.socket", "pickle.load", "pickle.loads",
}
FORBIDDEN_PYTHON_IMPORTS = {"subprocess", "socket", "ctypes", "multiprocessing"}

ALLOWED_SCALA_IMPORT_PREFIXES = (
    "org.apache.spark.sql",
    "org.apache.spark.sql.functions",
    "org.apache.spark.sql.expressions",
    "org.apache.spark.sql.types",
    "triad.core",
    "scala.",
    "java.time",
)


@dataclass
class GateResult:
    gate: int
    name: str
    passed: bool
    detail: str = ""
    violations: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    error_class: ErrorClass | None = None
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail[:2000],
            "violations": self.violations,
            "errors": self.errors,
            "error_class": self.error_class.value if self.error_class else None,
            "duration_ms": self.duration_ms,
        }


@dataclass
class GateReport:
    results: list[GateResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def first_failure(self) -> GateResult | None:
        return next((r for r in self.results if not r.passed), None)

    def add(self, result: GateResult) -> GateResult:
        self.results.append(result)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "gates": [r.to_dict() for r in self.results]}


# --- gate 1: structured output -------------------------------------------------

GENERATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["slot_id", "language", "code", "declared_imports", "declared_output_schema"],
    "properties": {
        "slot_id": {"type": "string", "minLength": 1},
        "language": {"type": "string", "enum": ["scala", "python", "sql"]},
        "code": {"type": "string", "minLength": 1},
        "declared_imports": {"type": "array", "items": {"type": "string"}},
        "declared_output_schema": {
            "type": "object", "additionalProperties": {"type": "string"}
        },
        "notes": {"type": "string", "maxLength": 300},
    },
}


def gate1_structured_output(payload: dict[str, Any]) -> GateResult:
    from jsonschema import Draft202012Validator, ValidationError

    try:
        Draft202012Validator(GENERATION_SCHEMA).validate(payload)
    except ValidationError as exc:
        return GateResult(1, "structured_output", False, str(exc.message),
                          error_class=ErrorClass.POLICY_VIOLATION)
    return GateResult(1, "structured_output", True, "output validates against the schema")


# --- gate 2: static checks -----------------------------------------------------


def scan_scala(code: str) -> list[str]:
    violations = []
    for pattern, reason in FORBIDDEN_SCALA:
        if re.search(pattern, code):
            violations.append(f"forbidden API: {reason}")
    return violations


def scan_python(code: str) -> list[str]:
    """AST-based scan: catches aliased imports and attribute calls, not just text matches."""
    violations: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"python syntax error: {exc}"]

    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                aliases[a.asname or a.name] = a.name
                if root in FORBIDDEN_PYTHON_IMPORTS:
                    violations.append(f"forbidden import: {a.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod in FORBIDDEN_PYTHON_IMPORTS:
                violations.append(f"forbidden import: from {node.module}")
            for a in node.names:
                aliases[a.asname or a.name] = f"{node.module}.{a.name}"
        elif isinstance(node, ast.Call):
            name = _call_name(node.func, aliases)
            if name in FORBIDDEN_PYTHON_CALLS:
                violations.append(f"forbidden call: {name}")
            if name and name.endswith(".collect"):
                violations.append("forbidden call: collect() on event data (LR-09)")
            if name and name.endswith(".toPandas"):
                violations.append("forbidden call: unguarded toPandas() on event data (LR-09)")
    return violations


def _call_name(node: ast.AST, aliases: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value, aliases)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def gate2_static_checks(artifact: RenderedArtifact, template: Template) -> GateResult:
    violations: list[str] = []

    # a) slot compliance: nothing outside a declared slot may change (FR-038)
    outside = diff_outside_slots(template, artifact)
    if outside:
        violations.extend(f"out-of-slot change: {d}" for d in outside)

    # b) forbidden APIs
    slot_code = artifact.source
    violations.extend(
        scan_scala(slot_code) if artifact.language == "scala" else scan_python(slot_code)
    )

    passed = not violations
    return GateResult(
        2, "static_checks", passed,
        "no violations" if passed else f"{len(violations)} violation(s)",
        violations=violations,
        error_class=None if passed else ErrorClass.POLICY_VIOLATION,
    )


# --- gate 3: dependency validation --------------------------------------------

BUILD_FILES = (
    SCALA_ROOT / "build.sbt",
    SCALA_ROOT / "project" / "build.properties",
    REPO_ROOT / "requirements.lock.txt",
)


def build_file_hashes() -> dict[str, str]:
    return {str(p.relative_to(REPO_ROOT)): sha256_file(p) for p in BUILD_FILES if p.exists()}


def gate3_dependencies(
    artifact: RenderedArtifact, baseline_hashes: dict[str, str], declared_imports: list[str]
) -> GateResult:
    violations: list[str] = []

    current = build_file_hashes()
    for name, digest in baseline_hashes.items():
        if current.get(name) != digest:
            violations.append(f"build file changed during generation: {name}")

    if artifact.language == "scala":
        for imp in re.findall(r"^import\s+([\w.]+)", artifact.source, flags=re.M):
            if not any(imp.startswith(p) for p in ALLOWED_SCALA_IMPORT_PREFIXES):
                violations.append(f"import not on the allowlist: {imp}")
        for imp in declared_imports:
            if imp not in artifact.source:
                violations.append(f"declared import not present in the code: {imp}")

    passed = not violations
    return GateResult(
        3, "dependency_validation", passed,
        "dependencies unchanged and imports allowlisted" if passed else "dependency violation",
        violations=violations,
        error_class=None if passed else ErrorClass.POLICY_VIOLATION,
    )


# --- gate 4: compile -----------------------------------------------------------


def gate4_compile(
    *, scala: bool = True, timeout: int = 3600, sbt_home: str | None = None
) -> GateResult:
    """sbt incremental compile for Scala. Warnings are fatal in the generated module.

    `package` rather than `compile`, because gates 6 and 9 submit JARs: compiling alone
    leaves the freshly generated class in `target/classes` and `spark-submit` would load a
    JAR packaged before this attempt existed. Both projects are packaged because the job is
    submitted as `jobs.jar` with `--jars core.jar` (DEC-04), so a stale core JAR is the same
    bug one level down. `package` depends on `compile`, so compile errors surface here
    exactly as before.
    """
    import time

    started = time.perf_counter()
    if not scala:
        return GateResult(4, "compile", True, "no compilation required")

    sbt = Path(sbt_home or "/opt/sbt") / "bin" / "sbt"
    if not sbt.exists():
        return GateResult(4, "compile", False, "sbt is not available in this environment",
                          error_class=ErrorClass.UNKNOWN)

    proc = subprocess.run(
        [str(sbt), "-batch", "core/package", "jobs/package"],
        cwd=SCALA_ROOT, capture_output=True, text=True, timeout=timeout,
    )
    duration = int((time.perf_counter() - started) * 1000)
    output = proc.stdout + proc.stderr
    if proc.returncode == 0:
        return GateResult(4, "compile", True, "compiled and packaged", duration_ms=duration)

    errors: list[CompileError] = parse_scala_errors(output)
    return GateResult(
        4, "compile", False,
        output[-4000:],
        errors=[e.to_dict() for e in errors],
        # Only a compile error when the compiler said so. This used to be set for every
        # non-zero sbt exit, and the stage runner passes it to `classify` as a hint that
        # skips pattern matching - so a dependency download failing inside sbt was diagnosed
        # as a compile error, whatever the output said, and sent to code regeneration (EXP-6).
        # With no parsed diagnostics the classifier reads the output itself.
        error_class=ErrorClass.SCALA_COMPILE_ERROR if errors else None,
        duration_ms=duration,
    )


def gate4_byte_compile_python(code: str) -> GateResult:
    try:
        compile(code, "<generated>", "exec")
    except SyntaxError as exc:
        return GateResult(
            4, "compile", False, f"{exc.msg} at line {exc.lineno}",
            errors=[{"file": "<generated>", "line": exc.lineno or 0, "column": exc.offset,
                     "message": exc.msg or ""}],
            error_class=ErrorClass.PYTHON_EXCEPTION,
        )
    return GateResult(4, "compile", True, "byte-compiled")


def gate4_explain_sql(sql: str, spark: Any) -> GateResult:
    """Parse and analyse SQL with EXPLAIN, without executing it."""
    try:
        spark.sql(f"EXPLAIN {sql}").collect()
    except Exception as exc:
        return GateResult(4, "compile", False, str(exc),
                          error_class=ErrorClass.UNRESOLVED_COLUMN)
    return GateResult(4, "compile", True, "SQL analysed")


# --- gate 5: fixture tests -----------------------------------------------------


def gate5_fixture_tests(*, timeout: int = 3600, sbt_home: str | None = None) -> GateResult:
    import time

    started = time.perf_counter()
    sbt = Path(sbt_home or "/opt/sbt") / "bin" / "sbt"
    if not sbt.exists():
        return GateResult(5, "fixture_tests", False, "sbt is not available",
                          error_class=ErrorClass.UNKNOWN)
    proc = subprocess.run(
        [str(sbt), "-batch", "core/test"],
        cwd=SCALA_ROOT, capture_output=True, text=True, timeout=timeout,
    )
    duration = int((time.perf_counter() - started) * 1000)
    if proc.returncode == 0:
        return GateResult(5, "fixture_tests", True, "all fixture tests passed",
                          duration_ms=duration)
    return GateResult(
        5, "fixture_tests", False, (proc.stdout + proc.stderr)[-4000:],
        error_class=ErrorClass.CONTRACT_VIOLATION, duration_ms=duration,
    )


# --- gates 6-9 are driven by the executor -------------------------------------


def gate6_dry_run(result: dict[str, Any]) -> GateResult:
    ok = bool(result.get("ok"))
    return GateResult(
        6, "sandbox_dry_run", ok,
        result.get("detail", "dry run on a sample completed" if ok else "dry run failed"),
        error_class=None if ok else ErrorClass.CONTRACT_VIOLATION,
    )


def gate7_contract(contract_result: Any) -> GateResult:
    passed = bool(getattr(contract_result, "passed", False))
    violations = [v.message for v in getattr(contract_result, "violations", [])]
    return GateResult(
        7, "contract_check", passed,
        "output contract satisfied" if passed else "contract violation",
        violations=violations,
        error_class=None if passed else ErrorClass.CONTRACT_VIOLATION,
    )


def gate8_store(storage_uri: str, content_hash: str) -> GateResult:
    return GateResult(
        8, "store_and_version", True,
        f"artifact stored at {storage_uri} with hash {content_hash[:12]}",
    )


def gate9_execute(result: dict[str, Any], contract_result: Any) -> GateResult:
    ok = bool(result.get("ok")) and bool(getattr(contract_result, "passed", False))
    return GateResult(
        9, "full_execution", ok,
        result.get("detail", "full run completed and the contract passed again"
                   if ok else "full run failed"),
        error_class=None if ok else ErrorClass.CONTRACT_VIOLATION,
    )
