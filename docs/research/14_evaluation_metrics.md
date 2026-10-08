# 14. Evaluation Metrics

The faculty plan asks for metrics of performance, scalability, automation, trust, explainability,
resource utilization, and operational efficiency. TRIAD's evaluation uses one rule for all of them:
a metric is reported only if it was measured by an instrument named in Table 9 and stored with the
experiment's output; a metric that could not be measured is reported as not measured, never as zero
or as an estimate. Fig. 20 shows how the metrics attach to the platform.

**Fig. 20. Evaluation Framework.** *(figures/fig20_evaluation_framework.drawio)*

## 14.1 Predictive quality

Churn is imbalanced - roughly one customer-snapshot in four churns in the data used here - so the
primary metric is the area under the precision-recall curve (PR-AUC), computed as average
precision, which is more informative than the ROC curve when positives are rare
[@saito2015prplot]; ROC-AUC is reported alongside. Decisions are made at an operating threshold
chosen on validation data only, by capacity: the threshold that flags the top 15% of customers,
standing for a retention team that can contact that many. Recall at that threshold, overall and per
segment, feeds the fairness check.

Probabilities are judged by two further metrics. The expected calibration error averages the gap
between predicted probability and observed churn rate over ten equal-width bins, weighted by the
share of rows in each [@silvafilho2023calibration]:

$$
\mathrm{ECE} = \sum_{b=1}^{10} \frac{n_b}{n} \left\lvert \bar{p}_b - \bar{y}_b \right\rvert \tag{8}
$$

where $n_b$ is the number of rows whose score falls in bin $b$, $\bar{p}_b$ their mean predicted
probability, and $\bar{y}_b$ their observed churn rate. The Brier skill score measures the
improvement of the Brier score [@brier1950] over a forecast that always predicts the base rate
$\bar{y}$:

$$
\mathrm{BSS} = 1 - \frac{\frac{1}{n}\sum_{i=1}^{n} (\hat{p}_i - y_i)^2}{\frac{1}{n}\sum_{i=1}^{n} (\bar{y} - y_i)^2} \tag{9}
$$

## 14.2 Uncertainty and comparison

Every reported model metric carries a 95% bootstrap interval [@efron1979bootstrap]. A challenger
is compared with the champion by a paired bootstrap on the same test rows: the rows are resampled
1 000 times with a fixed seed, both models are scored on each resample, and the promotion rule
requires the 2.5th percentile of the differences to be above zero,

$$
\Delta^{*}_{(k)} = \mathrm{PRAUC}\!\left(\hat{p}^{\,\mathrm{ch}}, y\right)_{(k)} -
\mathrm{PRAUC}\!\left(\hat{p}^{\,\mathrm{cp}}, y\right)_{(k)}, \qquad
\text{promote only if } Q_{0.025}\!\left(\{\Delta^{*}_{(k)}\}_{k=1}^{1000}\right) > 0 \tag{10}
$$

where $(k)$ denotes the $k$-th resample, $\mathrm{ch}$ the challenger, and $\mathrm{cp}$ the
champion. Pairing removes the variation that comes from which customers happen to be in the test
set, which is most of it.

## 14.3 Platform metrics

The remaining categories measure the platform rather than the model. **Performance** is wall-clock
time per stage and per run, with the cost of starting a Java virtual machine measured separately so
that it is not counted against the work. **Resource utilization** is peak resident memory, read for
each baseline in a process of its own and, for Spark, from the Java virtual machine's own high-water
mark, because a single process's figure includes whatever ran earlier in it. **Automation** is the
number of hand-written lines and manual steps a configuration needs, and the share of injected
faults handled without a person. **Reliability** is the share of injected faults detected before
execution, the gate at which each was caught, and whether a run's outputs are reproduced exactly on
replay. **Trust and explainability** are the components of the trust score of Section 10.5, the
coverage of per-customer reasons, and the claim-check outcome. **Operational efficiency** is the
number of approvals, escalations, and decision records a run produces, which is the work left for
people.

**Table 9. Metrics Definition Matrix**

| Metric | Category | Definition | Instrument |
| --- | --- | --- | --- |
| PR-AUC, ROC-AUC | Predictive quality | Average precision; area under ROC | scikit-learn, with bootstrap intervals |
| Recall at threshold | Predictive quality, fairness | Recall at the top-15% capacity threshold | Evaluation code, per segment |
| ECE | Calibration | Equation (8), ten equal-width bins | Evaluation code |
| Brier skill | Calibration | Equation (9) | Evaluation code |
| Promotion evidence | Comparison | Equation (10), 1 000 paired resamples | Evaluation code |
| Segment recall gap | Fairness | Largest within-segmentation recall difference | Evaluation code (AP-6) |
| Leakage inflation | Reliability | PR-AUC with a leaking feature minus PR-AUC without | EXP-9 |
| Wall-clock time | Performance | Seconds per stage and per run; JVM start-up separately | Experiment harness timers |
| Peak memory | Resource utilization | Peak resident set of the process, and of the Spark JVM | `/proc` high-water marks, one process per baseline |
| Shuffle and spill | Resource utilization | Bytes written and read by shuffles; bytes spilled from execution memory to disk | Spark listener, every stage of every job (FR-036) |
| Task skew, core utilization | Performance | Slowest over median task per stage; sum of task run times over wall clock times cores | Spark listener; EXP-3 |
| Hand-written lines, manual steps | Automation | Lines of code and steps a person must perform | Counted from each configuration |
| Faults detected before execution | Reliability | Share of injected faults stopped by a gate, and which gate | EXP-4, EXP-8 |
| Faults recovered without a person | Automation, reliability | Share of injected failures resolved within budget | EXP-6 |
| Drift false alarms | Reliability | Retraining triggers fired on data with no change | EXP-7 |
| Replay identity | Reproducibility | Content hashes of outputs equal on replay | Replay script |
| Trust score | Trust | Equation (6) and its components | Analysis script over run records |
| Decisions, approvals, escalations | Operational efficiency | Counts per run | Database records |

---

## Evidence (not part of the manuscript)

- ECE (8): `python_ml/triad_ml/evaluate.py` `expected_calibration_error` (10 equal-width bins).
- BSS (9) and AP-4: `evaluate.py`; Brier skill against the base rate.
- Equation (10): `evaluate.bootstrap_difference` (paired, `DEFAULT_BOOTSTRAP = 1000`, fixed seed);
  AP-8: DEC-19.
- Threshold: `choose_threshold`, `pipelines/churn_v1.yaml` (`strategy: capacity`,
  `capacity_share: 0.15`).
- JVM start-up measured separately and peak memory per process: commit `9f89a24` (#14, #15);
  `experiments/baselines.py` (`run_isolated`, `jvm_peak_rss_mb`).
