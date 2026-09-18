"""Drift measurement (PRD section 19.2, FR-081).

The population stability index compares the share of rows per bin now against the share at
training time. Bin edges are cut at the **training** deciles and stored with the reference, so
every later scoring run is measured on the same ruler; recutting the bins on current data
would hide exactly the shift being looked for.

The 0.10 and 0.25 cut-offs are rules of thumb rather than tests, so a Kolmogorov-Smirnov
statistic (numeric) or a chi-square statistic (categorical) is reported next to every index.
Nothing here decides to retrain: `agents/retraining.py` maps these flags onto RT-2 and RT-3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from triad_core.ids import utcnow

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd

DEFAULT_BINS = 10
EPSILON = 1e-6          # keeps ln(a/e) defined when a bin empties out
OTHER = "__other__"     # categories unseen at training time land here

# PRD 19.1 RT-2/RT-3 defaults; overridden from pipelines/churn_v1.yaml:retraining.
DEFAULT_THRESHOLDS: dict[str, Any] = {
    "psi_feature_threshold": 0.25,
    "psi_feature_count_threshold": {"psi": 0.10, "features": 3},
    "psi_score_threshold": 0.20,
    "high_risk_share_change": 0.5,   # "changes by more than half", relative
}


def psi(expected: np.ndarray, actual: np.ndarray) -> float:
    """PSI between two share vectors that are aligned bin for bin."""
    e = np.clip(np.asarray(expected, dtype=float), EPSILON, None)
    a = np.clip(np.asarray(actual, dtype=float), EPSILON, None)
    return float(np.sum((a - e) * np.log(a / e)))


def _numeric_edges(values: np.ndarray, bins: int) -> list[float]:
    """Training deciles, de-duplicated, open at both ends.

    A column with heavy ties (many zeros is the usual case) yields fewer than `bins` distinct
    edges; that is reported as it is rather than padded, because inventing empty bins would
    understate the index.
    """
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return [-np.inf, np.inf]
    cuts = np.quantile(finite, np.linspace(0.0, 1.0, bins + 1))
    inner = sorted({float(c) for c in cuts[1:-1]})
    return [-np.inf, *inner, np.inf]


def _numeric_shares(values: np.ndarray, edges: list[float]) -> np.ndarray:
    """Share of rows per bin, plus a final bin for nulls so they cannot vanish."""
    arr = np.asarray(values, dtype=float)
    missing = int(np.sum(~np.isfinite(arr)))
    finite = arr[np.isfinite(arr)]
    counts = np.histogram(finite, bins=np.asarray(edges, dtype=float))[0].astype(float)
    counts = np.append(counts, float(missing))
    total = counts.sum()
    return counts / total if total else counts


def _categorical_shares(values: Any, categories: list[str]) -> np.ndarray:
    """Share per known category; everything else, including nulls, goes to `__other__`."""
    import pandas as pd

    series = pd.Series(values).astype("object")
    labels = series.map(lambda v: str(v) if v is not None and v == v else OTHER)
    known = labels.where(labels.isin(categories), OTHER)
    counts = known.value_counts()
    ordered = np.array([float(counts.get(c, 0)) for c in [*categories, OTHER]])
    total = ordered.sum()
    return ordered / total if total else ordered


@dataclass
class Reference:
    """The training-time distribution a later run is compared against.

    Stored with the model version as JSON, never recomputed from current data.
    """

    numeric: dict[str, dict[str, Any]] = field(default_factory=dict)
    categorical: dict[str, dict[str, Any]] = field(default_factory=dict)
    score_edges: list[float] = field(default_factory=list)
    score_shares: list[float] = field(default_factory=list)
    high_risk_share: float | None = None
    n_rows: int = 0
    captured_at: str = ""

    @property
    def features(self) -> list[str]:
        return [*self.numeric, *self.categorical]

    @classmethod
    def from_frame(
        cls,
        frame: pd.DataFrame,
        features: list[str],
        *,
        scores: np.ndarray | None = None,
        high_risk_threshold: float | None = None,
        bins: int = DEFAULT_BINS,
        max_categories: int = 50,
    ) -> Reference:
        import pandas as pd

        ref = cls(n_rows=len(frame), captured_at=utcnow().isoformat())
        for name in features:
            if name not in frame.columns:
                continue
            column = frame[name]
            if pd.api.types.is_numeric_dtype(column) and not pd.api.types.is_bool_dtype(column):
                values = column.to_numpy(dtype=float, na_value=np.nan)
                edges = _numeric_edges(values, bins)
                ref.numeric[name] = {
                    "edges": edges,
                    "shares": _numeric_shares(values, edges).tolist(),
                }
            else:
                counts = column.astype("object").value_counts()
                categories = [str(c) for c in counts.head(max_categories).index]
                ref.categorical[name] = {
                    "categories": categories,
                    "shares": _categorical_shares(column, categories).tolist(),
                }

        if scores is not None and len(scores):
            arr = np.asarray(scores, dtype=float)
            ref.score_edges = _numeric_edges(arr, bins)
            ref.score_shares = _numeric_shares(arr, ref.score_edges).tolist()
            if high_risk_threshold is not None:
                ref.high_risk_share = float(np.mean(arr >= high_risk_threshold))
        return ref

    def to_dict(self) -> dict[str, Any]:
        return {
            "numeric": self.numeric,
            "categorical": self.categorical,
            "score_edges": self.score_edges,
            "score_shares": self.score_shares,
            "high_risk_share": self.high_risk_share,
            "n_rows": self.n_rows,
            "captured_at": self.captured_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Reference:
        return cls(
            numeric={k: dict(v) for k, v in (payload.get("numeric") or {}).items()},
            categorical={k: dict(v) for k, v in (payload.get("categorical") or {}).items()},
            score_edges=list(payload.get("score_edges") or []),
            score_shares=list(payload.get("score_shares") or []),
            high_risk_share=payload.get("high_risk_share"),
            n_rows=int(payload.get("n_rows") or 0),
            captured_at=str(payload.get("captured_at") or ""),
        )


@dataclass
class FeatureDrift:
    feature: str
    kind: str                 # numeric | categorical
    psi: float
    test: str                 # ks | chi2 | none
    statistic: float | None = None
    p_value: float | None = None
    flagged: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature, "kind": self.kind, "psi": round(self.psi, 6),
            "test": self.test, "statistic": self.statistic, "p_value": self.p_value,
            "flagged": self.flagged, "note": self.note,
        }


@dataclass
class ScoreDrift:
    psi: float
    reference_high_risk_share: float | None
    current_high_risk_share: float | None
    share_change: float | None          # relative change; None when there is no reference
    flagged: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "psi": round(self.psi, 6),
            "reference_high_risk_share": self.reference_high_risk_share,
            "current_high_risk_share": self.current_high_risk_share,
            "share_change": self.share_change,
            "flagged": self.flagged,
            "reason": self.reason,
        }


@dataclass
class DriftResult:
    features: list[FeatureDrift] = field(default_factory=list)
    score: ScoreDrift | None = None
    thresholds: dict[str, Any] = field(default_factory=dict)
    n_rows: int = 0
    computed_at: str = ""

    @property
    def flagged_features(self) -> list[str]:
        return [f.feature for f in self.features if f.flagged]

    @property
    def max_psi(self) -> float:
        return max((f.psi for f in self.features if f.psi == f.psi), default=0.0)

    def psi_map(self) -> dict[str, float]:
        return {f.feature: f.psi for f in self.features}

    def to_dict(self) -> dict[str, Any]:
        return {
            "computed_at": self.computed_at,
            "n_rows": self.n_rows,
            "thresholds": self.thresholds,
            "features": [f.to_dict() for f in self.features],
            "score": self.score.to_dict() if self.score else None,
            "flagged_features": self.flagged_features,
            "max_psi": round(self.max_psi, 6),
        }


def top_raw_features(
    importance: list[dict[str, Any]], columns: list[str], *, limit: int = 10
) -> list[str]:
    """Map explanation feature names back to the raw columns drift is measured on.

    Explanations name features *after* the pipeline transformed them, so a one-hot arrives as
    `country_UK` while the reference holds `country`. The longest matching column wins, so
    `orders_365d` is never mistaken for `orders`. A name that matches no column is dropped
    rather than guessed: passing it through would silently empty the top-feature set and stop
    RT-2 ever flagging anything.
    """
    by_length = sorted(columns, key=len, reverse=True)
    out: list[str] = []
    for entry in importance:
        name = str(entry.get("feature", ""))
        match = next((c for c in by_length if name == c or name.startswith(c)), None)
        if match and match not in out:
            out.append(match)
        if len(out) >= limit:
            break
    return out


def compute_drift(
    reference: Reference,
    current: pd.DataFrame,
    *,
    scores: np.ndarray | None = None,
    high_risk_threshold: float | None = None,
    top_features: list[str] | None = None,
    thresholds: dict[str, Any] | None = None,
    reference_sample: pd.DataFrame | None = None,
) -> DriftResult:
    """Compare `current` against the training reference.

    `top_features` limits the RT-2 "any top-10 feature" flag to the features the model
    actually leans on; features outside it are still measured and reported. `reference_sample`
    is optional: with it the KS statistic is computed against real training rows, without it
    only PSI is reported and the test is named `none` rather than guessed.
    """
    limits = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    single = float(limits["psi_feature_threshold"])
    top = set(top_features or reference.features)

    results: list[FeatureDrift] = []
    for name, spec in reference.numeric.items():
        if name not in current.columns:
            results.append(FeatureDrift(name, "numeric", float("nan"), "none",
                                        flagged=True, note="feature missing from current data"))
            continue
        values = current[name].to_numpy(dtype=float, na_value=np.nan)
        actual = _numeric_shares(values, list(spec["edges"]))
        index = psi(np.asarray(spec["shares"], dtype=float), actual)
        stat, pvalue, test = _ks(values, reference_sample, name)
        results.append(FeatureDrift(name, "numeric", index, test, stat, pvalue,
                                    flagged=index > single and name in top,
                                    note=_window_note(current[name])))

    for name, spec in reference.categorical.items():
        if name not in current.columns:
            results.append(FeatureDrift(name, "categorical", float("nan"), "none",
                                        flagged=True, note="feature missing from current data"))
            continue
        categories = [str(c) for c in spec["categories"]]
        actual = _categorical_shares(current[name], categories)
        expected = np.asarray(spec["shares"], dtype=float)
        index = psi(expected, actual)
        stat, pvalue, test = _chi2(expected, actual, reference.n_rows, len(current))
        results.append(FeatureDrift(name, "categorical", index, test, stat, pvalue,
                                    flagged=index > single and name in top,
                                    note=_window_note(current[name])))

    score_drift = None
    if scores is not None and reference.score_shares:
        score_drift = _score_drift(reference, np.asarray(scores, dtype=float),
                                   high_risk_threshold, limits)

    return DriftResult(
        features=results, score=score_drift, thresholds=limits,
        n_rows=len(current), computed_at=utcnow().isoformat(),
    )


def _window_note(column: pd.Series) -> str:
    """Explain a large index that comes from the shape of the window, not the population.

    A scoring run at a single cutoff makes every cutoff-derived feature constant -
    `snapshot_month` is 4 in April and nothing else - while the training reference spans many
    cutoffs. The index is then enormous and says nothing about customer behaviour.

    The flag is deliberately *not* suppressed: a feature that collapsed to one value because
    an upstream source broke looks identical, and that is drift worth waking someone for. The
    note is what tells the two apart, so a reader is never left to guess.
    """
    return (
        "single value in the scoring window; expected for cutoff-derived features"
        if column.nunique(dropna=False) <= 1
        else ""
    )


def _score_drift(
    reference: Reference, scores: np.ndarray, high_risk_threshold: float | None,
    limits: dict[str, Any],
) -> ScoreDrift:
    actual = _numeric_shares(scores, list(reference.score_edges))
    index = psi(np.asarray(reference.score_shares, dtype=float), actual)

    current_share = (
        float(np.mean(scores >= high_risk_threshold)) if high_risk_threshold is not None else None
    )
    change = None
    reasons: list[str] = []
    if index > float(limits["psi_score_threshold"]):
        reasons.append(f"score PSI {index:.3f} > {limits['psi_score_threshold']}")
    if reference.high_risk_share is not None and current_share is not None:
        base = reference.high_risk_share
        if base > 0:
            change = (current_share - base) / base
        else:
            change = float("inf") if current_share > 0 else 0.0
        if abs(change) > float(limits["high_risk_share_change"]):
            reasons.append(f"high-risk share moved from {base:.3f} to {current_share:.3f}")
    return ScoreDrift(
        psi=index,
        reference_high_risk_share=reference.high_risk_share,
        current_high_risk_share=current_share,
        share_change=change,
        flagged=bool(reasons),
        reason="; ".join(reasons),
    )


def _ks(
    values: np.ndarray, reference_sample: pd.DataFrame | None, name: str
) -> tuple[float | None, float | None, str]:
    if reference_sample is None or name not in getattr(reference_sample, "columns", []):
        return None, None, "none"
    from scipy.stats import ks_2samp

    a = values[np.isfinite(values)]
    b = reference_sample[name].to_numpy(dtype=float, na_value=np.nan)
    b = b[np.isfinite(b)]
    if a.size == 0 or b.size == 0:
        return None, None, "none"
    result = ks_2samp(b, a)
    return float(result.statistic), float(result.pvalue), "ks"


def _chi2(
    expected_shares: np.ndarray, actual_shares: np.ndarray, n_expected: int, n_actual: int
) -> tuple[float | None, float | None, str]:
    """Chi-square on the two count vectors, dropping bins empty in both."""
    if not n_expected or not n_actual:
        return None, None, "none"
    from scipy.stats import chi2_contingency

    table = np.vstack([expected_shares * n_expected, actual_shares * n_actual])
    table = table[:, table.sum(axis=0) > 0]
    if table.shape[1] < 2:
        return None, None, "none"
    stat, pvalue, _, _ = chi2_contingency(table)
    return float(stat), float(pvalue), "chi2"
