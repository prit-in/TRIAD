# Research manuscript sources

The case-study report for team **26FSPDSCPMID4096** - *Agentic Data Science Programming:
Autonomous Python-Scala-Spark Ecosystems for Intelligent Analytics* - is written here, one file
per section of the team's planning document, and assembled into the faculty template at the end
(`docs/faculty/FACULTY_MATERIALS_ANALYSIS.md` records every rule it is assembled against).

## Rules every file follows

1. **Citations** are `[@key]`, where `key` is an entry in `references/bibliography.json`. Every
   entry there was fetched from Crossref by DOI and its title checked
   (`references/verify_references.py`); nothing is cited from memory. At assembly the keys become
   IEEE numbers in order of first citation, and entries never cited are dropped.
2. **A claim about TRIAD** is backed by the repository, never by a citation: the code, a test, a
   recorded run, or an experiment's stored output. Each file ends with an *Evidence* block naming
   them; that block is not part of the manuscript.
3. **A number** in the text is a measured value from a named artifact (an experiment's
   `experiments/EXP-n/<timestamp>/` output, or a run's database rows). PRD values marked (EX) are
   illustrative and never appear as results.
4. **What was not built or not measured** is said in the section it would belong to, not only in
   Section 20.
5. Figures are numbered as the planning document numbers them (Fig. 1-32); tables use the
   template's Roman numerals at assembly (Table 1 here is TABLE I there); equations are LaTeX here
   and native Word equations in the manuscript.
6. Team names, photographs and author order are placeholders until finalization:
   `[TEAM MEMBER 1]` ... `[TEAM MEMBER 4]`, `[TEAM MEMBER PHOTOS - TO BE INSERTED AT FINALIZATION]`,
   `[FINAL AUTHOR DETAILS - TO BE CONFIRMED]`, `[CORRESPONDING AUTHOR - TO BE CONFIRMED]`.

## Files

| File | Section | Words (plan) | Depends on new measurements |
| --- | --- | --- | --- |
| `00_front_matter.md` | Executive Summary, Abstract, Keywords, Table 1 | 450-750 + 20-40 | yes |
| `01_introduction.md` | 1. Introduction (Fig. 1, 2) | 800-1200 | no |
| `02_problem_statement.md` | 2. Problem Statement (Fig. 3, Table 2) | 400-700 | no |
| `03_background.md` | 3. Background and Literature Review (Fig. 4, Table 3) | 1500-2500 | no |
| `04_requirements.md` | 4. Requirements Analysis (Fig. 5, Table 4) | 600-1000 | no |
| `05_architecture.md` | 5. Ecosystem Architecture (Fig. 6, 7) | 800-1200 | no |
| `06_agentic_framework.md` | 6. Agentic Programming Framework (Fig. 8, 9) | 1000-1500 | no |
| `07_data_engineering.md` | 7. Autonomous Data Engineering Layer (Fig. 10, Table 5) | 1200-1800 | no |
| `08_analytics_ml.md` | 8. Intelligent Analytics and ML Layer (Fig. 11, 12) | 1200-1800 | no |
| `09_spark_layer.md` | 9. Spark Distributed Processing Layer (Fig. 13, Table 6, Eq.) | 1000-1500 | no |
| `10_explainability_trust.md` | 10. Explainable and Trustworthy Analytics (Fig. 14, 15, Eq.) | 1000-1500 | trust score values |
| `11_orchestration.md` | 11. Workflow Orchestration and Decision Intelligence (Fig. 16, 17) | 1000-1500 | no |
| `12_security_governance.md` | 12. Security, Privacy, and Governance (Fig. 18, Table 7) | 800-1200 | no |
| `13_experimental_setup.md` | 13. Experimental Setup (Fig. 19, Table 8) | 700-1200 | yes |
| `14_evaluation_metrics.md` | 14. Evaluation Metrics (Fig. 20, Table 9) | 500-800 | no |
| `15_results.md` | 15. Results and Findings (Fig. 21-23, Table 10) | 1200-2000 | yes |
| `16_comparative_analysis.md` | 16. Comparative Analysis (Fig. 24, Table 11) | 800-1200 | yes |
| `17_reliability_trust.md` | 17. Explainability, Reliability, Trustworthiness (Fig. 25, Table 12) | 700-1000 | yes |
| `18_use_cases.md` | 18. Industry Use Cases - conceptual (Fig. 26, 27, Table 13) | 800-1500 | no |
| `19_business_impact.md` | 19. Business Impact Assessment (Fig. 28, Table 14) | 500-1000 | yes |
| `20_challenges.md` | 20. Challenges and Limitations (Fig. 29) | 600-1000 | no |
| `21_future_directions.md` | 21. Future Research Directions (Fig. 30, 31) | 600-1200 | no |
| `22_conclusion.md` | 22. Conclusion (Fig. 32) | 500-800 | yes |
| `23_appendix.md` | Appendix: algorithms, configurations, settings | 500-2000 | no |
| `references/` | bibliography and its verification | - | - |
| `figures/` | draw.io sources, chart scripts, 600-DPI exports | - | - |
| `data_availability.md` | Data availability statement (first prescribed form) | - | no |
| `tables/`, `analysis/` | Tables 10-11 and the trust score and business snapshot, from stored outputs | - | yes |

## Assembly

```text
python docs/research/assemble_manuscript.py            # draft into docs/final/
python docs/research/assemble_manuscript.py --final    # refuses pending markers and gaps
powershell -ExecutionPolicy Bypass -File docs/research/finalize_manuscript.ps1
```

The assembler needs `python-docx` and `latex2mathml`, and Word's `MML2OMML.XSL`; the second step
needs Word itself, which fills the page numbers of the Index and the lists and writes a PDF. The
.docx and .pdf are not committed (the faculty template is not either); `docs/final/manuscript_report.json`
is, with each section's word count against its range, and the counts of figures, tables,
equations, and cited references.

Words are counted as the words of prose paragraphs and list items. Headings, tables, captions,
equations, and code are not counted, so a section within its range here is within it on any
broader count too; a section above its upper bound is noted, as the team agreed (08-10-2026).

