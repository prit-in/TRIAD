# This run failed at its last rung; its rungs up to 100 000 customers are a record

The ladder ran 400 to 100 000 customers with both baselines completing at every rung (B2 134.5 s
and B3 725.5 s at 100 000, peaks 2 279 MB and 2 270 MB). At 200 000 customers B2 was killed by the
container's out-of-memory killer 21 s in, and B3's Spark JVM died inside its `toPandas` collect of
the feature table to the driver. B3's own `run` had made a failed result of that, but its
`finally` then called `spark.stop()` on the dead JVM, which raised `ConnectionRefusedError` and hid
it; EXP-2's `_measured` caught a killed process but not an exception, so the whole experiment
ended `failed` with no verdict and P was never run.

The fixes (DEC-50): B3 hands its feature table to the modelling through parquet, as P does -
collecting it to the driver is the pattern TRIAD's routing policy forbids at scale (LR-09) -
a dead JVM no longer hides B3's result, and EXP-2 records a baseline that raised as a failed
baseline. EXP-2 was then re-run; this run is kept as the record of what happened.
