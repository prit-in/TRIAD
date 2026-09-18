"""M11 Hyper-parameter search (PRD sections 18.3, 18.4).

`SELECT_MODEL` tunes only the finalists - the top families after screening - because tuning
every candidate spends the search budget on models that were never going to win. The PRD caps
the whole search, so the budget is enforced here rather than trusted: trials, wall clock, and
a hard ceiling on total fitting time.

Two properties this has to keep, both from 18.3:

  * **reproducibility.** The sampler is seeded, so the same seed, data and versions search the
    same points in the same order. A search that wandered would make "reproduces validation
    PR-AUC within 0.001" untestable.
  * **honesty about the budget.** A study that stops early because it ran out of time says so,
    and the result records how many trials actually ran. A tuned model is never presented as
    the outcome of a search that did not happen.

Nothing here touches the test split. Trials are scored on validation, exactly like the
screened candidates they are ranked against.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from python_ml.triad_ml.candidates import Candidate

if TYPE_CHECKING:  # pragma: no cover
    import optuna

    from python_ml.triad_ml.splits import SplitData
    from python_ml.triad_ml.train import TrainedModel

# Families worth searching. `dummy` is a floor and has nothing to tune; `logreg` is the
# parsimony reference and is deliberately left at its default so AP-2 compares a tuned
# challenger against the same simple model every time.
TUNABLE = ("random_forest", "hist_gb", "xgboost", "mlp")

DEFAULT_TUNING: dict[str, Any] = {
    "enabled": True,
    "sampler": "tpe",
    "seed": 42,
    "n_trials": 30,
    "timeout_seconds": 900,
    "pruner": "median",
    "objective": "valid_pr_auc",
    "finalists": 2,
    "max_search_minutes": 30,   # the whole search, across finalists
}


def search_space(family: str, trial: optuna.Trial) -> dict[str, Any]:
    """Ranges chosen to stay inside AP-7 (training time and model size) on this data size."""
    if family == "random_forest":
        return {
            "n_estimators": trial.suggest_int("n_estimators", 100, 500, step=50),
            "max_depth": trial.suggest_int("max_depth", 4, 20),
            "min_samples_leaf": trial.suggest_int("min_samples_leaf", 1, 20),
        }
    if family == "hist_gb":
        return {
            "max_iter": trial.suggest_int("max_iter", 100, 400, step=50),
            "learning_rate": trial.suggest_float("learning_rate", 0.02, 0.3, log=True),
            "max_leaf_nodes": trial.suggest_int("max_leaf_nodes", 15, 63),
            "l2_regularization": trial.suggest_float("l2_regularization", 1e-4, 1.0, log=True),
        }
    if family == "xgboost":
        return {
            "n_estimators": trial.suggest_int("n_estimators", 100, 500, step=50),
            "max_depth": trial.suggest_int("max_depth", 3, 10),
            "learning_rate": trial.suggest_float("learning_rate", 0.02, 0.3, log=True),
            "subsample": trial.suggest_float("subsample", 0.6, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
        }
    if family == "mlp":
        return {
            "hidden_layer_sizes": (trial.suggest_int("hidden_units", 32, 256, step=32),),
            "alpha": trial.suggest_float("alpha", 1e-5, 1e-1, log=True),
            "learning_rate_init": trial.suggest_float("learning_rate_init", 1e-4, 1e-2,
                                                      log=True),
        }
    raise ValueError(
        f"{family} has no search space; tunable families are {', '.join(TUNABLE)}"
    )


@dataclass
class TuningResult:
    family: str
    best_params: dict[str, Any] = field(default_factory=dict)
    best_value: float | None = None
    trials_run: int = 0          # trials that produced a value
    trials_attempted: int = 0    # including those pruned or cut short by the budget
    trials_requested: int = 0
    seconds: float = 0.0
    stopped_because: str = ""
    baseline_value: float | None = None

    @property
    def improved(self) -> bool:
        """True only when the search found something better than the model it started from."""
        return (
            self.best_value is not None
            and self.baseline_value is not None
            and self.best_value > self.baseline_value
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "best_params": self.best_params,
            "best_value": self.best_value,
            "baseline_value": self.baseline_value,
            "improved": self.improved,
            "trials_run": self.trials_run,
            "trials_attempted": self.trials_attempted,
            "trials_requested": self.trials_requested,
            "seconds": round(self.seconds, 1),
            "stopped_because": self.stopped_because,
        }


def tune_family(
    family: str,
    split: SplitData,
    *,
    config: dict[str, Any] | None = None,
    seed: int = 42,
    baseline: float | None = None,
    fit: Callable[[Candidate], TrainedModel] | None = None,
    deadline: float | None = None,
) -> TuningResult:
    """Search one family's hyper-parameters on the validation split.

    `fit` exists so the caller can pass the very same training function used everywhere else;
    a tuned model must be produced by the same code path as a screened one, or the two are
    not comparable and ranking them together is meaningless.
    """
    import optuna

    settings = {**DEFAULT_TUNING, **(config or {})}
    if family not in TUNABLE:
        return TuningResult(family, stopped_because=f"{family} has no search space")

    if fit is None:
        from python_ml.triad_ml.train import train_candidate

        def fit(candidate: Candidate) -> TrainedModel:
            return train_candidate(candidate, split, seed=seed)

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    sampler = (
        optuna.samplers.TPESampler(seed=int(settings["seed"]))
        if settings["sampler"] == "tpe"
        else optuna.samplers.RandomSampler(seed=int(settings["seed"]))
    )
    pruner = (
        optuna.pruners.MedianPruner() if settings["pruner"] == "median"
        else optuna.pruners.NopPruner()
    )
    study = optuna.create_study(direction="maximize", sampler=sampler, pruner=pruner)

    started = time.perf_counter()
    stopped = ""

    def objective(trial: optuna.Trial) -> float:
        # The whole search has a ceiling, not just each study. Overrunning it would spend the
        # run's budget on tuning and leave nothing for the rest of the pipeline.
        if deadline is not None and time.perf_counter() > deadline:
            trial.study.stop()
            raise optuna.TrialPruned("the search budget for this run is spent")

        params = search_space(family, trial)
        trained = fit(Candidate(family=family, role="tuned", params=params))
        if trained.failed:
            raise optuna.TrialPruned(trained.error or "the candidate failed to fit")
        value = trained.valid_pr_auc
        return float(value) if value == value else float("-inf")

    try:
        study.optimize(
            objective,
            n_trials=int(settings["n_trials"]),
            timeout=float(settings["timeout_seconds"]),
            catch=(),
        )
    except KeyboardInterrupt:  # pragma: no cover - only in an interactive session
        stopped = "interrupted"

    elapsed = time.perf_counter() - started
    completed = [t for t in study.trials if t.value is not None]
    if not stopped:
        if deadline is not None and time.perf_counter() > deadline:
            stopped = "the run's search budget was spent"
        elif len(study.trials) < int(settings["n_trials"]):
            stopped = f"the {settings['timeout_seconds']}s study timeout was reached"

    if not completed:
        return TuningResult(
            family, trials_requested=int(settings["n_trials"]),
            trials_run=0, trials_attempted=len(study.trials),
            seconds=elapsed, baseline_value=baseline,
            stopped_because=stopped or "no trial produced a value",
        )

    return TuningResult(
        family=family,
        best_params=dict(study.best_params),
        best_value=float(study.best_value),
        trials_run=len(completed),
        trials_attempted=len(study.trials),
        trials_requested=int(settings["n_trials"]),
        seconds=elapsed,
        stopped_because=stopped,
        baseline_value=baseline,
    )


def tune_finalists(
    models: list[TrainedModel],
    split: SplitData,
    *,
    config: dict[str, Any] | None = None,
    seed: int = 42,
    fit: Callable[[Candidate], TrainedModel] | None = None,
) -> tuple[list[TrainedModel], list[dict[str, Any]]]:
    """Tune the top tunable families and return the retrained models plus the search record.

    A tuned model is only returned when it actually beat the one it started from. Returning a
    worse model and letting the ranking sort it out would be harmless but dishonest: the
    decision record would claim a search improved something it did not.
    """
    settings = {**DEFAULT_TUNING, **(config or {})}
    records: list[dict[str, Any]] = []
    if not settings.get("enabled", True):
        return [], [{"skipped": "tuning is disabled in the pipeline configuration"}]

    if fit is None:
        from python_ml.triad_ml.train import train_candidate

        def fit(candidate: Candidate) -> TrainedModel:
            return train_candidate(candidate, split, seed=seed)

    ranked = sorted(
        (m for m in models if m.family in TUNABLE and not m.failed),
        key=lambda m: -(m.valid_pr_auc if m.valid_pr_auc == m.valid_pr_auc else -1),
    )
    finalists = ranked[: int(settings["finalists"])]
    if not finalists:
        return [], [{"skipped": "no tunable family survived screening"}]

    deadline = time.perf_counter() + float(settings["max_search_minutes"]) * 60.0
    tuned: list[TrainedModel] = []
    for model in finalists:
        result = tune_family(
            model.family, split, config=settings, seed=seed,
            baseline=model.valid_pr_auc, fit=fit, deadline=deadline,
        )
        records.append(result.to_dict())
        if not result.improved:
            continue
        retrained = fit(
            Candidate(family=model.family, role="tuned", params=dict(result.best_params))
        )
        if not retrained.failed:
            tuned.append(retrained)

    return tuned, records
