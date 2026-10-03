"""Temporal splitting with the gap rule (PRD section 17.2, FR-027, AT-06).

The gap rule is the single most important defence against optimistic churn metrics: each
later cutoff must sit at least one label horizon after the previous group's last cutoff,
otherwise a training label is decided by purchases that also fall inside the validation or
test label window.

Nothing here samples randomly across cutoffs. Ever.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd


class GapRuleViolation(ValueError):
    """Raised when a proposed split would let label windows overlap."""


@dataclass
class SplitSpec:
    """A reproducible description of one split; stored with the experiment."""

    train_cutoffs: list[str]
    calibration_cutoffs: list[str]
    valid_cutoff: str
    test_cutoff: str
    horizon_days: int
    gap_days: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "train_cutoffs": self.train_cutoffs,
            "calibration_cutoffs": self.calibration_cutoffs,
            "valid_cutoff": self.valid_cutoff,
            "test_cutoff": self.test_cutoff,
            "horizon_days": self.horizon_days,
            "gap_days": self.gap_days,
        }


@dataclass
class SplitData:
    train: pd.DataFrame
    calibration: pd.DataFrame
    valid: pd.DataFrame
    test: pd.DataFrame
    spec: SplitSpec
    class_balance: dict[str, float] = field(default_factory=dict)
    # Columns present in these frames for *evaluation only* - the customer attributes the
    # segment checks group by. They live on the split rather than being passed to each
    # training call because there are three such calls and forgetting one silently turns a
    # demographic attribute into a model feature. That is exactly what happened: joining
    # `country`, `loyalty_tier` and `acquisition_channel` for AP-6 moved every family's
    # validation PR-AUC and escalated the run on MISCALIBRATED, because `train_candidate` and
    # `calibrate` derive their own feature list from `split.train`.
    evaluation_only_cols: tuple[str, ...] = ()
    # Columns hygiene rule H4 dropped as degenerate *in training*. They stay in the frames, for
    # the same reason the segment attributes do: the deployed champion was trained on its own
    # feature set and may need a column this run's challenger may not use, and AP-8 scores the
    # champion on these same frames.
    hygiene_dropped_cols: tuple[str, ...] = ()

    @property
    def excluded_cols(self) -> tuple[str, ...]:
        """Every column present in the frames that is not a model input."""
        return (*self.evaluation_only_cols, *self.hygiene_dropped_cols)

    def summary(self) -> dict[str, Any]:
        return {
            "rows": {
                "train": len(self.train),
                "calibration": len(self.calibration),
                "valid": len(self.valid),
                "test": len(self.test),
            },
            "class_balance": self.class_balance,
            "evaluation_only_cols": list(self.evaluation_only_cols),
            "spec": self.spec.to_dict(),
        }


def _as_date(value: Any) -> dt.date:
    import pandas as pd

    return pd.Timestamp(value).date()


def check_gap_rule(
    train_cutoffs: list[Any], valid_cutoff: Any, test_cutoff: Any, horizon_days: int
) -> None:
    """Raise unless every later cutoff is at least one horizon after the previous group."""
    if not train_cutoffs:
        raise GapRuleViolation("no training cutoffs available")

    last_train = max(_as_date(c) for c in train_cutoffs)
    v = _as_date(valid_cutoff)
    t = _as_date(test_cutoff)

    if (v - last_train).days < horizon_days:
        raise GapRuleViolation(
            f"gap rule violated: validation cutoff {v} is only {(v - last_train).days} days "
            f"after the last training cutoff {last_train}; at least {horizon_days} are required"
        )
    if (t - v).days < horizon_days:
        raise GapRuleViolation(
            f"gap rule violated: test cutoff {t} is only {(t - v).days} days after the "
            f"validation cutoff {v}; at least {horizon_days} are required"
        )


def plan_split(
    snapshot_dates: list[Any], horizon_days: int = 90, calibration_fraction: float = 0.2
) -> SplitSpec:
    """Choose cutoffs automatically from the available snapshots.

    Strategy: the latest snapshot becomes test, the latest one at least a horizon earlier
    becomes validation, and everything at least a horizon before that trains. A slice of the
    *training* cutoffs is held out for calibration, so calibration never sees validation.
    """

    dates = sorted({_as_date(d) for d in snapshot_dates})
    if len(dates) < 3:
        raise GapRuleViolation(
            f"insufficient data: {len(dates)} snapshot(s) available, at least 3 are needed "
            "for a train/validation/test split with the gap rule"
        )

    test = dates[-1]
    valid_candidates = [d for d in dates if (test - d).days >= horizon_days]
    if not valid_candidates:
        raise GapRuleViolation(
            f"insufficient data: no cutoff is at least {horizon_days} days before the last "
            f"snapshot {test}; the data window is too short for a {horizon_days}-day horizon"
        )
    valid = valid_candidates[-1]

    train = [d for d in dates if (valid - d).days >= horizon_days]
    if not train:
        raise GapRuleViolation(
            f"insufficient data: no cutoff is at least {horizon_days} days before the "
            f"validation cutoff {valid}"
        )

    n_calib = max(1, round(len(train) * calibration_fraction)) if len(train) > 1 else 0
    calibration = train[-n_calib:] if n_calib else []
    fit_train = train[: len(train) - n_calib] if n_calib else train
    if not fit_train:  # never leave training empty
        fit_train, calibration = train, []

    check_gap_rule(fit_train + calibration, valid, test, horizon_days)
    fmt = "%Y-%m-%d"
    return SplitSpec(
        train_cutoffs=[d.strftime(fmt) for d in fit_train],
        calibration_cutoffs=[d.strftime(fmt) for d in calibration],
        valid_cutoff=valid.strftime(fmt),
        test_cutoff=test.strftime(fmt),
        horizon_days=horizon_days,
        gap_days=horizon_days,
    )


def apply_split(
    df: pd.DataFrame,
    spec: SplitSpec,
    *,
    snapshot_col: str = "snapshot_date",
    label_col: str = "churned_90d",
) -> SplitData:
    """Slice a feature table by the planned cutoffs and report class balance per split."""
    import pandas as pd

    dates = pd.to_datetime(df[snapshot_col]).dt.date

    def _subset(cutoffs: list[str]) -> pd.DataFrame:
        wanted = {_as_date(c) for c in cutoffs}
        return df[dates.isin(wanted)].copy()

    train = _subset(spec.train_cutoffs)
    calibration = _subset(spec.calibration_cutoffs)
    valid = _subset([spec.valid_cutoff])
    test = _subset([spec.test_cutoff])

    balance = {}
    for name, part in (
        ("train", train), ("calibration", calibration), ("valid", valid), ("test", test)
    ):
        if len(part) and label_col in part:
            balance[name] = float(part[label_col].mean())
        else:
            balance[name] = float("nan")

    return SplitData(train, calibration, valid, test, spec, balance)


def assert_no_overlap(split: SplitData, *, snapshot_col: str = "snapshot_date") -> None:
    """A split must never reuse a snapshot date in two parts."""
    import pandas as pd

    seen: dict[dt.date, str] = {}
    for name, part in (
        ("train", split.train),
        ("calibration", split.calibration),
        ("valid", split.valid),
        ("test", split.test),
    ):
        for d in pd.to_datetime(part[snapshot_col]).dt.date.unique():
            if d in seen:
                raise GapRuleViolation(
                    f"snapshot {d} appears in both {seen[d]} and {name}"
                )
            seen[d] = name


def check_positive_count(split: SplitData, minimum: int = 200, label_col: str = "churned_90d") -> None:
    """PRD section 17.7: fewer than 200 positives is escalated as insufficient data."""
    positives = int(split.train[label_col].sum()) if label_col in split.train else 0
    if positives < minimum:
        raise GapRuleViolation(
            f"insufficient data: only {positives} positive training examples, "
            f"at least {minimum} are required"
        )
