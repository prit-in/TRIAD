# This run's end-to-end verdict is not valid

The review of 11-10-2026 found that `MonitoringService.start_retraining` looked each of a
retrain's inputs up by the input's name (`transactions`, ...) rather than by the dataset the
parent run had read. This run's champion read the experiment's own datasets
(`transactions__exp07_62e8ac`, ...); its retrain, run-2026-10-10-032, was given
`raw/transactions/v=20` and the other bare-named datasets that EXP-5 and the demo had
registered - not the three drifted months this run registered for it. So the challenger was
trained, and compared with the champion by AP-8, on unrelated data, and the "completed, held"
recorded here does not test what the end-to-end arm claims.

The detector arms (stable months, covariate shift, segment shift, concept drift) do not use the
platform's retraining and are unaffected. The platform was fixed (DEC-48: a retrain follows the
dataset its parent read) and EXP-7 was re-run; this run is kept as the record of what happened.
