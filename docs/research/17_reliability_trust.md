# 17. Explainability, Reliability, and Trustworthiness Analysis

Section 10 described the mechanisms; this section reports how they behaved under measurement:
whether outputs could be explained, whether runs could be reproduced, whether faults were
contained, whether governance held when people and agents pushed against it, and what the trust
score of equation (6) says about recorded runs (Fig. 25, Table 12).

**Fig. 25. Reliability Assessment Model.** *(figures/png/Fig 25 Reliability Assessment Model.png)*

## 17.1 Transparency and explanation

Every customer the published champion scored received reasons with its probability: all 6 814
official predictions carry their top contributing features, signed by direction, computed with
TreeSHAP and named as such. The model card records the training cutoffs, the candidates screened
and why each lost, test metrics with intervals, calibration, every segment's recall, the drivers
(stated as associations, not causes, and computed on the uncalibrated model, since calibration
is a monotone map of its score), the known limitations - including the tenure-band recall gap
the model was promoted despite - and, since this work, who approved the promotion and on what
written grounds.
Every agent decision in a run - each routing choice, policy check, hygiene rule, and acceptance
verdict - is a record naming its rule, its evidence, and the options it rejected.

## 17.2 Reproducibility

The toolchain is one pinned image, the data are seeded, and the experiments write their full
configuration and environment beside their results. A completed run can be replayed from its
stored generated code; on the recorded replay the content hashes of all three Spark outputs
matched the original run's and no language-model call was made, which is what the replay check
requires before it reports success.

## 17.3 Fault containment

Fig. 25a summarizes the injected-fault experiments. Generated-code faults: 15 of 20 stopped
before execution (EXP-8), with the five wrong column names as the measured blind spot of static
checking. Leakage: both planted leaks blocked (EXP-9). Governance bypass: all four attempts
refused and audited (EXP-10). <!-- RESULTS-PENDING: schema faults (EXP-4) and Spark failures
(EXP-6) on the current code. --> Beyond the experiments, a worker killed in the middle of a run
lost its lease, and the run's task was re-queued and resumed by the next claim, which is the
fault tolerance a long, governed run needs most.

## 17.4 Governance effectiveness

Governance was tested by use, not only by tests. The promotion despite a failed check was walked
end to end on a 10 000-customer run: escalation on AP-6, a written justification, one approval
each from an ML Engineer and an Admin, promotion, smoke test, scoring, and a claim-checked
report, with every state transition legal under the enforced state machine and the audit chain
verifying over 1 753 entries. The same exercise found what governance had missed: the approvers'
evidence bundle omitted the measurements they were deciding on, and the model card did not record
the override. A later review found that a person's rollback could restore any recorded version,
including a challenger never approved, and so deploy a model without an approval. All three were
corrected and are covered by tests. Governance that is never exercised cannot show these gaps,
which is the strongest argument for exercising it.

## 17.5 The trust score in practice

<!-- RESULTS-PENDING: trust scores for a failed run alongside these. -->

On the published override run every applicable component of equation (6) scored 1: the audit
chain verified, all three final generated jobs passed all nine gates, calibration error was
within the limit after calibration, the report passed its claim check, both approvals came from
people other than the requester and the run's starter, and every output had a recorded lineage.
Two escalated runs scored 1 on the four components that applied to them; calibration, claims,
and approval did not apply because nothing was calibrated, reported, or promoted. The score
therefore separates runs whose evidence is incomplete from runs whose evidence is whole, and it
does not distinguish a good model from a bad one - an escalated run with complete evidence is
trustworthy as a record, which is the property the score measures.

**Table 12. Trustworthiness Evaluation Criteria**

| Criterion | Measure | Result | Evidence |
| --- | --- | --- | --- |
| Explanation | Scored customers with reasons | 6 814 of 6 814, method named | official predictions, run-2026-10-07-001 |
| Calibration | Expected calibration error after calibration | 0.019 (limit 0.05) | the same run's evaluation |
| Reproducibility | Output hashes equal on replay | all three Spark outputs | replay check |
| Containment of code faults | Stopped before execution | 15 of 20 | EXP-8 |
| Leakage defence | Planted leaks blocked | 2 of 2 | EXP-9 |
| Governance under attack | Bypass attempts refused and audited | 4 of 4 | EXP-10 |
| Separation of duties | Override shares decided by distinct, eligible people | 2 of 2 | approvals of run-2026-10-07-001 |
| Tamper evidence | Audit chain verifies | 1 753 entries | `/audit-logs/verify` |
| Trust score | Equation (6), applicable components | 1.00 on the published run | `trust_scores.json` |

---

## Evidence (not part of the manuscript)

- 17.1: predictions with `top_reasons` (6 814/6 814, query in the 08-10-2026 session); model
  card `reports/run-2026-10-07-001/model_card.md` (approval section backfilled 08-10-2026).
- 17.2: `scripts/replay_run.py`; ATC-18 run-2026-09-21-007 (traceability).
- 17.3: EXP-8, EXP-9, EXP-10 `latest.json`; lease recovery run-2026-10-03-002.
- 17.4: run-2026-10-07-001 audit entries and approvals (Section 15/19 evidence); defects:
  commits 36038cb (bundle, card), 46dd494 (rollback).
- 17.5: `docs/research/analysis/trust_scores.json` (runs 2026-10-07-001, 2026-10-04-006,
  2026-10-04-004).
