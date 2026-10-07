# 8. Intelligent Analytics and Machine Learning Layer

The analytics layer turns point-in-time feature tables into a calibrated churn probability for
each customer, together with the reasons behind it. It automates what AutoML automates - choosing
among model families and tuning them [@he2021automl] [@karmaker2021automl] - and adds what AutoML
leaves to the analyst: a split that cannot leak the future, hygiene and leakage checks on the
features, calibration, segment checks, a comparison with the model already in production, and a
written acceptance policy. Fig. 11 shows the workflow and Fig. 12 the automated selection and
feature machinery inside it.

**Fig. 11. Intelligent Analytics Workflow.** *(figures/fig11_analytics_workflow.drawio)*

## 8.1 The prediction task

For a customer $i$ and a monthly cutoff $T$, the label is $y_{i,T} = 1$ if the customer makes no
purchase in the 90 days after $T$, and the model estimates $\hat{p}_{i,T} = P(y_{i,T} = 1 \mid
\mathbf{x}_{i,T})$ from features computed only from events before $T$. Each customer contributes
one row per cutoff at which they are active, so a training set is a panel of customer-snapshots,
and every row's label horizon must have matured before the data end.

## 8.2 Features, and the hygiene that guards them

The features follow the structure that churn research has long relied on - recency, frequency,
and monetary value [@fader2005rfm] - extended to returns, discounts, product breadth, payments,
web engagement, and customer service, for 38 implemented definitions over five groups (Section
7.5). Before any model sees them, hygiene rules run in two places. Rules that judge a column by its
name run before the data are split and block training outright: no identifier may be a feature
(H2), no column derived from the label may be a feature (H3), and every feature must have an
entry in the catalogue (H7). The rule that judges a column by its values runs on the training
rows only, after the split, so that what is dropped is decided without looking at validation or
test data: a feature that is almost entirely null or almost constant in training is excluded
(H4). Every hygiene verdict is written as a decision record.

Leakage gets a second, behavioural line of defence, because a leaking feature can have an
innocent name [@kaufman2012leakage] [@kapoor2023leakage]. A sentinel test injects events after
the cutoff and requires that no feature changes, and an alarm flags any candidate whose
validation ROC-AUC is implausibly high for the problem (0.98 by default). Section 15 reports an
experiment in which a feature named as harmlessly as a lifetime order count passes the name
rules and is caught only by these behavioural checks.

## 8.3 A split that respects time

Rows are split by cutoff, never at random: the latest matured cutoff is the test set, an earlier
one the validation set, and earlier cutoffs still train the model. A gap of at least one 90-day
horizon separates the last training cutoff from the validation cutoff, and the validation cutoff
from the test cutoff, so that no label window overlaps the period being evaluated; a split that
violates the rule is refused rather than used [@cerqueira2020tsvalidation]. A fifth of the training cutoffs is held out
for calibration. The split, the cutoffs, and the churn rate of each part are recorded with the
run.

**Fig. 12. AutoML and Feature Engineering Architecture.** *(figures/fig12_automl_architecture.drawio)*

## 8.4 Candidates and selection

Five model families are trained on every run, each with a role: a dummy model that predicts the
base rate and sets the floor; logistic regression as the parsimony reference; random forest
[@breiman2001rf]; histogram-based gradient boosting [@friedman2001gbm]; and XGBoost
[@chen2016xgboost] as the primary non-linear family, which is consistent with the evidence that
tree ensembles remain strong on tabular data [@shwartz2022tabular] [@borisov2024tabular]. Class
imbalance is handled by class weighting [@he2009imbalanced], which distorts probabilities and
is one reason calibration is mandatory.

Selection then proceeds in four stages. Stage A screens every candidate on validation data
against the rules that do not depend on calibration, including the leakage alarm and the cost
limits. Stage B tunes the finalists' hyperparameters with Optuna [@akiba2019optuna]
[@yang2020hpo] [@bischl2023hpo] within the run's optimization budget. Stage C calibrates the
best candidate on the held-out calibration slice - isotonic regression where at least 500 rows
are available, Platt-style sigmoid scaling otherwise [@zadrozny2002calibration]
[@niculescu2005probabilities] [@bocker2021platt] - and applies the calibration-sensitive rules;
if calibration fails, a remedy chain tries sigmoid scaling and then refitting without class
weighting before giving up. Stage D scores the test snapshot exactly once.

## 8.5 The acceptance policy

A candidate may be used only if it satisfies eight rules, written in the pipeline's policy and
judged by code, not by an agent:

