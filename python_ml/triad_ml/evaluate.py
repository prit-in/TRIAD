"""Evaluation suite and acceptance policy (FR-047, PRD sections 17.5, 17.6).

PR-AUC is the primary metric because its random baseline is the churn rate; accuracy is
computed but never selected on. Every verdict cites the rule and the metric behind it
(UC-11 success criterion).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd

DEFAULT_BOOTSTRAP = 1000
SEED = 42


@dataclass
class Metrics:
    """All metrics for one model on one split (and optionally one segment)."""

    split: str
    segment: str | None = None
    values: dict[str, float] = field(default_factory=dict)
    intervals: dict[str, tuple[float, float]] = field(default_factory=dict)
    confusion: dict[str, int] = field(default_factory=dict)
    n_rows: int = 0
    prevalence: float = 0.0

    def get(self, name: str, default: float = float("nan")) -> float:
        return self.values.get(name, default)

    def to_dict(self) -> dict[str, Any]:
        return {
            "split": self.split,
            "segment": self.segment,
            "n_rows": self.n_rows,
            "prevalence": self.prevalence,
            "values": self.values,
            "intervals": {k: list(v) for k, v in self.intervals.items()},
            "confusion": self.confusion,
        }


def evaluate(
    y_true: np.ndarray,
    y_score: np.ndarray,
    *,
    split: str,
    threshold: float = 0.5,
    segment: str | None = None,
    bootstrap: int = 0,
    seed: int = SEED,
) -> Metrics:
    """Compute the full metric suite. `bootstrap` > 0 adds 95% intervals for PR-AUC/ROC-AUC."""
    from sklearn.metrics import (
        accuracy_score,
        average_precision_score,
        brier_score_loss,
        confusion_matrix,
        f1_score,
        log_loss,
        precision_score,
        recall_score,
        roc_auc_score,
    )

    y_true = np.asarray(y_true).astype(int)
    y_score = np.asarray(y_score, dtype=float)
    n = len(y_true)
    prevalence = float(y_true.mean()) if n else 0.0
    y_pred = (y_score >= threshold).astype(int)

    single_class = len(np.unique(y_true)) < 2
    values: dict[str, float] = {
        "prevalence": prevalence,
        "threshold": float(threshold),
        "accuracy": float(accuracy_score(y_true, y_pred)) if n else float("nan"),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "f2": float(fbeta(y_true, y_pred, beta=2.0)),
    }

    if single_class:
        # A segment with one class cannot support ranking metrics: say so, don't invent them.
        values.update({"pr_auc": float("nan"), "roc_auc": float("nan"),
                       "log_loss": float("nan"), "brier": float("nan"),
                       "brier_skill": float("nan"), "ece": float("nan"),
                       "lift_at_10pct": float("nan")})
    else:
        values["pr_auc"] = float(average_precision_score(y_true, y_score))
        values["roc_auc"] = float(roc_auc_score(y_true, y_score))
        values["log_loss"] = float(log_loss(y_true, np.clip(y_score, 1e-9, 1 - 1e-9)))
        values["brier"] = float(brier_score_loss(y_true, y_score))
        values["brier_skill"] = brier_skill_score(y_true, y_score)
        values["ece"] = expected_calibration_error(y_true, y_score)
        values["lift_at_10pct"] = lift_at_k(y_true, y_score, 0.10)
        values["lift_at_20pct"] = lift_at_k(y_true, y_score, 0.20)

    tn, fp, fn, tp = (
        confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel() if n else (0, 0, 0, 0)
    )
    metrics = Metrics(
        split=split,
        segment=segment,
        values=values,
        confusion={"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        n_rows=n,
        prevalence=prevalence,
    )

    if bootstrap and not single_class:
        metrics.intervals = bootstrap_intervals(y_true, y_score, n_boot=bootstrap, seed=seed)
    return metrics


def fbeta(y_true: np.ndarray, y_pred: np.ndarray, beta: float = 2.0) -> float:
    from sklearn.metrics import fbeta_score

    return float(fbeta_score(y_true, y_pred, beta=beta, zero_division=0))


def brier_skill_score(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """Improvement over a forecast that always predicts the base rate (PRD section 17.5)."""
    from sklearn.metrics import brier_score_loss

    base = float(np.mean(y_true))
    reference = brier_score_loss(y_true, np.full_like(y_score, base, dtype=float))
    if reference == 0:
        return float("nan")
    return float(1.0 - brier_score_loss(y_true, y_score) / reference)


def expected_calibration_error(y_true: np.ndarray, y_score: np.ndarray, bins: int = 10) -> float:
    """Weighted gap between predicted probability and observed rate, over equal-width bins."""
    edges = np.linspace(0.0, 1.0, bins + 1)
    idx = np.clip(np.digitize(y_score, edges[1:-1], right=False), 0, bins - 1)
    total = 0.0
    n = len(y_true)
    for b in range(bins):
        mask = idx == b
        if not mask.any():
            continue
        total += (mask.sum() / n) * abs(y_score[mask].mean() - y_true[mask].mean())
    return float(total)


def lift_at_k(y_true: np.ndarray, y_score: np.ndarray, k: float = 0.10) -> float:
    """Churn rate among the top k of scores divided by the overall rate."""
    n = len(y_true)
    if n == 0:
        return float("nan")
    top_n = max(1, int(round(n * k)))
    order = np.argsort(-y_score, kind="stable")[:top_n]
    base = float(y_true.mean())
    if base == 0:
        return float("nan")
    return float(y_true[order].mean() / base)


def bootstrap_intervals(
    y_true: np.ndarray,
    y_score: np.ndarray,
    *,
    n_boot: int = DEFAULT_BOOTSTRAP,
    seed: int = SEED,
    metrics: tuple[str, ...] = ("pr_auc", "roc_auc"),
) -> dict[str, tuple[float, float]]:
    """Percentile bootstrap intervals. Seeded, so the same data gives the same interval."""
    from sklearn.metrics import average_precision_score, roc_auc_score

    rng = np.random.default_rng(seed)
    n = len(y_true)
    samples: dict[str, list[float]] = {m: [] for m in metrics}
    for _ in range(n_boot):
        idx = rng.integers(0, n, size=n)
        yt, ys = y_true[idx], y_score[idx]
        if len(np.unique(yt)) < 2:
            continue
        if "pr_auc" in samples:
            samples["pr_auc"].append(float(average_precision_score(yt, ys)))
        if "roc_auc" in samples:
            samples["roc_auc"].append(float(roc_auc_score(yt, ys)))
    out: dict[str, tuple[float, float]] = {}
    for name, values in samples.items():
        if values:
            out[name] = (
                float(np.percentile(values, 2.5)),
                float(np.percentile(values, 97.5)),
            )
    return out


def bootstrap_difference(
    y_true: np.ndarray,
    score_a: np.ndarray,
    score_b: np.ndarray,
    *,
    metric: str = "pr_auc",
    n_boot: int = DEFAULT_BOOTSTRAP,
    seed: int = SEED,
) -> dict[str, float]:
    """Paired bootstrap of (A - B) on the same rows — the AP-8 promotion evidence."""
    from sklearn.metrics import average_precision_score, roc_auc_score

    fn = average_precision_score if metric == "pr_auc" else roc_auc_score
    rng = np.random.default_rng(seed)
    n = len(y_true)
    diffs: list[float] = []
    for _ in range(n_boot):
        idx = rng.integers(0, n, size=n)
        yt = y_true[idx]
        if len(np.unique(yt)) < 2:
            continue
        diffs.append(float(fn(yt, score_a[idx]) - fn(yt, score_b[idx])))
    if not diffs:
        return {"mean": float("nan"), "lower_95": float("nan"), "upper_95": float("nan")}
    return {
        "mean": float(np.mean(diffs)),
        "lower_95": float(np.percentile(diffs, 2.5)),
        "upper_95": float(np.percentile(diffs, 97.5)),
    }


def segment_metrics(
    df: pd.DataFrame,
    y_true: np.ndarray,
    y_score: np.ndarray,
    *,
    segment_cols: list[str],
    threshold: float,
    min_rows: int = 50,
    min_positives: int = 30,
) -> list[Metrics]:
    """Per-segment metrics. Thin segments are marked, never silently averaged away.

    A segment needs enough rows **and** enough positives: recall estimated from a handful of
    churners is mostly sampling noise, and letting it drive the AP-6 gap would escalate runs
    for no real reason (UC-10 exception flow: "insufficient data").
    """
    out: list[Metrics] = []
    for col in segment_cols:
        if col not in df.columns:
            continue
        for value in sorted(df[col].dropna().unique().tolist()):
            mask = (df[col] == value).to_numpy()
            positives = int(y_true[mask].sum()) if mask.any() else 0
            if mask.sum() < min_rows or positives < min_positives:
                m = Metrics(split="valid", segment=f"{col}={value}", n_rows=int(mask.sum()))
                m.values["insufficient_data"] = 1.0
                m.values["positives"] = float(positives)
                out.append(m)
                continue
            m = evaluate(
                y_true[mask], y_score[mask], split="valid",
                threshold=threshold, segment=f"{col}={value}",
            )
            out.append(m)
    return out


def recall_gap(segments: list[Metrics]) -> float:
    """Largest recall difference between segments with enough data (AP-6)."""
    recalls = [
        m.get("recall") for m in segments if not m.values.get("insufficient_data")
        and not np.isnan(m.get("recall"))
    ]
    return float(max(recalls) - min(recalls)) if len(recalls) >= 2 else 0.0


# --- Acceptance policy (PRD section 17.6) -------------------------------------

REASON_CODES = (
    "PR_AUC_BELOW_FLOOR",
    "NO_LIFT_OVER_BASELINE",
    "MISCALIBRATED",
    "SEGMENT_RECALL_GAP",
    "GENERALIZATION_GAP",
    "DRIFT_DETECTED",
    "LEAKAGE_SUSPECTED",
    "MODEL_TOO_LARGE",
    "TRAINING_TOO_SLOW",
    "ROC_AUC_BELOW_FLOOR",
    "BRIER_SKILL_TOO_LOW",
)


@dataclass
class AcceptanceVerdict:
    passed: bool
    reason_codes: list[str] = field(default_factory=list)
    rules_checked: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reason_codes": self.reason_codes,
            "rules_checked": self.rules_checked,
            "evidence": self.evidence,
        }


def check_acceptance(
    metrics: Metrics,
    policy: dict[str, Any],
    *,
    logreg_pr_auc: float | None = None,
    train_pr_auc: float | None = None,
    segments: list[Metrics] | None = None,
    model_size_mb: float | None = None,
    train_minutes: float | None = None,
    is_logreg: bool = False,
) -> AcceptanceVerdict:
    """Apply AP-1..AP-7 plus the leakage alarm. Every failure names its rule."""
    codes: list[str] = []
    checked: list[str] = []
    evidence: dict[str, Any] = {}

    pr_auc = metrics.get("pr_auc")
    prevalence = metrics.prevalence
    evidence["valid_pr_auc"] = pr_auc
    evidence["valid_churn_rate"] = prevalence

    # Leakage alarm first: a suspiciously good model blocks selection outright.
    alarm = float(policy.get("leakage_alarm_roc_auc", 0.98))
    checked.append("leakage_alarm")
    if not np.isnan(metrics.get("roc_auc")) and metrics.get("roc_auc") >= alarm:
        codes.append("LEAKAGE_SUSPECTED")
        evidence["roc_auc"] = metrics.get("roc_auc")

    # AP-1: PR-AUC at least churn rate + min lift.
    checked.append("AP-1")
    min_lift = float(policy.get("AP1_min_pr_auc_over_churn_rate", 0.10))
    if np.isnan(pr_auc) or pr_auc < prevalence + min_lift:
        codes.append("PR_AUC_BELOW_FLOOR")

    # AP-2: parsimony margin against logistic regression.
    checked.append("AP-2")
    margin = float(policy.get("AP2_parsimony_margin", 0.01))
    if logreg_pr_auc is not None and not is_logreg:
        evidence["logreg_pr_auc"] = logreg_pr_auc
        if pr_auc - logreg_pr_auc < margin:
            codes.append("NO_LIFT_OVER_BASELINE")

    # AP-3: ROC-AUC floor.
    checked.append("AP-3")
    if not np.isnan(metrics.get("roc_auc")) and metrics.get("roc_auc") < float(
        policy.get("AP3_min_roc_auc", 0.70)
    ):
        codes.append("ROC_AUC_BELOW_FLOOR")

    # AP-4: Brier skill.
    checked.append("AP-4")
    bss = metrics.get("brier_skill")
    evidence["brier_skill"] = bss
    if not np.isnan(bss) and bss < float(policy.get("AP4_min_brier_skill", 0.10)):
        codes.append("BRIER_SKILL_TOO_LOW")

    # AP-5: calibration error.
    checked.append("AP-5")
    ece = metrics.get("ece")
    evidence["ece"] = ece
    if not np.isnan(ece) and ece > float(policy.get("AP5_max_ece_after_calibration", 0.05)):
        codes.append("MISCALIBRATED")

    # AP-6: segment recall gap.
    checked.append("AP-6")
    if segments:
        gap = recall_gap(segments)
        evidence["segment_recall_gap"] = gap
        if gap > float(policy.get("AP6_max_segment_recall_gap", 0.10)):
            codes.append("SEGMENT_RECALL_GAP")

    # AP-7: cost constraints.
    checked.append("AP-7")
    if model_size_mb is not None:
        evidence["model_size_mb"] = model_size_mb
        if model_size_mb > float(policy.get("AP7_max_model_size_mb", 200)):
            codes.append("MODEL_TOO_LARGE")
    if train_minutes is not None:
        evidence["train_minutes"] = train_minutes
        if train_minutes > float(policy.get("AP7_max_train_minutes_per_family", 30)):
            codes.append("TRAINING_TOO_SLOW")

    # Generalisation gap is a diagnostic, not a hard constraint.
    if train_pr_auc is not None and not np.isnan(pr_auc):
        evidence["train_pr_auc"] = train_pr_auc
        if train_pr_auc - pr_auc > 0.25:
            codes.append("GENERALIZATION_GAP")

    return AcceptanceVerdict(passed=not codes, reason_codes=codes, rules_checked=checked,
                             evidence=evidence)


def choose_threshold(
    y_true: np.ndarray,
    y_score: np.ndarray,
    *,
    strategy: str = "capacity",
    capacity_share: float = 0.15,
    beta: float = 2.0,
) -> float:
    """Threshold chosen on validation data only (PRD section 17.9)."""
    if strategy == "capacity":
        if len(y_score) == 0:
            return 0.5
        return float(np.quantile(y_score, 1.0 - capacity_share))
    best_t, best_score = 0.5, -1.0
    for t in np.linspace(0.05, 0.95, 19):
        s = fbeta(y_true, (y_score >= t).astype(int), beta=beta)
        if s > best_score:
            best_t, best_score = float(t), s
    return best_t
