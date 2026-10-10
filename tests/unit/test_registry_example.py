"""The input example MLflow infers a model's signature from: integers become floats, so the
signature accepts a missing value at scoring time."""

from __future__ import annotations

import pandas as pd

from python_ml.triad_ml.registry import _nullable


def test_integer_columns_become_floats_and_nothing_else_changes() -> None:
    example = pd.DataFrame({"orders_365d": [3, 5], "spend": [10.5, 2.0], "country": ["IN", "GB"]})
    out = _nullable(example)
    assert str(out["orders_365d"].dtype) == "float64"
    assert str(out["spend"].dtype) == "float64" and list(out["country"]) == ["IN", "GB"]
    assert _nullable(None) is None
