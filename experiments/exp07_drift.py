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

**The fourth arm runs the whole chain on the platform** (`_end_to_end`): a champion trained by
`churn.prepare` and promoted through the approval the policy requires, a drift report from its
own publish, the scheduler's evaluation queueing the retrain, the production worker running it,
AP-8 comparing it with the champion, and its promotion waiting for a person while the registry's
alias is read. The first three arms measure the detectors; this one measures what they set off.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
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


# --- arm 4: the chain on the platform itself ---------------------------------------------------
# The three arms above use the detectors and the policy object directly. This one runs the chain
# the hypothesis names through the platform's own code, end to end, and is what makes the
# experiment complete (DEC-45).
E2E_CUSTOMERS = 1500
E2E_MONTHS = 24
E2E_NEW_MONTHS = 3          # what has arrived by the time the retrain runs
E2E_DRIFT_FROM_MONTH = 18   # 2025-07: in the champion's test window, after its training cutoffs
E2E_DRIFT_STRENGTH = 1.0    # the generator's basket-size and channel-mix shift, at full strength
DATA_START = "2024-01-01"
OVERRIDE_NOTE = (
    "EXP-7 harness, as the requesting ML Engineer: the candidate failed a calibration or segment "
    "rule; it is promoted anyway so that the drift-to-retraining chain has a champion to test."
)


def _person(session: Any, tag: str, role: str) -> Any:
    """A harness user who can decide but never log in, as EXP-4's Data Engineer."""
    from triad_core.models import User

    person = User(email=f"{tag}-{role}-{uuid.uuid4().hex[:6]}@triad.invalid",
                  display_name=f"EXP-7 harness as {role}", role=role,
                  password_hash="!experiment-only-no-login")
    session.add(person)
    session.flush()
    return person


