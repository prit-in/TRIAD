# Not an experiment run

This folder was written at 18:48 UTC on 10-10-2026 by an accident, not by EXP-7. The batch script
(`scripts/run_experiments_batch.sh`) was edited while it was running the real EXP-7
(`20261010T175642Z`, completed, hypothesis held). Bash reads a running script from disk as it
goes, so when the run returned it resumed at a shifted offset and executed
`python scripts/run_experiment.py EXP-7` on the Windows host, which has no scikit-learn: the run
failed on its first import, 0.09 s in, measured nothing, and overwrote `latest.json`.

`latest.json` was restored from the real run's `result.json` (plus its `run_dir`, as the harness
writes it). This folder is kept so the record shows what happened rather than a gap.
