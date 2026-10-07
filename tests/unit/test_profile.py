"""TC-009..TC-015 / AT-02: profiling reports seeded defects exactly."""

from __future__ import annotations

import pytest

from dataeng.profile import (
    compare_profiles,
    infer_semantic_type,
    profile_dataframe,
    quality_score,
)

pytestmark = pytest.mark.spark


@pytest.fixture(scope="module")
def seeded(spark):
    """100 rows: 7 null customer_ids, 5 empty countries, 3 duplicated rows, 1 outlier."""
    rows = []
    for i in range(100):
        rows.append(
            {
                "order_id": f"O{i:04d}",
                "customer_id": None if i < 7 else f"C{i % 20:03d}",
                "country": "" if 10 <= i < 15 else ("IN" if i % 2 else "GB"),
                "price": 100.0 + i,
                "quantity": 2,
            }
        )
    # three exact duplicates of row 50
    rows.extend([dict(rows[50]) for _ in range(3)])
    # one clear outlier
    rows.append({**rows[60], "order_id": "O9999", "price": 100000.0})
    return spark.createDataFrame(rows)


def test_profile_counts_nulls_and_empty_strings_exactly(seeded) -> None:
    profile = profile_dataframe(seeded, dataset="seeded", key_columns=["order_id"])
    cols = {c.name: c for c in profile.columns}

    assert profile.row_count == 104
    assert cols["customer_id"].null_count == 7
    assert cols["country"].empty_string_count == 5
    assert cols["price"].null_count == 0
    assert cols["customer_id"].null_rate == pytest.approx(7 / 104, rel=1e-6)


def test_profile_counts_duplicates_exactly(seeded) -> None:
    profile = profile_dataframe(seeded, dataset="seeded", key_columns=["order_id"])
    # Row 50 appears four times: three full duplicates.
    assert profile.duplicate_rows == 3
    assert profile.key_duplicate_rows == 3


def test_profile_computes_distribution_statistics(seeded) -> None:
    profile = profile_dataframe(seeded, dataset="seeded", key_columns=["order_id"])
    price = next(c for c in profile.columns if c.name == "price")
    assert price.min_value == 100.0
    assert price.max_value == 100000.0
    assert price.mean is not None and price.stddev is not None
    assert "p50" in price.quantiles and "p99" in price.quantiles
    assert price.outlier_count and price.outlier_count >= 1


def test_profile_flags_issues_with_evidence(seeded) -> None:
    profile = profile_dataframe(seeded, dataset="seeded", key_columns=["order_id"])
    ids = {i.id for i in profile.issues}
    assert "DUPLICATE_ROWS" in ids
    assert "DUPLICATE_KEYS" in ids
    # customer_id is an identifier with 7 nulls (6.7%): below the 20% warn rate, but an
    # identifier with any nulls is always reported, because those rows cannot be joined.
    assert "NULL_IN_IDENTIFIER.customer_id" in ids
    evidence = next(i for i in profile.issues if i.id == "NULL_IN_IDENTIFIER.customer_id")
    assert evidence.evidence["null_count"] == 7
    for issue in profile.issues:
        assert issue.message
        assert issue.severity in ("blocking", "warning", "info")


def test_duplicate_keys_are_blocking(seeded) -> None:
    profile = profile_dataframe(seeded, dataset="seeded", key_columns=["order_id"])
    blocking = {i.id for i in profile.blocking_issues}
    assert "DUPLICATE_KEYS" in blocking


def test_quality_score_is_between_zero_and_one_and_reproducible(seeded) -> None:
    a = profile_dataframe(seeded, dataset="seeded", key_columns=["order_id"])
    b = profile_dataframe(seeded, dataset="seeded", key_columns=["order_id"])
    assert 0.0 <= a.quality_score <= 1.0
    assert a.quality_score == b.quality_score  # exact metrics reproduce exactly (UC-02)
    assert set(a.quality_dimensions) == {"completeness", "uniqueness", "validity", "consistency"}
    assert a.schema_hash == b.schema_hash


def test_semantic_types_are_labelled(spark) -> None:
    """FR-009: at least 10 of 12 semantic types labelled correctly on the fixture."""
    import datetime as dt

    df = spark.createDataFrame(
        [
            (
                "C-1", dt.datetime(2026, 1, 1), dt.date(2026, 1, 1), 99.5, 3, "IN",
                "a@b.com", "+919812345678", True,
                "A long free text description that goes on and on for a while, truly.",
                "electronics",
            )
        ],
        "customer_id string, created_at timestamp, order_day date, price double, quantity int, "
        "country string, email string, phone string, is_active boolean, notes string, "
        "category_l1 string",
    )
    profile = profile_dataframe(df, dataset="types")
    got = {c.name: c.semantic_type for c in profile.columns}

    expected = {
        "customer_id": "identifier",
        "created_at": "timestamp",
        "order_day": "date",
        "price": "currency",
        "quantity": "quantity",
        "country": "country_code",
        "email": "email",
        "phone": "phone",
        "is_active": "boolean_flag",
        "notes": "free_text",
        "category_l1": "category",
    }
    correct = sum(1 for k, v in expected.items() if got[k] == v)
    assert correct >= 10, f"only {correct}/11 semantic types correct: {got}"


