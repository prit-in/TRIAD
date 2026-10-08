# 18. Industry Use Cases and Case Study Evaluation

TRIAD was implemented and measured on one case study: churn prediction for an e-commerce
business, on seeded synthetic data. That case study is evaluated in Sections 13 to 17. This section
asks a different question - which parts of the platform would carry over to other domains unchanged,
and which would need new work - and answers it as a **conceptual transfer analysis**. Nothing in
this section was implemented or measured, and no result here should be read as one.

The transfer analysis rests on a separation the architecture already makes. The *domain-neutral*
part of TRIAD is the governance machinery: contracts at ingestion, profiling and the quality score,
quarantine and row accounting, template-constrained code generation with nine validation gates,
the sandbox, routing rules, the acceptance-policy structure, approvals with separation of duties,
decision records, the audit chain, and replay. The *domain-specific* part is what those mechanisms
are configured with: the source contracts, the feature catalogue and its point-in-time rules, the
label definition, the acceptance thresholds, the segmentations the fairness check uses, and the
governance policy's limits. Table 13 applies that separation to six domains.

## 18.1 Retail and e-commerce (the implemented case)

Customer churn in retail is the case this report measures. The same pipeline, with a new label job
and feature catalogue, would serve the adjacent retail problems the literature treats with similar
customer-level panels - demand at the customer level, next-purchase timing, or retention offers -
because the snapshot-and-horizon structure of Section 7.6 is common to all of them
[@manzoor2024churnreview] [@ecommerce2023churn].

## 18.2 Finance

Fraud detection and credit risk are the closest analogues to churn in structure - imbalanced labels,
transactional features, a decision at a threshold - and the domain where explainability and
calibration are most directly regulated [@fraud2023banking] [@weber2024xaifinance]. TRIAD's
calibration gate, per-decision reasons with a named method, and model-integrity checks transfer
directly. What would not transfer is the label: fraud labels arrive late and partially, so the
label-maturity rule would need a domain-specific confirmation window, and the acceptance policy
would need cost-weighted metrics in place of PR-AUC alone.

## 18.3 Smart manufacturing

Industrial analytics combines high-rate sensor data with maintenance and quality records
[@smartfactory2022] [@ahmed2022xai40]. Fig. 26 sketches the mapping. The Spark layer and its
routing rules suit the data volumes, and the point-in-time feature discipline maps naturally onto
predictive maintenance, where a feature computed with information from after a failure is the
same leakage as a churn feature computed after the cutoff. What would be new is streaming
ingestion - TRIAD's streaming connector is unimplemented (Section 7) - and time-series feature
templates in place of the customer-day aggregation.

**Fig. 26. Smart Manufacturing Analytics Ecosystem.** *(figures/fig26_manufacturing_ecosystem.drawio)*

## 18.4 Healthcare

Healthcare analytics on large clinical and operational data is well established
[@batko2022healthcare], and it is where the requirements for trustworthy AI are strictest
[@tjoa2021xai] [@thiebes2021trustworthy]. Fig. 27 sketches a healthcare intelligence platform built
from TRIAD's components. The sandbox, the audit chain, separation of duties, and the policy that an
agent may never delete data are exactly the controls such a deployment needs. The gaps are
substantial: real patient data would require de-identification before ingestion, the fairness
check would have to use clinically meaningful groups chosen with clinicians, and the absence of
transport encryption in the local deployment (Section 12) would be disqualifying rather than
incidental.

**Fig. 27. Healthcare Intelligence Platform.** *(figures/fig27_healthcare_platform.drawio)*

## 18.5 Smart cities

Urban analytics draws on many heterogeneous public and operational sources, with policy decisions
downstream [@smartcities2020] [@kandt2021cities] [@smartgrid2021]. Here the contracts and profiling
layer would carry most of the value, because the main risk is not the model but the silent change
of a source. The fairness checks would matter more than in retail, since decisions affect
neighbourhoods rather than customers, and the segmentations would have to be geographic and
demographic groups chosen with the affected public authority.

## 18.6 Cybersecurity

Intrusion detection is a classification problem over event streams, with severe class imbalance and
an adversary who adapts [@chou2021nids] [@wang2020xaiids]. The platform's drift monitoring and
retraining triggers transfer, but an adversary changes the input distribution on purpose, so the
fixed PSI thresholds of Section 11 would be the weakest part of the transfer, and the injection
defences of Section 12 would face content written specifically to defeat them.

## 18.7 What every transfer would ask of the platform

Read across the five domains, the same three needs recur, and they are where the conceptual
designs above would meet the limits of the implemented one. First, most of the domains produce
events continuously, and TRIAD reads batches of files: the streaming connector its requirements
describe is not built. Second, each domain has its own groups for the segment check - patients,
neighbourhoods, plants, network zones - which the acceptance policy can name as configuration but
someone accountable in that domain has to choose. Third, every domain would inherit the small-sample
behaviour of fixed thresholds reported in Section 15, so the interval-based rules proposed in
Section 21 would matter more in a hospital or a city than in this retail case. The profile's
volume and shift checks of Section 7 are, by contrast, the part most directly reusable: a silent
change of a source is a risk in every row of Table 13. None of the rows beyond the first is a
result; each is a design to be tested by the measurements Sections 13 to 15 describe.

**Table 13. Domain-Specific Use Cases** *(conceptual: none of rows 2 to 6 was implemented or
measured)*

| Domain | Typical prediction task | Carries over unchanged | Needs new work | Main risk in transfer |
| --- | --- | --- | --- | --- |
| Retail and e-commerce (implemented) | Customer churn within 90 days | Everything (this report) | - | - |
| Finance | Fraud, credit default | Calibration gate, reasons, integrity checks, approvals, audit | Late-label maturity rule; cost-weighted acceptance | Regulatory explainability standards |
| Healthcare | Readmission, no-show, deterioration | Sandbox, audit, separation of duties, no-deletion policy | De-identification; clinical fairness groups; transport encryption | Patient safety and privacy |
| Smart manufacturing | Failure and quality prediction | Spark routing, point-in-time discipline, gates | Streaming ingestion; time-series feature templates | Sensor data volume and latency |
| Smart cities | Demand and service forecasting | Contracts, profiling, quality score, drift monitoring | Geographic fairness groups; many-source contracts | Silent source changes; public accountability |
| Cybersecurity | Intrusion and anomaly detection | Drift triggers, retraining, injection defences | Adversarial robustness; adaptive thresholds | An adversary shifting the data deliberately |

---

## Evidence (not part of the manuscript)

- Conceptual status: `docs/faculty/FACULTY_MATERIALS_ANALYSIS.md` section 6 ("Industry use cases").
- Unimplemented streaming connector: FR-008 (Could), traceability.
- No TLS locally: ST-05.
- Domain-neutral components: Sections 5 to 12 of this report.
