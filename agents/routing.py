"""Deterministic language and engine routing (PRD section 13.3, rules LR-01..LR-10).

The LLM never chooses the runtime. `decide()` matches the first rule whose condition holds,
returns the route plus the evidence that produced it, and the caller stores a decision record
citing the rule id — which is what PO-03 and SC-02 are measured on.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from triad_core.config import load_yaml, settings


class RoutingError(Exception):
    pass


@dataclass(frozen=True)
class Route:
    rule_id: str
    engine: str
    language: str | None
    rationale: str
    evidence: dict[str, Any]
    deploy_mode: str | None = None
    extra: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "engine": self.engine,
            "language": self.language,
            "rationale": self.rationale,
            "evidence": self.evidence,
            "deploy_mode": self.deploy_mode,
            **(self.extra or {}),
        }


class RoutingEngine:
    def __init__(self, policy_path: str | Path | None = None) -> None:
        self.policy = load_yaml(policy_path or settings().routing_policy_path)
        self.thresholds: dict[str, int] = self.policy.get("thresholds", {})
        self.rules: list[dict[str, Any]] = self.policy.get("rules", [])

    def threshold(self, name: str) -> int:
        if name not in self.thresholds:
            raise RoutingError(f"routing threshold '{name}' is not configured")
        return int(self.thresholds[name])

    def decide(
        self,
        stage: str,
        *,
        kind: str | None = None,
        input_rows: int = 0,
        input_bytes: int = 0,
        read_only: bool = False,
        needs_row_logic: bool = False,
        needs_custom_aggregation: bool = False,
        estimated_memory_bytes: int | None = None,
        ml_worker_ram_bytes: int | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """Match rules in order and return the first route that applies."""
        kind = kind or _infer_kind(stage)
        evidence = {
            "stage": stage,
            "kind": kind,
            "input_rows": input_rows,
            "input_bytes": input_bytes,
            "read_only": read_only,
        }

        # LR-03: logic that would need a UDF goes to a typed Scala Dataset.
        if needs_row_logic or needs_custom_aggregation:
            return Route(
                "LR-03", "spark", "scala",
                "per-row or per-group logic uses a typed Dataset or aggregator; Python UDFs "
                "are blocked on event data",
                evidence,
            ).to_dict()

        # LR-04: read-only profiling runs from PySpark.
        if kind == "profiling" or (read_only and kind in (None, "exploration")):
            return Route(
                "LR-04", "spark", "python",
                "read-only profiling and exploration run from PySpark or Spark SQL",
                evidence,
                deploy_mode=self._deploy_mode(input_bytes),
            ).to_dict()

        # LR-05: the ML stages are Python.
        if kind in ("training", "tuning", "evaluation", "explanation", "scoring_small"):
            return Route("LR-05", "python", "python",
                         "training, tuning, evaluation and explanation live in Python",
                         evidence).to_dict()

        # LR-06: the Spark-to-Python hand-off and its size guard.
        if kind == "handoff":
            ram = ml_worker_ram_bytes or self.threshold("ml_worker_ram_bytes")
            fraction = float(self.thresholds.get("pandas_max_fraction_of_ram", 0.25))
            estimated = estimated_memory_bytes or 0
            fits = estimated <= ram * fraction
            evidence.update({"estimated_memory_bytes": estimated,
                             "ml_worker_ram_bytes": ram, "fraction": fraction})
            return Route(
                "LR-06", "python" if fits else "spark", "python",
                ("estimated in-memory size is within the pandas budget"
                 if fits else
                 "estimated size exceeds the pandas budget: the distributed trainer is used"),
                evidence,
                extra={"target": "pandas" if fits else "distributed", "format": "parquet"},
            ).to_dict()

        # LR-08: scoring routes by size.
        if kind == "scoring":
            limit = self.threshold("scoring_rows_for_spark_udf")
            big = input_rows > limit
            evidence["threshold_rows"] = limit
            return Route(
                "LR-08", "spark" if big else "python", "python",
                ("an MLflow Spark UDF scores above the row threshold"
                 if big else "in-process Python scoring below the row threshold"),
                evidence,
            ).to_dict()

        # LR-02: reusable pipeline stages are compiled Scala jobs.
        if kind in ("cleaning", "labels", "features"):
            return Route(
                "LR-02", "spark", "scala",
                "cleaning, labels and features are reusable stages, so they are compiled, "
                "typed and versioned Scala jobs",
                evidence,
                deploy_mode=self._deploy_mode(input_bytes),
            ).to_dict()

        # LR-01: any other event-level transformation above the size threshold.
        if kind == "transformation" and input_rows > self.threshold("event_rows_for_spark"):
            evidence["threshold_rows"] = self.threshold("event_rows_for_spark")
            return Route("LR-01", "spark", "scala",
                         "event-level data above the size threshold stays on Spark",
                         evidence,
                         deploy_mode=self._deploy_mode(input_bytes)).to_dict()

        # LR-07 as the fallback: small inputs run the same Spark job in local mode.
        return Route(
            "LR-07", "spark", "scala",
            "input is below the small-data threshold, so the same Spark job runs in local mode",
            evidence,
            deploy_mode="local[2]",
        ).to_dict()

    def _deploy_mode(self, input_bytes: int) -> str:
        return "local[2]" if input_bytes <= self.threshold("small_data_bytes") else "cluster"

    # -- static scan support (LR-09) -------------------------------------------
    def forbidden_patterns(self) -> list[str]:
        for rule in self.rules:
            if rule.get("id") == "LR-09":
                return ["collect", "toPandas"]
        return []

    def allowed_spark_settings(self, governance_policy: dict[str, Any]) -> list[str]:
        """LR-10: per-job settings only, from the governance allowlist."""
        return list(
            governance_policy.get("actions", {})
            .get("spark.config_change", {})
            .get("allowlist", [])
        )


def _infer_kind(stage: str) -> str:
    s = stage.lower()
    if "profile" in s:
        return "profiling"
    if "clean" in s:
        return "cleaning"
    if "label" in s:
        return "labels"
    if "feature" in s or "snapshot" in s or "aggregate" in s:
        return "features"
    if "train" in s or "tune" in s:
        return "training"
    if "evaluat" in s:
        return "evaluation"
    if "score" in s or "predict" in s:
        return "scoring"
    if "handoff" in s or "training_set" in s:
        return "handoff"
    return "transformation"
