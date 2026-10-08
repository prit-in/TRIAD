"""The non-agentic baselines of PRD section 32.1, for EXP-1, EXP-2 and EXP-3.

    B1  Manual pipeline            a person follows a written runbook
    B2  Python-only automated      one pandas + scikit-learn script on a single node
    B3  Spark and Python, no agents  hand-written PySpark plus scikit-learn, a fixed DAG
    P   TRIAD                      the governed agentic loop

**B1 is not implemented and cannot be.** It is defined as a person following a runbook, and
its metrics are engineer hours from a time sheet. There is no second person in this
environment, and inventing hours would be fabricating the central comparison of the case
study. Every experiment that mentions B1 records it as not measured, with that reason.

**The feature subset is deliberate, declared, and shared by B2 and B3.** The churn feature
catalogue has about forty features; these baselines compute the twelve below. Implementing all
forty twice by hand is what the baselines exist to *avoid* doing, and the effort measurement
would then be a measurement of how long I was willing to spend. So the subset is defined once,
here, and both baselines compute exactly it - which keeps B2 and B3 comparable with each other
(fairness rule 32.3) even though neither is feature-for-feature comparable with P.

That has a consequence for reading EXP-1, and it is stated in the experiment rather than
buried here: a PR-AUC difference between P and a baseline is partly a difference in feature
sets, so EXP-1 also measures quality a second way - every configuration's models fitted on one
shared feature table - which is the only comparison in which a PR-AUC difference is
attributable to the modelling rather than to the features.

Every window below is measured strictly before the cutoff, so the baselines are as
leakage-safe as the pipeline. A baseline that leaked would beat P for the wrong reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

# name -> what it means. The definitions match the catalogue entries of the same names.
BASELINE_FEATURES: dict[str, str] = {
    "recency_days": "days from the last pre-cutoff order to the cutoff",
    "frequency_90d": "distinct orders in the 90 days before the cutoff",
    "frequency_365d": "distinct orders in the 365 days before the cutoff",
    "monetary_365d": "net spend in the 365 days before the cutoff",
    "aov_365d": "monetary_365d / frequency_365d, 0 when there are no orders",
    "items_per_order_365d": "units divided by orders in the 365-day window",
    "discount_share_365d": "discount divided by gross spend in the 365-day window",
    "category_count_365d": "distinct product categories bought in the 365-day window",
    "tenure_days": "days from signup to the cutoff",
    "sessions_90d": "web sessions in the 90 days before the cutoff",
    "tickets_365d": "support tickets raised in the 365 days before the cutoff",
    "returns_365d": "returns in the 365 days before the cutoff",
}
CATEGORICAL = ("country", "loyalty_tier", "acquisition_channel")
LABEL = "churned_90d"
WINDOW_DAYS = 365
HORIZON_DAYS = 90


@dataclass
class BaselineResult:
    """What a baseline run produced, and what it cost."""

    baseline: str
    ok: bool
    wall_clock_s: float
    rows_trained: int = 0
    rows_tested: int = 0
    test_pr_auc: float | None = None
    test_roc_auc: float | None = None
    test_brier: float | None = None
    selected_family: str = ""
    families_tried: list[str] | None = None
    human_actions: int = 0
    llm_calls: int = 0
    peak_rss_mb: float | None = None
    # The Spark JVM's own peak resident set (VmHWM). Spark's memory lives in a separate
    # process, so the Python process's figure never included it.
    jvm_peak_rss_mb: float | None = None
    detail: str = ""
    per_stage_s: dict[str, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline,
            "ok": self.ok,
            "wall_clock_s": round(self.wall_clock_s, 2),
            "rows_trained": self.rows_trained,
            "rows_tested": self.rows_tested,
            "test_pr_auc": self.test_pr_auc,
            "test_roc_auc": self.test_roc_auc,
            "test_brier": self.test_brier,
            "selected_family": self.selected_family,
            "families_tried": self.families_tried or [],
            "human_actions": self.human_actions,
            "llm_calls": self.llm_calls,
            "peak_rss_mb": self.peak_rss_mb,
            "jvm_peak_rss_mb": self.jvm_peak_rss_mb,
            "detail": self.detail,
            "per_stage_s": self.per_stage_s or {},
        }


def peak_rss_mb() -> float | None:
    """Peak resident set size of this process, in MB, or None where it cannot be read.

    A high-water mark for the **whole life of the process**: it never goes down. Read inside a
    long experiment it reports whatever was largest earlier - the data generator, or an earlier
    arm - so it is only a measurement of one baseline when that baseline ran in a process of
    its own. `run_isolated` gives it one.
    """
    try:
        import resource

        # ru_maxrss is kilobytes on Linux.
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    except Exception:
        return None


# --- the model shortlist both baselines use ---------------------------------------------
# The same four families the pipeline screens, with the same seed, so the modelling half of
# the comparison is like for like even where the features are not.
def candidate_models(seed: int = 42) -> dict[str, Any]:
    from sklearn.dummy import DummyClassifier
    from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
    from sklearn.linear_model import LogisticRegression

    return {
        "dummy": DummyClassifier(strategy="prior"),
        "logreg": LogisticRegression(max_iter=1000, class_weight="balanced"),
        "random_forest": RandomForestClassifier(
            n_estimators=200, min_samples_leaf=5, random_state=seed, n_jobs=-1
        ),
        "hist_gb": HistGradientBoostingClassifier(random_state=seed),
    }


def fit_and_select(
    train: Any, valid: Any, test: Any, feature_cols: list[str], *, seed: int = 42
) -> dict[str, Any]:
    """Screen the families on validation, pick the best PR-AUC, score once on test.

    The test split is touched exactly once, by the family validation already chose. A baseline
    that picked its family on the test split would beat P by cheating, and the comparison would
    be worthless.
    """
    import numpy as np
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    numeric = [c for c in feature_cols if c not in CATEGORICAL]
    categorical = [c for c in feature_cols if c in CATEGORICAL]

    def build(estimator: Any, linear: bool) -> Pipeline:
        num_steps: list[Any] = [("impute", SimpleImputer(strategy="median"))]
        if linear:
            num_steps.append(("scale", StandardScaler()))
        return Pipeline([
            ("prep", ColumnTransformer([
                ("num", Pipeline(num_steps), numeric),
                ("cat", OneHotEncoder(handle_unknown="infrequent_if_exist",
                                      min_frequency=20, sparse_output=False), categorical),
            ])),
            ("model", estimator),
        ])

    y_train = train[LABEL].to_numpy()
    y_valid = valid[LABEL].to_numpy()
    y_test = test[LABEL].to_numpy()

    scored: list[tuple[str, float, Any]] = []
    for name, estimator in candidate_models(seed).items():
        pipe = build(estimator, linear=name == "logreg")
        pipe.fit(train[feature_cols], y_train)
        valid_scores = pipe.predict_proba(valid[feature_cols])[:, 1]
        scored.append((name, float(average_precision_score(y_valid, valid_scores)), pipe))

    scored.sort(key=lambda row: row[1], reverse=True)
    best_name, best_valid, best_pipe = scored[0]
    test_scores = best_pipe.predict_proba(test[feature_cols])[:, 1]
    return {
        "selected_family": best_name,
        "valid_pr_auc": round(best_valid, 4),
        "families_tried": [{"family": n, "valid_pr_auc": round(v, 4)} for n, v, _ in scored],
        "test_pr_auc": round(float(average_precision_score(y_test, test_scores)), 4),
        "test_roc_auc": round(float(roc_auc_score(y_test, test_scores)), 4)
        if len(np.unique(y_test)) > 1 else None,
        "test_brier": round(float(brier_score_loss(y_test, test_scores)), 4),
    }



# --- measuring one baseline at a time ----------------------------------------------------------


def jvm_peak_rss_mb(spark: Any) -> float | None:
    """The Spark JVM's peak resident set (Linux VmHWM), read before the session stops."""
    try:
        pid = spark.sparkContext._gateway.proc.pid
        with open(f"/proc/{pid}/status", encoding="ascii") as status:
            for line in status:
                if line.startswith("VmHWM:"):
                    return round(int(line.split()[1]) / 1024.0, 1)
    except Exception:
        return None
    return None


