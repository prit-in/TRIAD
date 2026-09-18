"""M13 Prediction and M10 explanations (FR-050, FR-051, PRD sections 15, 28.3).

Every prediction row carries the model version, the feature-set version, the scoring date and
its risk band, so a score can always be traced back to what produced it. Shadow scores are
written to a separate set and never published as official (UC-15).

Explanations are labelled as **associations**; nothing here claims causation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from triad_core.ids import utcnow

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd

OFFICIAL = "official"
SHADOW = "shadow"


@dataclass
class PredictionSet:
    frame: pd.DataFrame
    model_name: str
    model_version: str
    feature_set_version: str
    score_date: str
    set_type: str = OFFICIAL
    threshold: float = 0.5

    def summary(self) -> dict[str, Any]:
        band_counts = self.frame["risk_band"].value_counts().to_dict()
        return {
            "model": self.model_name,
            "model_version": self.model_version,
            "feature_set_version": self.feature_set_version,
            "score_date": self.score_date,
            "set": self.set_type,
            "rows": len(self.frame),
            "mean_score": float(self.frame["churn_probability"].mean()),
            "high_risk_count": int(band_counts.get("high", 0)),
            "risk_bands": {k: int(v) for k, v in band_counts.items()},
            "threshold": self.threshold,
        }


def risk_band(score: float, *, high: float = 0.66, medium: float = 0.33) -> str:
    if score >= high:
        return "high"
    if score >= medium:
        return "medium"
    return "low"


def score(
    model: Any,
    features: pd.DataFrame,
    *,
    feature_cols: list[str],
    model_name: str,
    model_version: str,
    feature_set_version: str,
    score_date: str | None = None,
    set_type: str = OFFICIAL,
    threshold: float = 0.5,
    bands: tuple[float, float] = (0.66, 0.33),
    id_col: str = "customer_id",
    reasons: dict[str, list[dict[str, Any]]] | None = None,
) -> PredictionSet:
    """Score a feature table and stamp every row with its provenance."""
    import pandas as pd

    probabilities = model.predict_proba(features[feature_cols])[:, 1]
    high, medium = bands
    frame = pd.DataFrame(
        {
            "customer_ref": features[id_col].astype(str).to_numpy(),
            "churn_probability": probabilities,
            "risk_band": [risk_band(p, high=high, medium=medium) for p in probabilities],
            "model_version": model_version,
            "feature_set_version": feature_set_version,
            "score_date": score_date or utcnow().date().isoformat(),
            "prediction_set": set_type,
            "threshold": threshold,
        }
    )
    if reasons:
        frame["top_reasons"] = [reasons.get(ref, []) for ref in frame["customer_ref"]]
    else:
        frame["top_reasons"] = [[] for _ in range(len(frame))]

    return PredictionSet(
        frame=frame,
        model_name=model_name,
        model_version=model_version,
        feature_set_version=feature_set_version,
        score_date=frame["score_date"].iloc[0] if len(frame) else (score_date or ""),
        set_type=set_type,
        threshold=threshold,
    )


def revenue_at_risk(
    predictions: pd.DataFrame, expected_value: pd.Series | np.ndarray
) -> dict[str, Any]:
    """Sum of churn probability times expected 90-day value (PRD 27.2).

    The formula is stated in the report as an Assumption, never presented as measured.
    """
    values = np.asarray(expected_value, dtype=float)
    probs = predictions["churn_probability"].to_numpy(dtype=float)
    n = min(len(values), len(probs))
    total = float(np.nansum(probs[:n] * values[:n]))
    return {
        "revenue_at_risk": total,
        "formula": "sum(churn_probability * expected_value_90d)",
        "assumption": "expected 90-day value equals one quarter of the last year's net revenue",
        "rows": int(n),
    }


# --- explanations (FR-051) -----------------------------------------------------


@dataclass
class Explanations:
    method: str
    global_importance: list[dict[str, Any]] = field(default_factory=list)
    per_customer: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    note: str = "These are associations between features and the model's score, not causes."

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "global_importance": self.global_importance,
            "per_customer_count": len(self.per_customer),
            "note": self.note,
        }


def explain(
    model: Any,
    features: pd.DataFrame,
    feature_cols: list[str],
    *,
    sample: int = 200,
    top_k: int = 5,
    id_col: str = "customer_id",
    seed: int = 42,
) -> Explanations:
    """Global and per-customer explanations.

    SHAP where the model supports it, permutation importance otherwise, and linear
    coefficients for linear models. The method is always named, because a chart without its
    method is not an explanation (FR-067).
    """

    x = features[feature_cols]
    if len(x) > sample:
        x = x.sample(n=sample, random_state=seed)
    ids = features.loc[x.index, id_col].astype(str).tolist() if id_col in features else []

    estimator = model
    transformed = x
    transformed_names = list(feature_cols)
    if hasattr(model, "named_steps") and "prep" in getattr(model, "named_steps", {}):
        prep = model.named_steps["prep"]
        estimator = model.named_steps.get("clf", model)
        try:
            transformed = prep.transform(x)
            transformed_names = [str(n) for n in prep.get_feature_names_out()]
        except Exception:
            transformed, transformed_names = x, list(feature_cols)

    # 1) linear coefficients
    if hasattr(estimator, "coef_"):
        coefs = np.ravel(estimator.coef_)
        order = np.argsort(-np.abs(coefs))[:top_k]
        return Explanations(
            method="logistic regression coefficients (standardised inputs)",
            global_importance=[
                {"feature": transformed_names[i] if i < len(transformed_names) else str(i),
                 "value": float(coefs[i])}
                for i in order
            ],
        )

    # 2) SHAP for tree models
    try:
        import shap

        explainer = shap.TreeExplainer(estimator)
        values = explainer.shap_values(transformed)
        if isinstance(values, list):
            values = values[-1]
        values = np.asarray(values)
        if values.ndim == 3:
            values = values[:, :, -1]
        mean_abs = np.abs(values).mean(axis=0)
        order = np.argsort(-mean_abs)[:top_k]
        global_importance = [
            {"feature": transformed_names[i] if i < len(transformed_names) else str(i),
             "value": float(mean_abs[i])}
            for i in order
        ]
        per_customer: dict[str, list[dict[str, Any]]] = {}
        for row, ref in enumerate(ids):
            contributions = values[row]
            top = np.argsort(-np.abs(contributions))[:3]
            per_customer[ref] = [
                {"feature": transformed_names[i] if i < len(transformed_names) else str(i),
                 "contribution": float(contributions[i]),
                 "direction": "raises" if contributions[i] > 0 else "lowers"}
                for i in top
            ]
        return Explanations(method="SHAP (TreeExplainer)",
                            global_importance=global_importance,
                            per_customer=per_customer)
    except Exception:
        pass

    # 3) permutation importance as the general fallback
    try:
        from sklearn.inspection import permutation_importance

        if hasattr(model, "predict_proba"):
            baseline = model.predict_proba(x)[:, 1]
            pseudo_y = (baseline >= np.median(baseline)).astype(int)
            result = permutation_importance(
                model, x, pseudo_y, n_repeats=3, random_state=seed, scoring="roc_auc"
            )
            order = np.argsort(-result.importances_mean)[:top_k]
            return Explanations(
                method="permutation importance (against the model's own median split)",
                global_importance=[
                    {"feature": feature_cols[i], "value": float(result.importances_mean[i])}
                    for i in order
                ],
            )
    except Exception:
        pass

    return Explanations(method="unavailable", global_importance=[])


def reasons_text(contributions: list[dict[str, Any]]) -> list[str]:
    """Plain-language, associative phrasing for the UI and reports."""
    out = []
    for c in contributions:
        verb = "is associated with a higher score" if c["direction"] == "raises" \
            else "is associated with a lower score"
        out.append(f"{c['feature']} {verb}")
    return out
