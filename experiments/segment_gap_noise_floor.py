"""What AP-6's 0.10 recall-gap limit means at the split sizes this project runs.

run-2026-10-04-005 escalated on "segment recall gap 0.115 within acquisition_channel": recall
0.433 among 30 paid-search churners against 0.548 among 31 referral churners. The synthetic
generator assigns country, acquisition channel and loyalty tier at random, independently of
behaviour and of churn, so the true recall gap across them is zero by construction - the 0.115
was sampling noise. The question is how often a perfectly fair model is refused.

This measures it directly. Each simulated validation set gives every customer independent
attributes with the generator's own proportions, makes a share of them churners, and detects each
churner with the same probability whatever their segment - so every segment's true recall is
equal. AP-6 is then applied exactly as the pipeline applies it: segments need 50 rows and 30
churners (DEC-02), gaps are taken within each segmentation (DEC-26), and the largest is compared
with 0.10. The share of draws refused is the gate's false-alarm rate.

    python -m experiments.segment_gap_noise_floor

Nothing here trains a model or reads run data; it is a property of the gate and the sample size.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from python_ml.triad_ml.evaluate import Metrics, recall_gap

LIMIT = 0.10          # AP6_max_segment_recall_gap
MIN_ROWS, MIN_POSITIVES = 50, 30   # DEC-02
PREVALENCE = 0.253    # validation churn rate of the 800-customer runs (run-2026-10-04-005)
RECALL = 0.5          # the same in every segment: the null of a perfectly fair model

# The generator's own proportions (datasets/synthetic/generator.py); tenure-band shares as
# measured on run-2026-10-04-005's validation split (126, 132, 236 of 494).
SEGMENTATIONS: dict[str, list[float]] = {
    "country": [0.5, 0.15, 0.15, 0.08, 0.07, 0.05],
    "acquisition_channel": [0.2] * 5,
    "loyalty_tier": [0.6, 0.3, 0.1],
    "tenure_band": [126 / 494, 132 / 494, 236 / 494],
}
GATES = {
    "all four segmentations (as configured)": list(SEGMENTATIONS),
    "country and tenure band only (the PRD's AP-6 wording)": ["country", "tenure_band"],
}
# 494 is the validation split of the 800-customer runs; the others show the rate falling.
SIZES = (494, 1000, 2000, 4000, 8000)
TRIALS = 20_000
SEED = 20261004

OUT = Path(__file__).resolve().parent / "policy_floors" / "segment_gap_noise_floor.json"


def _statistic(rng: np.random.Generator, n: int, names: list[str]) -> float:
    churner = rng.random(n) < PREVALENCE
    detected = churner & (rng.random(n) < RECALL)
    segments: list[Metrics] = []
    for name in names:
        shares = SEGMENTATIONS[name]
        group = rng.choice(len(shares), size=n, p=shares)
        rows = np.bincount(group, minlength=len(shares))
        positives = np.bincount(group, weights=churner, minlength=len(shares))
        hits = np.bincount(group, weights=detected, minlength=len(shares))
        for k in range(len(shares)):
            m = Metrics(split="valid", segment=f"{name}={k}", n_rows=int(rows[k]))
            if rows[k] < MIN_ROWS or positives[k] < MIN_POSITIVES:
                m.values["insufficient_data"] = 1.0
            else:
                m.values["recall"] = float(hits[k] / positives[k])
            segments.append(m)
    return recall_gap(segments)


def false_alarm_rate(n: int, names: list[str], *, trials: int = TRIALS, seed: int = SEED) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    stats = np.array([_statistic(rng, n, names) for _ in range(trials)])
    return {
        "n_rows": n,
        "segmentations": names,
        "median_gap": float(np.median(stats)),
        "p90_gap": float(np.quantile(stats, 0.90)),
        "false_alarm_rate": float((stats > LIMIT).mean()),
    }


def run() -> dict[str, Any]:
    rows = []
    for label, names in GATES.items():
        for n in SIZES:
            r = false_alarm_rate(n, names)
            r["gate"] = label
            rows.append(r)
            print(f"{label:<55} n={n:>5}  median gap {r['median_gap']:.3f}  "
                  f"p90 {r['p90_gap']:.3f}  refused {r['false_alarm_rate']:.1%}")

    at_run = next(r for r in rows if r["n_rows"] == 494 and r["segmentations"] == list(SEGMENTATIONS))
    largest = max(rows, key=lambda r: r["n_rows"])
    conclusion = (
        f"At the 800-customer runs' validation size (494 rows, prevalence {PREVALENCE}), a model "
        f"whose recall is identical in every segment is refused by AP-6 in "
        f"{at_run['false_alarm_rate']:.0%} of draws. A segment is admitted with as few as 30 "
        f"churners, where the standard error of recall alone is about 0.09 - nearly the whole "
        f"0.10 limit. The rate does not fall steadily with size: more rows admit more segments, "
        f"each another noisy comparison, so it rises before it falls, and at "
        f"{largest['n_rows']} rows it is still {largest['false_alarm_rate']:.0%}. The 0.115 gap "
        f"that escalated run-2026-10-04-005 is well inside this noise."
    )
    print("\n" + conclusion)
    result = {
        "question": "How often does AP-6 refuse a model whose recall is equal across segments?",
        "limit": LIMIT, "min_rows": MIN_ROWS, "min_positives": MIN_POSITIVES,
        "prevalence": PREVALENCE, "true_recall": RECALL, "segmentation_shares": SEGMENTATIONS,
        "trials_per_cell": TRIALS, "seed": SEED,
        "statistic": "recall_gap, python_ml/triad_ml/evaluate.py (within-segmentation, DEC-26)",
        "rows": rows, "conclusion": conclusion,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"written to {OUT}")
    return result


if __name__ == "__main__":
    run()