def _call(target: str, kwargs: dict[str, Any]) -> Any:
    import importlib

    module, _, name = target.partition(":")
    return getattr(importlib.import_module(module), name)(**kwargs)


class IsolatedProcessKilled(RuntimeError):
    """The spawned process died without returning, or ran past its wall-clock ceiling."""

    def __init__(self, target: str, elapsed_s: float, oom_kills: int | None, *,
                 exitcode: int | None = None, timed_out: bool = False) -> None:
        self.elapsed_s = elapsed_s
        self.oom_kills = oom_kills
        self.exitcode = exitcode
        self.timed_out = timed_out
        if timed_out:
            cause = "it was stopped at its wall-clock ceiling"
        elif oom_kills:
            cause = f"the container's cgroup recorded {oom_kills} out-of-memory kill(s) meanwhile"
        else:
            cause = "no out-of-memory kill was recorded by the cgroup"
        signal = f" (exit code {exitcode})" if exitcode is not None else ""
        super().__init__(
            f"{target} ended after {elapsed_s:.0f}s without returning{signal}; {cause}")


def cgroup_oom_kills() -> int | None:
    """This container's cumulative OOM-kill count (cgroup v2 `memory.events`), if readable."""
    try:
        for line in Path("/sys/fs/cgroup/memory.events").read_text(encoding="ascii").splitlines():
            key, _, value = line.partition(" ")
            if key == "oom_kill":
                return int(value)
    except (OSError, ValueError):
        return None
    return None


