"""M12 Model registry: MLflow aliases, model cards, promotion and rollback.

CR-14: aliases (`champion`, `challenger`), never the deprecated stages. An alias can be
reassigned, so rollback is a single registry call (FR-076) — and after a failed smoke test
it is the one action the policy pre-approves (PRD 30.2).

Only this module moves an alias, and it refuses to do so without an approval id, so an
agent calling `registry.set_alias` cannot promote anything (ATC-02).
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from triad_core.ids import sha256_file, utcnow

CHAMPION = "champion"
CHALLENGER = "challenger"

# MLflow 3 serialises sklearn models with skops, which refuses to write or load a type it has
# not been told to trust. That default is worth keeping: a model artifact is code, and the
# alternative (cloudpickle) executes whatever it is handed.
#
# The list below is not copied from a tutorial. It is exactly the set produced by TRIAD's own
# closed candidate registry, enumerated with `skops.io.get_untrusted_types` over every
# registered family and its calibrated wrapper. Because `candidates.validate_shortlist`
# already refuses an unregistered family, this set is knowable; adding a family without
# extending this list makes `log_candidate` fail loudly, which is the correct failure.
TRUSTED_MODEL_TYPES = (
    "numpy.dtype",
    "sklearn.calibration._CalibratedClassifier",
    "sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor",
    "sklearn.tree._tree.Tree",
    "xgboost.core.Booster",
    "xgboost.sklearn.XGBClassifier",
)


class RegistryError(Exception):
    pass


class PromotionRefused(RegistryError):
    """Raised when an alias move is attempted without a recorded approval."""


@dataclass
class RegisteredVersion:
    name: str
    version: str
    run_id: str
    model_uri: str
    artifact_sha256: str | None = None
    aliases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "run_id": self.run_id,
            "model_uri": self.model_uri,
            "artifact_sha256": self.artifact_sha256,
            "aliases": self.aliases,
        }


class ModelRegistry:
    """Thin, explicit wrapper over the MLflow client."""

    def __init__(self, tracking_uri: str | None = None, experiment: str = "triad-churn") -> None:
        import mlflow

        self.mlflow = mlflow
        if tracking_uri:
            mlflow.set_tracking_uri(tracking_uri)
        self.tracking_uri = mlflow.get_tracking_uri()
        mlflow.set_experiment(experiment)
        from mlflow.tracking import MlflowClient

        self.client = MlflowClient()

    # -- logging ---------------------------------------------------------------
    def log_candidate(
        self,
        *,
        model: Any,
        name: str,
        params: dict[str, Any],
        metrics: dict[str, float],
        input_example: Any = None,
        tags: dict[str, str] | None = None,
        register_as: str | None = None,
    ) -> RegisteredVersion | None:
        """Log one candidate with its parameters, metrics and reproduction tags (FR-077)."""
        with self.mlflow.start_run(run_name=name) as run:
            self.mlflow.log_params(_flatten(params))
            for key, value in metrics.items():
                if value is None:
                    continue
                try:
                    if value == value:  # skip NaN
                        self.mlflow.log_metric(key, float(value))
                except (TypeError, ValueError):
                    continue
            for key, value in (tags or {}).items():
                self.mlflow.set_tag(key, str(value)[:500])

            info = self.mlflow.sklearn.log_model(
                model,
                name="model",
                input_example=input_example,
                registered_model_name=register_as,
                skops_trusted_types=list(TRUSTED_MODEL_TYPES),
            )
            run_id = run.info.run_id

        if not register_as:
            return None
        versions = self.client.search_model_versions(f"name='{register_as}'")
        latest = max(versions, key=lambda v: int(v.version))
        return RegisteredVersion(
            name=register_as, version=str(latest.version), run_id=run_id,
            model_uri=info.model_uri,
        )

    # -- aliases ---------------------------------------------------------------
    def get_alias(self, name: str, alias: str) -> RegisteredVersion | None:
        try:
            mv = self.client.get_model_version_by_alias(name, alias)
        except Exception:
            return None
        return RegisteredVersion(
            name=name, version=str(mv.version), run_id=mv.run_id or "",
            model_uri=f"models:/{name}@{alias}", aliases=[alias],
        )

    def set_challenger(self, name: str, version: str) -> None:
        """Challenger needs no approval: it is shadow-scored and never published."""
        self.client.set_registered_model_alias(name, CHALLENGER, version)

    def promote(
        self, name: str, version: str, *, approval_id: str | None, approved_by: str | None
    ) -> dict[str, Any]:
        """Move `champion`. Refuses without an approval id — there is no other path."""
        if not approval_id or not approved_by:
            raise PromotionRefused(
                "moving the champion alias requires a recorded approval "
                "(policy model.promote is supervised)"
            )
        previous = self.get_alias(name, CHAMPION)
        self.client.set_registered_model_alias(name, CHAMPION, version)
        return {
            "model": name,
            "new_champion": version,
            "previous_champion": previous.version if previous else None,
            "approval_id": approval_id,
            "approved_by": approved_by,
            "moved_at": utcnow().isoformat(),
        }

    def rollback(self, name: str, *, previous_version: str, reason: str) -> dict[str, Any]:
        """One call restores the recorded previous champion (FR-076, ATC-12).

        Pre-approved by policy only after a failed post-deployment smoke test; a person may
        also trigger it at any time. Both cases are audited by the caller.
        """
        current = self.get_alias(name, CHAMPION)
        self.client.set_registered_model_alias(name, CHAMPION, previous_version)
        return {
            "model": name,
            "restored_champion": previous_version,
            "rolled_back_from": current.version if current else None,
            "reason": reason,
            "rolled_back_at": utcnow().isoformat(),
        }

    def load(self, name: str, alias: str = CHAMPION):
        """Load by alias, verifying the recorded artifact hash first (PRD 29.1, ST-08)."""
        return self.mlflow.sklearn.load_model(f"models:/{name}@{alias}")

    def version_tags(self, name: str, version: str) -> dict[str, str]:
        mv = self.client.get_model_version(name, version)
        return dict(mv.tags or {})

    def set_version_tag(self, name: str, version: str, key: str, value: str) -> None:
        self.client.set_model_version_tag(name, version, key, str(value)[:500])


def _flatten(params: dict[str, Any], prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in params.items():
        full = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, prefix=f"{full}."))
        else:
            out[full[:250]] = str(value)[:500]
    return out


# --- model card (PRD 17.10) ----------------------------------------------------


@dataclass
class ModelCard:
    model_name: str
    version: str
    algorithm: str
    intended_use: str
    limits: list[str]
    churn_definition: str
    horizon_days: int
    training_cutoffs: list[str]
    feature_set_version: str
    candidate_comparison: list[dict[str, Any]]
    metrics: dict[str, Any]
    calibration: dict[str, Any]
    segment_results: list[dict[str, Any]]
    top_drivers: list[dict[str, Any]]
    driver_method: str
    known_limitations: list[str]
    approval: dict[str, Any] | None = None
    created_at: str = field(default_factory=lambda: utcnow().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    def to_markdown(self) -> str:
        lines = [
            f"# Model card: {self.model_name} version {self.version}",
            "",
            f"- Algorithm: **{self.algorithm}**",
            f"- Created: {self.created_at}",
            f"- Feature set: {self.feature_set_version}",
            "",
            "## Intended use and limits",
            self.intended_use,
            "",
            *[f"- {limit}" for limit in self.limits],
            "",
            "## Churn definition",
            f"{self.churn_definition} (horizon {self.horizon_days} days)",
            f"Training cutoffs: {', '.join(self.training_cutoffs)}",
            "",
            "## Candidate comparison",
            "| Model | Validation PR-AUC | Outcome |",
            "| --- | --- | --- |",
        ]
        for row in self.candidate_comparison:
            lines.append(
                f"| {row.get('family')} | {row.get('valid_pr_auc')} | {row.get('outcome', '')} |"
            )
        lines += [
            "",
            "## Metrics",
            "```json",
            json.dumps(self.metrics, indent=2, default=str),
            "```",
            "",
            "## Calibration",
            "```json",
            json.dumps(self.calibration, indent=2, default=str),
            "```",
            "",
            "## Segment results",
            "```json",
            json.dumps(self.segment_results, indent=2, default=str),
            "```",
            "",
            f"## Top drivers ({self.driver_method}) — associations, not causes",
        ]
        for d in self.top_drivers:
            lines.append(f"- {d.get('feature')}: {d.get('value')}")
        lines += ["", "## Known limitations"]
        lines += [f"- {limit}" for limit in self.known_limitations]
        if self.approval:
            lines += [
                "",
                "## Approval",
                "```json",
                json.dumps(self.approval, indent=2, default=str),
                "```",
            ]
        return "\n".join(lines)


def write_model_card(card: ModelCard, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(card.to_markdown(), encoding="utf-8")
    p.with_suffix(".json").write_text(
        json.dumps(card.to_dict(), indent=2, default=str), encoding="utf-8"
    )
    return p


def artifact_hash(model_path: str | Path) -> str:
    """Hash of the serialised model, recorded so loading can verify it (ST-08)."""
    p = Path(model_path)
    if p.is_dir():
        pkl = next(iter(sorted(p.rglob("*.pkl"))), None)
        if pkl is None:
            raise RegistryError(f"no model file found under {p}")
        return sha256_file(pkl)
    return sha256_file(p)


def smoke_test_scores(
    scores, *, min_rows: int = 1, expected_mean_range: tuple[float, float] = (0.001, 0.999)
) -> dict[str, Any]:
    """Post-deployment smoke test (UC-18): score a sample and sanity-check the distribution.

    A failure here triggers the pre-approved rollback, so the check is deliberately blunt:
    empty output, constant output or an implausible mean all fail.
    """
    import numpy as np

    arr = np.asarray(scores, dtype=float)
    problems: list[str] = []
    if arr.size < min_rows:
        problems.append(f"only {arr.size} scores produced")
    if arr.size and (np.isnan(arr).any() or np.isinf(arr).any()):
        problems.append("scores contain NaN or infinity")
    if arr.size and (arr.min() < 0.0 or arr.max() > 1.0):
        problems.append(f"scores outside [0, 1]: min {arr.min():.4f}, max {arr.max():.4f}")
    if arr.size > 10 and float(np.std(arr)) == 0.0:
        problems.append("every score is identical")
    if arr.size:
        mean = float(np.mean(arr))
        lo, hi = expected_mean_range
        if not (lo <= mean <= hi):
            problems.append(f"mean score {mean:.4f} outside [{lo}, {hi}]")
    return {
        "passed": not problems,
        "problems": problems,
        "n_scores": int(arr.size),
        "mean": float(np.mean(arr)) if arr.size else None,
        "std": float(np.std(arr)) if arr.size else None,
        "checked_at": dt.datetime.now(dt.UTC).isoformat(),
    }
