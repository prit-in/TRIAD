"""EXP-9: the leakage sentinel.

PRD section 33:

    Setup: add a feature built from post-cutoff orders and a lifetime total computed over
    the whole dataset.
    Hypothesis: static checks and the leakage alarm block both.
    Independent variable: leak type.
    Dependent measures: blocked or not; metric inflation in an unguarded control run.
    Expected observation: both blocked; the unguarded control shows inflated scores, for
    demonstration only.

The two leaks are deliberately different in kind, and that is the point of the experiment:

* the **post-cutoff feature** is caught by name, before any model is fitted, because
  `post_cutoff` is on the forbidden-pattern list. A cheap check catches a careless leak.
* the **lifetime total** is name-innocent. `customer_lifetime_orders` looks like a perfectly
  ordinary feature and no static rule can see anything wrong with it. Catching it needs a
  behavioural check: the point-in-time sentinel test, which adds post-cutoff events and
  requires that no feature move, and the leakage alarm on implausibly high ROC-AUC.

So the experiment tests whether the defences cover both kinds, and measures how much the
unguarded control inflates - which is the number that says why the defences are worth having.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from experiments.harness import Experiment

SEED = 42
N_CUSTOMERS = 1200
CUTOFF = pd.Timestamp("2025-06-01")
HORIZON_DAYS = 90


def _events(rng: np.random.Generator) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Order lines for each customer, some before the cutoff and some after it.

    Behaviour is drawn first and the label second, from a noisy function of that behaviour.
    Generating it the other way round - picking churners and then giving them distinctive
    behaviour - makes `recency_days` separate the classes perfectly, and a point-in-time
    control then scores 0.99 and trips the leakage alarm itself. That is a flaw in the
    simulation, not a finding, and it would hide the thing this experiment is for: a leak has
    to be distinguishable from an honestly good model.
    """
    rows: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    for i in range(N_CUSTOMERS):
        cid = f"C-{i:06d}"
        # Pre-cutoff behaviour, from overlapping distributions.
        rate = float(rng.gamma(2.0, 3.0))                  # orders per year
        pre_n = max(1, int(rng.poisson(max(1.0, rate))))
        last_gap = float(rng.exponential(60.0))            # days since the last order
        for k in range(pre_n):
            offset = last_gap + float(rng.exponential(45.0)) * k
            rows.append({
                "customer_id": cid,
                "order_ts": CUTOFF - pd.Timedelta(days=min(720.0, offset)),
                "amount": float(rng.gamma(2.0, 30.0)),
            })
        # Churn is *likely* when a customer is lapsing and infrequent, not certain. The noise
        # term is what keeps a correct model at a plausible PR-AUC.
        logit = -0.9 + 0.016 * last_gap - 0.22 * rate + float(rng.normal(0.0, 1.1))
        churner = rng.random() < 1.0 / (1.0 + np.exp(-logit))
        # Post-cutoff behaviour, inside the label window: a churner makes no further orders,
        # which is what the label means. Any feature reading this window is a leak.
        post_n = 0 if churner else int(rng.integers(1, 6))
        for _ in range(post_n):
            offset = int(rng.integers(1, HORIZON_DAYS))
            rows.append({
                "customer_id": cid,
                "order_ts": CUTOFF + pd.Timedelta(days=offset),
                "amount": float(rng.gamma(2.0, 30.0)),
            })
        labels.append({"customer_id": cid, "churned_90d": int(churner)})
    return pd.DataFrame(rows), pd.DataFrame(labels)


def _clean_features(events: pd.DataFrame) -> pd.DataFrame:
    """Point-in-time correct: every aggregate reads only rows strictly before the cutoff."""
    pre = events[events.order_ts < CUTOFF]
    g = pre.groupby("customer_id")
    out = pd.DataFrame({
        "orders_365d": g.order_ts.count(),
        "spend_365d": g.amount.sum(),
        "recency_days": (CUTOFF - g.order_ts.max()).dt.days,
    })
    out["snapshot_date"] = CUTOFF
    return out.reset_index()


