# One observation in this run reads 0.0 where nothing was recorded

P's `per_stage_s` observation lists its profile job (`spark:fa6d57`) at 0.0 s. Profiling runs
in-process, reported no duration, and the execution row stored none; the harness turned the
missing value into 0 (DEC-51). Every measurement stands: `full_runs_s` is gate 9 of the three
generated jobs (165.6 s), and no verdict, table or chart reads the per-stage times. EXP-1 is
re-run after DEC-51; this run is kept as the record.