def _end_to_end(exp: Experiment) -> Any:
    """Drift fires, retraining starts, the challenger meets the champion, promotion waits.

    Every step is the platform's: `churn.prepare` trains the champion; its promotion is decided
    the way the policy routes it (one approver for a proposal, two for a candidate that failed a
    calibration or segment rule, through the API's own override handler); `churn.publish`, run
    by the production `Worker` from the queued publish task, promotes it, scores the latest
    cutoff and stores the drift report; three more months of data arrive; the scheduler's own
    `evaluate_champion` reads that report and queues the retrain; the `Worker` runs it, AP-8
    compares it with the champion, and its promotion - if one is due - waits for a person
    while the registry's alias is read.

    Two things are the harness's. The retraining cooldown is set to 0 days for its evaluation,
    because the champion is minutes old and the question is whether the triggers fire, not
    whether a 7-day cooldown is honoured (publish's own evaluation, which keeps the cooldown,
    is recorded beside it). And the runs train a model of their own name (`model_name` in the
    run's config, inherited by the retrain), so no promotion here moves the champion the
    platform serves; the model is retired at the end, so the scheduler stops watching it.
    Tasks are claimed by run inside the transaction that queues them, so a running orchestrator
    cannot take them first.
    """
    from sqlalchemy import select

    from agents.graph import churn
    from agents.graph import queue as q
    from agents.graph.state import TERMINAL_STATES
    from agents.monitoring import MonitoringService
    from agents.scheduler import Scheduler
    from agents.worker import Worker
    from backend.app.api.v1.pipelines import request_override
    from backend.app.schemas import OverrideRequest
    from backend.app.services import enqueue_publish
    from datasets.synthetic.generator import GeneratorConfig, SyntheticGenerator
    from experiments.configuration_p import _spark, register_tables
    from python_ml.triad_ml.registry import ModelRegistry
    from triad_core.config import load_yaml, settings
    from triad_core.db import session_scope
    from triad_core.governance import GovernanceService
    from triad_core.models import AuditLog, DriftReport, Model, PipelineRun
    from triad_core.policy import PolicyEngine
    from triad_core.storage import storage

    trial = exp.trial("end_to_end_drift_to_approval")
    tag = f"exp07_{uuid.uuid4().hex[:6]}"
    model_name = f"churn_classifier_{tag}"
    store = storage()
    root = store.root / "experiments" / "exp07" / tag
    registry = ModelRegistry(settings().mlflow_tracking_uri)
    worker = Worker(name=f"{tag}-worker", lease_seconds=3600)
    started = time.perf_counter()
    trial.observations["setup"] = {
        "customers": E2E_CUSTOMERS, "months": E2E_MONTHS, "new_months": E2E_NEW_MONTHS,
        "drift_from_month": E2E_DRIFT_FROM_MONTH, "drift_strength": E2E_DRIFT_STRENGTH,
        "model_name": model_name, "pipeline_name": tag,
        "cooldown_days_for_the_scheduler_evaluation": 0,
    }

    def champion_now() -> str | None:
        current = registry.get_alias(model_name, "champion")
        return current.version if current else None

    def generate(months: int, out: Path) -> None:
        SyntheticGenerator(GeneratorConfig(
            customers=E2E_CUSTOMERS, months=months, seed=SEED, start_date=DATA_START,
            drift_from_month=E2E_DRIFT_FROM_MONTH, drift_strength=E2E_DRIFT_STRENGTH,
        )).write(out)

    def decide_promotion(session: Any, run: Any, who: str) -> Any:
        """File and decide the run's promotion the way the policy routes it; return the publish
        task, claimed. None when the candidate is neither proposed nor eligible."""
        governance = GovernanceService(session, PolicyEngine(settings().governance_policy_path))
        state = _state(store, run)
        if not state:
            trial.record(f"{who}_promotion_path", None,
                         note=f"{run.state}: the run stopped before selection")
            return None
        outcome = state.get("selection_outcome")
        if outcome == "PROPOSE_PROMOTION":
            path = "model.promote"
            pending = [a for a in governance.pending_for(run.id)
                       if a.action_type == "model_promotion"]
        elif (state.get("override") or {}).get("eligible"):
            path = "model.promote_with_failed_check"
            request_override(run.run_key, OverrideRequest(note=OVERRIDE_NOTE), session,
                             _person(session, tag, "ml_engineer"))
            pending = [a for a in governance.pending_for(run.id)
                       if a.policy_action == "model.promote_with_failed_check"]
        else:
            trial.record(f"{who}_promotion_path", None,
                         note=f"{outcome}: neither proposed nor eligible for an override")
            return None
        session.refresh(run)
        trial.record(f"{who}_promotion_path", path)
        trial.record(f"{who}_approvals_pending", len(pending))
        trial.record(f"{who}_run_state_while_pending", run.state)
        trial.record(f"{who}_champion_while_pending", champion_now(),
                     note="the registry's champion alias while the decision is open")
        decided = None
        for approval in pending:
            approver = _person(session, tag, approval.required_role or "ml_engineer")
            decided = governance.decide(
                approval.id, approver=approver, approved=True,
                comment=f"EXP-7 harness as {approver.role}: approved so the chain can continue")
        enqueue_publish(session, decided, decision_status=governance.group_status(decided))
        return q.claim(session, owner=worker.name, lease_seconds=3600, run_id=run.id)

    try:
        # --- 1. the champion -----------------------------------------------------------------
        generate(E2E_MONTHS, root / "history")
        with session_scope() as session:
            run_a = q.create_run(session, pipeline_name=tag, trigger_type="manual",
                                 triggered_by=tag,
                                 config={"experiment": "EXP-7", "model_name": model_name})
            inputs = register_tables(session, store, root / "history", tag, actor=tag)
            run_a_id = run_a.id
        t0 = time.perf_counter()
        with session_scope() as session:
            run_a = session.get(PipelineRun, run_a_id)
            spark = _spark("triad-exp07-champion")
            try:
                prepared = churn.prepare(session, run_a, inputs=inputs, spark=spark,
                                         store=store, seed=SEED, requested_by=tag)
            finally:
                spark.stop()
            trial.record("champion_prepare_s", round(time.perf_counter() - t0, 1), "s")
            trial.record("champion_selection", (prepared.details.get("selection") or {})
                         .get("outcome") if prepared.details else None)
            trial.record("champion_failed_rules", ((prepared.details or {}).get("selection")
                                                   or {}).get("failed_rules") or [])
            task = decide_promotion(session, run_a, "champion")
        if task is None:
            return _conclude_chain(trial, parts=dict.fromkeys(CHAIN_PARTS), why=(
                f"the first model could not be promoted ({prepared.state}: "
                f"{prepared.reason[:200]}), so there was no champion to test against"))
        worker.handle(task)
        champion_version = champion_now()
        trial.record("champion_after_publish", champion_version)
        if champion_version is None:
            return _conclude_chain(trial, parts=dict.fromkeys(CHAIN_PARTS), why=(
                "the first model's promotion was approved but its publish set no champion "
                "alias, so there was no champion to test against"))
        # It waited if no champion alias existed while its approval was pending - this is the
        # model's first promotion - and one exists now that it has been decided.
        champion_waited = _maybe(trial, "champion_champion_while_pending") is None

        with session_scope() as session:
            model = session.execute(select(Model).where(Model.name == model_name)).scalar_one()
            report = session.execute(
                select(DriftReport).where(DriftReport.model_version_id == model.champion_version_id)
                .order_by(DriftReport.computed_at.desc())).scalars().first()
            psi = (report.psi_json or {}) if report is not None else {}
            score = psi.get("score") or {}
            trial.record("drift_flagged_features", list(report.flagged_features or [])
                         if report is not None else None,
                         note="the champion's publish: its latest cutoff against its training rows")
            trial.record("drift_max_feature_psi",
                         max((float(f.get("psi") or 0) for f in psi.get("features", [])),
                             default=None))
            trial.record("drift_score_psi", score.get("psi"))
            trial.record("drift_score_flagged", score.get("flagged"))
            publish_eval = session.execute(
                select(AuditLog).where(AuditLog.run_id == run_a_id,
                                       AuditLog.action == "retraining.evaluated")
                .order_by(AuditLog.id.desc())).scalars().first()
            trial.record("publish_retraining_outcome",
                         publish_eval.outcome if publish_eval is not None else None,
                         note="publish's own evaluation, with the configured 7-day cooldown")
        flagged = bool(_maybe(trial, "drift_flagged_features")
                       or _maybe(trial, "drift_score_flagged"))

        # --- 2. new data arrives, and the scheduler evaluates the champion --------------------
        generate(E2E_MONTHS + E2E_NEW_MONTHS, root / "latest")
        base = load_yaml(settings().pipeline_config_path)
        config = {**base, "retraining": {**(base.get("retraining") or {}), "cooldown_days": 0}}
        with session_scope() as session:
            arrived = register_tables(session, store, root / "latest", tag, actor=tag)
            trial.record("new_versions_registered", len(arrived))
            model = session.execute(select(Model).where(Model.name == model_name)).scalar_one()
            evaluated = Scheduler(config).evaluate_champion(session, model)
            decision, queued = evaluated if evaluated else (None, None)
            trial.record("retraining_rules_fired", decision.rule_ids if decision else None)
            trial.record("retraining_should_start", bool(decision and decision.should_retrain))
            trial.record("retraining_blocked_by", (decision.blocked_by or "") if decision else None)
            trial.record("retraining_run", queued.run_key if queued is not None else None)
            retrain_task = (q.claim(session, owner=worker.name, lease_seconds=3600,
                                    run_id=queued.id) if queued is not None else None)
            retrain_id = queued.id if queued is not None else None
            if queued is not None:
                trial.record("retraining_trigger_type", queued.trigger_type)
                trial.record("retraining_read_the_arrived_data",
                             all((queued.input_versions or {}).get(t) == uri
                                 for t, uri in arrived.items()),
                             note="every input is the version that arrived (DEC-48)")
                trial.record("retraining_trains_the_same_model",
                             (queued.config_json or {}).get("model_name") == model_name)
        if retrain_task is None:
            # Drift that fired and started nothing refutes the second step; no drift leaves it
            # untested, since nothing should have started.
            return _conclude_chain(trial, parts={
                "drift_flagged": flagged,
                "retraining_started": False if flagged else None,
                "challenger_compared": None,
                "promotion_waited": champion_waited,
            }, why=("drift was flagged on the champion's latest cutoff and no retrain started"
                    if flagged else "no drift was flagged on the champion's latest cutoff"))

        # --- 3. the retrain runs, meets the champion, and waits --------------------------------
        t0 = time.perf_counter()
        worker.handle(retrain_task)
        trial.record("retraining_prepare_s", round(time.perf_counter() - t0, 1), "s")
        with session_scope() as session:
            run_c = session.get(PipelineRun, retrain_id)
            state = _state(store, run_c)
            # A retrain that stopped before selection never met the champion: the comparison is
            # then untested, and its state says where it stopped.
            challenger_reached = bool(state)
            selection = state.get("selection") or {}
            delta = selection.get("champion_delta") or {}
            trial.record("challenger_state", run_c.state)
            trial.record("challenger_selection", state.get("selection_outcome"))
            trial.record("challenger_failed_rules", selection.get("failed_rules") or [])
            trial.record("challenger_compared_with_champion", bool(delta),
                         note="AP-8's comparison on the challenger's test cutoff, with the "
                              "champion's own scores")
            trial.observations["challenger_vs_champion"] = delta
            publish_task = decide_promotion(session, run_c, "challenger")
        if publish_task is not None:
            worker.handle(publish_task)
            trial.record("champion_after_challenger_publish", champion_now())

        challenger_pending = _maybe(trial, "challenger_promotion_path") is not None
        if challenger_pending:
            trial.record("challenger_promoted_after_approval",
                         _maybe(trial, "champion_after_challenger_publish")
                         not in (None, champion_version))
        return _conclude_chain(trial, parts={
            "drift_flagged": flagged,
            # Started, and on the data that arrived: a retrain of the wrong data is not the
            # retraining the hypothesis means (DEC-48).
            "retraining_started": bool(_maybe(trial, "retraining_should_start")
                                       and _maybe(trial, "retraining_read_the_arrived_data")),
            "challenger_compared": (_maybe(trial, "challenger_compared_with_champion")
                                    if challenger_reached else None),
            "promotion_waited": champion_waited and (
                _maybe(trial, "challenger_champion_while_pending") == champion_version
                if challenger_pending else True),
        }, why=(f"the scheduler's evaluation fired {_maybe(trial, 'retraining_rules_fired')} and "
                f"queued {_maybe(trial, 'retraining_run')}, which read "
                f"{_maybe(trial, 'retraining_read_the_arrived_data')} the data that had "
                f"arrived; the challenger reached selection: {challenger_reached} "
                f"({_maybe(trial, 'challenger_selection')}), against champion "
                f"v{champion_version}"))
    finally:
        trial.record("elapsed_s", round(time.perf_counter() - started, 1), "s")
        worker.close()
        with session_scope() as session:
            model = session.execute(
                select(Model).where(Model.name == model_name)).scalar_one_or_none()
            if model is not None:
                MonitoringService(session).retire(
                    model, actor=tag, reason="EXP-7 finished: an experiment's model is not "
                                             "served, so nothing should monitor or retrain it")
            for run in session.execute(
                    select(PipelineRun).where(PipelineRun.pipeline_name == tag)).scalars():
                # A published run is the record of a promotion; anything still open is closed,
                # which withdraws an approval nobody will now decide.
                if run.state not in TERMINAL_STATES | {"PUBLISHED", "MONITORING"}:
                    q.set_state(session, run, "CANCELLED", actor=tag)


