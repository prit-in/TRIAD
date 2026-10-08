"""A seed must keep producing the same data (DEC-37).

Every experiment and recorded run is reproducible only while the generator's random draws, and
their order, stay fixed. The hashes were taken from the dict-per-line generator before it was
rewritten to store columns; tables generated after `transactions` are included because they
consume the same random stream, so a changed draw anywhere upstream changes them too.
"""

from __future__ import annotations

import pandas as pd
import pytest

from datasets.synthetic.generator import GeneratorConfig, SyntheticGenerator

FAULTS = {"seed": 7, "duplicate_rate": 0.05, "null_rate_key": 0.02, "null_rate_non_key": 0.1,
          "invalid_price_rate": 0.03, "price_as_text": True}


@pytest.mark.parametrize(("config", "expected"), [
    ({}, {"transactions": (1047, 8572520141523322375), "returns": (74, 1528702350595098849),
          "web_sessions": (2160, 1063435576573864744)}),
    (FAULTS, {"transactions": (1341, 6903803314434655121), "returns": (74, 6611924519909540301),
              "web_sessions": (2160, 1814467257867583029)}),
])
def test_a_seed_produces_the_same_tables(config, expected):
    tables = SyntheticGenerator(GeneratorConfig(customers=60, **config)).generate()
    got = {name: (len(tables[name]),
                  int(pd.util.hash_pandas_object(tables[name], index=False).sum() % 2**63))
           for name in expected}
    assert got == expected
