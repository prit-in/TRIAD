"""Structured error taxonomy and deterministic classification (PRD section 20).

Classification always runs before any LLM is consulted: a failure is matched against the
patterns below, given a stable fingerprint, and mapped to a playbook action. Only errors
that match nothing are eligible for LLM diagnosis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from triad_core.ids import error_fingerprint


class ErrorClass(str, Enum):
    MALFORMED_FILE = "MALFORMED_FILE"
    TYPE_CHANGE = "TYPE_CHANGE"
    MISSING_OPTIONAL_COLUMN = "MISSING_OPTIONAL_COLUMN"
    MISSING_REQUIRED_COLUMN = "MISSING_REQUIRED_COLUMN"
    RENAMED_REQUIRED_COLUMN = "RENAMED_REQUIRED_COLUMN"
    EXECUTOR_LOST = "EXECUTOR_LOST"
    EXECUTOR_OOM = "EXECUTOR_OOM"
    DRIVER_OOM = "DRIVER_OOM"
    DATA_SKEW = "DATA_SKEW"
    INVALID_CAST_ANSI = "INVALID_CAST_ANSI"
    UNRESOLVED_COLUMN = "UNRESOLVED_COLUMN"
    SCALA_COMPILE_ERROR = "SCALA_COMPILE_ERROR"
    PYTHON_EXCEPTION = "PYTHON_EXCEPTION"
    MISSING_PACKAGE = "MISSING_PACKAGE"
    CONVERGENCE_WARNING = "CONVERGENCE_WARNING"
    EXTREME_IMBALANCE = "EXTREME_IMBALANCE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    TIMEOUT = "TIMEOUT"
    LLM_ERROR = "LLM_ERROR"
    STORAGE_ERROR = "STORAGE_ERROR"
    CONTRACT_VIOLATION = "CONTRACT_VIOLATION"
    ROW_ACCOUNTING_MISMATCH = "ROW_ACCOUNTING_MISMATCH"
    LEAKAGE_SUSPECTED = "LEAKAGE_SUSPECTED"
    UNKNOWN = "UNKNOWN"


class Remedy(str, Enum):
    QUARANTINE_AND_CONTINUE = "quarantine_and_continue"
    RETRY_PARSE_OPTIONS = "retry_parse_options"
    TRY_CAST_WITH_QUARANTINE = "try_cast_with_quarantine"
    DISABLE_DEPENDENT_FEATURES = "disable_dependent_features"
    PROPOSE_MAPPING = "propose_mapping"
    RESUBMIT = "resubmit"
    RAISE_SHUFFLE_PARTITIONS = "raise_shuffle_partitions"
    RAISE_EXECUTOR_MEMORY = "raise_executor_memory"
    REWRITE_TO_STORAGE = "rewrite_to_storage"
    ENABLE_SKEW_HANDLING = "enable_skew_handling"
    REGENERATE_CODE = "regenerate_code"
    SCALE_AND_MORE_ITERATIONS = "scale_and_more_iterations"
    SWITCH_METRICS_AND_WEIGHTING = "switch_metrics_and_weighting"
    BACKOFF_AND_REASK = "backoff_and_reask"
    REFRESH_CREDENTIALS = "refresh_credentials"
    OPTIMISED_RETRY = "optimised_retry"
    NONE = "none"


@dataclass(frozen=True)
class PlaybookEntry:
    error_class: ErrorClass
    remedy: Remedy
    retry_limit: int
    recoverable: bool
    needs_approval: bool = False
    note: str = ""


# PRD section 20.2, one row per failure mode.
PLAYBOOK: dict[ErrorClass, PlaybookEntry] = {
    ErrorClass.MALFORMED_FILE: PlaybookEntry(
        ErrorClass.MALFORMED_FILE, Remedy.QUARANTINE_AND_CONTINUE, 1, True,
        note="up to 1% bad lines quarantined; otherwise retry parse options once"),
    ErrorClass.TYPE_CHANGE: PlaybookEntry(
        ErrorClass.TYPE_CHANGE, Remedy.TRY_CAST_WITH_QUARANTINE, 1, True),
    ErrorClass.MISSING_OPTIONAL_COLUMN: PlaybookEntry(
        ErrorClass.MISSING_OPTIONAL_COLUMN, Remedy.DISABLE_DEPENDENT_FEATURES, 0, True),
    ErrorClass.MISSING_REQUIRED_COLUMN: PlaybookEntry(
        ErrorClass.MISSING_REQUIRED_COLUMN, Remedy.NONE, 0, False,
        note="escalate immediately; no automatic fix exists"),
    ErrorClass.RENAMED_REQUIRED_COLUMN: PlaybookEntry(
        ErrorClass.RENAMED_REQUIRED_COLUMN, Remedy.PROPOSE_MAPPING, 0, True, needs_approval=True),
    ErrorClass.EXECUTOR_LOST: PlaybookEntry(ErrorClass.EXECUTOR_LOST, Remedy.RESUBMIT, 2, True),
    ErrorClass.EXECUTOR_OOM: PlaybookEntry(
        ErrorClass.EXECUTOR_OOM, Remedy.RAISE_SHUFFLE_PARTITIONS, 2, True),
    ErrorClass.DRIVER_OOM: PlaybookEntry(ErrorClass.DRIVER_OOM, Remedy.REWRITE_TO_STORAGE, 1, True),
    ErrorClass.DATA_SKEW: PlaybookEntry(ErrorClass.DATA_SKEW, Remedy.ENABLE_SKEW_HANDLING, 1, True),
    ErrorClass.INVALID_CAST_ANSI: PlaybookEntry(
        ErrorClass.INVALID_CAST_ANSI, Remedy.TRY_CAST_WITH_QUARANTINE, 1, True,
        note="never disable ANSI mode (CR-19)"),
    ErrorClass.UNRESOLVED_COLUMN: PlaybookEntry(
        ErrorClass.UNRESOLVED_COLUMN, Remedy.REGENERATE_CODE, 3, True),
    ErrorClass.SCALA_COMPILE_ERROR: PlaybookEntry(
        ErrorClass.SCALA_COMPILE_ERROR, Remedy.REGENERATE_CODE, 3, True),
    ErrorClass.PYTHON_EXCEPTION: PlaybookEntry(
        ErrorClass.PYTHON_EXCEPTION, Remedy.REGENERATE_CODE, 3, True),
    ErrorClass.MISSING_PACKAGE: PlaybookEntry(
        ErrorClass.MISSING_PACKAGE, Remedy.NONE, 0, False,
        note="packages are never installed at run time"),
    ErrorClass.CONVERGENCE_WARNING: PlaybookEntry(
        ErrorClass.CONVERGENCE_WARNING, Remedy.SCALE_AND_MORE_ITERATIONS, 2, True),
    ErrorClass.EXTREME_IMBALANCE: PlaybookEntry(
        ErrorClass.EXTREME_IMBALANCE, Remedy.SWITCH_METRICS_AND_WEIGHTING, 0, True),
    ErrorClass.INSUFFICIENT_DATA: PlaybookEntry(
        ErrorClass.INSUFFICIENT_DATA, Remedy.NONE, 0, False,
        note="relaxing the gap rule would risk leakage"),
    ErrorClass.POLICY_VIOLATION: PlaybookEntry(
        ErrorClass.POLICY_VIOLATION, Remedy.REGENERATE_CODE, 2, True),
    ErrorClass.TIMEOUT: PlaybookEntry(ErrorClass.TIMEOUT, Remedy.OPTIMISED_RETRY, 1, True),
    ErrorClass.LLM_ERROR: PlaybookEntry(ErrorClass.LLM_ERROR, Remedy.BACKOFF_AND_REASK, 3, True),
    ErrorClass.STORAGE_ERROR: PlaybookEntry(
        ErrorClass.STORAGE_ERROR, Remedy.REFRESH_CREDENTIALS, 1, True),
    ErrorClass.CONTRACT_VIOLATION: PlaybookEntry(
        ErrorClass.CONTRACT_VIOLATION, Remedy.REGENERATE_CODE, 2, True),
    ErrorClass.ROW_ACCOUNTING_MISMATCH: PlaybookEntry(
        ErrorClass.ROW_ACCOUNTING_MISMATCH, Remedy.NONE, 0, False,
        note="a mismatch means rows vanished; never auto-continue"),
    ErrorClass.LEAKAGE_SUSPECTED: PlaybookEntry(
        ErrorClass.LEAKAGE_SUSPECTED, Remedy.NONE, 0, False, note="block selection and escalate"),
    ErrorClass.UNKNOWN: PlaybookEntry(ErrorClass.UNKNOWN, Remedy.NONE, 1, True),
}

# Ordered: the first pattern that matches wins.
_PATTERNS: list[tuple[ErrorClass, re.Pattern[str]]] = [
    (ErrorClass.MISSING_PACKAGE, re.compile(r"ModuleNotFoundError|ImportError: No module", re.I)),
    (ErrorClass.SCALA_COMPILE_ERROR, re.compile(
        r"\[error\][^\n]*\.scala|type mismatch;|not found: (value|type)|error: value .* is not a member", re.I)),
    (ErrorClass.INVALID_CAST_ANSI, re.compile(
        r"CAST_INVALID_INPUT|NUMERIC_VALUE_OUT_OF_RANGE|cannot be cast to|invalid input syntax", re.I)),
    (ErrorClass.UNRESOLVED_COLUMN, re.compile(
        r"UNRESOLVED_COLUMN|cannot resolve '?[`\w.]+'? given input columns", re.I)),
    (ErrorClass.EXECUTOR_OOM, re.compile(
        r"ExecutorLostFailure.*(OutOfMemory|memory)|Container killed.*memory|java\.lang\.OutOfMemoryError.*executor", re.I)),
    (ErrorClass.DRIVER_OOM, re.compile(
        r"driver.*OutOfMemoryError|Total size of serialized results.*bigger than spark\.driver\.maxResultSize", re.I)),
    (ErrorClass.EXECUTOR_LOST, re.compile(
        r"ExecutorLostFailure|FetchFailedException|Lost executor|Connection reset by peer", re.I)),
    (ErrorClass.DATA_SKEW, re.compile(r"skew(ed)? (partition|key)|task .* took .* median", re.I)),
    (ErrorClass.TIMEOUT, re.compile(r"TimeoutException|deadline exceeded|hard timeout", re.I)),
    (ErrorClass.STORAGE_ERROR, re.compile(
        r"(FileNotFoundException|NoSuchFileException|Permission denied|AccessDenied|PathNotFound)", re.I)),
    (ErrorClass.MISSING_REQUIRED_COLUMN, re.compile(r"required column .* (missing|not found)", re.I)),
    (ErrorClass.MISSING_OPTIONAL_COLUMN, re.compile(r"optional column .* (missing|not found)", re.I)),
    (ErrorClass.RENAMED_REQUIRED_COLUMN, re.compile(r"renamed column|candidate mapping", re.I)),
    (ErrorClass.MALFORMED_FILE, re.compile(r"_corrupt_record|malformed (line|record)|ParseException", re.I)),
    (ErrorClass.CONVERGENCE_WARNING, re.compile(r"ConvergenceWarning|failed to converge", re.I)),
    (ErrorClass.ROW_ACCOUNTING_MISMATCH, re.compile(r"row accounting", re.I)),
    (ErrorClass.CONTRACT_VIOLATION, re.compile(r"contract (check )?failed|schema hash mismatch", re.I)),
    (ErrorClass.POLICY_VIOLATION, re.compile(r"forbidden (api|import|call)|out-of-slot|policy violation", re.I)),
    (ErrorClass.LEAKAGE_SUSPECTED, re.compile(r"leakage", re.I)),
    (ErrorClass.INSUFFICIENT_DATA, re.compile(r"insufficient (data|rows|positives)|gap rule violated", re.I)),
    (ErrorClass.EXTREME_IMBALANCE, re.compile(r"class(es)? imbalance|only one class present", re.I)),
    (ErrorClass.LLM_ERROR, re.compile(r"rate limit|LLM (error|timeout)|invalid JSON from model", re.I)),
    (ErrorClass.PYTHON_EXCEPTION, re.compile(r"Traceback \(most recent call last\)|\w+Error:", re.I)),
]


@dataclass
class Diagnosis:
    error_class: ErrorClass
    fingerprint: str
    message: str
    remedy: Remedy
    recoverable: bool
    retry_limit: int
    needs_approval: bool = False
    matched_by: str = "taxonomy"
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def unknown(self) -> bool:
        return self.error_class is ErrorClass.UNKNOWN

    @property
    def non_recoverable(self) -> bool:
        return not self.recoverable

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_class": self.error_class.value,
            "fingerprint": self.fingerprint,
            "message": self.message[:2000],
            "remedy": self.remedy.value,
            "recoverable": self.recoverable,
            "retry_limit": self.retry_limit,
            "needs_approval": self.needs_approval,
            "matched_by": self.matched_by,
            "details": self.details,
        }


def classify(message: str, *, hint: ErrorClass | None = None) -> Diagnosis:
    """Deterministically classify a failure. Never calls an LLM."""
    text = message or ""
    chosen = hint
    if chosen is None:
        for error_class, pattern in _PATTERNS:
            if pattern.search(text):
                chosen = error_class
                break
    chosen = chosen or ErrorClass.UNKNOWN
    entry = PLAYBOOK[chosen]
    return Diagnosis(
        error_class=chosen,
        fingerprint=error_fingerprint(chosen.value, text),
        message=text,
        remedy=entry.remedy,
        recoverable=entry.recoverable,
        retry_limit=entry.retry_limit,
        needs_approval=entry.needs_approval,
        matched_by="taxonomy" if chosen is not ErrorClass.UNKNOWN else "unmatched",
    )


# --- Compiler error parsing (FR-043) -----------------------------------------

_SCALAC_ERROR = re.compile(
    r"^\[error\]\s+(?P<file>[^:\s]+\.scala):(?P<line>\d+):(?:(?P<col>\d+):)?\s*(?P<msg>.+)$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class CompileError:
    file: str
    line: int
    column: int | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "message": self.message,
        }


def parse_scala_errors(sbt_output: str) -> list[CompileError]:
    """Turn raw sbt output into structured errors so repairs are targeted (SYS-02)."""
    out: list[CompileError] = []
    for m in _SCALAC_ERROR.finditer(sbt_output or ""):
        out.append(
            CompileError(
                file=m.group("file"),
                line=int(m.group("line")),
                column=int(m.group("col")) if m.group("col") else None,
                message=m.group("msg").strip(),
            )
        )
    return out