def _leaky_post_cutoff(events: pd.DataFrame) -> pd.DataFrame:
    """Leak 1: a feature that names what it does. Caught by the static name check."""
    out = _clean_features(events)
    post = events[events.order_ts >= CUTOFF].groupby("customer_id").order_ts.count()
    out["post_cutoff_orders"] = out.customer_id.map(post).fillna(0.0)
    return out


def _leaky_lifetime(events: pd.DataFrame) -> pd.DataFrame:
    """Leak 2: a lifetime total over the whole dataset. Nothing in the name gives it away."""
    out = _clean_features(events)
    lifetime = events.groupby("customer_id").order_ts.count()
    out["customer_lifetime_orders"] = out.customer_id.map(lifetime).fillna(0.0)
    return out


def _fit_and_score(x: pd.DataFrame, y: np.ndarray) -> np.ndarray:
    """A deliberately ordinary model, so the metric reflects the features, not the algorithm."""
    from sklearn.ensemble import RandomForestClassifier

    model = RandomForestClassifier(n_estimators=120, random_state=SEED, min_samples_leaf=5)
    model.fit(x, y)
    return model.predict_proba(x)[:, 1]


def _held_out_pr_auc(x: pd.DataFrame, y: np.ndarray) -> tuple[float, float]:
    """PR-AUC and ROC-AUC on a held-out half, which is where inflation shows."""
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import average_precision_score, roc_auc_score
    from sklearn.model_selection import train_test_split

    x_tr, x_te, y_tr, y_te = train_test_split(
        x, y, test_size=0.4, random_state=SEED, stratify=y
    )
    model = RandomForestClassifier(n_estimators=120, random_state=SEED, min_samples_leaf=5)
    model.fit(x_tr, y_tr)
    scores = model.predict_proba(x_te)[:, 1]
    return float(average_precision_score(y_te, scores)), float(roc_auc_score(y_te, scores))


