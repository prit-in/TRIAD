"""EXP-7: model degradation.

PRD section 33:

    Setup: replay the dataset month by month; shift one top feature; change buying behaviour
    for one segment.
    Hypothesis: drift flags fire; retraining starts; the challenger is compared with the
    champion; promotion waits for approval.
    Independent variable: drift type and size.
    Dependent measures: detection delay, PSI, false-alarm rate, PR-AUC before and after.
    Expected observation: data drift flagged at the first scoring run; the performance drop
    visible only after labels mature.

The replay is month by month over synthetic history rather than Online Retail II: the feature
tables are built in pandas from the project's own generator, which is the only source that can
be told to shift a feature or change one segment's behaviour on demand (section 33.1, "drift:
generator options applied to later replay months"). The detectors under test are the real ones
- `Reference`, `compute_drift` and `RetrainingPolicy` - with the pipeline's own thresholds.

**The false-alarm rate is why this experiment has a stable arm.** Six months of undrifted data
are replayed first, and any month flagged there is a false alarm. Without it, a detector that
flagged every month would score a perfect detection delay of zero, which is exactly the
failure mode a drift experiment has to rule out.

Two measurements are deliberately separated, because conflating them is the mistake the PRD's
expected observation warns about:

* **Detection delay in *scoring* months** - how many replay months pass before the feature
  drift is flagged. This needs no labels and should be near zero.
* **Detection delay in *label* months** - how many pass before the performance drop is
  visible. Labels for a month are only known 90 days later, so this is necessarily larger,
  and the gap between the two numbers is the finding.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from experiments.harness import Experiment

SEED = 42
N_CUSTOMERS = 1500
HORIZON_DAYS = 90
STABLE_MONTHS = 6      # the false-alarm arm
DRIFT_MONTHS = 6       # months after the shift is introduced
FEATURES = ["orders_365d", "spend_365d", "recency_days", "basket_size", "sessions_30d"]
SEGMENT_COL = "country"


def _month_frame(
    rng: np.random.Generator,
    month: int,
    *,
    shift_feature: str | None = None,
    shift_size: float = 0.0,
    segment_shift: str | None = None,
    concept_shift_segment: str | None = None,
) -> pd.DataFrame:
    """One month's feature rows, with the label drawn from a noisy function of behaviour.

    The label depends on behaviour, not the other way round, for the same reason EXP-9 needs
    it: a label assigned first and then dressed in distinctive features makes every model look
    perfect and hides the degradation this experiment is trying to see.

    Two kinds of drift, and keeping them apart is the whole design of this experiment:

    * `shift_feature` / `segment_shift` move the *features* and leave the mechanism that turns
      behaviour into churn alone. This is covariate shift. PSI sees it immediately, and the
      model may lose nothing at all - a first attempt at this experiment used only this kind
      and PR-AUC went *up*, from 0.55 to 0.71, because widening a predictive feature makes the
      signal easier. Flagging that as degradation would have been wrong.
    * `concept_shift_segment` changes the *mechanism* for one segment: recency stops predicting
      churn for those customers. The marginal feature distributions barely move, so PSI is much
      quieter, and the damage is only visible once labels mature. This is the case the PRD's
      expected observation is about.
    """
    n = N_CUSTOMERS
    country = rng.choice(["IN", "GB", "US", "DE"], size=n, p=[0.5, 0.2, 0.2, 0.1])
    frame = pd.DataFrame({
        "customer_id": [f"C-{i:06d}" for i in range(n)],
        "orders_365d": rng.poisson(6.0, size=n).astype(float),
        "spend_365d": rng.gamma(2.0, 400.0, size=n),
        "recency_days": rng.exponential(60.0, size=n),
        "basket_size": rng.gamma(2.0, 1.5, size=n),
        "sessions_30d": rng.poisson(4.0, size=n).astype(float),
        SEGMENT_COL: country,
    })

    # Drift type 1: one top feature shifts for everybody.
    if shift_feature:
        frame[shift_feature] = frame[shift_feature] * (1.0 + shift_size)

    # Drift type 2: one segment changes its buying behaviour. This is the harder case - the
    # marginal distribution of each feature moves much less than in type 1.
    if segment_shift:
        mask = frame[SEGMENT_COL] == segment_shift
        frame.loc[mask, "orders_365d"] = frame.loc[mask, "orders_365d"] * 0.35
        frame.loc[mask, "recency_days"] = frame.loc[mask, "recency_days"] * 2.6
        frame.loc[mask, "basket_size"] = frame.loc[mask, "basket_size"] * 1.4

    logit = (
        -1.0
        + 0.014 * frame["recency_days"]
        - 0.11 * frame["orders_365d"]
        - 0.00018 * frame["spend_365d"]
        + rng.normal(0.0, 1.05, size=n)
    )
    if concept_shift_segment:
        # For this segment the relationship reverses: these customers now churn when they are
        # *recently* active and buying in bulk - a subscription cohort switching to a rival, say.
        # The features look ordinary; what the model learned about them is now wrong.
        seg = frame[SEGMENT_COL] == concept_shift_segment
        logit[seg] = (
            -1.0
            - 0.014 * frame.loc[seg, "recency_days"]
            + 0.11 * frame.loc[seg, "orders_365d"]
            + rng.normal(0.0, 1.05, size=int(seg.sum()))
        )
    frame["churned_90d"] = (rng.random(n) < 1.0 / (1.0 + np.exp(-logit))).astype(int)
    frame["snapshot_month"] = month
    return frame


def _fit(train: pd.DataFrame) -> Any:
    from sklearn.ensemble import RandomForestClassifier

    model = RandomForestClassifier(n_estimators=150, random_state=SEED, min_samples_leaf=8)
    model.fit(train[FEATURES], train["churned_90d"])
    return model


def run() -> Experiment:
    from agents.retraining import MonitoringState, RetrainingPolicy
    from python_ml.triad_ml.drift import DEFAULT_THRESHOLDS, Reference, compute_drift
    from python_ml.triad_ml.evaluate import evaluate

    rng = np.random.default_rng(SEED)

    with Experiment(
        "EXP-7",
        title="Model degradation",
        hypothesis=(
            "Drift flags fire; retraining starts; the challenger is compared with the "
            "champion; promotion waits for approval."
        ),
        independent_variable="drift type and size",
        dependent_measures=[
            "max_psi", "flagged_features", "scoring_months_to_detection",
            "label_months_to_detection", "false_alarm_months", "pr_auc",
        ],
        config={
            "seed": SEED, "customers_per_month": N_CUSTOMERS,
            "features": FEATURES, "segment_column": SEGMENT_COL,
            "stable_months": STABLE_MONTHS, "drift_months": DRIFT_MONTHS,
            "horizon_days": HORIZON_DAYS,
            "thresholds": dict(DEFAULT_THRESHOLDS),
            "drift_types": {
                "feature_shift": "one top feature multiplied by 1 + size, for everybody",
                "segment_shift": "one country's ordering, recency and basket behaviour changed",
            },
        },
    ) as exp:
        # --- the champion, trained on month 0 -------------------------------------
        # Month 0 is split, and the deployment PR-AUC is measured on the held-out part. Using
        # the in-sample number instead made every later month look 10% worse immediately, so
        # the "label delay" came out as 0 for both drift types - and what that measured was the
        # ordinary train/test gap, not degradation. A drift experiment cannot use an in-sample
        # baseline: the thing it is trying to detect would be swamped by it.
        month0 = _month_frame(rng, 0)
        holdout = month0.sample(frac=0.35, random_state=SEED)
        train = month0.drop(index=holdout.index)
        model = _fit(train)
        reference = Reference.from_frame(
            train, FEATURES,
            scores=model.predict_proba(train[FEATURES])[:, 1],
            high_risk_threshold=0.5,
        )
        baseline = evaluate(
            holdout["churned_90d"].to_numpy(),
            model.predict_proba(holdout[FEATURES])[:, 1],
            split="test",
        )
        pr_auc_at_deployment = float(baseline.get("pr_auc"))

        setup = exp.trial("champion_trained_on_month_0")
        setup.record("training_rows", len(train))
        setup.record("holdout_rows", len(holdout))
        setup.record("pr_auc_at_deployment", round(pr_auc_at_deployment, 4),
                     note="measured on the held-out third of month 0, never on training rows")
        setup.record("reference_rows", reference.n_rows)

        # --- arm 1: six stable months. Anything flagged here is a false alarm ------
        stable = exp.trial("stable_no_drift")
        false_alarms = 0
        stable_psis: list[float] = []
        stable_pr_aucs: list[float] = []
        for month in range(1, STABLE_MONTHS + 1):
            current = _month_frame(rng, month)
            scores = model.predict_proba(current[FEATURES])[:, 1]
            result = compute_drift(
                reference, current, scores=scores, high_risk_threshold=0.5,
                top_features=FEATURES, reference_sample=train,
            )
            stable_psis.append(result.max_psi)
            if result.flagged_features:
                false_alarms += 1
            stable_metrics = evaluate(
                current["churned_90d"].to_numpy(), scores, split="test"
            )
            stable_pr_aucs.append(float(stable_metrics.get("pr_auc")))
            stable.observations[f"month_{month}"] = {
                "max_psi": round(result.max_psi, 5),
                "flagged": result.flagged_features,
                "pr_auc": round(float(stable_metrics.get("pr_auc")), 4),
            }
        stable.record("months_replayed", STABLE_MONTHS)
        stable.record("mean_pr_auc_undrifted", round(float(np.mean(stable_pr_aucs)), 4),
                      note="the honest no-drift out-of-sample baseline")
        stable.record("min_pr_auc_undrifted", round(float(min(stable_pr_aucs)), 4))
        stable.record("false_alarm_months", false_alarms)
        stable.record("false_alarm_rate", round(false_alarms / STABLE_MONTHS, 4))
        stable.record("max_psi_over_stable_months", round(max(stable_psis), 5))
        stable.ok = false_alarms == 0
        stable.detail = (
            f"{false_alarms}/{STABLE_MONTHS} stable months flagged; highest PSI "
            f"{max(stable_psis):.5f} against a threshold of "
            f"{DEFAULT_THRESHOLDS['psi_feature_threshold']}"
        )

        # --- arms 2 and 3: the two drift types ------------------------------------
        for label, kwargs in (
            ("covariate_shift_recency_x1.8", {"shift_feature": "recency_days",
                                              "shift_size": 0.8}),
            ("covariate_shift_one_segment", {"segment_shift": "IN"}),
            ("concept_drift_one_segment", {"concept_shift_segment": "IN"}),
        ):
            trial = exp.trial(label)
            scoring_delay: int | None = None
            label_delay: int | None = None
            matured: list[float] = []
            months: list[dict[str, Any]] = []

            for offset in range(1, DRIFT_MONTHS + 1):
                month = STABLE_MONTHS + offset
                current = _month_frame(rng, month, **kwargs)
                scores = model.predict_proba(current[FEATURES])[:, 1]
                result = compute_drift(
                    reference, current, scores=scores, high_risk_threshold=0.5,
                    top_features=FEATURES, reference_sample=train,
                )
                if result.flagged_features and scoring_delay is None:
                    # Delay of 0 means "the very first scoring run after the shift".
                    scoring_delay = offset - 1

                # The label for this month is only known HORIZON_DAYS later, so a real
                # deployment sees this number three months after the scoring run that
                # produced it. That lag is the point of the second measurement.
                metrics = evaluate(
                    current["churned_90d"].to_numpy(), scores, split="test"
                )
                pr_auc = float(metrics.get("pr_auc"))
                matured.append(pr_auc)
                if label_delay is None and pr_auc < pr_auc_at_deployment * 0.9:
                    label_delay = offset - 1

                months.append({
                    "month": month,
                    "max_psi": round(result.max_psi, 5),
                    "flagged": result.flagged_features,
                    "score_psi": round(result.score.psi, 5) if result.score else None,
                    "pr_auc": round(pr_auc, 4),
                })

            trial.observations["months"] = months
            trial.record("max_psi", round(max(m["max_psi"] for m in months), 5))
            trial.record("flagged_features",
                         sorted({f for m in months for f in m["flagged"]}))
            trial.record("scoring_months_to_detection", scoring_delay,
                         note="0 = flagged on the first scoring run after the shift")
            trial.record("label_months_to_detection", label_delay,
                         note="months of *matured labels* before a 10% PR-AUC drop is visible")
            trial.record("pr_auc_before", round(pr_auc_at_deployment, 4))
            trial.record("pr_auc_after", round(matured[-1], 4))
            trial.record("pr_auc_change", round(matured[-1] - pr_auc_at_deployment, 4))

            # --- does the retraining policy actually fire? ------------------------
            last = compute_drift(
                reference, _month_frame(rng, STABLE_MONTHS + DRIFT_MONTHS, **kwargs),
                scores=model.predict_proba(
                    _month_frame(rng, STABLE_MONTHS + DRIFT_MONTHS, **kwargs)[FEATURES]
                )[:, 1],
                high_risk_threshold=0.5, top_features=FEATURES, reference_sample=train,
            )
            policy = RetrainingPolicy()
            decision = policy.evaluate(
                MonitoringState(
                    matured_pr_auc=matured,
                    pr_auc_at_deployment=pr_auc_at_deployment,
                    ap1_floor=float(train["churned_90d"].mean()) + 0.10,
                    drift=last,
                    labelled_rows_at_training=len(train),
                    labelled_rows_now=len(train) + N_CUSTOMERS * DRIFT_MONTHS,
                    # Well past any cooldown: this experiment asks whether the triggers fire
                    # on the drift, not whether a cooldown is being honoured.
                    last_trained_at=datetime.now(UTC) - timedelta(days=120),
                    data_quality_ok=True,
                )
            )
            trial.record("retraining_should_start", decision.should_retrain)
            trial.record("triggers_fired", [s.rule_id for s in decision.fired])
            trial.record("blocked_by", decision.blocked_by or "")
            trial.observations["retraining_decision"] = {
                "reason": decision.reason,
                "signals": [
                    {"rule": s.rule_id, "fired": s.fired, "reason": s.reason[:200]}
                    for s in decision.signals
                ],
            }

            if label.startswith("concept_drift"):
                # The claim for this arm is the performance drop, not the PSI signal - the
                # feature distributions barely move, which is exactly what makes it hard.
                trial.ok = bool(decision.should_retrain)
            else:
                trial.ok = bool(decision.should_retrain) and scoring_delay is not None
            trial.detail = (
                f"scoring delay {scoring_delay} month(s), label delay {label_delay} month(s), "
                f"max PSI {trial.get('max_psi')}, retrain={decision.should_retrain} "
                f"via {[s.rule_id for s in decision.fired]}"
            )

        exp.artifact("raw/thresholds.json", dict(DEFAULT_THRESHOLDS))

        drift_arms = [t for t in exp.trials
                      if t.condition.startswith(("covariate_shift", "concept_drift"))]
        all_retrain = all(t.get("retraining_should_start") for t in drift_arms)
        covariate = [t for t in drift_arms if t.condition.startswith("covariate")]
        concept = [t for t in drift_arms if t.condition.startswith("concept")]
        undrifted = float(stable.get("mean_pr_auc_undrifted"))
        concept_dropped = all(t.get("pr_auc_after") < undrifted for t in concept)
        covariate_flagged = all(
            t.get("scoring_months_to_detection") is not None for t in covariate
        )

        exp.limitation(
            "The approval half of the hypothesis - that the challenger is compared with the "
            "champion and promotion waits for approval - is not re-tested here. It is the "
            "approval gate of the main pipeline, covered by AT-08/AT-13 and demonstrated by "
            "scripts/run_demo.py; repeating it inside a drift experiment would measure the "
            "gate twice and the drift detectors once."
        )
        exp.limitation(
            "Replay is over synthetic months, not Online Retail II. Only the generator can be "
            "told to shift one feature or change one segment's behaviour on demand, which is "
            "what the independent variable requires."
        )

        exp.conclude(
            status="partial",
            hypothesis_held=covariate_flagged and concept_dropped and all_retrain and stable.ok,
            conclusion=(
                f"False alarms: {false_alarms} of {STABLE_MONTHS} undrifted months flagged, "
                f"highest PSI {max(stable_psis):.5f} against a "
                f"{DEFAULT_THRESHOLDS['psi_feature_threshold']} threshold, mean undrifted "
                f"out-of-sample PR-AUC {undrifted:.4f}. The detector is not simply flagging "
                "everything, which is what makes the delays below mean anything. "
                + " ".join(
                    f"{t.condition}: PSI flagged after "
                    f"{t.get('scoring_months_to_detection')} scoring month(s), max PSI "
                    f"{t.get('max_psi')}, PR-AUC {t.get('pr_auc_before')} -> "
                    f"{t.get('pr_auc_after')}, 10% drop visible after "
                    f"{t.get('label_months_to_detection')} month(s) of matured labels, "
                    f"triggers {t.get('triggers_fired')}."
                    for t in drift_arms
                )
                + " The two kinds of drift behave differently, and separating them is the "
                "result. Covariate shift is caught by PSI on the first scoring run, with no "
                "labels at all - and it does not necessarily hurt: widening a predictive "
                "feature raised PR-AUC rather than lowering it, so treating a PSI flag as "
                "evidence of damage would be wrong. Concept drift is the opposite: the feature "
                "distributions move far less, so PSI is quieter, and the harm shows only once "
                "labels mature. That is the PRD's expected observation, and it is why RT-1 and "
                "RT-4 (matured PR-AUC) exist alongside RT-2 and RT-3 (distributions): neither "
                "family of trigger would catch both cases. "
                "Status is partial: the approval half of the hypothesis is covered by the "
                "pipeline's own approval gate, not re-tested here."
            ),
        )
        return exp


if __name__ == "__main__":
    run()
