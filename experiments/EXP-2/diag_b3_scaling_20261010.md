# Why B3 stopped at 100 000 customers in EXP-2, 10-10-2026

EXP-2 (`20261008T171242Z`) ran B3 on 50 000 customers in 227 s, with its Spark JVM peaking at
2 223 MB, and then stopped it at the 1 800 s ceiling on 100 000 customers, where B2 finished in
130 s. The run recorded the hypothesis as refuted. Two diagnostics, not committed because
`experiments/_logs` is ignored, re-ran B3 on that run's own 100 000-customer data
(`exp02/7d86b165/n100000`) in the same `dev` container (3 000 MB, no swap). Both sampled the
container's cgroup every 10 s.

**Before the fix** (`diag_b3_scaling.py`, B3's own `build_features` and `fit_and_select` with
the 1500m driver it always had):

```
STAGE features_spark 365.5s rows=316545
MEM t=  502.4s current=2991MiB ... max_hits=37    oom_kill=0 procs={'python:8': 723, 'java:18': 2203}
MEM t=  577.4s current=2320MiB ... max_hits=19429 oom_kill=1 procs={'python:8': 740}
MEM t=  587.7s current=781MiB  ... max_hits=19429 oom_kill=1 procs={'python:8': 750}
STAGE train_and_select 279.7s pr_auc=0.7843
```

The Spark stage finished in 366 s. `spark.stop()` was not called until the end, and does not end
the gateway JVM anyway, so the JVM stayed resident at 2.2 GB while scikit-learn trained beside it.
The container reached its limit, hit it 19 429 times, and the kernel OOM-killed the idle JVM.
Training then finished on its own; in the EXP-2 run the same squeeze ran to the ceiling instead.

**After the fix** (`diag_b3_fixed.py`, B3 through `run_isolated` and `run_with_own_session` as
EXP-2 calls it, the JVM ended once the features were a pandas frame, DEC-44):

```
RESULT {'baseline': 'B3', 'ok': True, 'wall_clock_s': 455.19, 'rows_trained': 184492,
        'rows_tested': 70747, 'test_pr_auc': 0.7843, 'peak_rss_mb': 1131.2,
        'jvm_peak_rss_mb': 2311.7, 'per_stage_s': {'features_spark': 351.75, 'train_and_select': 99.7}}
MEM ... max_hits=0 oom_kill=0 (every sample)
```

So B3 failed for a resource-handling defect in the baseline, not because Spark could not process
the data. With the JVM ended first, the rung completed in a quarter of the ceiling, with the same
test PR-AUC as the killed run's training produced.
