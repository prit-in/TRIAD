"""EXP-2: medium dataset - where a single node gives out.

PRD section 33:

    Setup: 10 GB synthetic tier, plus REES46 monthly files where available, on Env-B.
    Hypothesis: B2 fails or must sample on a 32 GB node; B3 and P finish; P stays within 20%
    of B3's time.
    Independent variable: configuration.
    Dependent measures: completion, wall-clock, overhead share, coding effort.
    Expected observation: B2 runs out of memory; P's extra time is mostly LLM and compile time.

**The 10 GB tier cannot run here, and no amount of care makes it possible.** Env-A-mini gives
Docker a 5.27 GB virtual machine and the sandbox runner clamps any container to 3 GB
(`HOST_LIMITS`, ADR-0002). A 10 GB dataset would not fit in memory for any configuration,
including the ones the hypothesis expects to finish, so running it would produce three failures
and no information.

What is testable is the **shape** of the hypothesis: that the single-node pandas configuration
is the first to give out as the data grows, while the Spark configurations keep going. So this
experiment climbs a ladder of dataset sizes and finds the rung where B2 stops and B3 does not.
That is the crossover the PRD is asserting exists; the absolute tier at which it happens on a
32 GB node is not measurable here and is not claimed.

This is a reduced experiment and is recorded as `partial`. It does not report a 10 GB result,
and it does not extrapolate one.

The ladder stops climbing as soon as B2 has failed and B3 has succeeded at the same rung -
there is nothing further to learn by making both fail - and it logs where it stopped, so a
reader can tell a deliberate stop from a truncated run.
"""

from __future__ import annotations

import datetime as dt
import time
import uuid
from pathlib import Path

import pandas as pd

from experiments.baselines import BaselineResult, IsolatedProcessKilled, run_isolated
from experiments.harness import Experiment

MONTHS = 24
SEED = 42
DATA_START = "2024-01-01"
# Customers per rung. Rows grow faster than customers: each customer contributes a stream of
# order lines over 24 months, so 12 000 customers is roughly a quarter of a million lines.
# The first run stopped at 20 000, where both configurations still finished and B2 peaked at
# 1 065 MB; the ladder now climbs until one gives out inside the 3 GB container (ADR-0002),
# which on that growth is expected between 50 000 and 100 000 customers.
LADDER = (400, 1_500, 6_000, 20_000, 50_000, 100_000, 200_000)


