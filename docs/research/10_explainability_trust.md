# 10. Explainable and Trustworthy Analytics Layer

Trustworthy AI is usually described as a set of properties - transparency, accountability,
fairness, robustness, and the like [@kaur2022trustworthy] [@li2023trustworthy]
[@thiebes2021trustworthy]. This layer is TRIAD's attempt to make each property something the
platform *records* rather than asserts: an explanation that names its method, a probability that
is calibrated, a decision that carries its evidence, a segment check whose inputs are visible, and
a report whose every number is checked. Fig. 14 shows the explainability services; Fig. 15 shows
how the properties combine into the trust model and the trust score defined at the end of the
section.

**Fig. 14. Explainability Framework.** *(figures/fig14_explainability_framework.drawio)*

## 10.1 Explanations that say what they are

Explanation methods are not interchangeable, and a reader who does not know which method produced
an explanation cannot judge it [@arrieta2020xai] [@tjoa2021xai]. TRIAD therefore always stores
the method beside the explanation. For tree ensembles, per-customer reasons are TreeSHAP
attributions, which are exact for the model [@lundberg2020trees]; for logistic regression they are
the coefficient-times-input decomposition of the log-odds, which is exact by construction; where
neither applies, permutation importance gives a global ranking and the per-customer reasons are
marked unavailable rather than approximated. Because calibration is a monotone transformation of
the model's score, the reasons are those of the underlying model and are labelled so. Two further
artifacts are produced for every registered model: an evaluation record with the
receiver-operating-characteristic (ROC), precision-recall, calibration, and lift curves, their
bootstrap intervals, and per-segment metrics; and a model card with the training data versions,
features, metrics, segment results, intended use, and limitations [@mitchell2019modelcards].

## 10.2 Probabilities that mean what they say

A churn probability drives an action - who is contacted, and at what cost - so a predicted 0.3
must mean roughly three in ten [@silvafilho2023calibration] [@vancalster2019calibration].
Calibration is a stage of selection, not an option (Section 8.4), and a model whose calibration
error stays above the policy's limit after every remedy is escalated with the measured error, the
number of rows it was measured on, and the remedies tried. That last detail matters: on a few
hundred rows, the expected calibration error of a perfectly calibrated model is itself close to
the limit (Section 15), and a reviewer can only tell sampling noise from a real defect if the row
count is in front of them.

## 10.3 Decisions that can be traced

Every consequential choice is a *decision record*: which agent made it, of which type, the decision
itself, the policy rules it applied, the evidence it was judged on, the alternatives it rejected,
and its outcome. Routing, hygiene, acceptance, diagnosis, escalation, and promotion all write them.
Around the decisions sits lineage: each run stores the exact input versions it read, each output a
manifest with its content hash, and each generated job a content-addressed artifact - which is
what allows a past run to be replayed from its stored code and inputs and its outputs compared
byte for byte [@chapman2020provenance] [@werder2022provenance]. And around everything sits the
audit trail, in which each entry carries a hash of the previous entry of its run, so that a changed
or deleted entry breaks a chain that can be verified (Section 12).

## 10.4 Fairness, monitored and qualified

The fairness check compares recall at the operating threshold across segments within each
segmentation - country, loyalty tier, acquisition channel, and tenure band - and escalates when the
largest within-segmentation gap exceeds 0.10. Three qualifications are stated with it, because
fairness checks are easy to overstate [@mehrabi2021fairness] [@pessach2022fairness]
[@caton2024fairness]. The segment attributes are joined for evaluation only and never trained on,
since a model given `country` as an input answers a different question; but leaving an attribute
out does not remove its proxies, which is why the check measures outcomes rather than relying on
the omission. A segment with fewer than 50 rows or 30 churners is reported as having insufficient
data rather than allowed to drive the gap. And recall parity is one definition of fairness among
several that cannot in general be satisfied together; choosing it is a policy choice, recorded
where it can be reviewed.

**Fig. 15. Trustworthy Analytics Model.** *(figures/fig15_trustworthy_model.drawio)*

## 10.5 The trust score

The planning document for this report asks for a trust score. The platform does not compute one,
and the PRD does not define one; a single number is a lossy summary of the evidence above, and a
reader should always prefer the components. It is nevertheless useful as a compact summary of how
much of a run's output is backed by verified evidence, so it is defined here, from quantities TRIAD
records, and computed from the stored run records by an analysis script (Section 17):

$$
\mathrm{TS}(r) = \frac{1}{\lvert A(r) \rvert} \sum_{k \in A(r)} s_k(r), \qquad s_k(r) \in [0, 1] \tag{6}
$$

where $A(r)$ is the set of components that apply to run $r$, and the components are:

- $s_{\mathrm{chain}}$: 1 if the run's audit chain verifies end to end, otherwise 0;
- $s_{\mathrm{gates}}$: the share of the run's final generated jobs that passed all nine
  validation gates;
- $s_{\mathrm{calib}} = \max\!\left(0, \min\!\left(1, 2 - \mathrm{ECE}/\tau\right)\right)$: 1 when
  the expected calibration error is within the policy limit $\tau = 0.05$, falling linearly to 0
  at twice the limit;
- $s_{\mathrm{claims}}$: 1 if the run's report passed its claim check, 0 if it failed; not
  applicable if the run produced no report;
- $s_{\mathrm{approval}}$: 1 if every promotion in the run was approved by a person other than its
  requester and the run's starter - and, for a promotion despite a failed check, each required
  share by a different person - and 0 if any promotion took effect without such an approval; not
  applicable if nothing was promoted;
- $s_{\mathrm{lineage}}$: 1 if the run recorded its input versions and every output it published
  has a manifest with a content hash, otherwise 0.

The components are weighted equally. That is a design choice, not a validated result: no study
here establishes that, for instance, calibration should count as much as lineage, and an adopting
organization would set the weights from its own risk assessment. Equal weights are used because
they are the choice that encodes the fewest unexamined assumptions.

---

## Evidence (not part of the manuscript)

- Explanation methods and labels: `python_ml/triad_ml/predict.py`; per-customer reasons fixed
  03-10-2026 (commit `9f89a24`).
- Evaluation record and curves: `reports/<run_key>/evaluation.json`, served at
  `GET /models/{ref}/versions/{id}/evaluation`; model card: `model_card_uri`.
- Calibration remedy chain and evidence fields: DEC-03; `ece_n_rows` in acceptance evidence
  (run-2026-10-03-002: ECE 0.0617 on 243 rows).
- Decision records: `decision_records` table; alternatives populated since 23-09-2026.
- Replay: ATC-18; lineage: `pipeline_runs.input_versions`, manifests (`triad_core/storage.py`).
- Fairness: DEC-02, DEC-21, DEC-26.
- Trust score: defined here per `docs/faculty/FACULTY_MATERIALS_ANALYSIS.md` section 6;
  computed by `docs/research/analysis/trust_score.py` (Section 17).
- Claim check: `python_ml/triad_ml/reports.py` (`check_claims`).
