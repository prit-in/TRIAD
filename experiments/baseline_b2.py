"""B2: the Python-only automated pipeline (PRD section 32.1).

One pandas + scikit-learn script on a single node. No Spark, no agents, no repair loop, no
governance. It reads the raw parquet, builds the baseline feature subset, splits temporally,
screens the four model families and scores the winner once on the sealed test split.

This is written the way a competent engineer writes a one-file pipeline, on purpose: it is the
thing TRIAD is being compared against, so making it artificially clumsy would flatter TRIAD and
making it artificially clever would not be a baseline any more. It is straightforward pandas.

"Failures beyond memory limits are recorded as results" (32.1), so an out-of-memory failure
here is a finding, not an error to be worked around. `run` catches MemoryError and returns a
result that says so.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

from experiments.baselines import (
    BASELINE_FEATURES,
    CATEGORICAL,
    HORIZON_DAYS,
    LABEL,
    WINDOW_DAYS,
    BaselineResult,
    fit_and_select,
    peak_rss_mb,
)


def _read(root: Path, table: str) -> pd.DataFrame:
    path = root / table
    files = sorted(path.glob("*.parquet")) if path.is_dir() else [path]
    if not files:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def build_features(root: Path, cutoffs: list[pd.Timestamp]) -> pd.DataFrame:
    """One row per customer per cutoff, from raw parquet, point-in-time correct.

    Every aggregate filters on `< cutoff` before grouping. The label looks forward, into
    (cutoff, cutoff + horizon], and is the only thing in this function that may.
    """
    customers = _read(root, "customers")
    tx = _read(root, "transactions")
    sessions = _read(root, "web_sessions")
    tickets = _read(root, "support_tickets")
    returns = _read(root, "returns")
    products = _read(root, "products")

    tx["order_date"] = pd.to_datetime(tx["order_date"])
    tx["price"] = pd.to_numeric(tx["price"], errors="coerce")
    tx["discount"] = pd.to_numeric(tx.get("discount"), errors="coerce").fillna(0.0)
    tx["quantity"] = pd.to_numeric(tx["quantity"], errors="coerce")
    tx["gross"] = tx["price"] * tx["quantity"].abs()
    tx["net"] = tx["gross"] - tx["discount"]
    if not products.empty:
        tx = tx.merge(products[["product_id", "category_l1"]], on="product_id", how="left")
    else:
        tx["category_l1"] = None

    for frame, col in ((sessions, "session_start"), (tickets, "created_at"),
                       (returns, "return_date")):
        if not frame.empty:
            frame[col] = pd.to_datetime(frame[col])

    customers["signup_date"] = pd.to_datetime(customers["signup_date"])
    rows: list[pd.DataFrame] = []

    for cutoff in cutoffs:
        window_start = cutoff - pd.Timedelta(days=WINDOW_DAYS)
        past = tx[tx["order_date"] < cutoff]
        w365 = past[past["order_date"] >= window_start]
        w90 = past[past["order_date"] >= cutoff - pd.Timedelta(days=90)]

        g365 = w365.groupby("customer_id")
        frame = pd.DataFrame(index=customers["customer_id"])
        frame["recency_days"] = (
            cutoff - past.groupby("customer_id")["order_date"].max()
        ).dt.days
        frame["frequency_90d"] = w90.groupby("customer_id")["order_id"].nunique()
        frame["frequency_365d"] = g365["order_id"].nunique()
        frame["monetary_365d"] = g365["net"].sum()
        frame["items_per_order_365d"] = (
            g365["quantity"].sum().abs() / g365["order_id"].nunique()
        )
        frame["discount_share_365d"] = g365["discount"].sum() / g365["gross"].sum()
        frame["category_count_365d"] = g365["category_l1"].nunique()
        frame["aov_365d"] = frame["monetary_365d"] / frame["frequency_365d"]

        if not sessions.empty:
            s90 = sessions[
                (sessions["session_start"] < cutoff)
                & (sessions["session_start"] >= cutoff - pd.Timedelta(days=90))
            ]
            frame["sessions_90d"] = s90.groupby("customer_id")["session_id"].nunique()
        else:
            frame["sessions_90d"] = 0.0
        if not tickets.empty:
            t365 = tickets[
                (tickets["created_at"] < cutoff) & (tickets["created_at"] >= window_start)
            ]
            frame["tickets_365d"] = t365.groupby("customer_id")["ticket_id"].nunique()
        else:
            frame["tickets_365d"] = 0.0
        if not returns.empty:
            r365 = returns[
                (returns["return_date"] < cutoff) & (returns["return_date"] >= window_start)
            ]
            frame["returns_365d"] = r365.groupby("customer_id")["return_id"].nunique()
        else:
            frame["returns_365d"] = 0.0

        frame["tenure_days"] = (
            cutoff - customers.set_index("customer_id")["signup_date"]
        ).dt.days
        frame = frame.reindex(columns=list(BASELINE_FEATURES), fill_value=np.nan)

        # Counts are genuinely zero when a customer did nothing; only recency is unknown.
        for col in ("frequency_90d", "frequency_365d", "monetary_365d", "category_count_365d",
                    "sessions_90d", "tickets_365d", "returns_365d"):
            frame[col] = frame[col].fillna(0.0)

        # The label: did they order again inside the horizon? Nothing else reads this window.
        horizon = tx[
            (tx["order_date"] >= cutoff)
            & (tx["order_date"] < cutoff + pd.Timedelta(days=HORIZON_DAYS))
        ]
        active_after = set(horizon["customer_id"].unique())
        frame[LABEL] = (~frame.index.isin(active_after)).astype(int)

        frame = frame.join(
            customers.set_index("customer_id")[list(CATEGORICAL)], how="left"
        )
        frame["snapshot_date"] = cutoff
        # A customer who had never ordered before the cutoff is not a churn candidate.
        frame = frame[frame["recency_days"].notna()]
        rows.append(frame.reset_index())

    return pd.concat(rows, ignore_index=True)


def run(
    root: Path, cutoffs: dict[str, list[pd.Timestamp]], *, seed: int = 42
) -> BaselineResult:
    """Build, split, screen and score. `cutoffs` names the train/valid/test month starts."""
    started = time.perf_counter()
    stages: dict[str, float] = {}
    try:
        t0 = time.perf_counter()
        features = build_features(
            root, sorted({c for group in cutoffs.values() for c in group})
        )
        stages["features"] = round(time.perf_counter() - t0, 2)

        feature_cols = [c for c in features.columns
                        if c not in ("customer_id", "snapshot_date", LABEL)]
        parts = {
            name: features[features["snapshot_date"].isin(group)]
            for name, group in cutoffs.items()
        }
        if any(len(p) == 0 for p in parts.values()):
            return BaselineResult(
                "B2", False, time.perf_counter() - started,
                detail="a split came out empty: "
                       + ", ".join(f"{k}={len(v)}" for k, v in parts.items()),
                per_stage_s=stages,
            )

        t0 = time.perf_counter()
        outcome = fit_and_select(
            parts["train"], parts["valid"], parts["test"], feature_cols, seed=seed
        )
        stages["train_and_select"] = round(time.perf_counter() - t0, 2)

        return BaselineResult(
            "B2", True, time.perf_counter() - started,
            rows_trained=len(parts["train"]),
            rows_tested=len(parts["test"]),
            test_pr_auc=outcome["test_pr_auc"],
            test_roc_auc=outcome["test_roc_auc"],
            test_brier=outcome["test_brier"],
            selected_family=outcome["selected_family"],
            families_tried=[f["family"] for f in outcome["families_tried"]],
            # A script has no approval gate, so no human action is required - and none is
            # possible either. That is the point of the comparison, not an omission.
            human_actions=0,
            llm_calls=0,
            peak_rss_mb=peak_rss_mb(),
            detail=f"selected {outcome['selected_family']} on validation PR-AUC "
                   f"{outcome['valid_pr_auc']}",
            per_stage_s=stages,
        )
    except MemoryError as exc:
        # 32.1: "failures beyond memory limits are recorded as results".
        return BaselineResult(
            "B2", False, time.perf_counter() - started,
            detail=f"out of memory on a single node: {exc}",
            peak_rss_mb=peak_rss_mb(), per_stage_s=stages,
        )
    except Exception as exc:
        return BaselineResult(
            "B2", False, time.perf_counter() - started,
            detail=f"{type(exc).__name__}: {exc}",
            peak_rss_mb=peak_rss_mb(), per_stage_s=stages,
        )


def source_lines() -> int:
    """Hand-written pipeline lines, for the coding-effort metric (32.2).

    Comments and blank lines are excluded, because the metric is meant to reflect the work of
    writing a pipeline and not the work of documenting it.
    """
    text = Path(__file__).read_text(encoding="utf-8").splitlines()
    return sum(
        1 for line in text
        if line.strip() and not line.strip().startswith("#")
    )

