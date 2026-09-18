"""Leakage checks and feature hygiene (PRD sections 16.2, 17.8; FR-026, EXP-9, ATC-15).

These run before training and can block it. They are deliberately blunt: a suspected leak
stops the pipeline, because a leaking model looks excellent right up to the day it is used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd

# Columns that must never be features: identifiers and anything computed from the label
# window (hygiene rules H2 and H3).
FORBIDDEN_NAME_PATTERNS = (
    "customer_id",
    "order_id",
    "session_id",
    "ticket_id",
    "_id",
    "churn",           # any column derived from the label
    "future_",
    "post_cutoff",
    "label",
    "target",
)

ALLOWED_EXACT = {"churned_90d"}  # the label itself, which is never a feature


@dataclass
class HygieneReport:
    passed: bool
    violations: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "violations": self.violations,
            "dropped": self.dropped,
            "checks_run": self.checks_run,
        }


def check_forbidden_features(feature_cols: list[str]) -> list[dict[str, Any]]:
    """H2/H3: identifiers and label-derived columns may not be features."""
    out: list[dict[str, Any]] = []
    for col in feature_cols:
        if col in ALLOWED_EXACT:
            continue
        low = col.lower()
        for pattern in FORBIDDEN_NAME_PATTERNS:
            if pattern in low:
                out.append({
                    "column": col,
                    "rule": "H2" if pattern.endswith("id") else "H3",
                    "reason": f"column name matches the forbidden pattern '{pattern}'",
                })
                break
    return out


def drop_degenerate_features(
    df: pd.DataFrame,
    feature_cols: list[str],
    *,
    max_null_share: float = 0.95,
    max_dominant_share: float = 0.99,
) -> tuple[list[str], list[str]]:
    """H4: drop features that are almost all null or almost always the same value."""
    keep, dropped = [], []
    n = len(df)
    for col in feature_cols:
        if col not in df.columns or n == 0:
            continue
        s = df[col]
        if s.isna().mean() > max_null_share:
            dropped.append(f"{col}: null share above {max_null_share}")
            continue
        counts = s.value_counts(dropna=True)
        if len(counts) and counts.iloc[0] / n > max_dominant_share:
            dropped.append(f"{col}: single value dominates above {max_dominant_share}")
            continue
        keep.append(col)
    return keep, dropped


def drop_correlated_for_linear(
    df: pd.DataFrame, num_cols: list[str], *, max_abs_corr: float = 0.98
) -> tuple[list[str], list[str]]:
    """H5: for linear models, keep one of any pair correlated above the limit."""
    if len(num_cols) < 2:
        return num_cols, []
    corr = df[num_cols].corr(numeric_only=True).abs()
    keep, dropped = [], []
    for i, col in enumerate(num_cols):
        redundant = False
        for other in num_cols[:i]:
            if other in keep and corr.loc[col, other] > max_abs_corr:
                dropped.append(f"{col}: correlated above {max_abs_corr} with {other}")
                redundant = True
                break
        if not redundant:
            keep.append(col)
    return keep, dropped


def sentinel_test(
    build_features,  # callable: (events) -> DataFrame of features at the cutoff
    events: pd.DataFrame,
    sentinel_events: pd.DataFrame,
    *,
    key_cols: tuple[str, ...] = ("customer_id", "snapshot_date"),
) -> dict[str, Any]:
    """FR-026 / TC-026 in Python: features must not move when post-cutoff events are added.

    The Scala job has its own version of this test; this one guards any Python-side feature
    construction (for example a scoring-time transformation).
    """
    import pandas as pd

    before = build_features(events).sort_values(list(key_cols)).reset_index(drop=True)
    after = (
        build_features(pd.concat([events, sentinel_events], ignore_index=True))
        .sort_values(list(key_cols))
        .reset_index(drop=True)
    )
    if before.shape != after.shape:
        return {"passed": False, "reason": f"shape changed {before.shape} -> {after.shape}"}
    changed = [c for c in before.columns if not before[c].equals(after[c])]
    return {"passed": not changed, "changed_columns": changed}


def shuffled_label_test(
    fit_and_score,  # callable: (X, y) -> scores on the same rows
    x: pd.DataFrame,
    y: np.ndarray,
    *,
    seed: int = 42,
    tolerance: float = 0.05,
) -> dict[str, Any]:
    """PRD 17.8: with shuffled labels, PR-AUC must fall to about the churn rate.

    If a model still separates shuffled labels, the features carry row identity — a leak.
    """
    from sklearn.metrics import average_precision_score

    rng = np.random.default_rng(seed)
    shuffled = rng.permutation(y)
    scores = fit_and_score(x, shuffled)
    pr_auc = float(average_precision_score(shuffled, scores))
    prevalence = float(np.mean(shuffled))
    passed = pr_auc <= prevalence + tolerance
    return {
        "passed": passed,
        "pr_auc_on_shuffled": pr_auc,
        "prevalence": prevalence,
        "tolerance": tolerance,
        "reason": "" if passed else
                  f"PR-AUC {pr_auc:.4f} on shuffled labels exceeds prevalence "
                  f"{prevalence:.4f} + {tolerance}",
    }


def run_hygiene(
    df: pd.DataFrame,
    feature_cols: list[str],
    *,
    linear: bool = False,
    catalog_names: set[str] | None = None,
) -> HygieneReport:
    """Run every static hygiene rule and report what would be dropped or blocked."""
    report = HygieneReport(passed=True)
    report.checks_run = ["H2", "H3", "H4", "H7"]

    violations = check_forbidden_features(feature_cols)
    if violations:
        report.passed = False
        report.violations.extend(violations)

    if catalog_names is not None:
        # H7: every feature must have a catalogue entry.
        missing = [c for c in feature_cols if c not in catalog_names]
        if missing:
            report.passed = False
            report.violations.append({
                "rule": "H7",
                "reason": "features without a catalogue entry",
                "columns": missing,
            })

    kept, dropped = drop_degenerate_features(df, feature_cols)
    report.dropped.extend(dropped)

    if linear:
        report.checks_run.append("H5")
        num_cols = [c for c in kept if np.issubdtype(df[c].dtype, np.number)]
        _, corr_dropped = drop_correlated_for_linear(df, num_cols)
        report.dropped.extend(corr_dropped)

    return report