def test_semantic_type_inference_is_a_pure_function() -> None:
    assert infer_semantic_type("customer_id", "string", ["C1"]) == "identifier"
    assert infer_semantic_type("unit_price", "decimal(18,2)", [1]) == "currency"
    assert infer_semantic_type("weird", "binary", []) == "unknown"


def test_quality_score_penalises_blocking_issues() -> None:
    from dataeng.profile import ColumnProfile, Issue

    cols = [
        ColumnProfile("a", "string", "category", 0, 0.0, 0, 5, 5),
        ColumnProfile("b", "double", "quantity", 0, 0.0, 0, 5, 5),
    ]
    clean, _ = quality_score(cols, 100, 0, 0, [])
    dirty, _ = quality_score(
        cols, 100, 0, 0, [Issue("X", "blocking", "a", "bad"), Issue("Y", "warning", "b", "meh")]
    )
    assert clean > dirty


def test_version_comparison_detects_schema_change_and_shift(spark) -> None:
    """FR-015: a renamed column and a shifted distribution both appear in the diff."""
    v1 = spark.createDataFrame(
        [(f"C{i}", float(i), "x") for i in range(100)], "customer_id string, amount double, note string"
    )
    # 'note' renamed to 'comment'; amount shifted by 1000.
    v2 = spark.createDataFrame(
        [(f"C{i}", float(i) + 1000.0, "x") for i in range(100)],
        "customer_id string, amount double, comment string",
    )
    p1 = profile_dataframe(v1, dataset="d", version_no=1)
    p2 = profile_dataframe(v2, dataset="d", version_no=2)

    diff, psi = compare_profiles(p2, p1)
    assert diff["removed_columns"] == ["note"]
    assert diff["added_columns"] == ["comment"]
    assert diff["rename_candidates"], "a same-typed add/remove pair should be proposed"
    assert psi["amount"] > 0.5, "a large distribution shift must show up"


def test_sampling_keeps_exact_row_count(spark) -> None:
    """UC-02 alternative flow: sampled profiling still reports the true row count."""
    df = spark.range(5000).selectExpr("id", "cast(id % 7 as double) as v")
    profile = profile_dataframe(df, dataset="big", sample_fraction=0.2)
    assert profile.row_count == 5000
    assert profile.sample_fraction == 0.2


# --- FR-022: seeded anomalies raise issues with evidence ---------------------------


def _orders(spark, *, days: int = 30, per_day: int = 20, spike_day: int | None = None,
            bad_prices: int = 0, price_shift: float = 0.0):
    import datetime as dt

    rows = []
    for d in range(days):
        n = per_day * (6 if d == spike_day else 1)
        for _ in range(n):
            k = len(rows)
            rows.append((f"O{k:05d}", dt.date(2026, 1, 1) + dt.timedelta(days=d),
                         -5.0 if k < bad_prices else 10.0 + (k % 50) + price_shift, 1 + k % 3))
    return spark.createDataFrame(rows, "order_id string, order_date date, price double, quantity int")


def test_a_seeded_volume_spike_is_reported_with_the_day(spark) -> None:
    profile = profile_dataframe(_orders(spark, spike_day=12), dataset="orders",
                                key_columns=["order_id"])
    volume = next(i for i in profile.issues if i.id == "VOLUME_ANOMALY")
    assert volume.severity == "info"
    assert [d["day"] for d in volume.evidence["days"]] == ["2026-01-13"]
    assert volume.evidence["days"][0]["rows"] == 120


def test_steady_volume_raises_nothing(spark) -> None:
    profile = profile_dataframe(_orders(spark), dataset="orders", key_columns=["order_id"])
    assert not [i for i in profile.issues if i.id in ("VOLUME_ANOMALY",) or i.id.startswith("RULE_BREACH")]


def test_seeded_non_positive_prices_are_a_rule_breach_with_examples(spark) -> None:
    profile = profile_dataframe(_orders(spark, bad_prices=4), dataset="orders",
                                key_columns=["order_id"])
    breach = next(i for i in profile.issues if i.id == "RULE_BREACH.price")
    assert breach.severity == "warning"
    assert breach.evidence["rows"] == 4
    assert sorted(breach.evidence["examples"]) == ["O00000", "O00001", "O00002", "O00003"]


def test_a_shifted_distribution_is_measured_against_the_previous_version(spark) -> None:
    before = profile_dataframe(_orders(spark), dataset="orders", version_no=1).to_dict()
    same = profile_dataframe(_orders(spark), dataset="orders", reference=before)
    moved = profile_dataframe(_orders(spark, price_shift=30.0), dataset="orders", reference=before)

    assert same.population_stability["price"] == pytest.approx(0.0, abs=1e-6)
    assert not [i for i in same.issues if i.id.startswith("DISTRIBUTION_SHIFT")]
    shift = next(i for i in moved.issues if i.id == "DISTRIBUTION_SHIFT.price")
    assert shift.evidence["psi"] >= 0.25 and shift.evidence["reference_version"] == 1
    assert shift.severity == "info", "a shift is reported, not held against the quality score"
    assert moved.quality_score == same.quality_score


def test_bin_shares_count_ties_rather_than_trusting_quantile_levels(spark) -> None:
    from dataeng.profile import bin_shares

    df = spark.createDataFrame([(1.0,)] * 80 + [(5.0,)] * 20, "q double")
    shares = bin_shares(df, {"q": [1.0, 5.0]})["q"]["shares"]
    assert shares == [0.8, 0.2, 0.0]