def month_starts(start: str, months: int) -> list[pd.Timestamp]:
    """The usable month-start cutoffs for a window: a 365-day lookback, a 90-day horizon."""
    begin = dt.date.fromisoformat(start)
    total = begin.year * 12 + (begin.month - 1) + months
    data_end = dt.date(total // 12, total % 12 + 1, begin.day) - dt.timedelta(days=1)

    first = begin + dt.timedelta(days=365)
    if first.day != 1:
        t = first.year * 12 + first.month
        first = dt.date(t // 12, t % 12 + 1, 1)
    last = (data_end - dt.timedelta(days=90)).replace(day=1)

    out: list[pd.Timestamp] = []
    day = first
    while day <= last:
        out.append(pd.Timestamp(day))
        t = day.year * 12 + day.month
        day = dt.date(t // 12, t % 12 + 1, 1)
    return out


def split_groups(cutoffs: list[pd.Timestamp]) -> dict[str, list[pd.Timestamp]]:
    """Train/validation/test month starts, from the project's own temporal split planner.

    Using `plan_split` rather than a hand-rolled slice keeps the 90-day gap rule that protects
    against leakage, so the baselines are split exactly as the pipeline splits.
    """
    from python_ml.triad_ml.splits import plan_split

    spec = plan_split([c.date() for c in cutoffs], horizon_days=90)
    # `valid_cutoff` and `test_cutoff` are single dates, not lists - the split holds out one
    # cutoff each. The calibration cutoffs are carved out of the *training* ones, so they
    # belong with training here: these baselines have no separate calibration step, and
    # dropping those months would train them on less data than the pipeline gets.
    return {
        "train": [pd.Timestamp(c) for c in [*spec.train_cutoffs, *spec.calibration_cutoffs]],
        "valid": [pd.Timestamp(spec.valid_cutoff)],
        "test": [pd.Timestamp(spec.test_cutoff)],
    }


def _spark(app: str):
    from pyspark.sql import SparkSession

    from triad_core.config import settings

    session = (
        SparkSession.builder.appName(app)
        .master(settings().spark_master_url)
        .config("spark.sql.ansi.enabled", "true")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.parquet.outputTimestampType", "TIMESTAMP_MICROS")
        .config("spark.sql.shuffle.partitions", "16")
        .config("spark.ui.enabled", "false")
        .config("spark.driver.memory", "1g")
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    return session


def _bytes_on_disk(root: Path) -> int:
    return sum(f.stat().st_size for f in root.rglob("*.parquet"))


def generate(source: str, customers: int) -> float:
    """Write one rung's dataset; run in its own process so its memory leaves with it."""
    from datasets.synthetic.generator import GeneratorConfig, SyntheticGenerator

    t0 = time.perf_counter()
    SyntheticGenerator(
        GeneratorConfig(customers=customers, months=MONTHS, seed=SEED, start_date=DATA_START)
    ).write(Path(source))
    return time.perf_counter() - t0


def _measured(target: str, baseline: str, **kwargs) -> BaselineResult:
    """A baseline's result, or - when the kernel killed it - that failure, as a result.

    "Failures beyond memory limits are recorded as results" (PRD 32.1). An out-of-memory kill
    at the container's limit is SIGKILL, which no `except MemoryError` inside the baseline can
    see; it is caught here, with the cgroup's OOM-kill count as the evidence of what it was.
    """
    try:
        return run_isolated(target, **kwargs)
    except IsolatedProcessKilled as exc:
        return BaselineResult(baseline=baseline, ok=False, wall_clock_s=exc.elapsed_s,
                              detail=f"killed: {exc}")


def run() -> Experiment:
    from triad_core.storage import storage

    store = storage()
    root = store.root / "experiments" / "exp02" / uuid.uuid4().hex[:8]
    cutoffs = month_starts(DATA_START, MONTHS)
    groups = split_groups(cutoffs)

    with Experiment(
        "EXP-2",
        title="Medium dataset: where a single node gives out",
        hypothesis=(
            "The single-node pandas configuration (B2) is the first to fail as the data grows, "
            "while the Spark configuration (B3) keeps going."
        ),
        independent_variable="dataset size (customers), configuration",
        dependent_measures=[
            "completed", "wall_clock_s", "peak_rss_mb", "bytes_on_disk", "rows_trained",
            "test_pr_auc",
        ],
        config={
            "ladder_customers": list(LADDER),
            "months": MONTHS, "seed": SEED,
            "cutoffs": [str(c.date()) for c in cutoffs],
            "split": {k: [str(c.date()) for c in v] for k, v in groups.items()},
            "prd_tier_not_run": "10 GB on a 32 GB node",
            "environment_ceiling": (
                "Docker is given a 5.27 GB VM and the sandbox clamps containers to 3 GB "
                "(HOST_LIMITS, ADR-0002)"
            ),
        },
    ) as exp:
        exp.limitation(
            "The PRD's 10 GB tier was not run and is not estimated. Docker has a 5.27 GB VM "
            "here and the sandbox clamps any container to 3 GB (ADR-0002), so a 10 GB dataset "
            "would fail for every configuration including the ones the hypothesis expects to "
            "finish - three failures and no information. What is measured instead is the "
            "crossover the hypothesis asserts: the rung at which B2 stops and B3 does not."
        )
        exp.limitation(
            "REES46 monthly files were not used. The project has no network access for dataset "
            "downloads in this environment and large datasets must not be committed to git."
        )

        crossover: str | None = None
        for customers in LADDER:
            trial = exp.trial(f"customers_{customers}")
            source = root / f"n{customers}"
            try:
                generated_s = run_isolated("experiments.exp02_medium_dataset:generate",
                                           source=str(source), customers=customers)
            except IsolatedProcessKilled as exc:
                trial.ok = False
                trial.detail = f"{customers} customers: generating the data itself failed ({exc})"
                exp.limitation(
                    f"The ladder stopped at {customers} customers because generating the dataset "
                    f"exceeded what the container holds ({exc}); neither configuration was run "
                    "on it."
                )
                break
            trial.record("generate_s", round(generated_s, 1), "s")
            trial.record("customers", customers)
            trial.record("bytes_on_disk", _bytes_on_disk(source), "bytes")

            # Each baseline in a process of its own: a high-water mark read in this process
            # would carry the generator's peak and every earlier size's.
            b2 = _measured("experiments.baseline_b2:run", "B2",
                           root=source, cutoffs=groups, seed=SEED)
            trial.record("b2_completed", b2.ok)
            trial.record("b2_wall_clock_s", round(b2.wall_clock_s, 1), "s")
            trial.record("b2_peak_rss_mb", b2.peak_rss_mb, "MB")
            trial.record("b2_rows_trained", b2.rows_trained)
            trial.record("b2_test_pr_auc", b2.test_pr_auc)
            trial.observations["b2"] = b2.to_dict()

            b3 = _measured("experiments.baseline_b3:run_with_own_session", "B3",
                           root=source, cutoffs=groups, seed=SEED)
            trial.record("b3_completed", b3.ok)
            trial.record("b3_wall_clock_s", round(b3.wall_clock_s, 1), "s")
            trial.record("b3_peak_rss_mb", b3.peak_rss_mb, "MB",
                         note="the Python driver process only")
            trial.record("b3_jvm_peak_rss_mb", b3.jvm_peak_rss_mb, "MB",
                         note="the Spark JVM's own peak (VmHWM)")
            trial.record("b3_rows_trained", b3.rows_trained)
            trial.record("b3_test_pr_auc", b3.test_pr_auc)
            trial.observations["b3"] = b3.to_dict()

            trial.ok = b2.ok or b3.ok
            trial.detail = (
                f"{customers} customers, {trial.get('bytes_on_disk') / 1e6:.1f} MB on disk: "
                f"B2 {'ok' if b2.ok else 'FAILED'} in {b2.wall_clock_s:.0f}s, "
                f"B3 {'ok' if b3.ok else 'FAILED'} in {b3.wall_clock_s:.0f}s"
            )
            if not b2.ok:
                trial.record("b2_failure", b2.detail[:300])
            if not b3.ok:
                trial.record("b3_failure", b3.detail[:300])

            if not b2.ok and b3.ok and crossover is None:
                crossover = trial.condition
                # Nothing further to learn: the hypothesis's crossover has been observed, and
                # making both configurations fail would only measure the machine.
                exp.limitation(
                    f"The ladder stopped at {customers} customers, where B2 failed and B3 "
                    f"succeeded. Rungs above that were not run: the crossover is the "
                    f"measurement, and pushing both to failure adds nothing."
                )
                break
            if not b3.ok:
                exp.limitation(
                    f"The ladder stopped at {customers} customers because B3 failed too "
                    f"({b3.detail[:150]}), so no crossover was observed below the machine's "
                    "own ceiling."
                )
                break

        rungs = [t for t in exp.trials if t.condition.startswith("customers_")]
        largest_both = [t for t in rungs if t.get("b2_completed") and t.get("b3_completed")]

        exp.conclude(
            status="partial",
            hypothesis_held=crossover is not None,
            conclusion=(
                "Ladder: "
                + "; ".join(t.detail for t in rungs)
                + ". "
                + (
                    f"Crossover observed at {crossover}: the single-node pandas configuration "
                    f"failed while the Spark configuration completed, which is the shape the "
                    f"hypothesis asserts."
                    if crossover
                    else "No crossover was observed within the sizes this machine can hold, so "
                         "the hypothesis is neither supported nor refuted here."
                )
                + (
                    f" At the largest rung both completed ({largest_both[-1].condition}), B2 "
                    f"took {largest_both[-1].get('b2_wall_clock_s')}s and B3 "
                    f"{largest_both[-1].get('b3_wall_clock_s')}s, with peak RSS "
                    f"{largest_both[-1].get('b2_peak_rss_mb')} MB and "
                    f"{largest_both[-1].get('b3_peak_rss_mb')} MB."
                    if largest_both else ""
                )
                + " The PRD's 10 GB tier is not reported: see the limitations. Configuration P "
                "is not on this ladder either - it is measured against B3 in EXP-1, where the "
                "comparison is at a size all three can complete."
            ),
        )
        return exp


if __name__ == "__main__":
    run()
