"""Retraining triggers RT-1..RT-9 and the validity gate (PRD section 19).

Deterministic, like routing: no LLM decides whether to retrain. Every rule returns the signal
it matched and the numbers behind it, so a decision record can cite them. Firing a trigger
starts a *run*; it never promotes anything, because a challenger still has to win on the same
test cutoff and a person still has to approve (section 19.3).

The validity gate in 19.3 comes after the rules: a fired trigger inside the cooldown, on data
that failed its quality gate, or without enough new labelled rows is recorded and parked, not
acted on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import pairwise
from typing import TYPE_CHECKING, Any

from triad_core.ids import utcnow

if TYPE_CHECKING:  # pragma: no cover
    from python_ml.triad_ml.drift import DriftResult

# PRD 19.1 thresholds (EX = example values, configurable per deployment).
DEFAULTS: dict[str, Any] = {
    "cooldown_days": 7,
    "schedule_days": 30,                 # RT-6 "monthly"
    "metric_drop_vs_deployment": 0.05,   # RT-1
    "min_new_label_share": 0.20,         # RT-5 "at least 20% more"
    "min_new_labelled_rows": 500,        # 19.3 "enough new labelled rows exist"
    "concept_drift_window": 3,           # RT-4 "three matured cutoffs"
}

ACTIONS = {
    "RT-1": "retrain and compare",
    "RT-2": "investigate, then retrain",
    "RT-3": "investigate, then retrain",
    "RT-4": "retrain on a recent window",
    "RT-5": "retrain",
    "RT-6": "retrain",
    "RT-7": "mandatory retrain; new model signature",
    "RT-8": "adapt pipeline, then retrain",
    "RT-9": "new label version and new model lineage",
}


@dataclass(frozen=True)
class TriggerSignal:
    rule_id: str
    name: str
    fired: bool
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def action(self) -> str:
        return ACTIONS.get(self.rule_id, "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id, "name": self.name, "fired": self.fired,
            "reason": self.reason, "evidence": self.evidence, "action": self.action,
        }


@dataclass
class MonitoringState:
    """Everything the rules are allowed to look at.

    Anything unknown stays `None`, and a rule with a `None` input reports
    "not evaluated - no signal" instead of a verdict. A missing measurement is never read as
    a passing one.
    """

    # RT-1 / RT-4: PR-AUC on matured cutoffs, oldest first.
    matured_pr_auc: list[float] = field(default_factory=list)
    pr_auc_at_deployment: float | None = None
    ap1_floor: float | None = None
    # RT-2 / RT-3
    drift: DriftResult | None = None
    # RT-5
    labelled_rows_at_training: int | None = None
    labelled_rows_now: int | None = None
    # RT-6
    last_trained_at: datetime | None = None
    # RT-7 / RT-9
    feature_catalog_version: str | None = None
    model_feature_catalog_version: str | None = None
    label_definition_hash: str | None = None
    model_label_definition_hash: str | None = None
    # RT-8
    breaking_schema_changes: list[str] = field(default_factory=list)
    # 19.3 validity gate
    data_quality_ok: bool = True
    data_quality_detail: str = ""
    now: datetime | None = None

    def clock(self) -> datetime:
        return self.now or utcnow()


@dataclass
class RetrainingDecision:
    should_retrain: bool
    signals: list[TriggerSignal]
    blocked_by: str = ""
    reason: str = ""
    evaluated_at: str = ""

    @property
    def fired(self) -> list[TriggerSignal]:
        return [s for s in self.signals if s.fired]

    @property
    def rule_ids(self) -> list[str]:
        return [s.rule_id for s in self.fired]

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_retrain": self.should_retrain,
            "blocked_by": self.blocked_by,
            "reason": self.reason,
            "evaluated_at": self.evaluated_at,
            "fired": [s.to_dict() for s in self.fired],
            "signals": [s.to_dict() for s in self.signals],
        }


def _quiet(rule_id: str, name: str, why: str) -> TriggerSignal:
    return TriggerSignal(rule_id, name, False, why)


def rt1_metric_below_threshold(state: MonitoringState, cfg: dict[str, Any]) -> TriggerSignal:
    name = "metric below threshold"
    if not state.matured_pr_auc:
        return _quiet("RT-1", name, "not evaluated - no matured cutoff yet")
    latest = state.matured_pr_auc[-1]
    evidence: dict[str, Any] = {"latest_pr_auc": latest, "ap1_floor": state.ap1_floor,
                                "pr_auc_at_deployment": state.pr_auc_at_deployment}
    if state.ap1_floor is not None and latest < state.ap1_floor:
        return TriggerSignal("RT-1", name, True,
                             f"PR-AUC {latest:.4f} is below the AP-1 floor {state.ap1_floor:.4f}",
                             evidence)
    drop = float(cfg["metric_drop_vs_deployment"])
    if state.pr_auc_at_deployment is not None and latest < state.pr_auc_at_deployment - drop:
        return TriggerSignal("RT-1", name, True,
                             f"PR-AUC {latest:.4f} is more than {drop} below the value at "
                             f"deployment ({state.pr_auc_at_deployment:.4f})", evidence)
    return TriggerSignal("RT-1", name, False, "matured PR-AUC is within bounds", evidence)


def rt2_data_drift(state: MonitoringState, cfg: dict[str, Any]) -> TriggerSignal:
    name = "data drift"
    if state.drift is None:
        return _quiet("RT-2", name, "not evaluated - no drift report")
    limits = state.drift.thresholds
    single = float(limits.get("psi_feature_threshold", 0.25))
    count_rule = limits.get("psi_feature_count_threshold") or {}
    count_psi = float(count_rule.get("psi", 0.10))
    count_n = int(count_rule.get("features", 3))

    psi_map = {k: v for k, v in state.drift.psi_map().items() if v == v}  # drop NaN
    over_single = state.drift.flagged_features
    over_many = sorted([f for f, v in psi_map.items() if v > count_psi])
    evidence = {"psi": {k: round(v, 4) for k, v in psi_map.items()},
                "over_single_threshold": over_single, "over_count_threshold": over_many}

    if over_single:
        return TriggerSignal("RT-2", name, True,
                             f"PSI above {single} on {', '.join(over_single)}", evidence)
    if len(over_many) >= count_n:
        return TriggerSignal("RT-2", name, True,
                             f"PSI above {count_psi} on {len(over_many)} features "
                             f"({', '.join(over_many)})", evidence)
    return TriggerSignal("RT-2", name, False, "feature distributions are stable", evidence)


def rt3_prediction_drift(state: MonitoringState, cfg: dict[str, Any]) -> TriggerSignal:
    name = "prediction drift"
    if state.drift is None or state.drift.score is None:
        return _quiet("RT-3", name, "not evaluated - no score distribution")
    score = state.drift.score
    if score.flagged:
        return TriggerSignal("RT-3", name, True, score.reason, score.to_dict())
    return TriggerSignal("RT-3", name, False, "score distribution is stable", score.to_dict())


def rt4_concept_drift(state: MonitoringState, cfg: dict[str, Any]) -> TriggerSignal:
    name = "concept drift"
    window = int(cfg["concept_drift_window"])
    series = state.matured_pr_auc
    if len(series) < window:
        return _quiet("RT-4", name,
                      f"not evaluated - {len(series)} of {window} matured cutoffs available")
    recent = series[-window:]
    declining = all(b < a for a, b in pairwise(recent))
    evidence = {"recent_pr_auc": recent}
    if declining:
        return TriggerSignal("RT-4", name, True,
                             f"PR-AUC declined across all {window} matured cutoffs: "
                             + " -> ".join(f"{v:.4f}" for v in recent), evidence)
    return TriggerSignal("RT-4", name, False, "no monotone decline", evidence)


def rt5_new_data_volume(state: MonitoringState, cfg: dict[str, Any]) -> TriggerSignal:
    name = "new data volume"
    if not state.labelled_rows_at_training or state.labelled_rows_now is None:
        return _quiet("RT-5", name, "not evaluated - label counts unknown")
    added = state.labelled_rows_now - state.labelled_rows_at_training
    share = added / state.labelled_rows_at_training
    evidence = {"rows_at_training": state.labelled_rows_at_training,
                "rows_now": state.labelled_rows_now, "added": added,
                "share": round(share, 4)}
    minimum = float(cfg["min_new_label_share"])
    if share >= minimum:
        return TriggerSignal("RT-5", name, True,
                             f"{added} new labelled rows, {share:.1%} more than at training",
                             evidence)
    return TriggerSignal("RT-5", name, False,
                         f"only {share:.1%} more labelled rows; {minimum:.0%} required",
                         evidence)


def rt6_schedule(state: MonitoringState, cfg: dict[str, Any]) -> TriggerSignal:
    name = "schedule"
    if state.last_trained_at is None:
        return _quiet("RT-6", name, "not evaluated - no previous training run")
    days = (state.clock() - state.last_trained_at).days
    due = int(cfg["schedule_days"])
    evidence = {"days_since_training": days, "schedule_days": due}
    if days >= due:
        return TriggerSignal("RT-6", name, True,
                             f"{days} days since the last training run", evidence)
    return TriggerSignal("RT-6", name, False, f"{days} of {due} days elapsed", evidence)


def rt7_feature_change(state: MonitoringState, cfg: dict[str, Any]) -> TriggerSignal:
    name = "feature change"
    current, trained = state.feature_catalog_version, state.model_feature_catalog_version
    if current is None or trained is None:
        return _quiet("RT-7", name, "not evaluated - feature catalogue version unknown")
    evidence = {"catalogue_version": current, "model_catalogue_version": trained}
    if current != trained:
        return TriggerSignal("RT-7", name, True,
                             f"feature catalogue moved from {trained} to {current}", evidence)
    return TriggerSignal("RT-7", name, False, "feature catalogue unchanged", evidence)


def rt8_schema_change(state: MonitoringState, cfg: dict[str, Any]) -> TriggerSignal:
    name = "schema change"
    evidence = {"breaking_changes": state.breaking_schema_changes}
    if state.breaking_schema_changes:
        return TriggerSignal("RT-8", name, True,
                             "breaking upstream change to "
                             + ", ".join(state.breaking_schema_changes), evidence)
    return TriggerSignal("RT-8", name, False, "no breaking schema change", evidence)


def rt9_business_rule_change(state: MonitoringState, cfg: dict[str, Any]) -> TriggerSignal:
    name = "business rule change"
    current, trained = state.label_definition_hash, state.model_label_definition_hash
    if current is None or trained is None:
        return _quiet("RT-9", name, "not evaluated - label definition hash unknown")
    evidence = {"label_definition": current, "model_label_definition": trained}
    if current != trained:
        return TriggerSignal("RT-9", name, True,
                             "the churn definition or horizon changed since training", evidence)
    return TriggerSignal("RT-9", name, False, "churn definition unchanged", evidence)


RULES = (
    rt1_metric_below_threshold, rt2_data_drift, rt3_prediction_drift, rt4_concept_drift,
    rt5_new_data_volume, rt6_schedule, rt7_feature_change, rt8_schema_change,
    rt9_business_rule_change,
)


class RetrainingPolicy:
    """Evaluates every rule, then applies the 19.3 validity gate to whatever fired."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = {**DEFAULTS, **(config or {})}

    @property
    def enabled_rules(self) -> set[str]:
        configured = self.config.get("triggers")
        return set(configured) if configured else set(ACTIONS)

    def evaluate(self, state: MonitoringState) -> RetrainingDecision:
        signals = [rule(state, self.config) for rule in RULES]
        enabled = self.enabled_rules
        signals = [
            s if s.rule_id in enabled
            else TriggerSignal(s.rule_id, s.name, False, "disabled in configuration", s.evidence)
            for s in signals
        ]
        decision = RetrainingDecision(
            should_retrain=False, signals=signals, evaluated_at=state.clock().isoformat()
        )
        fired = decision.fired
        if not fired:
            decision.reason = "no trigger fired"
            return decision

        blocked, why = self._validity_gate(state, fired)
        if blocked:
            decision.blocked_by = blocked
            decision.reason = why
            return decision

        decision.should_retrain = True
        decision.reason = "; ".join(f"{s.rule_id}: {s.reason}" for s in fired)
        return decision

    def _validity_gate(
        self, state: MonitoringState, fired: list[TriggerSignal]
    ) -> tuple[str, str]:
        """19.3: quality passes, the cooldown has ended, enough new labelled rows exist.

        RT-7, RT-8 and RT-9 are definition changes rather than noisy signals, so the cooldown
        does not hold them back: the deployed model no longer matches the pipeline it was
        trained for.
        """
        if not state.data_quality_ok:
            return "quality", f"data quality gate failed: {state.data_quality_detail}"

        definition_change = {"RT-7", "RT-8", "RT-9"} & {s.rule_id for s in fired}
        if not definition_change and state.last_trained_at is not None:
            cooldown = timedelta(days=int(self.config["cooldown_days"]))
            elapsed = state.clock() - state.last_trained_at
            if elapsed < cooldown:
                remaining = cooldown - elapsed
                return "cooldown", (
                    f"cooldown active: {remaining.days}d "
                    f"{remaining.seconds // 3600}h remaining of "
                    f"{self.config['cooldown_days']} days"
                )

        minimum = int(self.config["min_new_labelled_rows"])
        volume_only = {s.rule_id for s in fired} <= {"RT-5", "RT-6"}
        if volume_only and state.labelled_rows_now is not None and \
                state.labelled_rows_at_training is not None:
            added = state.labelled_rows_now - state.labelled_rows_at_training
            if added < minimum:
                return "insufficient_labels", (
                    f"only {added} new labelled rows; {minimum} required to retrain on "
                    "volume or schedule alone"
                )
        return "", ""
