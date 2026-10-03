"""Training, calibration and the deterministic selection procedure (PRD sections 17, 18).

The selection procedure is a fixed five-stage pipeline — screen, tune, calibrate, test once,
compare with the champion — and every choice inside it cites the metric and rule that
produced it, so the outcome is reconstructable from stored metrics alone (UC-14).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from python_ml.triad_ml.candidates import (
    Candidate,
    build_pipeline,
    split_feature_types,
)
from python_ml.triad_ml.evaluate import (
    AcceptanceVerdict,
    Metrics,
    bootstrap_difference,
    check_acceptance,
    choose_threshold,
    evaluate,
    segment_metrics,
)
from python_ml.triad_ml.splits import SplitData

if TYPE_CHECKING:  # pragma: no cover
    from sklearn.pipeline import Pipeline

LABEL = "churned_90d"


@dataclass
class TrainedModel:
    candidate: Candidate
    pipeline: Pipeline
    train_metrics: Metrics
    valid_metrics: Metrics
    train_seconds: float
    model_size_mb: float
    seed: int
    calibrated: bool = False
    calibration_method: str | None = None
    calibration_note: str | None = None
    failed: bool = False
    error: str | None = None

    @property
    def family(self) -> str:
        return self.candidate.family

    @property
    def valid_pr_auc(self) -> float:
        return self.valid_metrics.get("pr_auc")

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "role": self.candidate.role,
            "params": self.candidate.params,
            "seed": self.seed,
            "train_seconds": round(self.train_seconds, 3),
            "model_size_mb": round(self.model_size_mb, 3),
            "calibrated": self.calibrated,
            "calibration_method": self.calibration_method,
            "valid": self.valid_metrics.to_dict(),
            "train": self.train_metrics.to_dict(),
            "failed": self.failed,
            "error": self.error,
        }


def positive_weight(y: np.ndarray) -> float:
    pos = float(np.sum(y))
    neg = float(len(y) - pos)
    return (neg / pos) if pos > 0 else 1.0


def model_size_mb(pipeline: Pipeline) -> float:
    import pickle

    return len(pickle.dumps(pipeline)) / (1024 * 1024)


def train_candidate(
    candidate: Candidate,
    split: SplitData,
    *,
    seed: int = 42,
    threshold: float = 0.5,
    label_col: str = LABEL,
) -> TrainedModel:
    """Train one candidate. A failure is captured, not raised: UC-09 drops the candidate
    if at least two others survive."""
    num_cols, cat_cols = split_feature_types(
        split.train, label_col=label_col, exclude=split.excluded_cols
    )
    x_train = split.train[num_cols + cat_cols]
    y_train = split.train[label_col].to_numpy().astype(int)
    x_valid = split.valid[num_cols + cat_cols]
    y_valid = split.valid[label_col].to_numpy().astype(int)

    pipeline = build_pipeline(
        candidate, num_cols, cat_cols, pos_weight=positive_weight(y_train), seed=seed
    )

    started = time.perf_counter()
    try:
        pipeline.fit(x_train, y_train)
    except Exception as exc:
        empty = Metrics(split="valid")
        return TrainedModel(
            candidate, pipeline, Metrics(split="train"), empty, 0.0, 0.0, seed,
            failed=True, error=f"{type(exc).__name__}: {exc}",
        )
    elapsed = time.perf_counter() - started

    p_train = pipeline.predict_proba(x_train)[:, 1]
    p_valid = pipeline.predict_proba(x_valid)[:, 1]

    return TrainedModel(
        candidate=candidate,
        pipeline=pipeline,
        train_metrics=evaluate(y_train, p_train, split="train", threshold=threshold),
        valid_metrics=evaluate(y_valid, p_valid, split="valid", threshold=threshold),
        train_seconds=elapsed,
        model_size_mb=model_size_mb(pipeline),
        seed=seed,
    )


def train_all(
    candidates: list[Candidate], split: SplitData, *, seed: int = 42, threshold: float = 0.5
) -> list[TrainedModel]:
    return [train_candidate(c, split, seed=seed, threshold=threshold) for c in candidates]


def calibrate(
    model: TrainedModel, split: SplitData, *, method: str = "isotonic", label_col: str = LABEL
) -> TrainedModel:
    """Fit calibration on the calibration slice, which is held out from the training cutoffs.

    Falls back to sigmoid when the slice is too small for isotonic regression, and returns
    the model unchanged (with a recorded reason) when there is no slice at all.
    """
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.frozen import FrozenEstimator

    if split.calibration is None or len(split.calibration) == 0:
        model.calibration_method = "none: no calibration slice available"
        return model

    num_cols, cat_cols = split_feature_types(
        split.train, label_col=label_col, exclude=split.excluded_cols
    )
    x_cal = split.calibration[num_cols + cat_cols]
    y_cal = split.calibration[label_col].to_numpy().astype(int)
    if len(np.unique(y_cal)) < 2:
        model.calibration_method = "none: calibration slice has a single class"
        return model

    chosen = method if len(y_cal) >= 500 else "sigmoid"
    if chosen != method:
        # Silently substituting one calibrator for another turns the remedy chain into a
        # shorter chain that still reports its full length. EXP-5 escalated four times saying
        # "after isotonic and sigmoid" when isotonic had never been fitted.
        model.calibration_note = (
            f"{method} requested; calibration slice has {len(y_cal)} rows, "
            f"fewer than the 500 isotonic needs, so {chosen} was fitted instead"
        )
    calibrated = CalibratedClassifierCV(FrozenEstimator(model.pipeline), method=chosen)
    calibrated.fit(x_cal, y_cal)

    x_valid = split.valid[num_cols + cat_cols]
    y_valid = split.valid[label_col].to_numpy().astype(int)
    p_valid = calibrated.predict_proba(x_valid)[:, 1]

    model.pipeline = calibrated
    model.calibrated = True
    model.calibration_method = chosen
    model.valid_metrics = evaluate(
        y_valid, p_valid, split="valid", threshold=model.valid_metrics.get("threshold", 0.5)
    )
    return model


@dataclass
class SelectionResult:
    """Everything needed to reconstruct the decision from stored values (UC-14)."""

    outcome: str  # PROPOSE_PROMOTION | KEEP_CHAMPION | ESCALATE
    selected: TrainedModel | None
    reason: str
    rules_applied: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    test_metrics: Metrics | None = None
    champion_delta: dict[str, float] | None = None
    verdicts: dict[str, AcceptanceVerdict] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "selected_family": self.selected.family if self.selected else None,
            "reason": self.reason,
            "rules_applied": self.rules_applied,
            "evidence": self.evidence,
            "rejected": self.rejected,
            "test": self.test_metrics.to_dict() if self.test_metrics else None,
            "champion_delta": self.champion_delta,
            "verdicts": {k: v.to_dict() for k, v in self.verdicts.items()},
        }


def select_model(
    models: list[TrainedModel],
    split: SplitData,
    policy: dict[str, Any],
    *,
    champion_scores: np.ndarray | None = None,
    segment_cols: list[str] | None = None,
    label_col: str = LABEL,
    threshold_strategy: str = "capacity",
    capacity_share: float = 0.15,
    tuning: dict[str, Any] | None = None,
    seed: int = 42,
) -> SelectionResult:
    """PRD section 18.4, implemented literally.

    Order matters: hard constraints, then ranking, then parsimony, then floor, then
    calibration, then segment checks, then a single scoring of the test snapshot.
    """
    rules: list[str] = []
    rejected: list[dict[str, Any]] = []
    verdicts: dict[str, AcceptanceVerdict] = {}

    alive = [m for m in models if not m.failed]
    for m in models:
        if m.failed:
            rejected.append({"family": m.family, "reason": f"training failed: {m.error}",
                             "rule": "UC-09 exception flow"})
    if len(alive) < 2:
        return SelectionResult(
            "ESCALATE", None, "fewer than two candidates survived training", rules,
            rejected=rejected,
        )

    num_cols, cat_cols = split_feature_types(
        split.train, label_col=label_col, exclude=split.excluded_cols
    )
    y_valid = split.valid[label_col].to_numpy().astype(int)
    x_valid = split.valid[num_cols + cat_cols]

    logreg = next((m for m in alive if m.family == "logreg"), None)
    logreg_pr = logreg.valid_pr_auc if logreg else None

    # --- stage A: screen against hard constraints ----------------------------
    screened: list[TrainedModel] = []
    for m in alive:
        segs = None
        if segment_cols:
            p = m.pipeline.predict_proba(x_valid)[:, 1]
            segs = segment_metrics(
                split.valid, y_valid, p, segment_cols=segment_cols,
                threshold=m.valid_metrics.get("threshold", 0.5),
            )
        verdict = check_acceptance(
            m.valid_metrics,
            policy,
            logreg_pr_auc=logreg_pr,
            train_pr_auc=m.train_metrics.get("pr_auc"),
            segments=segs,
            model_size_mb=m.model_size_mb,
            train_minutes=m.train_seconds / 60.0,
            is_logreg=(m.family == "logreg"),
        )
        verdicts[m.family] = verdict
        # Screen-time hard constraints exclude the calibration-sensitive rules (AP-4, AP-5):
        # PRD 17.7 says class weighting distorts probabilities and 17.6 measures ECE *after*
        # calibration, so judging Brier skill before stage C would reject every weighted
        # model. Both rules are re-checked on the calibrated model below (DEC-01).
        hard = {"MODEL_TOO_LARGE", "TRAINING_TOO_SLOW", "ROC_AUC_BELOW_FLOOR",
                "LEAKAGE_SUSPECTED"}
        blocking = [c for c in verdict.reason_codes if c in hard]
        if blocking:
            rejected.append({
                "family": m.family,
                "reason": f"rejected by {', '.join(blocking)}",
                "rule": "AP-3/AP-4/AP-7 or the leakage alarm",
                "evidence": verdict.evidence,
            })
            if "LEAKAGE_SUSPECTED" in blocking:
                rules.append("leakage_alarm")
                return SelectionResult(
                    "ESCALATE", None,
                    f"leakage suspected: {m.family} reached ROC-AUC "
                    f"{m.valid_metrics.get('roc_auc'):.3f}",
                    rules, verdicts=verdicts, rejected=rejected,
                )
            continue
        screened.append(m)
    rules.extend(("AP-3", "AP-4", "AP-7"))

    # --- stage B: tune the finalists (PRD 18.4) -------------------------------
    # Only the finalists, and only on validation. A tuned model then goes through exactly the
    # same hard constraints as a screened one: a search is not a licence to return a model
    # that is too large or too slow to deploy.
    tuning_records: list[dict[str, Any]] = []
    if tuning and screened:
        from python_ml.triad_ml.tune import tune_finalists

        tuned, tuning_records = tune_finalists(screened, split, config=tuning, seed=seed)
        for m in tuned:
            verdict = check_acceptance(
                m.valid_metrics, policy, logreg_pr_auc=logreg_pr,
                train_pr_auc=m.train_metrics.get("pr_auc"),
                model_size_mb=m.model_size_mb, train_minutes=m.train_seconds / 60.0,
                is_logreg=False,
            )
            blocking = [c for c in verdict.reason_codes
                        if c in {"MODEL_TOO_LARGE", "TRAINING_TOO_SLOW",
                                 "ROC_AUC_BELOW_FLOOR", "LEAKAGE_SUSPECTED"}]
            if blocking:
                rejected.append({
                    "family": f"{m.family} (tuned)",
                    "reason": f"the tuned model is rejected by {', '.join(blocking)}",
                    "rule": "AP-3/AP-7 or the leakage alarm",
                    "evidence": verdict.evidence,
                })
                continue
            verdicts[f"{m.family} (tuned)"] = verdict
            screened.append(m)
        if tuning_records:
            rules.append("optuna_tuning")

    if not screened:
        return SelectionResult(
            "ESCALATE", None, "no candidate passed the hard constraints", rules,
            rejected=rejected, verdicts=verdicts,
        )

    # --- rank on validation PR-AUC -------------------------------------------
    ranked = sorted(
        [m for m in screened if m.family != "dummy"],
        key=lambda m: (-(m.valid_pr_auc if not np.isnan(m.valid_pr_auc) else -1), m.family),
    )
    if not ranked:
        return SelectionResult("ESCALATE", None, "only the dummy baseline survived", rules,
                               rejected=rejected, verdicts=verdicts)
    best = ranked[0]
    rules.append("rank_by_valid_pr_auc")
    search_evidence: dict[str, Any] = {"tuning": tuning_records} if tuning_records else {}

    # --- AP-2 parsimony -------------------------------------------------------
    margin = float(policy.get("AP2_parsimony_margin", 0.01))
    if (logreg is not None and best.family != "logreg"
            and best.valid_pr_auc - logreg.valid_pr_auc < margin):
            rejected.append({
                "family": best.family,
                "reason": f"beats logistic regression by less than the {margin} parsimony margin",
                "rule": "AP-2",
            })
            best = logreg
    rules.append("AP-2")

    # --- AP-1 floor -----------------------------------------------------------
    min_lift = float(policy.get("AP1_min_pr_auc_over_churn_rate", 0.10))
    floor = split.class_balance.get("valid", best.valid_metrics.prevalence) + min_lift
    rules.append("AP-1")
    if np.isnan(best.valid_pr_auc) or best.valid_pr_auc < floor:
        return SelectionResult(
            "ESCALATE", None,
            f"no candidate clears the performance floor: best validation PR-AUC "
            f"{best.valid_pr_auc:.4f} < churn rate + {min_lift} = {floor:.4f}",
            rules, evidence={"best_family": best.family, "floor": floor},
            rejected=rejected, verdicts=verdicts,
        )

    # --- stage C: calibrate, then judge the calibration-sensitive rules -------
    best = calibrate(best, split, label_col=label_col)
    rules.append("AP-5")
    rules.append("AP-4")

    # --- threshold on validation only ----------------------------------------
    p_valid = best.pipeline.predict_proba(x_valid)[:, 1]
    threshold = choose_threshold(
        y_valid, p_valid, strategy=threshold_strategy, capacity_share=capacity_share
    )
    best.valid_metrics = evaluate(y_valid, p_valid, split="valid", threshold=threshold,
                                  bootstrap=200)

    # PRD 19.4 remedy chain for MISCALIBRATED: isotonic, then sigmoid, then reduce class
    # weighting (weights distort probabilities, PRD 17.7). Each step is a recorded attempt.
    def _judge(model: TrainedModel) -> tuple[np.ndarray, AcceptanceVerdict, list[str]]:
        scores = model.pipeline.predict_proba(x_valid)[:, 1]
        model.valid_metrics = evaluate(
            y_valid, scores, split="valid", threshold=threshold, bootstrap=200
        )
        verdict = check_acceptance(model.valid_metrics, policy,
                                   is_logreg=(model.family == "logreg"))
        codes = [c for c in verdict.reason_codes
                 if c in {"MISCALIBRATED", "BRIER_SKILL_TOO_LOW"}]
        return scores, verdict, codes

    p_valid, post, calibration_codes = _judge(best)
    remedies_tried = [f"calibration:{best.calibration_method}"]

    for method, unweighted in (("sigmoid", False), ("sigmoid", True)):
        if not calibration_codes:
            break
        if method == best.calibration_method and not unweighted:
            continue
        retry_candidate = Candidate(
            best.candidate.family,
            best.candidate.role,
            {**best.candidate.params, **({"balance_classes": False} if unweighted else {})},
            best.candidate.justification,
        )
        retry = calibrate(
            train_candidate(retry_candidate, split, seed=best.seed, threshold=threshold),
            split, method=method, label_col=label_col,
        )
        remedies_tried.append(
            f"calibration:{method}" + (" + class weighting removed" if unweighted else "")
        )
        retry_scores, retry_post, retry_codes = _judge(retry)
        if not retry_codes:
            best, p_valid, post, calibration_codes = retry, retry_scores, retry_post, []

    rules.append("AP-5_remedy_chain")
    verdicts[f"{best.family}@calibrated"] = post
    if calibration_codes:
        return SelectionResult(
            "ESCALATE", best,
            f"calibration rules still failing after "
            f"{', '.join(remedies_tried)}: {', '.join(calibration_codes)}",
            rules,
            evidence={
                **post.evidence,
                "calibration": best.calibration_method,
                "calibration_note": best.calibration_note,
                "calibration_remedies_tried": remedies_tried,
                "ece_after_calibration": best.valid_metrics.get("ece"),
                "brier_skill_after_calibration": best.valid_metrics.get("brier_skill"),
                "class_weighting": "removed"
                if best.candidate.params.get("balance_classes") is False
                else "balanced",
            },
            rejected=rejected, verdicts=verdicts,
        )

    # --- AP-6 segment checks --------------------------------------------------
    if segment_cols:
        segs = segment_metrics(split.valid, y_valid, p_valid, segment_cols=segment_cols,
                               threshold=threshold)
        from python_ml.triad_ml.evaluate import recall_gap

        gap = recall_gap(segs)
        rules.append("AP-6")
        if gap > float(policy.get("AP6_max_segment_recall_gap", 0.10)):
            return SelectionResult(
                "ESCALATE", best, f"segment recall gap {gap:.3f} exceeds the limit", rules,
                evidence={"segment_recall_gap": gap}, rejected=rejected, verdicts=verdicts,
            )

    # --- stage D: score the test snapshot exactly once ------------------------
    x_test = split.test[num_cols + cat_cols]
    y_test = split.test[label_col].to_numpy().astype(int)
    p_test = best.pipeline.predict_proba(x_test)[:, 1]
    test_metrics = evaluate(y_test, p_test, split="test", threshold=threshold, bootstrap=500)
    rules.append("test_scored_once")

    evidence = {
        "valid_pr_auc": {m.family: m.valid_pr_auc for m in alive},
        "valid_churn_rate": split.class_balance.get("valid"),
        "threshold": threshold,
        "calibration": best.calibration_method,
        "calibration_note": best.calibration_note,
        "calibration_remedies_tried": remedies_tried,
        "class_weighting": "removed"
        if best.candidate.params.get("balance_classes") is False
        else "balanced",
        "ece_after_calibration": best.valid_metrics.get("ece"),
        "brier_skill_after_calibration": best.valid_metrics.get("brier_skill"),
        "selected_role": best.candidate.role,
        **search_evidence,
    }

    # --- stage E: compare with the champion on the same cutoff ---------------
    if champion_scores is None:
        return SelectionResult(
            "PROPOSE_PROMOTION", best, "no champion yet: the first model needs approval",
            rules, evidence=evidence, rejected=rejected, test_metrics=test_metrics,
            verdicts=verdicts,
        )

    delta = bootstrap_difference(y_test, p_test, np.asarray(champion_scores, dtype=float))
    rules.append("AP-8")
    if delta["lower_95"] > 0:
        return SelectionResult(
            "PROPOSE_PROMOTION", best,
            f"challenger beats the champion by {delta['mean']:.4f} PR-AUC "
            f"(95% interval {delta['lower_95']:.4f} to {delta['upper_95']:.4f})",
            rules, evidence=evidence, rejected=rejected, test_metrics=test_metrics,
            champion_delta=delta, verdicts=verdicts,
        )
    return SelectionResult(
        "KEEP_CHAMPION", best,
        f"challenger does not beat the champion with confidence: difference "
        f"{delta['mean']:.4f}, lower bound {delta['lower_95']:.4f}",
        rules, evidence=evidence, rejected=rejected, test_metrics=test_metrics,
        champion_delta=delta, verdicts=verdicts,
    )
