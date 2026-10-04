# 21. Future Research Directions

The limitations of Section 20 define most of the agenda; this section organizes it into five
research directions, from the most immediate to the most speculative (Fig. 30), and closes with
the next-generation ecosystem they point towards (Fig. 31).

**Fig. 30. Future Research Roadmap.** *(figures/fig30_research_roadmap.drawio)*

## 21.1 Statistically aware governance

The most immediate finding of this work is that acceptance rules written as fixed thresholds on
point estimates do not behave as intended on the data sizes a single team sees. A perfectly
calibrated model fails a 0.05 calibration-error limit about half the time on a few hundred rows, and
a model whose recall is identical in every segment fails a 0.10 recall-gap limit most of the time at
every size measured here (Section 15). The direction is to express each rule as a statement about an
interval - for instance, refusing a model only when the lower confidence bound of its segment gap
exceeds the limit, or requiring the upper bound of its calibration error to fall below it - with
the error rates of both kinds stated alongside the threshold, and to study how such rules interact
when several are applied to the same model [@caton2024fairness] [@silvafilho2023calibration].

## 21.2 LLM-assisted programming inside the gates

Every agent in this evaluation ran on its deterministic path. The obvious next experiment is to
connect a model provider and measure what the gates were built for: the share of generated slot
bodies that pass each gate, the repair rate within the attempt budget, the cost per accepted job,
and whether planted instructions in data change what the model writes [@hou2024llm4se]
[@jiang2024selfplanning] [@greshake2023injection]. Because the gates, budgets, and records are
already in place, such a study would measure the model, not the platform, which is the comparison
the agent literature most often lacks [@wang2024agents].

## 21.3 Self-evolving pipelines

TRIAD adapts within bounds: an allowlisted Spark setting after a diagnosed failure, hyperparameters
within a budget, a retrain when monitoring fires. A larger step is a pipeline that improves its own
configuration from its execution history - the Spark tuning loop the requirements describe and
this implementation does not have [@herodotou2020tuning] [@lin2023sparktuning] - and, further out,
one that proposes new feature definitions and templates for a person to review, with each proposal
passing the same gates and hygiene rules as hand-written work.

## 21.4 Federated and multi-party data science

Many of the domains of Section 18 cannot pool their data. A federated variant of TRIAD would keep
the governance layer - contracts, policies, approvals, audit - at each participant and exchange
only model updates and decision records, which raises the open question of how a hash-chained audit
trail is verified across organizations that do not share a database.

## 21.5 Cognitive programming and human-agent collaboration

The final direction is the interaction itself. In TRIAD a person sees an escalation with its
evidence and decides; the platform does not learn from the decision. Recording why people approve,
override, or reject - and using that record to propose better rules, or to explain a new escalation
by analogy to past ones - would move the platform from following governance to helping improve it,
while keeping the person as the decision-maker [@mosqueira2023hitl] [@wu2022hitl]
[@shneiderman2020hcai].

**Fig. 31. Next-Generation Agentic Analytics Ecosystem.** *(figures/fig31_next_generation_ecosystem.drawio)*

Fig. 31 assembles these directions into one picture: LLM agents proposing inside the gates,
interval-based policies deciding, a tuning loop adapting execution, federated participants sharing
decisions rather than data, and people whose decisions feed back into the policies they apply.

---

## Evidence (not part of the manuscript)

- Noise floors: `experiments/EXP-5/calibration_noise_floor.json`;
  `experiments/policy_floors/segment_gap_noise_floor.json`.
- No provider: ADR-0003. Allowlisted adaptation: `agents/policies/governance.yaml`. FR-037 not met.
