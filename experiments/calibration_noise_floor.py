"""What AP-5's threshold means at the split sizes this project actually runs.

EXP-5 left one question open. Three of its four conditions escalated on MISCALIBRATED, and
the write-up could not say whether that was the acceptance policy correctly refusing a model
that could not be calibrated, or a remedy chain that gave up too early. Neither answer was
available from the run, because the two look identical from outside: a number above a
threshold.

This measures the third possibility, which turns out to be the right one.

`expected_calibration_error` is the standard 10-bin equal-width estimator. It is *biased
upward* on small samples: each bin's observed rate is an average over few rows, so it differs
from the bin's mean predicted probability by sampling noise alone, and the estimator sums
those differences as if they were miscalibration. The smaller the split, the larger the floor.

So the question is not "is 0.0554 a large calibration error" but "what does a **perfectly
calibrated** model score on a split this size". A predictor whose scores are exactly the
probabilities its labels are drawn from is perfectly calibrated by construction. Drawing
labels that way and measuring the estimator gives the floor directly, with no model involved.

Run it:

    python -m experiments.calibration_noise_floor

Nothing here trains, and nothing here touches the sealed test set. It is a property of the
estimator and the sample size, not of any model.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from python_ml.triad_ml.evaluate import expected_calibration_error

# The AP-5 threshold in agents/policies/governance.yaml and in the experiments' policy.
THRESHOLD = 0.05

# n=324 at prevalence 0.2716 is EXP-5's validation split, read from its recorded
# `valid_churn_rate` of 0.2716049382716049, which is 88/324. The larger sizes are here to show
# the floor falling, so the effect is identified as a sample-size property and not as a
# peculiarity of one run.
SIZES = (324, 650, 1300, 2600, 5200)
PREVALENCE = 0.2716

# The concentration of the Beta the scores are drawn from. Varying it varies how spread out the
# predictions are; the floor should not depend much on it, and showing that is the point.
CONCENTRATIONS = (2.0, 4.0, 8.0)

TRIALS = 20_000
SEED = 20260922

OUT = Path(__file__).resolve().parent / "EXP-5" / "calibration_noise_floor.json"


def floor_at(n: int, concentration: float, *, trials: int = TRIALS, seed: int = SEED) -> dict[str, Any]:
    """The distribution of the ECE estimator under perfect calibration."""
    rng = np.random.default_rng(seed)
    a = PREVALENCE * concentration
    b = (1.0 - PREVALENCE) * concentration
    values = np.empty(trials)
    for i in range(trials):
        # The score IS the probability. This is what "perfectly calibrated" means.
        p = rng.beta(a, b, size=n)
        y = (rng.random(n) < p).astype(int)
        values[i] = expected_calibration_error(y, p)
    return {
        "n_rows": n,
        "concentration": concentration,
        "median": float(np.median(values)),
        "p90": float(np.quantile(values, 0.90)),
        "p99": float(np.quantile(values, 0.99)),
        "share_above_threshold": float((values > THRESHOLD).mean()),
    }


def run() -> dict[str, Any]:
    rows = [floor_at(n, c) for n in SIZES for c in CONCENTRATIONS]

    print(f"AP-5 threshold: ECE <= {THRESHOLD}")
    print(f"{trials_note()}\n")
    header = f"{'n_rows':>8} {'conc':>5} {'median':>8} {'p90':>8} {'p99':>8} {'P(fail)':>9}"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r['n_rows']:>8} {r['concentration']:>5.1f} {r['median']:>8.4f} "
            f"{r['p90']:>8.4f} {r['p99']:>8.4f} {r['share_above_threshold']:>8.1%}"
        )

    at_exp5 = [r for r in rows if r["n_rows"] == 324]
    worst = max(r["share_above_threshold"] for r in at_exp5)
    best = min(r["share_above_threshold"] for r in at_exp5)
    conclusion = (
        f"At EXP-5's validation size (n=324, prevalence {PREVALENCE}), a perfectly calibrated "
        f"predictor fails AP-5 between {best:.0%} and {worst:.0%} of the time depending on how "
        f"spread out its scores are. The gate sits at the median of the estimator's sampling "
        f"distribution under perfect calibration, so at this size it decides close to nothing: "
        f"an ECE of 0.0554 is not evidence that a model is miscalibrated. The same run's dummy "
        f"classifier - a constant at the base rate, calibrated by construction - measured "
        f"0.0511 and was itself flagged MISCALIBRATED, which is the same fact observed from "
        f"inside the pipeline."
    )
    print(f"\n{conclusion}")

    result = {
        "question": (
            "Is MISCALIBRATED at 5% non-key nulls the acceptance policy correctly refusing an "
            "uncalibratable model, or a calibration chain that gives up too early?"
        ),
        "answer": "Neither: at this validation size the AP-5 threshold is inside the ECE estimator's noise floor.",
        "threshold": THRESHOLD,
        "prevalence": PREVALENCE,
        "trials_per_cell": TRIALS,
        "seed": SEED,
        "estimator": "expected_calibration_error, 10 equal-width bins, python_ml/triad_ml/evaluate.py",
        "rows": rows,
        "conclusion": conclusion,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nwritten to {OUT}")
    return result


def trials_note() -> str:
    return f"{TRIALS:,} draws per cell, seed {SEED}; scores drawn Beta, labels Bernoulli(score)"


if __name__ == "__main__":
    run()
