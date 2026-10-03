"""PRD 16.2 hygiene, through the functions a training run calls, and the catalogue they read.

Until `churn.prepare` called these, no hygiene rule ran on any feature table a model was
trained on: `run_hygiene` had tests and no production caller, and the catalogue H7 checks
against did not exist. These tests hold the functions `prepare` now uses, not a parallel copy.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from agents.graph.churn import FEATURE_CATALOG, FEATURE_SET_VERSION
from python_ml.triad_ml.hygiene import check_feature_names, degenerate_in_training, load_catalog

# PRD section 16.1, the 42 names, copied from the table so the catalogue cannot drift from it.
PRD_FEATURES = {
    "recency_days", "frequency_30d", "frequency_90d", "frequency_180d", "frequency_365d",
    "monetary_90d", "monetary_365d", "aov_365d", "items_per_order_365d",
    "order_count_lifetime", "clv_proxy_lifetime", "return_count_365d", "return_rate_365d",
    "discount_share_365d", "promo_order_share_365d", "category_count_365d",
    "preferred_payment_method", "tickets_90d", "tickets_365d", "complaint_tickets_90d",
    "open_tickets_at_cutoff", "avg_resolution_hours_365d", "avg_csat_365d", "sessions_30d",
    "sessions_90d", "days_since_last_session", "product_views_90d", "add_to_cart_90d",
    "cart_conversion_90d", "app_session_share_90d", "avg_session_minutes_90d", "tenure_days",
    "inter_purchase_mean_days_365d", "inter_purchase_std_days_365d", "overdue_ratio",
    "trend_90_vs_prior_90", "snapshot_month", "holiday_season_buyer", "acquisition_channel",
    "country", "loyalty_tier_at_cutoff", "marketing_opt_in_at_cutoff",
}


@pytest.fixture(scope="module")
def catalog() -> dict:
    return load_catalog(FEATURE_CATALOG)


def test_the_catalogue_names_every_prd_feature(catalog) -> None:
    assert len(PRD_FEATURES) == 42
    prd_entries = {name for name, e in catalog.items() if e.get("prd", True)}
    assert prd_entries == PRD_FEATURES, (
        f"missing: {sorted(PRD_FEATURES - prd_entries)}; "
        f"extra: {sorted(prd_entries - PRD_FEATURES)}"
    )


def test_every_entry_carries_what_h7_requires(catalog) -> None:
    for name, entry in catalog.items():
        assert entry.get("leakage_guard"), f"{name} has no leakage guard"
        assert entry.get("source"), f"{name} has no source"
        assert entry.get("status") in {"implemented", "unavailable", "evaluation_only"}, name
        if entry["status"] != "implemented":
            assert entry.get("reason"), f"{name} is {entry['status']} without saying why"


def test_four_prd_features_are_honestly_not_model_inputs(catalog) -> None:
    """The catalogue says which PRD features this data cannot support, rather than faking them."""
    not_inputs = {n for n, e in catalog.items() if e["status"] != "implemented"}
    assert not_inputs == {
        "loyalty_tier_at_cutoff", "marketing_opt_in_at_cutoff",   # current-only (ASM-11)
        "country", "acquisition_channel",                         # AP-6 segments (DEC-21)
    }


def test_the_model_records_the_catalogues_version() -> None:
    """It was the literal "fsv=1" in three places, so RT-7 could never fire."""
    assert FEATURE_SET_VERSION == "fsv=2"


def test_a_feature_with_no_catalogue_entry_blocks_training(catalog) -> None:
    violations = check_feature_names(["recency_days", "mystery_signal_365d"], catalog)
    assert [v["rule"] for v in violations] == ["H7"]
    assert violations[0]["columns"] == ["mystery_signal_365d"]


def test_an_unavailable_feature_turning_up_blocks_training(catalog) -> None:
    """A column the catalogue says cannot be computed honestly is itself the defect."""
    violations = check_feature_names(["recency_days", "loyalty_tier_at_cutoff"], catalog)
    assert violations and violations[0]["rule"] == "H7"


@pytest.mark.parametrize("column", ["customer_id", "churn_score_prior", "future_orders_30d"])
def test_identifiers_and_label_derived_names_block_training(catalog, column: str) -> None:
    rules = {v["rule"] for v in check_feature_names(["recency_days", column], catalog)}
    assert rules & {"H2", "H3"}, rules


def test_every_implemented_feature_passes(catalog) -> None:
    implemented = [n for n, e in catalog.items() if e["status"] == "implemented"]
    assert check_feature_names(implemented, catalog) == []


def test_h4_is_decided_on_the_training_rows_only() -> None:
    """A column constant in training but varied later must still be dropped - and vice versa."""
    rng = np.random.default_rng(3)
    train = pd.DataFrame({
        "varied": rng.normal(size=500),
        "constant_in_training": np.zeros(500),
        "mostly_null": [np.nan] * 490 + list(range(10)),
    })
    dropped = degenerate_in_training(train, list(train.columns))
    assert set(dropped) == {"constant_in_training", "mostly_null"}
    assert "dominates" in dropped["constant_in_training"]
    assert "null share" in dropped["mostly_null"]
