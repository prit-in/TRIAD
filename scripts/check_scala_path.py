"""Run configuration P once on a small generated dataset, outside any experiment, and report how
each Scala stage went: a check of the generate-build-submit path after a change to it (DEC-55).

    docker compose -f docker/compose.yaml --profile tools run --rm -T dev \
        python scripts/check_scala_path.py

Nothing is written to `experiments/`; the run is recorded on the platform like any other and
cancelled at the end, as the experiments' runs of P are.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    from datasets.synthetic.generator import GeneratorConfig, SyntheticGenerator
    from experiments.baselines import run_isolated

    with tempfile.TemporaryDirectory(prefix="scala-path-") as tmp:
        source = Path(tmp) / "source"
        SyntheticGenerator(GeneratorConfig(customers=400, months=24, seed=42)).write(source)
        p = run_isolated("experiments.configuration_p:run_p", source=str(source),
                         prefix="check", experiment="scala path check", seed=42)
    keys = ("run_key", "prepare_ok", "prepare_state", "reason", "wall_clock_s", "spark_jobs",
            "validation_gates_s", "full_runs_s", "jvm_exit_code", "reached_selection",
            "selected_family", "test_pr_auc")
    print(json.dumps({k: p.get(k) for k in keys}, indent=1, default=str))
    return 0 if p.get("reached_selection") else 1


if __name__ == "__main__":
    sys.exit(main())
