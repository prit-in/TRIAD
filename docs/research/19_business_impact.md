# 19. Business Impact Assessment

This section asks what the platform is worth to the business that runs it, and answers only with
what was measured. No revenue per customer, campaign cost, or engineer salary exists for this
project's synthetic retailer, so no monetary return is computed; Section 19.4 states what such a
calculation needs and does not supply invented values for it.

**Fig. 28. Business Impact Dashboard.** *(figures/png/Fig 28 Business Impact Dashboard.png)*

## 19.1 What the model buys a retention team

The published champion - a calibrated gradient-boosting model on the full feature catalogue,
scored on 6 814 customers at the held-out test cutoff, of whom 28.5% churned - ranks customers
well enough to change where a retention budget goes (Fig. 28a). Contacting the 10% of customers
it ranks riskiest reaches 29.8% of all churners, 2.98 times what a random 10% would; contacting
20% reaches 50.2%, and 30% reaches 65.6%. At the operating threshold, set at the capacity of
the top 15% of the validation population, 74.6% of the customers flagged did churn, and 45.7%
of all churners were flagged. Because the model is calibrated - its expected calibration error
is 0.019 and its Brier skill 0.32 - its probabilities can be read as probabilities, which is what
lets the three risk bands of Fig. 28c mean something: 677 customers are high risk (a probability
of at least 0.66), 1 489 medium, and 4 648 low.

## 19.2 Where it would mislead, and how the platform says so

The same figure shows the cost of a single threshold. Customers in their first six months have
too little history to score well: at the global threshold only 16.4% of the churners among them
were flagged, against 45.8% to 49.5% in the older tenure bands (Fig. 28b). A retention campaign
run from this list would under-serve exactly the customers whose relationship is youngest. The
platform did not hide this: the segment rule (AP-6) refused the model, the threshold review
showed that tenure-specific thresholds would raise the newest customers' share to 46.0% at the
cost of flagging 17.6% of customers instead of 15.25%, and the model was promoted only through
the two-person override of Section 12, with the reason recorded on its model card. That record
is itself a business result: the decision about whom the campaign under-serves was made by named
people, on stated evidence, rather than by a default.

## 19.3 Operational efficiency, productivity, and infrastructure

On the small dataset of EXP-1 the comparison with the conventional pipelines is direct. TRIAD took
1 230 s where a hand-written Spark pipeline took 53 s and a single pandas process 6.9 s, and none
of the three needed a manual step; the conventional pipelines are 188 and 291 lines of code a person
writes for this one task, and they leave no decision record, audit entry, calibration check, or
policy verdict behind. Its validation gates took 553 s of that, so the price of running
generated code safely is roughly nine minutes per run on this workstation, and more on the first
run after a template changes, when the fixture tests run again in full (3 397 s in all).

What the governance costs in time is measured, not estimated. A full governed run on 10 000
customers took 49 minutes on the evaluation laptop, from ingestion to a published report, and
most of it was model selection - tuning, calibration, and the bootstrap comparison with the
champion - rather than data processing. No step of that run needed a person except the
decisions that are meant to need one. On infrastructure, local-mode routing keeps small inputs
off a cluster altogether, and the per-stage execution measurements of Section 9 let the
platform adjust Spark's shuffle partitioning between runs from what the previous run measured
rather than from a fixed default.

## 19.4 Cost-benefit, and what a return calculation needs

Table 14 sets the measured costs beside the measured benefits. A monetary return would follow as
$\mathrm{ROI} = \left( \sum_i p_i \, r_i \, v_i - C_{\mathrm{campaign}} - C_{\mathrm{platform}} \right) / C_{\mathrm{platform}}$,
summing over contacted customers the probability $p_i$ that they would churn, the probability
$r_i$ that contact retains them, and their value $v_i$. The model supplies $p_i$, calibrated; the
retention effect $r_i$, the customer value $v_i$, and the costs are business inputs this project
does not have, and a figure computed from guessed values would be a guess presented as a result.

**Table 14. Cost-Benefit Analysis**

| Item | Kind | Measured value | Source |
| --- | --- | --- | --- |
| Wall clock of one governed run, 10 000 customers | Cost | 49 min | run-2026-10-07-001 |
| People needed to promote a model | Cost | 1 approver; 2 for a promotion despite a failed check | Section 12, the override run |
| Churners reached by contacting 10% of customers | Benefit | 29.8% (2.98 times random) | test cutoff, run-2026-10-07-001 |
| Precision at the operating threshold | Benefit | 74.6% of flagged customers churned | same |
| Generated-code faults stopped before reading data | Risk avoided | 19 of 20 | EXP-8 |
| Planted leaks blocked | Risk avoided | 2 of 2, which would have inflated PR-AUC by about 0.5 | EXP-9 |
| Under-served segment surfaced before deployment | Risk avoided | newest customers: 16.4% of churners flagged | AP-6 escalation |
| Monetary return | - | not computed: no customer value, retention effect, or cost data | Section 19.4 |

---

## Evidence (not part of the manuscript)

- 19.1 and Fig. 28: `docs/research/analysis/business_snapshot.json`, written by
  `business_snapshot.py` from `reports/run-2026-10-07-001/evaluation.json`, `run_state.json` and
  the official predictions (test n 6 814, prevalence 0.2847, gain at 10/20/30% 0.298/0.502/0.656,
  precision 0.7458, recall 0.4567, ECE 0.0187, Brier skill 0.3211, bands 677/1 489/4 648; risk
  band thresholds `pipelines/churn_v1.yaml` 0.66/0.33).
- 19.2: `segment_threshold_review` in the same snapshot (recall at global threshold 0.164, 0.4578,
  0.4947; segment-aware 0.4603; flagged 0.1525 -> 0.176); model card approval section.
- 19.3: EXP-1 run `20261013T132108Z` (P 1 230.24 s, B3 53.24 s, B2 6.94 s; gates 553 s; lines
  188 and 291); the cold case, every fixture run fresh: EXP-1 run `20261009T161555Z`, P 3 396.87 s;
  run-2026-10-07-001 state transitions (RECEIVED 19:24:00 to PUBLISHED 20:13:13, of which
  EVALUATED 19:40:47 to ESCALATED 20:12:41 was selection); routing LR-07; FR-037 loop (DEC-31).