- **AP-1** validation PR-AUC at least the churn rate plus 0.10;
- **AP-2** a more complex model must beat logistic regression's PR-AUC by at least 0.01, or the
  simpler model wins;
- **AP-3** validation ROC-AUC at least 0.70;
- **AP-4** Brier skill at least 0.10 against the base-rate forecast;
- **AP-5** expected calibration error (ECE) at most 0.05 after calibration;
- **AP-6** recall gap within each customer segmentation - country, loyalty tier, acquisition
  channel, and tenure band - at most 0.10 at the operating threshold, counting only segments with
  at least 50 rows and 30 churners;
- **AP-7** training within 30 minutes per family, a model of at most 200 MB, and scoring within
  the time limit;
- **AP-8** for promotion, the lower bound of the 95% bootstrap interval of the PR-AUC difference
  between challenger and champion, on the same test cutoff, above zero [@efron1979bootstrap].

The outcome is one of three: *promote* (request approval), *keep the champion*, or *escalate*
with a reason code such as MISCALIBRATED or SEGMENT_RECALL_GAP. Every verdict is a decision
record carrying the rules applied, the evidence they were judged on, and the candidates that were
rejected and why. A run that proposes nothing does not ask anyone to approve anything.

## 8.6 When a rule fails: review, then a governed override

A candidate that fails a calibration rule (AP-5) or a segment rule (AP-6) is not discarded
midway. Selection still scores it once on the test cutoff and compares it with the champion, so
whoever decides next has the whole picture. For a segment failure the platform also performs
the first remedy the policy names for SEGMENT_RECALL_GAP - a segment-aware threshold review - as
analysis only: for each segment it finds the threshold at which that segment would reach the
recall the global threshold achieves overall, and reports the gap and the share of customers
flagged at those thresholds. On a 10 000-customer run the tenure-band gap was 0.331, because
customers in their first six months have too little history to score well; segment thresholds
would have narrowed it to 0.037 while flagging 17.6% of customers instead of 15.25%. The review
is never applied automatically, because an operating threshold per group is a policy decision.

Such a candidate can still be promoted, but only if it beat the champion (the AP-8 interval lies
above zero) and only through the two-approver path of Section 12: a written justification, and
one approval each from an ML Engineer and an Admin. On that run the candidate beat the champion
by 0.085 PR-AUC (95% interval 0.073 to 0.099) and was promoted this way, and its model card now
records the failed rule, the justification, and the approvers.

## 8.7 Explanations

Every scored customer receives reasons alongside a probability. For tree ensembles the reasons
are TreeSHAP attributions [@lundberg2020trees]; for logistic regression they are the exact
decomposition of the log-odds into coefficient-times-input terms; where neither applies,
permutation importance gives a global ranking. The method is always named next to the reasons,
and for a calibrated model the reasons are stated to be those of the underlying, uncalibrated
model, because calibration is a monotone map of the score and does not change which features
drove it.

---

## Evidence (not part of the manuscript)

- Candidates and split: `pipelines/churn_v1.yaml` (`split`, `candidates`); `python_ml/triad_ml/splits.py`.
- Hygiene H2/H3/H4/H7: `python_ml/triad_ml/hygiene.py`; decision records on run-2026-10-03-002.
- Leakage alarm 0.98: `python_ml/triad_ml/evaluate.py`; sentinel: TC-026; EXP-9.
- Selection stages A-D: `python_ml/triad_ml/train.py`; tuning `python_ml/triad_ml/tune.py`;
  sigmoid below 500 rows: `calibrate`; remedy chain: DEC-03.
- AP-1 to AP-8: PRD section 17.6; DEC-01, DEC-02, DEC-19, DEC-26.
- Explanations: `python_ml/triad_ml/predict.py` (SHAP TreeExplainer; coefficients x
  standardised inputs; permutation importance; "unavailable").
- Section 8.6: `segment_threshold_review` in `python_ml/triad_ml/evaluate.py`; selection past
  AP-5/AP-6 in `train.py` (DEC-27); run-2026-10-07-001 `reports/<run>/run_state.json`
  (`segment_recall_gaps.tenure_band` 0.3307, `gap_at_segment_thresholds` 0.0366,
  `flagged_share_global` 0.1525, `flagged_share_segment_aware` 0.176, champion delta 0.0851
  [0.0726, 0.0993]); approvals group 65eef591. The card's approval section was backfilled on
  08-10-2026 by `record_approval_on_card` from the stored approval rows, because the run predates
  that function; its evidence bundle likewise predates the enriched bundle (it carried the note
  and the champion delta, while the threshold review was in `evaluation.json`).
