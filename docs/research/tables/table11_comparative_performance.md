**Table 11. Comparative Performance Analysis** *(like-for-like, every shortlist refitted on P's feature table with the same split: test PR-AUC 0.776)*

| Measure | B2 pandas | B3 Spark | P TRIAD |
| --- | --- | --- | --- |
| Wall clock (s) | 6.9 | 53.2 | 1 230.2 |
| Test PR-AUC | 0.79 | 0.787 | 0.741 |
| Test ROC-AUC | 0.809 | 0.807 | 0.796 |
| Test Brier score | 0.208 | 0.213 | 0.145 |
| Peak memory (MiB) | 228 | 227 | 352 |
| Hand-written lines | 188 | 291 | not measured |
| Manual steps | 0 | 0 | 0 |
| LLM calls | 0 | 0 | 0 |
| Decision records | not measured | not measured | 8 |
| Audit entries | not measured | not measured | 17 |
| Sandboxed Spark jobs | not measured | not measured | 4 |
