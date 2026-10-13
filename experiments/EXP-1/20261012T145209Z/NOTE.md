# This run's timings were taken on a busy machine

The verdict and every count stand: P ran end to end with no manual step, the platform made 0 LLM
calls, and P's time beyond B3's was 0.944 of its wall clock. The per-stage times are recorded,
including the profile stage's own 86 s (DEC-51).

The timings do not. Every configuration ran 2.5 to 5 times slower than the run of 11-10-2026
(`20261011T174929Z`): P 2 296 s against 786 s, its compile gate 878 s against about 164 s, B3 128 s
against 49 s, and B2 - pure pandas - 17.9 s against 6.6 s. While it ran, a read-only review with
three agents was working on the same host, and the Docker VM had already used about 800 MB of its
swap. The environment record holds no host-load figure, so the slowdown is not attributed more
precisely than that.

EXP-1 was re-run with nothing else running; this run is kept as the record.
