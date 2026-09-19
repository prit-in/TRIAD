"""Run one experiment, or all of them, and print a table of what happened.

    python scripts/run_experiment.py --list
    python scripts/run_experiment.py EXP-9
    python scripts/run_experiment.py --all
    python scripts/run_experiment.py --all --only-fast     # skip the ones that need Spark/sbt

Results go to `experiments/<exp_id>/<timestamp>/`. Nothing here decides whether a hypothesis
held - each experiment reports that itself, from its own measurements - and nothing here
retries a failure quietly: an experiment that raised is printed as failed with its status.

The ones marked slow drive real Spark jobs or sbt, and they must not run concurrently with
each other: they share the Scala target directories and the pipeline's clean/feature folders,
so two at once would overwrite each other's outputs and the measurements would be fiction.
`--all` therefore runs strictly in sequence.
"""

from __future__ import annotations

import argparse
import importlib
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# exp_id -> (module, needs_spark_or_sbt, one-line summary)
EXPERIMENTS: dict[str, tuple[str, bool, str]] = {
    "EXP-1": ("experiments.exp01_small_dataset", True, "Small dataset, end to end vs baselines"),
    "EXP-2": ("experiments.exp02_medium_dataset", True, "Medium tier: where a single node fails"),
    "EXP-3": ("experiments.exp03_large_dataset", True, "Scaling with worker count"),
    "EXP-4": ("experiments.exp04_schema_errors", True, "Schema errors"),
    "EXP-5": ("experiments.exp05_missing_values", True, "Missing values"),
    "EXP-6": ("experiments.exp06_spark_failures", True, "Spark failures and recovery"),
    "EXP-7": ("experiments.exp07_drift", False, "Model degradation and drift"),
    "EXP-8": ("experiments.exp08_codegen_faults", True, "Scala compile failures"),
    "EXP-9": ("experiments.exp09_leakage", False, "Leakage sentinel"),
    "EXP-10": ("experiments.exp10_governance", False, "Governance bypass"),
}


def _run(exp_id: str) -> tuple[str, bool | None, str]:
    module_name, _, _ = EXPERIMENTS[exp_id]
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        return "not_implemented", None, f"{module_name} does not exist yet: {exc}"
    try:
        exp = module.run()
    except Exception as exc:
        traceback.print_exc()
        return "failed", None, f"{type(exc).__name__}: {exc}"
    return exp.status, exp.hypothesis_held, exp.conclusion


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("exp_id", nargs="?", help="for example EXP-9")
    parser.add_argument("--all", action="store_true", help="run every experiment, in order")
    parser.add_argument("--only-fast", action="store_true",
                        help="with --all, skip the experiments that need Spark or sbt")
    parser.add_argument("--list", action="store_true", help="list the experiments and exit")
    args = parser.parse_args()

    if args.list:
        for exp_id, (module, slow, summary) in EXPERIMENTS.items():
            mark = "slow" if slow else "fast"
            print(f"{exp_id:8} [{mark}] {summary:44} {module}")
        return 0

    if args.all:
        targets = [
            e for e in EXPERIMENTS
            if not (args.only_fast and EXPERIMENTS[e][1])
        ]
    elif args.exp_id:
        key = args.exp_id.upper()
        if key not in EXPERIMENTS:
            print(f"unknown experiment {args.exp_id!r}; try --list", file=sys.stderr)
            return 2
        targets = [key]
    else:
        parser.print_help()
        return 2

    results: list[tuple[str, str, bool | None, str]] = []
    for exp_id in targets:
        print(f"\n=== {exp_id}: {EXPERIMENTS[exp_id][2]} ===", flush=True)
        status, held, conclusion = _run(exp_id)
        print(f"  status={status} hypothesis_held={held}")
        print(f"  {conclusion}", flush=True)
        results.append((exp_id, status, held, conclusion))

    print("\n--- summary " + "-" * 60)
    for exp_id, status, held, _ in results:
        verdict = {True: "held", False: "did not hold", None: "no verdict"}[held]
        print(f"{exp_id:8} {status:16} {verdict}")

    # A failed or unimplemented experiment is a non-zero exit: this script is used in the
    # evidence trail, and a green exit on a failed run would be the wrong signal entirely.
    bad = [e for e, s, _, _ in results if s in ("failed", "not_implemented")]
    if bad:
        print(f"\n{len(bad)} experiment(s) did not produce a result: {', '.join(bad)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
