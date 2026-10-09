"""EXP-1's like-for-like refit must accept P's feature table, text columns included.

It once sent P's payment-method column to the median imputer, which failed on 'cod', so the one
comparison that holds the features constant was never made.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from experiments.baselines import LABEL, fit_and_select


def _split(n: int, rng: np.random.Generator) -> pd.DataFrame:
    spend = rng.gamma(2.0, 100.0, n)
    return pd.DataFrame({
        "monetary_365d": spend,
        "frequency_90d": rng.integers(0, 6, n),
        # A text column the baselines' own CATEGORICAL list does not name, with a gap in it.
        "preferred_payment_method": rng.choice(["cod", "card", "upi", None], n),
        LABEL: (spend + rng.normal(0, 60, n) < 160).astype(int),
    })


def test_a_text_column_outside_the_declared_list_is_treated_as_categorical():
    rng = np.random.default_rng(7)
    train, valid, test = _split(300, rng), _split(120, rng), _split(120, rng)
    cols = ["monetary_365d", "frequency_90d", "preferred_payment_method"]

    out = fit_and_select(train, valid, test, cols, seed=1, categorical=[])

    assert 0.0 <= out["test_pr_auc"] <= 1.0
    assert out["selected_family"]
