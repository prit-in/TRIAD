"""AT-06, AT-07 and TC-027/TC-044/TC-047: splits, leakage guards, selection.

These tests use a small synthetic feature table with a genuine churn signal, so a correct
pipeline beats the floor and an incorrect one does not.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from python_ml.triad_ml.candidates import (
    Candidate,
    UnregisteredFamily,
    build_pipeline,
    shortlist,
    split_feature_types,
    validate_shortlist,
)
from python_ml.triad_ml.evaluate import (
    bootstrap_difference,
    check_acceptance,
    choose_threshold,
    evaluate,
    expected_calibration_error,
    lift_at_k,
    recall_gap,
    segment_metrics,
)
from python_ml.triad_ml.hygiene import (
    check_forbidden_features,
    drop_degenerate_features,
    run_hygiene,
    shuffled_label_test,
)
from python_ml.triad_ml.splits import (
    GapRuleViolation,
    apply_split,
    assert_no_overlap,
    check_gap_rule,
    check_positive_count,
    plan_split,
)
from python_ml.triad_ml.train import select_model, train_all

POLICY = {
    "AP1_min_pr_auc_over_churn_rate": 0.10,
    "AP2_parsimony_margin": 0.01,
    "AP3_min_roc_auc": 0.70,
    "AP4_min_brier_skill": 0.10,
    "AP5_max_ece_after_calibration": 0.05,
    "AP6_max_segment_recall_gap": 0.10,
    "AP7_max_train_minutes_per_family": 30,
    "AP7_max_model_size_mb": 200,
    "leakage_alarm_roc_auc": 0.98,
}


def make_features(n_per_cutoff: int = 400, seed: int = 7) -> pd.DataFrame:
    """Monthly snapshots with a real signal: churn depends on recency and frequency."""
    rng = np.random.default_rng(seed)
    cutoffs = pd.date_range("2025-01-01", periods=12, freq="MS")
    rows = []
    for cutoff in cutoffs:
        recency = rng.gamma(3.0, 25.0, size=n_per_cutoff)
        frequency = rng.poisson(3.0, size=n_per_cutoff)
        monetary = rng.gamma(2.0, 400.0, size=n_per_cutoff)
        country = rng.choice(["IN", "GB", "US"], size=n_per_cutoff, p=[0.6, 0.25, 0.15])
        # Signal strength chosen so a correct pipeline lands around ROC-AUC 0.80: clearly
        # above the AP-3 floor of 0.70 and far below the 0.98 leakage alarm.
        logit = -1.0 + 0.022 * recency - 0.55 * frequency - 0.0010 * monetary
        p = 1.0 / (1.0 + np.exp(-logit))
        churn = rng.binomial(1, p)
        rows.append(
            pd.DataFrame(
                {
                    "customer_id": [f"C{i:05d}" for i in range(n_per_cutoff)],
                    "snapshot_date": cutoff,
                    "recency_days": recency,
                    "frequency_90d": frequency,
                    "monetary_365d": monetary,
                    "country": country,
                    "churned_90d": churn,
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


# --- splits and the gap rule ---------------------------------------------------


def test_gap_rule_accepts_a_valid_split() -> None:
    check_gap_rule(["2025-01-01", "2025-02-01"], "2025-06-01", "2025-09-01", 90)


def test_gap_rule_rejects_overlapping_label_windows() -> None:
    with pytest.raises(GapRuleViolation, match="gap rule violated"):
        check_gap_rule(["2025-01-01", "2025-05-01"], "2025-06-01", "2025-09-01", 90)
    with pytest.raises(GapRuleViolation, match="gap rule violated"):
        check_gap_rule(["2025-01-01"], "2025-06-01", "2025-07-01", 90)


def test_plan_split_produces_a_gap_respecting_plan() -> None:
    df = make_features(50)
    spec = plan_split(sorted(df.snapshot_date.unique()), horizon_days=90)
    check_gap_rule(
        spec.train_cutoffs + spec.calibration_cutoffs, spec.valid_cutoff, spec.test_cutoff, 90
    )
    assert spec.test_cutoff > spec.valid_cutoff
    assert max(spec.train_cutoffs) < spec.valid_cutoff


def test_plan_split_refuses_a_window_that_is_too_short() -> None:
    dates = pd.date_range("2025-01-01", periods=3, freq="MS")
    with pytest.raises(GapRuleViolation, match="insufficient data"):
        plan_split(list(dates), horizon_days=90)


def test_apply_split_partitions_without_overlap() -> None:
    """AT-06: the training set obeys the gap rule and no snapshot appears twice."""
    df = make_features(100)
    spec = plan_split(sorted(df.snapshot_date.unique()), horizon_days=90)
    split = apply_split(df, spec)

    assert_no_overlap(split)
    assert len(split.train) > 0 and len(split.valid) > 0 and len(split.test) > 0
    assert set(pd.to_datetime(split.valid.snapshot_date).dt.strftime("%Y-%m-%d")) == {
        spec.valid_cutoff
    }
    # Calibration comes out of the training cutoffs, never out of validation.
    cal_dates = set(pd.to_datetime(split.calibration.snapshot_date).dt.strftime("%Y-%m-%d"))
    assert cal_dates.isdisjoint({spec.valid_cutoff, spec.test_cutoff})


def test_insufficient_positives_is_refused() -> None:
    df = make_features(20)
    df["churned_90d"] = 0
    df.loc[:5, "churned_90d"] = 1
    spec = plan_split(sorted(df.snapshot_date.unique()), horizon_days=90)
    with pytest.raises(GapRuleViolation, match="insufficient data"):
        check_positive_count(apply_split(df, spec), minimum=200)


# --- hygiene and leakage -------------------------------------------------------


def test_identifiers_and_label_derived_columns_are_refused() -> None:
    violations = check_forbidden_features(
        ["recency_days", "customer_id", "future_orders", "churn_flag"]
    )
    names = {v["column"] for v in violations}
    assert names == {"customer_id", "future_orders", "churn_flag"}


def test_degenerate_features_are_dropped() -> None:
    df = pd.DataFrame(
        {
            "good": np.arange(100.0),
            "all_null": [None] * 100,
            "constant": [1] * 100,
        }
    )
    kept, dropped = drop_degenerate_features(df, ["good", "all_null", "constant"])
    assert kept == ["good"]
    assert len(dropped) == 2


def test_hygiene_report_blocks_on_forbidden_features() -> None:
    df = make_features(20)
    report = run_hygiene(df, ["recency_days", "customer_id"])
    assert report.passed is False
    assert any(v["column"] == "customer_id" for v in report.violations)


def test_shuffled_labels_collapse_to_the_base_rate() -> None:
    """PRD 17.8: a model that still separates shuffled labels is leaking."""
    df = make_features(200)
    num_cols, cat_cols = split_feature_types(df)
    x = df[num_cols + cat_cols]

    def fit_and_score(features: pd.DataFrame, y: np.ndarray) -> np.ndarray:
        pipe = build_pipeline(Candidate("logreg", "test"), num_cols, cat_cols)
        pipe.fit(features, y)
        return pipe.predict_proba(features)[:, 1]

    result = shuffled_label_test(fit_and_score, x, df.churned_90d.to_numpy())
    assert result["passed"], result["reason"]


# --- candidates and preprocessing ---------------------------------------------


def test_shortlist_always_includes_the_dummy_and_logreg() -> None:
    families = [c.family for c in shortlist(n_rows=5000, n_features=20)]
    assert families[0] == "dummy"
    assert "logreg" in families
    assert len(families) >= 4  # dummy + 3 real families (PO-04)


def test_neural_network_needs_both_conditions() -> None:
    assert "mlp" not in [c.family for c in shortlist(n_rows=500_000, n_features=20)]
    assert "mlp" not in [
        c.family for c in shortlist(n_rows=1000, n_features=20, has_high_dim_features=True)
    ]
    assert "mlp" in [
        c.family
        for c in shortlist(n_rows=500_000, n_features=20, has_high_dim_features=True)
    ]


def test_unregistered_family_is_refused() -> None:
    with pytest.raises(UnregisteredFamily):
        validate_shortlist(["logreg", "my_secret_model"])
    with pytest.raises(UnregisteredFamily):
        build_pipeline(Candidate("my_secret_model", "x"), ["a"], [])


def test_preprocessing_is_fitted_on_training_rows_only() -> None:
    """TC-027 / FR-044: transformers never see validation rows during fitting."""
    train = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0], "c": ["a", "a", "b", "b"]})
    valid = pd.DataFrame({"x": [1000.0, 2000.0], "c": ["a", "b"]})
    y = np.array([0, 1, 0, 1])

    pipe = build_pipeline(Candidate("logreg", "test"), ["x"], ["c"])
    pipe.fit(train, y)
    scaler = pipe.named_steps["prep"].named_transformers_["num"].named_steps["scale"]
    mean_after_train = float(scaler.mean_[0])

    pipe.predict_proba(valid)  # scoring must not refit anything
    assert float(scaler.mean_[0]) == mean_after_train == pytest.approx(2.5)


def test_identifiers_are_excluded_from_feature_columns() -> None:
    df = make_features(10)
    num_cols, cat_cols = split_feature_types(df)
    assert "customer_id" not in num_cols + cat_cols
    assert "snapshot_date" not in num_cols + cat_cols
    assert "churned_90d" not in num_cols + cat_cols
    assert "country" in cat_cols


# --- metrics ------------------------------------------------------------------


def test_metric_suite_is_complete_and_stored_per_split() -> None:
    """FR-047: every listed metric is computed."""
    rng = np.random.default_rng(0)
    y = rng.binomial(1, 0.4, size=500)
    s = np.clip(y * 0.6 + rng.normal(0.2, 0.2, size=500), 0, 1)
    m = evaluate(y, s, split="valid", threshold=0.5, bootstrap=50)

    for name in ("pr_auc", "roc_auc", "precision", "recall", "f1", "log_loss", "brier",
                 "brier_skill", "ece", "lift_at_10pct", "accuracy", "prevalence"):
        assert name in m.values, f"missing metric {name}"
    assert set(m.confusion) == {"tn", "fp", "fn", "tp"}
    assert "pr_auc" in m.intervals
    lo, hi = m.intervals["pr_auc"]
    assert lo <= m.get("pr_auc") <= hi


def test_dummy_pr_auc_equals_the_churn_rate() -> None:
    """The floor the PRD relies on: a prior-only model scores the prevalence."""
    rng = np.random.default_rng(1)
    y = rng.binomial(1, 0.4, size=2000)
    constant = np.full(len(y), 0.4)
    m = evaluate(y, constant, split="valid")
    assert m.get("pr_auc") == pytest.approx(float(y.mean()), abs=0.02)


def test_lift_and_calibration_error_behave() -> None:
    y = np.array([1] * 10 + [0] * 90)
    perfect = np.array([0.9] * 10 + [0.1] * 90)
    assert lift_at_k(y, perfect, 0.10) == pytest.approx(10.0, rel=0.01)
    assert expected_calibration_error(y, np.full(100, 0.1)) < 0.2


def test_segment_metrics_mark_thin_segments() -> None:
    df = pd.DataFrame({"country": ["IN"] * 100 + ["XX"] * 5})
    y = np.array([1, 0] * 50 + [1] * 5)
    s = np.linspace(0, 1, 105)
    segs = segment_metrics(df, y, s, segment_cols=["country"], threshold=0.5)
    thin = next(m for m in segs if m.segment == "country=XX")
    assert thin.values.get("insufficient_data") == 1.0
    assert recall_gap(segs) >= 0.0


def test_bootstrap_difference_detects_a_real_improvement() -> None:
    rng = np.random.default_rng(3)
    y = rng.binomial(1, 0.35, size=1500)
    better = np.clip(y * 0.5 + rng.normal(0.25, 0.18, size=1500), 0, 1)
    worse = np.clip(y * 0.2 + rng.normal(0.35, 0.30, size=1500), 0, 1)
    delta = bootstrap_difference(y, better, worse, n_boot=200)
    assert delta["mean"] > 0
    assert delta["lower_95"] > 0


def test_threshold_capacity_strategy_selects_the_top_share() -> None:
    scores = np.linspace(0, 1, 1000)
    t = choose_threshold(np.zeros(1000), scores, strategy="capacity", capacity_share=0.15)
    assert (scores >= t).mean() == pytest.approx(0.15, abs=0.01)


# --- acceptance policy ---------------------------------------------------------


def test_acceptance_flags_every_failing_rule_with_its_code() -> None:
    from python_ml.triad_ml.evaluate import Metrics

    m = Metrics(split="valid", prevalence=0.40, n_rows=1000)
    m.values = {"pr_auc": 0.42, "roc_auc": 0.62, "brier_skill": 0.02, "ece": 0.20,
                "recall": 0.5}
    verdict = check_acceptance(m, POLICY, model_size_mb=900, train_minutes=120)
    assert verdict.passed is False
    assert {"PR_AUC_BELOW_FLOOR", "ROC_AUC_BELOW_FLOOR", "BRIER_SKILL_TOO_LOW",
            "MISCALIBRATED", "MODEL_TOO_LARGE", "TRAINING_TOO_SLOW"} <= set(verdict.reason_codes)
    assert "AP-1" in verdict.rules_checked


def test_leakage_alarm_fires_on_a_suspiciously_good_model() -> None:
    from python_ml.triad_ml.evaluate import Metrics

    m = Metrics(split="valid", prevalence=0.4, n_rows=1000)
    m.values = {"pr_auc": 0.99, "roc_auc": 0.995, "brier_skill": 0.9, "ece": 0.01}
    verdict = check_acceptance(m, POLICY)
    assert "LEAKAGE_SUSPECTED" in verdict.reason_codes


def test_parsimony_rule_prefers_logistic_regression_within_the_margin() -> None:
    from python_ml.triad_ml.evaluate import Metrics

    m = Metrics(split="valid", prevalence=0.3, n_rows=1000)
    m.values = {"pr_auc": 0.605, "roc_auc": 0.80, "brier_skill": 0.2, "ece": 0.02}
    verdict = check_acceptance(m, POLICY, logreg_pr_auc=0.60)
    assert "NO_LIFT_OVER_BASELINE" in verdict.reason_codes


# --- end-to-end selection (AT-07) ---------------------------------------------


@pytest.mark.slow
def test_selection_is_evidence_based_and_reproducible() -> None:
    """AT-07: the chosen model meets the policy, rejections name their rule, and the
    decision is reconstructable from stored metrics."""
    df = make_features(400)
    spec = plan_split(sorted(df.snapshot_date.unique()), horizon_days=90)
    split = apply_split(df, spec)

    candidates = shortlist(n_rows=len(split.train), n_features=4)
    models = train_all(candidates, split, seed=42)
    assert sum(1 for m in models if not m.failed) >= 4

    result = select_model(models, split, POLICY, segment_cols=["country"])
    assert result.outcome in ("PROPOSE_PROMOTION", "KEEP_CHAMPION"), result.reason
    assert result.selected is not None
    assert result.test_metrics is not None
    assert "AP-1" in result.rules_applied and "AP-2" in result.rules_applied
    assert "test_scored_once" in result.rules_applied
    assert result.evidence["valid_pr_auc"], "per-family validation PR-AUC must be recorded"

    # Reproducible: the same seed and data produce the same selection and metric.
    again = select_model(train_all(candidates, split, seed=42), split, POLICY,
                         segment_cols=["country"])
    assert again.selected.family == result.selected.family
    assert again.selected.valid_pr_auc == pytest.approx(result.selected.valid_pr_auc, abs=1e-6)


@pytest.mark.slow
def test_selection_escalates_when_nothing_clears_the_floor() -> None:
    """ATC-09: features replaced by noise means no promotion, and a reason is recorded."""
    df = make_features(200)
    rng = np.random.default_rng(11)
    for col in ("recency_days", "frequency_90d", "monetary_365d"):
        df[col] = rng.normal(size=len(df))

    spec = plan_split(sorted(df.snapshot_date.unique()), horizon_days=90)
    split = apply_split(df, spec)
    models = train_all(shortlist(n_rows=len(split.train), n_features=4), split, seed=42)
    result = select_model(models, split, POLICY)

    assert result.outcome == "ESCALATE"
    # Noise fails either the ROC-AUC screen (AP-3) or the PR-AUC floor (AP-1); both are
    # legitimate escalations, and both must name the rule that stopped selection.
    assert "floor" in result.reason or "hard constraints" in result.reason
    assert {"AP-1", "AP-3"} & set(result.rules_applied)
    assert result.rejected, "rejections must be recorded with their rule"


@pytest.mark.slow
def test_a_failing_candidate_is_dropped_and_the_run_continues() -> None:
    """ATC-08: one candidate throwing must not stop the others."""
    df = make_features(150)
    spec = plan_split(sorted(df.snapshot_date.unique()), horizon_days=90)
    split = apply_split(df, spec)

    models = train_all(shortlist(n_rows=len(split.train), n_features=4), split, seed=42)
    # Simulate a training failure on one family.
    models[2].failed = True
    models[2].error = "simulated training failure"

    result = select_model(models, split, POLICY)
    assert result.outcome in ("PROPOSE_PROMOTION", "KEEP_CHAMPION", "ESCALATE")
    assert any(r.get("reason", "").startswith("training failed") for r in result.rejected)


# --- tuning inside the selection procedure (PRD 18.4) --------------------------


@pytest.mark.slow
def test_selection_tunes_the_finalists_and_records_the_search() -> None:
    """`tuned = [optuna_tune(f, ...) for f in finalists]` is a step of the procedure, and
    the decision record has to show what the search actually did."""
    df = make_features(400)
    split = apply_split(df, plan_split(sorted(df.snapshot_date.unique()), horizon_days=90))
    models = train_all(shortlist(n_rows=len(split.train), n_features=4), split, seed=42)

    result = select_model(
        models, split, POLICY, seed=42,
        # A small budget: this test is about the procedure, not about finding a better model.
        tuning={"enabled": True, "n_trials": 4, "timeout_seconds": 60, "finalists": 2},
    )
    assert "optuna_tuning" in result.rules_applied
    records = result.evidence["tuning"]
    assert records, "the search must be recorded even when it improves nothing"
    for record in records:
        assert record["trials_run"] <= record["trials_requested"]
        assert record["baseline_value"] is not None
        # A record that claims an improvement must have a value that actually beats it.
        if record["improved"]:
            assert record["best_value"] > record["baseline_value"]


@pytest.mark.slow
def test_selection_without_tuning_is_unchanged() -> None:
    """Tuning is a step, not a rewrite: omitting it must leave the old behaviour intact."""
    df = make_features(400)
    split = apply_split(df, plan_split(sorted(df.snapshot_date.unique()), horizon_days=90))
    models = train_all(shortlist(n_rows=len(split.train), n_features=4), split, seed=42)

    result = select_model(models, split, POLICY)
    assert "optuna_tuning" not in result.rules_applied
    assert "tuning" not in result.evidence


@pytest.mark.slow
def test_a_tuned_model_still_has_to_pass_the_hard_constraints() -> None:
    """A search is not a licence to return a model too large or too slow to deploy."""
    df = make_features(400)
    split = apply_split(df, plan_split(sorted(df.snapshot_date.unique()), horizon_days=90))
    models = train_all(shortlist(n_rows=len(split.train), n_features=4), split, seed=42)

    # AP-7 set so tight that nothing can pass it; the tuned models must be rejected by name.
    strict = {**POLICY, "AP7_max_model_size_mb": 0.0000001}
    result = select_model(
        models, split, strict, seed=42,
        tuning={"enabled": True, "n_trials": 3, "timeout_seconds": 60, "finalists": 1},
    )
    tuned_rejections = [r for r in result.rejected if "(tuned)" in r["family"]]
    if result.evidence.get("tuning") and any(r["improved"] for r in result.evidence["tuning"]):
        assert tuned_rejections, "a tuned model that improved must still face AP-7"
        assert all("MODEL_TOO_LARGE" in r["reason"] for r in tuned_rejections)


def test_the_trusted_type_list_covers_every_registered_family() -> None:
    """TRUSTED_MODEL_TYPES must be computed, not maintained by hand.

    skops refuses to serialise a type it has not been told to trust, and that refusal
    happens at registration - the very last step of a run, after training and tuning have
    already been paid for. A missing entry therefore costs a whole run to discover, which is
    exactly what happened twice: the sigmoid calibration fallback and the `mlp` family were
    both absent from a list whose comment claimed to be complete.

    This enumerates the real set the same way the fix did, over every registered family and
    both calibration methods, and fails if the tuple falls short of it.
    """
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.frozen import FrozenEstimator
    from skops.io import dumps, get_untrusted_types

    from python_ml.triad_ml.candidates import REGISTERED_FAMILIES
    from python_ml.triad_ml.registry import TRUSTED_MODEL_TYPES

    rng = np.random.default_rng(0)
    n = 400
    features = pd.DataFrame(
        {
            "num_a": rng.normal(size=n),
            "num_b": rng.normal(size=n),
            "cat_a": rng.choice(["x", "y", "z"], size=n),
        }
    )
    y = (rng.random(n) < 0.3).astype(int)

    needed: set[str] = set()
    for family in REGISTERED_FAMILIES:
        pipeline = build_pipeline(
            Candidate(family=family, role="candidate", params={}), ["num_a", "num_b"], ["cat_a"]
        )
        pipeline.fit(features, y)
        needed |= set(get_untrusted_types(data=dumps(pipeline)))
        # Both methods: `calibrate` falls back to sigmoid below 500 calibration rows, so a
        # small run reaches types a large one never does.
        for method in ("isotonic", "sigmoid"):
            calibrated = CalibratedClassifierCV(FrozenEstimator(pipeline), method=method)
            calibrated.fit(features, y)
            needed |= set(get_untrusted_types(data=dumps(calibrated)))

    missing = sorted(needed - set(TRUSTED_MODEL_TYPES))
    assert not missing, (
        "TRUSTED_MODEL_TYPES is short of what the registered families actually produce; "
        f"add: {missing}"
    )


def test_ap6_refuses_a_model_whose_recall_differs_by_segment() -> None:
    """The fairness gate has to be able to bite, and nothing proved that it could.

    AP-6 caps the recall gap between segments. It was never refusing anything in a real run,
    and for a structural reason rather than a threshold one: `churn.prepare` passed
    `segment_cols=["country"] if "country" in dataset.columns else None`, the feature job emits
    numeric aggregates and no customer attributes, so every run passed `None` and the gate
    compared an empty list. The attributes are joined for evaluation now; this pins the other
    half, that a genuine gap is actually refused.
    """
    rng = np.random.default_rng(7)
    n = 800
    country = np.where(rng.random(n) < 0.5, "IN", "GB")
    y = (rng.random(n) < 0.35).astype(int)

    # The model finds churners in one country and is close to blind in the other. Scores are
    # built from the label on purpose: this is a fixture for the gate, not a model.
    score = np.where(
        country == "IN",
        np.where(y == 1, rng.uniform(0.75, 0.99, n), rng.uniform(0.0, 0.25, n)),
        np.where(y == 1, rng.uniform(0.0, 0.45, n), rng.uniform(0.0, 0.45, n)),
    )
    frame = pd.DataFrame({"country": country})

    segments = segment_metrics(
        frame, y, score, segment_cols=["country"], threshold=0.5,
        min_rows=50, min_positives=30,
    )
    assert len(segments) == 2, "both countries have enough rows and positives to be compared"

    gap = recall_gap(segments)
    assert gap > 0.10, f"the fixture should produce a wide gap, got {gap}"

    verdict = check_acceptance(
        evaluate(y, score, split="valid", threshold=0.5),
        dict(POLICY),
        segments=segments,
    )
    assert "SEGMENT_RECALL_GAP" in verdict.reason_codes, verdict.reason_codes
    assert not verdict.passed


def test_ap6_passes_when_the_segments_agree() -> None:
    """The counterpart: a gate that refused everything would pass the test above."""
    rng = np.random.default_rng(11)
    n = 800
    country = np.where(rng.random(n) < 0.5, "IN", "GB")
    y = (rng.random(n) < 0.35).astype(int)
    # The same separation in both segments, so the recall gap is small.
    score = np.where(y == 1, rng.uniform(0.7, 0.99, n), rng.uniform(0.0, 0.3, n))

    segments = segment_metrics(
        pd.DataFrame({"country": country}), y, score, segment_cols=["country"],
        threshold=0.5, min_rows=50, min_positives=30,
    )
    assert recall_gap(segments) <= 0.10

    verdict = check_acceptance(
        evaluate(y, score, split="valid", threshold=0.5),
        dict(POLICY),
        segments=segments,
    )
    assert "SEGMENT_RECALL_GAP" not in verdict.reason_codes, verdict.reason_codes
