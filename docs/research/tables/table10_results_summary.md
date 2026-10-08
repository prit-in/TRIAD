**Table 10. Experimental Results Summary**

| Experiment | Hypothesis | Status | Verdict | Key measured result |
| --- | --- | --- | --- | --- |
| EXP-1 Small dataset, TRIAD against the non-agentic baselines | P finishes end to end with no manual step except approval; agent overhead dominates P's time at this size. | failed | no verdict | wall clock P 1,414 s, B3 not measured s, B2 not measured s; test PR-AUC P not measured; manual steps not measured |
| EXP-2 Medium dataset: where a single node gives out | The single-node pandas configuration (B2) is the first to fail as the data grows, while the Spark configuration (B3) keeps going. | partial | did not hold | no crossover; both completed up to 20,000 customers |
| EXP-3 Scaling with parallelism | Doubling the cores available to Spark gives at least 1.6 times speed-up. | partial | did not hold | speed-up 1 to 2 cores 1.628, 2 to 4 cores 0.842; task time 35 s on 1 core, 108 s on 4 |
| EXP-4 Schema errors | A renamed required column is detected and a mapping proposed but not applied without approval; prices are fixed by try_cast and quarantine; the missing optional table disables its features. | partial | held | faults stopped or handled: 3 of 3 |
| EXP-5 Missing values | Key nulls are quarantined, others are handled without losing rows; PR-AUC declines gradually with the null rate. | failed | no verdict |  |
| EXP-6 Spark failures and recovery | Spark's task retries absorb an executor loss; the playbook fixes out-of-memory within 2 retries; adaptive execution handles skew. | partial | did not hold | recovered 2 of 3, escalated 1 of 3 |
| EXP-7 Model degradation | Drift flags fire; retraining starts; the challenger is compared with the champion; promotion waits for approval. | partial | held | drift caught 3 of 3; false-alarm rate 0 over 6 stable months |
| EXP-8 Scala compile failures | At least 70% of seeded code faults are repaired within 3 attempts; repeated fingerprints escalate; forbidden APIs never run. | partial | no verdict | stopped before execution: 15 of 20 |
| EXP-9 Leakage sentinel | Static checks and the leakage alarm block both a post-cutoff feature and a lifetime total computed over the whole dataset. | completed | held | planted leaks blocked: 2 of 2 |
| EXP-10 Governance bypass | Every bypass attempt is blocked, logged and alerted. | completed | held | bypass attempts refused: 4 of 4 |
