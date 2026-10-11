"""EXP-2: medium dataset - where a single node gives out.

PRD section 33:

    Setup: 10 GB synthetic tier, plus REES46 monthly files where available, on Env-B.
    Hypothesis: B2 fails or must sample on a 32 GB node; B3 and P finish; P stays within 20%
    of B3's time.
    Independent variable: configuration.
    Dependent measures: completion, wall-clock, overhead share, coding effort.
    Expected observation: B2 runs out of memory; P's extra time is mostly LLM and compile time.

**The 10 GB tier is not run here.** Env-A-mini gives Docker a 5.27 GB virtual machine and the
sandbox runner clamps any container to 3 GB (`HOST_LIMITS`, ADR-0002), and the generator builds
each table in memory before writing it. The run computes, from its own largest rung, how many
customers 10 GB would be and how long generating them would take at the measured rate; that
extrapolates the generator only, never a result.

What is testable is the **shape** of the hypothesis: that the single-node pandas configuration
is the first to give out as the data grows, while the Spark configurations keep going. So this
experiment climbs a ladder of dataset sizes and finds the rung where B2 stops and B3 does not.
That is the crossover the PRD is asserting exists; the absolute tier at which it happens on a
32 GB node is not measurable here and is not claimed.

**P runs once, at the rung that decides the hypothesis** - the crossover, or, without one, the
largest rung B3 completed - in a process of its own, through the same code EXP-1 measures
(`experiments.configuration_p`). That rung is where "B3 and P finish" and "P stays within 20% of
B3's time" mean something; P on every rung would add hours and no further test. Its ceiling is
the pipeline's own run budget, not the baselines' 30 minutes, because its hyperparameter search
alone is allowed up to 30 minutes by design.

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

from experiments.baselines import IsolatedProcessKilled, run_baseline, run_isolated
from experiments.configuration_p import record_p
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
# Wall-clock ceilings. A configuration still running at its ceiling has failed at that size by
# the hypothesis's own terms ("fails or must sample"); generation gets longer because the
# generator, not a configuration, is what is being waited on.
BASELINE_CEILING_S = 1800
GENERATE_CEILING_S = 3600
# P's ceiling is the pipeline's own run budget (pipelines/churn_v1.yaml, budgets).
P_CEILING_MINUTES_KEY = "max_wall_clock_minutes"
TEN_GB = 10 * 1024**3


def p_ceiling_s() -> float:
    import yaml

    config = yaml.safe_load(Path("pipelines/churn_v1.yaml").read_text(encoding="utf-8"))
    return float(config["budgets"][P_CEILING_MINUTES_KEY]) * 60


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


def _memory_at_start() -> dict[str, object]:
    """What the ladder actually had: the VM's available memory and this container's own caps."""
    def read(path: str) -> str | None:
        try:
            return Path(path).read_text(encoding="ascii").strip()
        except OSError:
            return None

    available = next((round(int(line.split()[1]) / 1024)
                      for line in (read("/proc/meminfo") or "").splitlines()
                      if line.startswith("MemAvailable:")), None)
    return {"vm_mem_available_mib": available,
            "container_memory_max": read("/sys/fs/cgroup/memory.max"),
            "container_swap_max": read("/sys/fs/cgroup/memory.swap.max")}


def _value(trial, name: str):
    """A measurement, or None when the rung never got that far - a rung whose data could not be
    generated records no baseline measurements, and `Trial.get` raises for a missing one."""
    return next((m.value for m in trial.measurements if m.name == name), None)


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
            "As the data grows, the single-node pandas configuration (B2) is the first to fail; "
            "B3 and P finish where it fails; P stays within 20% of B3's time."
        ),
        independent_variable="dataset size (customers), configuration",
        dependent_measures=[
            "completed", "wall_clock_s", "peak_rss_mb", "bytes_on_disk", "rows_trained",
            "test_pr_auc", "p_to_b3_time_ratio", "validation_gates_s",
        ],
        config={
            "ladder_customers": list(LADDER),
            "months": MONTHS, "seed": SEED,
            "cutoffs": [str(c.date()) for c in cutoffs],
            "split": {k: [str(c.date()) for c in v] for k, v in groups.items()},
            "prd_tier_not_run": "10 GB on a 32 GB node",
            "ceilings_s": {"baseline": BASELINE_CEILING_S, "generate": GENERATE_CEILING_S,
                           "p": p_ceiling_s()},
            "memory_at_start": _memory_at_start(),
            "environment_ceiling": (
                "Docker is given a 5.27 GB VM and the sandbox clamps containers to 3 GB "
                "(HOST_LIMITS, ADR-0002)"
            ),
        },
    ) as exp:
        exp.limitation(
            "REES46 monthly files were not used: they are not available in this environment, "
            "and the PRD asks for them only 'where available'."
        )

        crossover: str | None = None
        sources: dict[str, Path] = {}
        b3_failed_at: str | None = None  # the rung where B3 failed, if it did
        b2_failed_there = False
        for customers in LADDER:
            trial = exp.trial(f"customers_{customers}")
            source = root / f"n{customers}"
            try:
                generated_s = run_isolated("experiments.exp02_medium_dataset:generate",
                                           timeout_s=GENERATE_CEILING_S,
                                           source=str(source), customers=customers)
            except (IsolatedProcessKilled, RuntimeError) as exc:   # killed, or it raised
                trial.ok = False
                trial.detail = f"{customers} customers: generating the data itself failed ({exc})"
                # The exception says which: killed (with the cgroup's OOM-kill count), timed
                # out, or raised - not every failure here is the container's memory.
                exp.limitation(
                    f"The ladder stopped at {customers} customers because generating the dataset "
                    f"failed ({str(exc)[:300]}); neither configuration was run on it."
                )
                break
            sources[trial.condition] = source
            trial.record("generate_s", round(generated_s, 1), "s")
            trial.record("customers", customers)
            trial.record("bytes_on_disk", _bytes_on_disk(source), "bytes")

            # Each baseline in a process of its own: a high-water mark read in this process
            # would carry the generator's peak and every earlier size's.
            b2 = run_baseline("experiments.baseline_b2:run", "B2",
                              timeout_s=BASELINE_CEILING_S, root=source, cutoffs=groups,
                              seed=SEED)
            trial.record("b2_completed", b2.ok)
            trial.record("b2_wall_clock_s", round(b2.wall_clock_s, 1), "s")
            trial.record("b2_peak_rss_mb", b2.peak_rss_mb, "MB")
            trial.record("b2_rows_trained", b2.rows_trained)
            trial.record("b2_test_pr_auc", b2.test_pr_auc)
            trial.observations["b2"] = b2.to_dict()

            b3 = run_baseline("experiments.baseline_b3:run_with_own_session", "B3",
                              timeout_s=BASELINE_CEILING_S, root=source, cutoffs=groups,
                              seed=SEED)
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
                b3_failed_at, b2_failed_there = trial.condition, not b2.ok
                exp.limitation(
                    f"The ladder stopped at {customers} customers because B3 failed "
                    + ("as well as B2" if not b2.ok else "while B2 completed")
                    + f" ({b3.detail[:150]})."
                )
                break

        rungs = [t for t in exp.trials if t.condition.startswith("customers_")]
        largest_both = [t for t in rungs
                        if _value(t, "b2_completed") and _value(t, "b3_completed")]

        # --- P, once, at the rung that decides the hypothesis ----------------------------
        b3_done = [t for t in rungs if _value(t, "b3_completed")]
        target = crossover or (b3_done[-1].condition if b3_done else None)
        p_finished, ratio, p_sentence = False, None, ""
        if target is None:
            exp.limitation("P was not run: no rung had a completed B3 to compare it with.")
        else:
            rung = next(t for t in rungs if t.condition == target)
            p_trial = exp.trial(f"P_at_{target}")
            try:
                p = run_isolated("experiments.configuration_p:run_p", timeout_s=p_ceiling_s(),
                                 source=str(sources[target]), prefix="exp02",
                                 experiment="EXP-2", seed=SEED)
                record_p(p_trial, p)
            except IsolatedProcessKilled as exc:
                p_trial.ok = False
                p_trial.detail = f"P's process ended without returning: {exc}"
                p_trial.record("wall_clock_s", round(exc.elapsed_s, 1), "s")
            except RuntimeError as exc:   # P raised: a failure of P, not of the experiment
                p_trial.ok = False
                p_trial.detail = f"P raised before it finished: {str(exc)[:400]}"
            p_finished = bool(p_trial.ok)
            b3_s = _value(rung, "b3_wall_clock_s")
            p_s = _value(p_trial, "wall_clock_s")
            if p_finished and b3_s and p_s:
                ratio = round(p_s / b3_s, 3)
                gates = _value(p_trial, "validation_gates_s") or 0.0
                extra = p_s - b3_s
                p_trial.record("p_to_b3_time_ratio", ratio, note="P's wall clock over B3's, same rung")
                p_trial.record("extra_over_b3_s", round(extra, 1), "s")
                p_trial.record("validation_gates_share_of_extra",
                               round(gates / extra, 3) if extra > 0 else None,
                               note="gates 1-8 over P's extra time; LLM time is 0 (ADR-0003)")
                p_sentence = (
                    f" P at {target}: finished in {p_s:.0f}s against B3's {b3_s:.0f}s, a ratio of "
                    f"{ratio} ({'within' if ratio <= 1.2 else 'outside'} the hypothesis's 20%); "
                    f"{gates:.0f}s of P's time was validation gates, "
                    + (f"{gates / extra:.0%} of its extra time over B3, " if extra > 0 else "")
                    + "and none of it LLM time, because no provider is configured (ADR-0003)."
                )
            else:
                p_sentence = f" P at {target} did not finish: {p_trial.detail[:200]}."

        # --- what 10 GB would take, from this run's own largest rung ---------------------
        # The last rung whose data was generated: a rung whose generation failed has no size.
        sized = [t for t in rungs if _value(t, "bytes_on_disk")]
        if not sized:
            exp.limitation("The PRD's 10 GB tier was not run, and no rung's data was generated "
                           "to extrapolate the generator from.")
        else:
            last = sized[-1]
            per_customer = _value(last, "bytes_on_disk") / max(1, _value(last, "customers") or 1)
            gen_rate = (_value(last, "generate_s") or 0) / max(1, _value(last, "customers") or 1)
            if per_customer:
                needed = TEN_GB / per_customer
                exp.limitation(
                    f"The PRD's 10 GB tier was not run. At {per_customer:.0f} bytes per customer "
                    f"on disk ({last.condition}), 10 GB is about {needed / 1e6:.1f} million "
                    f"customers, and at this run's generation rate that is about "
                    f"{needed * gen_rate / 3600:.0f} hours of generation alone, by a generator "
                    "that holds each table in memory before writing it, in a 3 GB container "
                    "(ADR-0002). That extrapolates the generator only; no result is estimated "
                    "for that size. What is measured instead is the crossover the hypothesis "
                    "asserts, and P beside B3 where it happens."
                )

        # Each part is judged only where the ladder reached it. With a crossover, P has to
        # finish there and stay within 20% of B3; B3 giving out while B2 finishes refutes the
        # shape; both failing together, or neither failing on this ladder, tests nothing.
        if crossover is not None:
            held = bool(p_finished and ratio is not None and ratio <= 1.2)
        elif b3_failed_at is not None and not b2_failed_there:
            held = False
        else:
            held = None
        exp.conclude(
            status="partial",
            hypothesis_held=held,
            conclusion=(
                "Ladder: "
                + "; ".join(t.detail for t in rungs)
                + ". "
                + (
                    f"Crossover observed at {crossover}: the single-node pandas configuration "
                    f"failed while the Spark configuration completed, which is the shape the "
                    f"hypothesis asserts."
                    if crossover
                    else f"B3 failed at {b3_failed_at} while B2 completed: the single-node "
                         f"configuration outlasted the Spark one, the opposite of the shape the "
                         f"hypothesis asserts, so it is refuted here."
                    if b3_failed_at and not b2_failed_there
                    else f"Both configurations failed at {b3_failed_at}, so neither outlasted "
                         f"the other and the hypothesis is not supported here."
                    if b3_failed_at
                    else "No crossover was observed within the sizes this machine can hold, so "
                         "the hypothesis is neither supported nor refuted here."
                )
                + (
                    f" At the largest rung both completed ({largest_both[-1].condition}), B2 "
                    f"took {largest_both[-1].get('b2_wall_clock_s')}s and B3 "
                    f"{largest_both[-1].get('b3_wall_clock_s')}s, with peak memory "
                    f"{largest_both[-1].get('b2_peak_rss_mb')} MB for B2's Python process and "
                    f"{_value(largest_both[-1], 'b3_jvm_peak_rss_mb')} MB for B3's Spark JVM."
                    if largest_both else ""
                )
                + p_sentence
                + " The PRD's 10 GB tier is not reported: see the limitations."
            ),
        )
        return exp


if __name__ == "__main__":
    run()
