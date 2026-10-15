# 22. Conclusion

This report set out to build and evaluate an agentic data science platform in which agents
propose and rules decide - Python for learning, Scala for compiled transformations, Spark for
data at scale - and to find out, by measurement, what that division of labour costs and what it
buys. TRIAD runs the whole loop for customer churn: governed ingestion, profiling with anomaly
checks, generated Scala jobs behind nine validation gates in network-less containers, point-in-
time features, calibrated candidates judged by a written acceptance policy, promotion only by a
person other than the requester, monitored scoring, and a claim-checked report, with every
decision recorded and every action on a tamper-evident audit trail (Fig. 32).

**Fig. 32. Research Contribution Framework.** *(figures/drawio/Fig 32 Research Contribution Framework.drawio)*

Six findings stand out.

**Bounded generation works where it can see.** The static gates refused every forbidden API call
in under a third of a second and every type or import fault at compile time, so none of them ran. A
wrong column name passed all four static gates, because a name is only text until Spark resolves
it; running the job on hand-checked fixtures stopped four of five, and the fifth, a fallback the
fixture could not tell from the intended behaviour, passed everything. The gates that run the job
are not optional extras but the only defence against semantic faults, and they are as good as
their fixtures.

**Fixed thresholds are not neutral at the sizes most teams see.** A perfectly calibrated model
fails a 0.05 calibration-error limit 40% to 46% of the time on a 324-row validation split, and a
model with identical recall in every segment fails a 0.10 recall-gap limit between 40% and 97% of
the time on validation splits of 494 to 8 000 rows. Rules written as point thresholds therefore mix real failures with noise,
and governance needs rules stated as intervals, with their error rates.

**Governance earns its cost when the model is wrong in a way the metrics hide.** On 10 000
customers the policy refused a model that beat the champion with confidence, because it flagged
16.4% of churners among its newest customers against 45.8% to 49.5% elsewhere. A segment-aware
threshold would have closed most of that gap at the price of contacting 2.4 more customers per
hundred; whether to accept the disparity became a decision two named people made on recorded
evidence, rather than a default. The cost was measured too: on 400 customers TRIAD took 1 230 s
against 53 s and 6.9 s for the conventional pipelines, 553 s of it in the validation gates,
and what the time bought was evidence rather than accuracy - with the features held constant, the
conventional model shortlist scored 0.776 against TRIAD's 0.741.

**Scaling claims need the hardware to test them.** On one four-core laptop, two Spark cores were
1.26 times faster than one and four only 1.33 times, because parallel tasks competed for shared
cores; as the data grew on a 3 GB node, pandas gave out first, and TRIAD, its sandboxed jobs
adding a JVM beside its own, outgrew the node too.

**Governance must be exercised to be trusted.** Walking the override end to end, and reviewing
the code that implements it, found an evidence bundle that omitted what approvers needed, a model
card silent about an override, and a rollback that could deploy a model nobody had approved.
Each was invisible to tests that checked the rules one at a time.

**Faults in the data were caught where they arose, and a person decided what only a person
should.** A renamed customer key was refused at ingestion and proposed as a mapping that waited for
a data engineer; prices written as text were converted; a missing optional table switched off
exactly its own features. Missing keys were quarantined to the row, missing values elsewhere cost
no row and, at the sizes measured, no ranking quality, and a cleaning plan that would have removed
a tenth of the rows paused for approval. A killed executor was absorbed with identical output, but
no out-of-memory could be provoked on this node at all: Spark spilled to disk instead.

The limits of this evaluation are its strongest qualifications. No language-model provider was
configured, so every agent ran its deterministic path and the repair rate of generated code is
unmeasured; the data are synthetic, generated for the purpose; and one workstation stood in for
the cluster the requirements assume, so the large-data tiers were not run and are not estimated.
The platform, experiments, and outputs are in the repository, so each measurement can be
repeated and extended on the environment missing here. The work
that follows most directly from it is statistically aware acceptance policy, language-model
generation measured inside the same gates, and tuning loops that learn from execution history
with enough repetitions to tell an effect from noise.

---

## Evidence (not part of the manuscript)

- Gates: EXP-8 `20261011T084444Z` (forbidden API 0.3 s per fault over two attempts; type and
  import faults stopped at compile; wrong column 0/5 at gates 1-4, 4/5 at gate 5).
- Data faults: EXP-4 `20261011T065017Z`, EXP-5 `20261011T111552Z` (PR-AUC 0.634 to 0.642, intervals
  about +/-0.046; over-limit 10.1% paused), EXP-6 `20261011T070906Z` (cluster kill absorbed by
  Spark's retries, identical output; local kill: EXECUTOR_LOST, resubmit, identical; 460m on
  20 000 customers: 183.6 MB spilled, no failure).
- Cost: EXP-1 `20261013T132108Z` (P 1 230.24 s, B3 53.24 s, B2 6.94 s; gates 553 s; like-for-like
  0.7757 against 0.7405).
- Noise floors: `experiments/EXP-5/calibration_noise_floor.json` (324 rows: 40-46% above 0.05);
  `experiments/policy_floors/segment_gap_noise_floor.json` (false-alarm rate of a fair model
  0.655/0.971/0.951/0.809/0.396 at 494/1000/2000/4000/8000 rows, all four segmentations).
- Override: run-2026-10-07-001; segment review (0.164 vs 0.4578/0.4947; flagged 0.1525 -> 0.176,
  i.e. 2.35 more per hundred).
- Scaling: EXP-3 `20261011T064506Z` (1.258x and 1.33x against one core; 1.057x from two to
  four); EXP-2 `20261014T171107Z`, `20261013T134333Z` (B2
  killed at 200 000, B3 every rung) and P's three attempts (Section 15 evidence).
- Defects: commits 36038cb, 46dd494.