CHAIN_PARTS = ("drift_flagged", "retraining_started", "challenger_compared", "promotion_waited")


def _state(store: Any, run: Any) -> dict[str, Any]:
    """A run's prepare state, or {} when its prepare stopped before selection wrote one."""
    from agents.graph import churn

    try:
        return churn.read_state(store, run)
    except FileNotFoundError:
        return {}


def _conclude_chain(trial: Any, *, parts: dict[str, bool | None], why: str) -> Any:
    """The end-to-end arm's verdict from its four parts: False if a tested part failed, None if
    a part was never tested because the chain did not reach it, True only if all four held. A
    step the chain never reached is untested, not refuted."""
    verdict = (False if any(v is False for v in parts.values())
               else None if any(v is None for v in parts.values()) else True)
    trial.observations["parts"] = parts
    trial.observations["verdict"] = verdict
    trial.ok = verdict is True
    trial.detail = why + ". Parts: " + ", ".join(f"{k}={v}" for k, v in parts.items())
    return trial


def _maybe(trial: Any, name: str) -> Any:
    """A measurement, or None when that branch never recorded it."""
    return next((m.value for m in trial.measurements if m.name == name), None)


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

        # --- arm 4: the same chain on the platform itself ----------------------------
        chain = _end_to_end(exp)

        exp.limitation(
            "In the end-to-end arm the retraining cooldown is 0 days for the scheduler's "
            "evaluation, because the champion is minutes old; the question is whether the "
            "triggers fire, and publish's own evaluation with the configured 7-day cooldown "
            "is recorded beside it. Its promotions are decided by the harness acting as the "
            "approvers, so they measure that promotion waits for a decision, not how long a "
            "person takes to make one."
        )
        exp.limitation(
            "Replay is over synthetic months, not Online Retail II. Only the generator can be "
            "told to shift one feature or change one segment's behaviour on demand, which is "
            "what the independent variable requires."
        )

        # The detector arms are tested in full; the chain may not reach a part (untested, None),
        # in which case the experiment is partial and its verdict is not a refutation.
        detectors = bool(covariate_flagged and concept_dropped and all_retrain and stable.ok)
        chain_verdict = chain.observations.get("verdict")
        if chain_verdict is None:
            exp.limitation(f"The end-to-end arm did not test every part: {chain.detail}.")
        exp.conclude(
            status="partial" if chain_verdict is None else "completed",
            hypothesis_held=(False if not detectors or chain_verdict is False
                             else None if chain_verdict is None else True),
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
                f"End to end on the platform: {chain.detail}."
            ),
        )
        return exp


if __name__ == "__main__":
    run()