def run() -> Experiment:
    from python_ml.triad_ml.evaluate import check_acceptance, evaluate
    from python_ml.triad_ml.hygiene import check_forbidden_features, sentinel_test
    from triad_core.config import load_yaml, settings

    # The real pipeline's thresholds, not a copy of them: an experiment that tested a
    # different `leakage_alarm_roc_auc` from the one production uses would prove nothing.
    policy = load_yaml(settings().pipeline_config_path)["acceptance_policy"]

    rng = np.random.default_rng(SEED)
    events, labels = _events(rng)

    # The sentinel events are post-cutoff rows added to probe whether a feature moves. They
    # are the same kind of row the label is built from, which is exactly the point.
    sentinel_events = events[events.order_ts >= CUTOFF].copy()
    sentinel_events["order_ts"] = sentinel_events["order_ts"] + pd.Timedelta(days=1)
    pre_only = events[events.order_ts < CUTOFF].copy()

    conditions = {
        "control_point_in_time": _clean_features,
        "leak_post_cutoff_feature": _leaky_post_cutoff,
        "leak_lifetime_total": _leaky_lifetime,
    }

    with Experiment(
        "EXP-9",
        title="Leakage sentinel",
        hypothesis=(
            "Static checks and the leakage alarm block both a post-cutoff feature and a "
            "lifetime total computed over the whole dataset."
        ),
        independent_variable="leak type",
        dependent_measures=[
            "blocked_by_static_check", "blocked_by_sentinel_test", "blocked_by_leakage_alarm",
            "held_out_pr_auc", "held_out_roc_auc", "inflation_vs_control",
        ],
        config={
            "seed": SEED, "customers": N_CUSTOMERS, "cutoff": str(CUTOFF.date()),
            "horizon_days": HORIZON_DAYS, "conditions": sorted(conditions),
            "model": "RandomForestClassifier(n_estimators=120, min_samples_leaf=5)",
            "acceptance_policy": policy,
        },
    ) as exp:
        control_pr_auc = None
        for name, build in conditions.items():
            trial = exp.trial(name)
            frame = build(events).merge(labels, on="customer_id")
            feature_cols = [
                c for c in frame.columns
                if c not in ("customer_id", "snapshot_date", "churned_90d")
            ]

            # --- defence 1: the static name check, before anything is fitted -------------
            violations = check_forbidden_features(feature_cols)
            static_blocked = bool(violations)
            trial.record("blocked_by_static_check", static_blocked)
            trial.observations["static_violations"] = violations

            # --- defence 2: the point-in-time sentinel test ------------------------------
            # Post-cutoff events are added; a correct feature set does not move.
            sentinel = sentinel_test(build, pre_only, sentinel_events)
            sentinel_blocked = not sentinel["passed"]
            trial.record("blocked_by_sentinel_test", sentinel_blocked)
            trial.observations["sentinel"] = sentinel

            # --- the unguarded control run: what the leak would have bought -------------
            # This is the "for demonstration only" run of the PRD: the defences are
            # bypassed on purpose so the inflation can be measured. Nothing from here is
            # ever registered or promoted; it exists to quantify the risk.
            x = frame[feature_cols].astype(float)
            y = frame["churned_90d"].to_numpy()
            pr_auc, roc_auc = _held_out_pr_auc(x, y)
            trial.record("held_out_pr_auc", round(pr_auc, 4), note="defences bypassed on purpose")
            trial.record("held_out_roc_auc", round(roc_auc, 4))

            if name == "control_point_in_time":
                control_pr_auc = pr_auc
                trial.record("inflation_vs_control", 0.0, "PR-AUC")
            else:
                assert control_pr_auc is not None, "the control must run first"
                trial.record(
                    "inflation_vs_control", round(pr_auc - control_pr_auc, 4), "PR-AUC",
                    note="how much the leak flatters the model on held-out data",
                )

            # --- defence 3: the leakage alarm in the acceptance policy -------------------
            # AP checks run on the metrics a candidate presents. An implausibly high ROC-AUC
            # trips the alarm whatever the feature is called.
            in_sample = _fit_and_score(x, y)
            metrics = evaluate(y, in_sample, split="valid")
            acceptance = check_acceptance(metrics, policy)
            alarmed = "LEAKAGE_SUSPECTED" in acceptance.reason_codes
            trial.record("blocked_by_leakage_alarm", alarmed)
            trial.observations["acceptance"] = acceptance.to_dict()

            blocked = static_blocked or sentinel_blocked or alarmed
            trial.record("blocked_by_any_defence", blocked)
            trial.ok = blocked if name.startswith("leak_") else not blocked
            trial.detail = (
                f"static={static_blocked} sentinel={sentinel_blocked} alarm={alarmed}"
            )

        exp.artifact("raw/feature_columns.json", {
            name: sorted(build(events).columns) for name, build in conditions.items()
        })

        leaks = [t for t in exp.trials if t.condition.startswith("leak_")]
        control = next(t for t in exp.trials if t.condition == "control_point_in_time")
        all_leaks_blocked = all(t.get("blocked_by_any_defence") for t in leaks)
        control_passed = not control.get("blocked_by_any_defence")

        exp.conclude(
            status="completed",
            hypothesis_held=all_leaks_blocked and control_passed,
            conclusion=(
                f"{sum(bool(t.get('blocked_by_any_defence')) for t in leaks)} of {len(leaks)} "
                f"leaks blocked; the point-in-time control was not blocked: {control_passed}. "
                "Inflation on held-out data, with the defences bypassed: "
                + ", ".join(
                    f"{t.condition} {t.get('inflation_vs_control'):+.4f} PR-AUC" for t in leaks
                )
                + ". The post-cutoff feature is caught by name before any model is fitted; "
                "the lifetime total is name-innocent and is caught only by the behavioural "
                "checks, which is why both kinds of defence are needed."
            ),
        )
        return exp


if __name__ == "__main__":
    run()
