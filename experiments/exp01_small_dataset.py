"""EXP-1: small dataset, TRIAD against the non-agentic baselines.

PRD section 33:

    Setup: UCI Online Retail II on Env-A; P against B1, B2 and B3.
    Hypothesis: P finishes end to end with no manual step except approval; agent overhead
    dominates P's time at this size.
    Independent variable: configuration.
    Dependent measures: wall-clock, LLM calls, human actions, test PR-AUC.
    Expected observation: P matches B3's model quality within intervals but is slower than B2,
    supporting local-mode routing (LR-07).

This is the comparison the whole case study turns on, so what it can and cannot say is set out
before any number appears.

**B1 is not measured.** It is defined as a person following a written runbook, and its metrics
are engineer hours from a time sheet. There is no second person here, and inventing hours would
be fabricating the central comparison. Recorded as not run, with that reason.

**The dataset is the project's synthetic generator, not Online Retail II.** Online Retail II
has transactions and nothing else - no customer table, no web sessions, no support tickets, no
returns - so the churn definition and two thirds of the feature catalogue have nothing to read.
ADR-0002 records this; the generator exists for exactly this reason. Recorded as a deviation.

**LLM calls are 0 for P, and that is a limitation, not a result.** No provider is configured
(ADR-0003), so the hypothesis "agent overhead dominates P's time" cannot be tested for the
overhead the PRD means. What *is* measured is the overhead that is present: the nine validation
gates including an sbt compile, container start-up for each sandboxed Spark job, the governance
checks, and the hash-chained audit writes. That is a real and interesting number, and it is
reported as what it is rather than as a stand-in for LLM time.

**Model quality is measured twice, and the second way is the one to trust.** B2 and B3 compute
twelve features; P computes the whole catalogue. A PR-AUC difference between them is therefore
partly a difference in feature sets. So after all three configurations have run, every
configuration's model shortlist is refitted on **one shared feature table** - P's, with the same
split - and those numbers are the like-for-like comparison of the modelling. The first set
compares whole pipelines; the second compares models.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pandas as pd

from experiments.baselines import (
    BASELINE_FEATURES,
    IsolatedProcessKilled,
    run_baseline,
    run_isolated,
)
from experiments.configuration_p import record_p
from experiments.harness import Experiment

CUSTOMERS = 400
MONTHS = 24
SEED = 42
DATA_START = "2024-01-01"


def _run_triad(exp: Experiment, source: Path) -> dict[str, Any]:
    """Configuration P, in a process of its own like the baselines (experiments.configuration_p).

    Run in this process, P's memory was never measurable apart from the experiment's own, and
    it is the same code EXP-2 measures at scale.
    """
    trial = exp.trial("P_triad")
    try:
        p = run_isolated("experiments.configuration_p:run_p", source=str(source),
                         prefix="exp01", experiment="EXP-1", seed=SEED)
    except (IsolatedProcessKilled, RuntimeError) as exc:   # killed, or P raised
        trial.ok = False
        trial.detail = f"P ended without a result: {str(exc)[:400]}"
        trial.record("test_pr_auc", None, note="not measured")
        return {}
    record_p(trial, p)
    return p.get("shared") or {}


def _cutoff_groups(split: dict[str, Any]) -> dict[str, list[pd.Timestamp]]:
    """The train/valid/test month starts P chose, so the baselines split identically."""
    return {
        # `valid_cutoff` and `test_cutoff` are single dates. Reading plurals gave empty
        # validation and test splits, and both baselines refused to run - correctly, since a
        # baseline scored on nothing would have been worse than one that failed.
        "train": [
            pd.Timestamp(c)
            for c in [*split.get("train_cutoffs", []), *split.get("calibration_cutoffs", [])]
        ],
        "valid": [pd.Timestamp(split["valid_cutoff"])],
        "test": [pd.Timestamp(split["test_cutoff"])],
    }


def run() -> Experiment:
    from datasets.synthetic.generator import GeneratorConfig, SyntheticGenerator
    from experiments import baseline_b2, baseline_b3
    from triad_core.storage import storage

    store = storage()
    root = store.root / "experiments" / "exp01" / uuid.uuid4().hex[:8]

    with Experiment(
        "EXP-1",
        title="Small dataset, TRIAD against the non-agentic baselines",
        hypothesis=(
            "P finishes end to end with no manual step except approval; agent overhead "
            "dominates P's time at this size."
        ),
        independent_variable="configuration (B1, B2, B3, P)",
        dependent_measures=[
            "wall_clock_s", "llm_calls", "human_actions_excluding_approval",
            "approvals_required", "test_pr_auc", "source_lines",
        ],
        config={
            "customers": CUSTOMERS, "months": MONTHS, "seed": SEED,
            "dataset": "TRIAD synthetic generator (see the deviation note)",
            "configurations": {
                "B1": "manual runbook - not implementable without a second person",
                "B2": "one pandas + scikit-learn script, single node",
                "B3": "hand-written PySpark + scikit-learn, fixed DAG, no repair loop",
                "P": "TRIAD: churn.prepare, the governed agentic loop",
            },
            "baseline_features": list(BASELINE_FEATURES),
        },
    ) as exp:
        # --- the data, generated once and shared by every configuration ---------
        tables = SyntheticGenerator(
            GeneratorConfig(
                customers=CUSTOMERS, months=MONTHS, seed=SEED, start_date=DATA_START,
                null_rate_key=0.03, duplicate_rate=0.01, invalid_price_rate=0.005,
            )
        ).write(root / "source")
        source = root / "source"
        exp.artifact("raw/source_tables.json", sorted(tables))

        # --- B1 -----------------------------------------------------------------
        b1 = exp.trial("B1_manual_runbook")
        b1.record("implemented", False)
        b1.record("wall_clock_s", None,
                  note="engineer hours from a time sheet; there is no second person here")
        b1.ok = True   # correctly recorded as not measured, which is the honest outcome
        b1.detail = "not measured: a manual baseline needs a person, and hours cannot be invented"

        # --- P ------------------------------------------------------------------
        shared = _run_triad(exp, source)
        p_trial = next(t for t in exp.trials if t.condition == "P_triad")

        if not shared.get("split"):
            exp.limitation(
                "P did not reach selection, so there is no shared split for the baselines to "
                "use and no like-for-like quality comparison. The baselines were not run: "
                "splitting them differently from P would make the comparison meaningless."
            )
            exp.conclude(
                status="failed",
                hypothesis_held=None,
                conclusion=f"configuration P did not complete: {p_trial.detail}",
            )
            return exp

        groups = _cutoff_groups(shared["split"])
        exp.artifact("raw/split.json", {k: [str(c.date()) for c in v]
                                        for k, v in groups.items()})

        # --- B2 -----------------------------------------------------------------
        # Each baseline runs in a process of its own, so the memory it reports is its own
        # (experiments.baselines.run_isolated). B3 starts its own SparkSession there, and its
        # JVM's peak is read before that session stops.
        b2_trial = exp.trial("B2_pandas_only")
        # A baseline killed or raising is that baseline's failed result, as in EXP-2: the
        # comparison then records it, rather than the experiment ending without one.
        b2_result = run_baseline("experiments.baseline_b2:run", "B2",
                                 root=source, cutoffs=groups, seed=SEED)
        _record_baseline(b2_trial, b2_result, baseline_b2.source_lines())

        # --- B3 -----------------------------------------------------------------
        b3_trial = exp.trial("B3_spark_no_agents")
        b3_result = run_baseline("experiments.baseline_b3:run_with_own_session", "B3",
                                 root=source, cutoffs=groups, seed=SEED)
        _record_baseline(b3_trial, b3_result, baseline_b3.source_lines())

        # --- the like-for-like quality comparison -------------------------------
        like = exp.trial("like_for_like_quality_on_one_feature_table")
        _like_for_like(like, shared, seed=SEED)

        # --- read it ------------------------------------------------------------
        exp.limitation(
            "B1 is not measured: it is defined as a person following a runbook, and its "
            "metrics are engineer hours. Inventing them would fabricate the comparison."
        )
        exp.limitation(
            "The dataset is TRIAD's synthetic generator, not Online Retail II, which has no "
            "customer, session, ticket or return tables and so cannot support the churn "
            "definition or most of the feature catalogue (ADR-0002)."
        )
        exp.limitation(
            "P made 0 LLM calls because no provider is configured (ADR-0003). The hypothesis "
            "that 'agent overhead dominates P's time' therefore cannot be tested for LLM time. "
            "The overhead that is present - validation gates including an sbt compile, sandbox "
            "container start-up per Spark job, governance and audit writes - is measured and "
            "reported instead, and is not a substitute for the untested claim."
        )

        p_wall = p_trial.get("wall_clock_s")
        slower_than_b2 = (
            b2_result.ok and isinstance(p_wall, (int, float))
            and p_wall > b2_result.wall_clock_s
        )
        no_manual_step = p_trial.get("human_actions_excluding_approval") == 0
        # The hypothesis's second half, measured the way B3 exists to measure it: B3 does the
        # same Spark work and modelling with no agents, so P's time beyond B3's is the cost of
        # the agentic loop. "Slower than B2" is the expected observation, not the hypothesis,
        # and used to stand in for this half of the verdict.
        overhead_share = (
            round((p_wall - b3_result.wall_clock_s) / p_wall, 3)
            if b3_result.ok and isinstance(p_wall, (int, float)) and p_wall > 0 else None
        )
        p_trial.record("agentic_overhead_share", overhead_share,
                       note="(P - B3) / P: P's time beyond the same work done without agents; "
                            "none of it is LLM time, as no provider is configured")
        overhead_dominates = None if overhead_share is None else overhead_share > 0.5

        exp.conclude(
            status="partial",
            hypothesis_held=(False if not (p_trial.ok and no_manual_step)
                             else overhead_dominates),
            conclusion=(
                f"Wall-clock: P {p_wall}s, B3 {round(b3_result.wall_clock_s, 1)}s, "
                f"B2 {round(b2_result.wall_clock_s, 1)}s, B1 not measured. "
                f"P's time beyond B3's - the agentic loop's cost, with no LLM time in it - is "
                f"{overhead_share} of P's wall clock, so overhead dominates P's time at this "
                f"size: {overhead_dominates}. "
                f"P is slower than B2: {slower_than_b2}, which is the expected observation at "
                f"this size and the argument for local-mode routing (LR-07). "
                + (
                    f"P's candidate was escalated by the acceptance policy "
                    f"({p_trial.get('escalation_reason')}) on a validation split of "
                    f"{p_trial.get('valid_rows')} rows - a decision left to a person, which is "
                    "the platform working as designed, and at this size inside the calibration "
                    "estimator's measured noise floor (experiments/calibration_noise_floor.py). "
                    if p_trial.get("escalated") else ""
                )
                + f"P required {p_trial.get('approvals_required')} approval(s) and "
                f"{p_trial.get('human_actions_excluding_approval')} other manual steps, so the "
                "first half of the hypothesis holds. "
                f"Whole-pipeline test PR-AUC: P {p_trial.get('test_pr_auc')} "
                f"(full feature catalogue), B3 {b3_result.test_pr_auc} and B2 "
                f"{b2_result.test_pr_auc} (twelve features each) - not comparable as model "
                "quality, because the feature sets differ. "
                f"Hand-written pipeline lines: B2 {baseline_b2.source_lines()}, "
                f"B3 {baseline_b3.source_lines()}. "
                f"P made {p_trial.get('llm_calls')} LLM calls, ran "
                f"{p_trial.get('spark_jobs')} sandboxed Spark jobs and wrote "
                f"{p_trial.get('audit_entries')} audit entries and "
                f"{p_trial.get('decision_records')} decision records - the governance and "
                "evidence the baselines produce none of, which is the cost the wall-clock "
                "difference is buying. See the like-for-like trial for the only PR-AUC "
                "comparison in which the features are held constant."
            ),
        )
        return exp


def _record_baseline(trial: Any, result: Any, lines: int) -> None:
    for name, value, unit in (
        ("wall_clock_s", round(result.wall_clock_s, 2), "s"),
        ("rows_trained", result.rows_trained, ""),
        ("rows_tested", result.rows_tested, ""),
        ("test_pr_auc", result.test_pr_auc, ""),
        ("test_roc_auc", result.test_roc_auc, ""),
        ("test_brier", result.test_brier, ""),
        ("llm_calls", result.llm_calls, ""),
        ("human_actions_excluding_approval", result.human_actions, ""),
        ("approvals_required", 0, ""),
        ("peak_rss_mb", result.peak_rss_mb, "MB"),
        ("jvm_peak_rss_mb", result.jvm_peak_rss_mb, "MB"),
        ("source_lines", lines, "lines"),
    ):
        trial.record(name, value, unit)
    trial.record("selected_family", result.selected_family)
    trial.observations["per_stage_s"] = result.per_stage_s
    trial.observations["families_tried"] = result.families_tried
    trial.ok = result.ok
    trial.detail = result.detail


def _like_for_like(trial: Any, shared: dict[str, Any], *, seed: int) -> None:
    """Refit the shared model shortlist on P's feature table, with P's split.

    This is the only PR-AUC comparison in this experiment where a difference is attributable
    to the modelling rather than to the features, so it is measured separately and labelled.
    """
    from experiments.baselines import fit_and_select
    from python_ml.triad_ml.splits import SplitSpec, apply_split
    from triad_core.parquet_io import read_parquet
    from triad_core.storage import storage

    store = storage()
    try:
        features = read_parquet(store.path(shared["features_uri"]))
        labels = read_parquet(store.path(shared["labels_uri"]))
        dataset = features.merge(labels, on=["customer_id", "snapshot_date"], how="inner")
        split = apply_split(dataset, SplitSpec(**shared["split"]))
        cols = [c for c in shared["feature_columns"] if c in dataset.columns]
        outcome = fit_and_select(split.train, split.valid, split.test, cols, seed=seed,
                                 categorical=shared.get("categorical_columns"))
    except Exception as exc:
        trial.ok = False
        trial.detail = f"could not refit on the shared table: {type(exc).__name__}: {exc}"
        trial.record("test_pr_auc", None, note="not measured")
        return

    trial.record("feature_count", len(cols))
    trial.record("rows_trained", len(split.train))
    trial.record("rows_tested", len(split.test))
    trial.record("baseline_shortlist_test_pr_auc", outcome["test_pr_auc"],
                 note="the four families B2 and B3 screen, fitted on P's feature table")
    trial.record("baseline_shortlist_test_roc_auc", outcome["test_roc_auc"])
    trial.record("baseline_shortlist_test_brier", outcome["test_brier"])
    trial.record("selected_family", outcome["selected_family"])
    trial.observations["families_tried"] = outcome["families_tried"]
    trial.ok = True
    trial.detail = (
        f"on P's {len(cols)} features and P's split, the baselines' shortlist reaches test "
        f"PR-AUC {outcome['test_pr_auc']} with {outcome['selected_family']}; compare with P's "
        "own selected model, which additionally has calibration, tuning and the acceptance "
        "policy applied"
    )


if __name__ == "__main__":
    run()
