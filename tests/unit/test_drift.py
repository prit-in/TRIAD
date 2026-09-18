"""Drift measurement and retraining triggers (PRD sections 19.1, 19.2; FR-081, PO-06).

The fixtures below shift a distribution by a known amount, so a working index has to move and
a stable feature has to stay put. Nothing here asserts an absolute PSI value from the
literature; the assertions are about ordering and about which rule fires.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from agents.retraining import MonitoringState, RetrainingPolicy
from python_ml.triad_ml.drift import (
    DEFAULT_THRESHOLDS,
    OTHER,
    DriftResult,
    FeatureDrift,
    Reference,
    ScoreDrift,
    compute_drift,
    psi,
)

RNG = np.random.default_rng(20260918)


def _frame(n: int, *, shift: float = 0.0, country_mix: tuple[float, float] = (0.7, 0.3)):
    countries = RNG.choice(["UK", "FR"], size=n, p=list(country_mix))
    return pd.DataFrame(
        {
            "recency_days": RNG.normal(60 + shift, 15, size=n),
            "orders_365d": RNG.poisson(4, size=n).astype(float),
            "country": countries,
        }
    )


@pytest.fixture()
def reference() -> Reference:
    train = _frame(4000)
    scores = RNG.beta(2, 8, size=4000)
    return Reference.from_frame(
        train, ["recency_days", "orders_365d", "country"],
        scores=scores, high_risk_threshold=0.66,
    )


# --- PSI itself ---------------------------------------------------------------


def test_psi_is_zero_for_identical_share_vectors() -> None:
    shares = np.array([0.1, 0.2, 0.3, 0.4])
    assert psi(shares, shares) == pytest.approx(0.0, abs=1e-12)


def test_psi_is_symmetric_and_grows_with_the_shift() -> None:
    base = np.array([0.25, 0.25, 0.25, 0.25])
    small = np.array([0.30, 0.25, 0.25, 0.20])
    large = np.array([0.60, 0.20, 0.15, 0.05])
    assert psi(base, small) == pytest.approx(psi(small, base))
    assert psi(base, large) > psi(base, small) > 0


def test_psi_stays_finite_when_a_bin_empties() -> None:
    """Without the epsilon this is ln(0) and the whole report becomes -inf."""
    assert np.isfinite(psi(np.array([0.5, 0.5]), np.array([1.0, 0.0])))


# --- feature drift ------------------------------------------------------------


def test_stable_data_is_not_flagged(reference: Reference) -> None:
    result = compute_drift(reference, _frame(4000))
    assert result.flagged_features == []
    assert result.max_psi < DEFAULT_THRESHOLDS["psi_feature_threshold"]


def test_a_shifted_numeric_feature_is_flagged(reference: Reference) -> None:
    result = compute_drift(reference, _frame(4000, shift=45.0))
    assert "recency_days" in result.flagged_features
    # the untouched feature must not be dragged along
    assert "orders_365d" not in result.flagged_features


def test_drift_grows_monotonically_with_the_size_of_the_shift(reference: Reference) -> None:
    indexes = [
        compute_drift(reference, _frame(4000, shift=s)).psi_map()["recency_days"]
        for s in (0.0, 10.0, 30.0, 60.0)
    ]
    assert indexes == sorted(indexes)


def test_a_categorical_shift_is_measured(reference: Reference) -> None:
    result = compute_drift(reference, _frame(4000, country_mix=(0.2, 0.8)))
    assert result.psi_map()["country"] > DEFAULT_THRESHOLDS["psi_feature_threshold"]
    country = next(f for f in result.features if f.feature == "country")
    assert country.test == "chi2" and country.p_value is not None


def test_unseen_categories_do_not_vanish(reference: Reference) -> None:
    current = _frame(2000)
    current.loc[: len(current) // 2, "country"] = "ZZ"   # a country never seen at training
    result = compute_drift(reference, current)
    assert result.psi_map()["country"] > 0.0
    assert "country" in result.flagged_features


def test_new_nulls_are_counted_rather_than_dropped(reference: Reference) -> None:
    current = _frame(4000)
    current.loc[: int(len(current) * 0.4), "recency_days"] = np.nan
    result = compute_drift(reference, current)
    assert result.psi_map()["recency_days"] > DEFAULT_THRESHOLDS["psi_feature_threshold"]


def test_a_feature_that_disappeared_is_flagged_not_skipped(reference: Reference) -> None:
    result = compute_drift(reference, _frame(1000).drop(columns=["recency_days"]))
    missing = next(f for f in result.features if f.feature == "recency_days")
    assert missing.flagged and "missing" in missing.note


def test_top_features_bound_the_flag_but_not_the_measurement(reference: Reference) -> None:
    result = compute_drift(reference, _frame(4000, shift=45.0), top_features=["orders_365d"])
    assert result.flagged_features == []
    assert result.psi_map()["recency_days"] > 0.25   # still measured and reported


def test_ks_is_reported_only_when_training_rows_are_available(reference: Reference) -> None:
    without = compute_drift(reference, _frame(1000))
    assert {f.test for f in without.features if f.kind == "numeric"} == {"none"}

    with_sample = compute_drift(reference, _frame(1000, shift=45.0),
                                reference_sample=_frame(1000))
    recency = next(f for f in with_sample.features if f.feature == "recency_days")
    assert recency.test == "ks" and recency.statistic is not None


def test_reference_round_trips_through_json(reference: Reference) -> None:
    import json

    restored = Reference.from_dict(json.loads(json.dumps(reference.to_dict())))
    current = _frame(2000, shift=20.0)
    assert compute_drift(restored, current).psi_map() == pytest.approx(
        compute_drift(reference, current).psi_map()
    )


def test_bins_are_cut_at_training_deciles_not_recut(reference: Reference) -> None:
    """Recutting on current data hides the shift; this is the test that would catch it."""
    edges = reference.numeric["recency_days"]["edges"]
    shifted = compute_drift(reference, _frame(4000, shift=45.0))
    assert reference.numeric["recency_days"]["edges"] == edges   # untouched by measuring
    assert shifted.psi_map()["recency_days"] > 0.25


# --- score drift --------------------------------------------------------------


def test_score_drift_flags_a_shifted_score_distribution(reference: Reference) -> None:
    result = compute_drift(reference, _frame(2000), scores=RNG.beta(8, 2, size=2000),
                           high_risk_threshold=0.66)
    assert result.score is not None and result.score.flagged
    assert result.score.psi > DEFAULT_THRESHOLDS["psi_score_threshold"]


def test_score_drift_is_quiet_on_the_same_distribution(reference: Reference) -> None:
    result = compute_drift(reference, _frame(2000), scores=RNG.beta(2, 8, size=2000),
                           high_risk_threshold=0.66)
    assert result.score is not None and not result.score.flagged


def test_high_risk_share_change_is_relative_to_the_reference(reference: Reference) -> None:
    scores = np.concatenate([RNG.beta(2, 8, size=1500), np.full(500, 0.9)])
    result = compute_drift(reference, _frame(2000), scores=scores, high_risk_threshold=0.66)
    assert result.score is not None
    assert result.score.share_change is not None and result.score.share_change > 0.5
    assert result.score.flagged


# --- triggers RT-1..RT-9 ------------------------------------------------------

NOW = datetime(2026, 9, 18, tzinfo=UTC)
LONG_AGO = NOW - timedelta(days=60)


def _policy(**overrides) -> RetrainingPolicy:
    return RetrainingPolicy({"cooldown_days": 7, **overrides})


def _state(**kwargs) -> MonitoringState:
    base = {"now": NOW, "last_trained_at": LONG_AGO}
    base.update(kwargs)
    return MonitoringState(**base)


def _drift(psi_by_feature: dict[str, float], score: ScoreDrift | None = None) -> DriftResult:
    single = DEFAULT_THRESHOLDS["psi_feature_threshold"]
    return DriftResult(
        features=[
            FeatureDrift(name, "numeric", value, "none", flagged=value > single)
            for name, value in psi_by_feature.items()
        ],
        score=score,
        thresholds=dict(DEFAULT_THRESHOLDS),
    )


def test_rt1_fires_below_the_ap1_floor() -> None:
    decision = _policy().evaluate(_state(matured_pr_auc=[0.18], ap1_floor=0.25))
    assert "RT-1" in decision.rule_ids and decision.should_retrain


def test_rt1_fires_when_the_metric_drops_below_deployment() -> None:
    decision = _policy().evaluate(
        _state(matured_pr_auc=[0.30], pr_auc_at_deployment=0.40, ap1_floor=0.25)
    )
    assert "RT-1" in decision.rule_ids


def test_rt1_is_quiet_within_bounds() -> None:
    decision = _policy().evaluate(
        _state(matured_pr_auc=[0.38], pr_auc_at_deployment=0.40, ap1_floor=0.25)
    )
    assert "RT-1" not in decision.rule_ids


def test_rt2_fires_on_one_feature_over_the_single_threshold() -> None:
    decision = _policy().evaluate(_state(drift=_drift({"recency_days": 0.31, "a": 0.01})))
    assert "RT-2" in decision.rule_ids


def test_rt2_fires_on_three_features_over_the_lower_threshold() -> None:
    decision = _policy().evaluate(
        _state(drift=_drift({"a": 0.12, "b": 0.13, "c": 0.11, "d": 0.02}))
    )
    assert "RT-2" in decision.rule_ids
    signal = next(s for s in decision.fired if s.rule_id == "RT-2")
    assert signal.evidence["over_count_threshold"] == ["a", "b", "c"]


def test_rt2_is_quiet_with_two_mild_features() -> None:
    decision = _policy().evaluate(_state(drift=_drift({"a": 0.12, "b": 0.13, "c": 0.02})))
    assert "RT-2" not in decision.rule_ids


def test_rt3_fires_on_score_drift() -> None:
    score = ScoreDrift(psi=0.4, reference_high_risk_share=0.1, current_high_risk_share=0.3,
                       share_change=2.0, flagged=True, reason="score PSI 0.400 > 0.2")
    decision = _policy().evaluate(_state(drift=_drift({"a": 0.01}, score=score)))
    assert "RT-3" in decision.rule_ids


def test_rt4_needs_a_decline_across_every_matured_cutoff() -> None:
    declining = _policy().evaluate(_state(matured_pr_auc=[0.42, 0.39, 0.35]))
    assert "RT-4" in declining.rule_ids

    bumpy = _policy().evaluate(_state(matured_pr_auc=[0.42, 0.35, 0.39]))
    assert "RT-4" not in bumpy.rule_ids


def test_rt4_reports_insufficient_history_instead_of_passing() -> None:
    decision = _policy().evaluate(_state(matured_pr_auc=[0.42, 0.39]))
    signal = next(s for s in decision.signals if s.rule_id == "RT-4")
    assert not signal.fired and "not evaluated" in signal.reason


def test_rt5_fires_at_twenty_percent_more_labels() -> None:
    decision = _policy().evaluate(
        _state(labelled_rows_at_training=10000, labelled_rows_now=12000)
    )
    assert "RT-5" in decision.rule_ids and decision.should_retrain


def test_rt5_is_quiet_below_the_share() -> None:
    decision = _policy().evaluate(
        _state(labelled_rows_at_training=10000, labelled_rows_now=11000)
    )
    assert "RT-5" not in decision.rule_ids


def test_rt6_fires_on_the_monthly_schedule() -> None:
    decision = _policy().evaluate(_state(last_trained_at=NOW - timedelta(days=31)))
    assert "RT-6" in decision.rule_ids

    fresh = _policy().evaluate(_state(last_trained_at=NOW - timedelta(days=10)))
    assert "RT-6" not in fresh.rule_ids


def test_rt7_fires_on_a_feature_catalogue_bump() -> None:
    decision = _policy().evaluate(
        _state(feature_catalog_version="fsv=2", model_feature_catalog_version="fsv=1")
    )
    assert "RT-7" in decision.rule_ids


def test_rt8_fires_on_a_breaking_schema_change() -> None:
    decision = _policy().evaluate(_state(breaking_schema_changes=["order_date: date -> string"]))
    assert "RT-8" in decision.rule_ids


def test_rt9_fires_when_the_churn_definition_changes() -> None:
    decision = _policy().evaluate(
        _state(label_definition_hash="b2", model_label_definition_hash="a1")
    )
    assert "RT-9" in decision.rule_ids


# --- the 19.3 validity gate ---------------------------------------------------


def test_cooldown_parks_a_noisy_signal() -> None:
    decision = _policy().evaluate(
        _state(last_trained_at=NOW - timedelta(days=2), drift=_drift({"a": 0.4}))
    )
    assert decision.fired and not decision.should_retrain
    assert decision.blocked_by == "cooldown"


def test_cooldown_does_not_hold_back_a_definition_change() -> None:
    decision = _policy().evaluate(
        _state(last_trained_at=NOW - timedelta(days=2),
               label_definition_hash="b2", model_label_definition_hash="a1")
    )
    assert decision.should_retrain and decision.blocked_by == ""


def test_a_failed_quality_gate_blocks_retraining() -> None:
    decision = _policy().evaluate(
        _state(drift=_drift({"a": 0.4}), data_quality_ok=False,
               data_quality_detail="quality score 0.41 below 0.70")
    )
    assert not decision.should_retrain and decision.blocked_by == "quality"
    assert "0.41" in decision.reason


def test_schedule_alone_will_not_retrain_on_too_few_new_labels() -> None:
    decision = _policy(min_new_labelled_rows=500).evaluate(
        _state(last_trained_at=NOW - timedelta(days=40),
               labelled_rows_at_training=10000, labelled_rows_now=10100)
    )
    assert "RT-6" in decision.rule_ids
    assert not decision.should_retrain and decision.blocked_by == "insufficient_labels"


def test_a_quality_signal_retrains_even_with_few_new_labels() -> None:
    """RT-1 is about the model being wrong, not about having more rows."""
    decision = _policy().evaluate(
        _state(matured_pr_auc=[0.10], ap1_floor=0.25,
               labelled_rows_at_training=10000, labelled_rows_now=10100)
    )
    assert decision.should_retrain


def test_nothing_fires_on_an_empty_state() -> None:
    decision = RetrainingPolicy().evaluate(MonitoringState(now=NOW))
    assert not decision.should_retrain and decision.reason == "no trigger fired"
    assert all(not s.fired for s in decision.signals)


def test_unknown_inputs_are_reported_as_not_evaluated_never_as_passing() -> None:
    decision = RetrainingPolicy().evaluate(MonitoringState(now=NOW))
    unevaluated = [s.rule_id for s in decision.signals if "not evaluated" in s.reason]
    assert {"RT-1", "RT-2", "RT-3", "RT-4", "RT-5", "RT-6", "RT-7", "RT-9"} <= set(unevaluated)


def test_a_disabled_trigger_cannot_fire() -> None:
    policy = RetrainingPolicy({"triggers": ["RT-1"], "cooldown_days": 7})
    decision = policy.evaluate(_state(drift=_drift({"a": 0.9})))
    assert "RT-2" not in decision.rule_ids
    assert not decision.should_retrain


def test_every_fired_signal_carries_its_action_from_the_prd_table() -> None:
    decision = _policy().evaluate(_state(drift=_drift({"a": 0.9})))
    assert decision.fired[0].action == "investigate, then retrain"


def test_the_decision_serialises_with_its_evidence() -> None:
    payload = _policy().evaluate(_state(matured_pr_auc=[0.18], ap1_floor=0.25)).to_dict()
    assert payload["should_retrain"] is True
    assert payload["fired"][0]["evidence"]["latest_pr_auc"] == 0.18
    assert len(payload["signals"]) == 9


def test_categorical_other_bucket_is_named_so_reports_can_explain_it() -> None:
    assert OTHER == "__other__"


# --- mapping explanation names back to raw columns ----------------------------


def test_top_raw_features_maps_one_hot_names_back_to_their_column() -> None:
    from python_ml.triad_ml.drift import top_raw_features

    importance = [{"feature": "country_UK"}, {"feature": "recency_days"},
                  {"feature": "country_FR"}]
    assert top_raw_features(importance, ["recency_days", "country"]) == \
        ["country", "recency_days"]


def test_top_raw_features_prefers_the_longest_matching_column() -> None:
    from python_ml.triad_ml.drift import top_raw_features

    assert top_raw_features([{"feature": "orders_365d"}], ["orders", "orders_365d"]) == \
        ["orders_365d"]


def test_top_raw_features_drops_a_name_it_cannot_map() -> None:
    """Passing an unmappable name through would empty the top set and mute RT-2."""
    from python_ml.triad_ml.drift import top_raw_features

    assert top_raw_features([{"feature": "pca_7"}], ["recency_days"]) == []


def test_top_raw_features_respects_the_limit_and_keeps_importance_order() -> None:
    from python_ml.triad_ml.drift import top_raw_features

    importance = [{"feature": f} for f in ("c", "b", "a")]
    assert top_raw_features(importance, ["a", "b", "c"], limit=2) == ["c", "b"]


def test_an_unmapped_top_feature_set_would_flag_nothing(reference: Reference) -> None:
    """The failure mode the mapping exists to prevent, pinned as a test."""
    result = compute_drift(reference, _frame(4000, shift=45.0),
                           top_features=["recency_days_scaled"])
    assert result.flagged_features == []
    assert result.psi_map()["recency_days"] > 0.25   # the drift was there all along


# --- a single-cutoff scoring window -------------------------------------------


def test_a_feature_constant_in_the_window_is_noted_not_silently_dropped(
    reference: Reference,
) -> None:
    """A scoring run at one cutoff makes cutoff-derived features constant."""
    current = _frame(2000)
    current["orders_365d"] = 4.0          # as `snapshot_month` is constant in one month
    result = compute_drift(reference, current)
    entry = next(f for f in result.features if f.feature == "orders_365d")
    assert "single value in the scoring window" in entry.note
    assert entry.psi > 0.0


def test_the_flag_still_fires_for_a_collapsed_feature(reference: Reference) -> None:
    """A source that broke and pinned a feature looks the same, and must still alarm."""
    current = _frame(2000)
    current["recency_days"] = 999.0
    result = compute_drift(reference, current, top_features=["recency_days"])
    assert "recency_days" in result.flagged_features
    entry = next(f for f in result.features if f.feature == "recency_days")
    assert entry.note                       # the reader is told why, and still warned


def test_a_normal_window_carries_no_note(reference: Reference) -> None:
    result = compute_drift(reference, _frame(2000))
    assert all(f.note == "" for f in result.features)