def _report(results: Any, target: str, kwargs: dict[str, Any]) -> None:
    """The child's side: put the result, or the exception's text, on the queue."""
    try:
        results.put((True, _call(target, kwargs)))
    except BaseException as exc:  # re-raised by the parent with its text
        results.put((False, f"{type(exc).__name__}: {exc}"))


def run_isolated(target: str, /, *, timeout_s: float | None = None, **kwargs: Any) -> Any:
    """Run `module:function(**kwargs)` in a freshly spawned interpreter and return its result.

    EXP-1 and EXP-2 read `peak_rss_mb` in the experiment's own process, after the generator and
    the other arms had run, so B3's figure was B2's high-water mark or the generator's, and
    Spark's memory was not in it at all. A spawned process starts with nothing, so the mark it
    reports is this baseline's own; the timings are taken inside the call, so the cost of
    spawning is not in them.

    A process the kernel kills raises no exception of its own - a cgroup out-of-memory kill is
    SIGKILL - so a child that dies without reporting becomes `IsolatedProcessKilled`, carrying
    its exit code (-9 for SIGKILL) and the cgroup's OOM-kill count, so a caller can record the
    failure as a result. `timeout_s` is a backstop for a child that neither finishes nor dies:
    it is stopped there and reported as such, never waited on for ever.
    """
    import multiprocessing as mp
    import queue
    import time

    ctx = mp.get_context("spawn")
    results = ctx.Queue()
    child = ctx.Process(target=_report, args=(results, target, kwargs), daemon=True)
    before, started = cgroup_oom_kills(), time.perf_counter()
    child.start()
    while True:
        try:
            ok, value = results.get(timeout=1.0)
            break
        except queue.Empty:
            elapsed = time.perf_counter() - started
            if not child.is_alive():
                after = cgroup_oom_kills()
                kills = after - before if before is not None and after is not None else None
                raise IsolatedProcessKilled(target, elapsed, kills,
                                            exitcode=child.exitcode) from None
            if timeout_s is not None and elapsed > timeout_s:
                child.kill()
                child.join(30)
                raise IsolatedProcessKilled(target, elapsed, None, exitcode=child.exitcode,
                                            timed_out=True) from None
    child.join(60)
    if ok:
        return value
    raise RuntimeError(f"{target} raised {value}")
