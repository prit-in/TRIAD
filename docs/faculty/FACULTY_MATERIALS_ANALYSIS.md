# Faculty materials analysis

For the team, and for whoever assembles the final manuscript. It records what each of the five
binding sources requires, where the sources disagree, and how each disagreement is resolved. The
manuscript is assembled last; this document is what it is assembled against.

**Team ID: `26FSPDSCPMID4096`** — used exactly as written wherever a project identifier is required.

## The sources

| # | Source | Location | Read as |
| --- | --- | --- | --- |
| 1 | `TRIAD_PRD_Agentic_Data_Science_Case_Study.md` | repository root | full text, 3 866 lines |
| 2 | `26FSPDSCPMID4096.pdf` | `docs/faculty/source/` | full text and all five page images |
| 3 | `4_Teams_Titles_Links.pdf` | `docs/faculty/source/` | full text and both page images |
| 4 | `3_Consolidated_Template.pdf` and `3_Consolidated_Template.docx` | `docs/faculty/source/` | PDF text and page images; the `.docx` parsed for page geometry, styles and tables |
| 5 | `1_Manuscript Guidelines.pdf` | `docs/faculty/source/` | full text and both page images |

The four faculty files were found in the team's Downloads folder and copied into the project
byte-for-byte (SHA-256 verified). They are **not committed**: `4_Teams_Titles_Links.pdf` lists
the registration numbers of every team in the course and one personal phone number, and the
deliverables call for the repository to be published. Everything they require is recorded
below, so nothing depends on the PDFs travelling with the code.

## What each source controls

| Source | What it controls | Important constraints | How TRIAD complies |
| --- | --- | --- | --- |
| TRIAD PRD | Technical implementation: architecture, the 18 modules, 84 functional requirements, NFRs, agents, governance, experiments, acceptance tests, MVP | Authoritative for behaviour. Its illustrative numbers are marked `(EX)` and are **not** results | The implementation; `docs/implementation/REQUIREMENTS_TRACEABILITY.md` |
| `26FSPDSCPMID4096.pdf` | This team's case-study report structure | 22 numbered sections plus Executive Summary and Abstract, Keywords, References, Appendix; a named figure or table for each section; a word range for each; 10 000–15 000 words, 25–35 figures, 12–15 tables, 5–10 equations, 70–120 references | `docs/research/` holds one evidence-backed content file per section; `MANUSCRIPT_COMPLIANCE_CHECKLIST.md` tracks each item |
| `4_Teams_Titles_Links.pdf` | Team identity and title | Team `26FSPDSCPMID4096`, four members, a point of contact, and the title below | The title is used verbatim for academic deliverables; TRIAD remains the system's name |
| `3_Consolidated_Template` (.pdf, .docx) | Report and paper format | VIT cover page; Index, List of Figures, List of Tables and Contributions tables; IEEE Access paper body in two columns; IEEE style rules for abstract, headings, equations, figures, tables and references | The final manuscript is built on the `.docx` itself, so its geometry and styles carry over rather than being imitated |
| `1_Manuscript Guidelines.pdf` | Manuscript and submission rules | Template must be used exactly; references 30–50 minimum, 80% journals, 2020–2025; diagrams redrawn in draw.io at ≥600 DPI in a separate folder; editable equations; similarity ≤10%; five deliverable folders; a data availability statement | `MANUSCRIPT_COMPLIANCE_CHECKLIST.md` |

## 1. The title, from the team file

`4_Teams_Titles_Links.pdf`, row 1:

> **Team ID:** 26FSPDSCPMID4096
> **Title:** Agentic Data Science Programming: Autonomous Python–Scala–Spark Ecosystems for
> Intelligent Analytics

The faculty-defined title and the PRD's case-study title are the same string, so there is no
conflict to resolve. Academic deliverables use it as the title; the system the report describes
is called TRIAD (Tri-engine Reasoning for Intelligent, Autonomous Data Science), as in the PRD.

The same row lists four member registration numbers and a point of contact, and links to the
team's section-planning document (source 2). **Those registration numbers are not copied here.**
They belong on the cover page and in the Contributions table, which are completed at
finalization; until then the manuscript carries `[TEAM MEMBER 1]` … `[TEAM MEMBER 4]`.

The file names the course: **Programming for Data Science (MACSE502)**.

## 2. The section plan, from `26FSPDSCPMID4096.pdf`

Subtitle: *Case Study Report: Section Planning Table with Content Focus, Visuals, and Word
Count.* It states the structure is "designed for IEEE/ACM conference publications, industrial
case studies, and future Q1 journal extensions, focusing on Agentic AI, Autonomous Data
Engineering, Python, Scala, Apache Spark, Intelligent Analytics, MLOps, DataOps, and Enterprise
AI Automation."

| Section | Content focus (faculty wording) | Visuals required | Words |
| --- | --- | --- | --- |
| Executive Summary and Abstract | Challenges in modern data science programming, autonomous analytics, agentic orchestration, the proposed Python–Scala–Spark ecosystem, key contributions | **Table 1:** Executive Summary of Contributions and Outcomes | 450–750 |
| Keywords | Agentic AI, Data Science Programming, Python, Scala, Apache Spark, Intelligent Analytics, Autonomous Data Engineering, MLOps | none | 20–40 |
| 1. Introduction | Evolution of data science programming, limits of manual pipelines, emergence of Agentic AI, motivation | **Fig. 1** Evolution of Data Science Programming; **Fig. 2** Agentic Analytics Ecosystem Overview | 800–1200 |
| 2. Problem Statement | Workflow complexity, code maintenance, scalability, heterogeneous environments, analytics automation | **Fig. 3** Challenges in Modern Data Science Platforms; **Table 2** Research Challenges and Gaps | 400–700 |
| 3. Background and Literature Review | Python analytics, Scala ecosystems, Spark architectures, Agentic AI, MLOps, autonomous analytics frameworks | **Fig. 4** Literature Taxonomy; **Table 3** Comparative Review of Existing Analytics Platforms | 1500–2500 |
| 4. Requirements Analysis for Autonomous Analytics Ecosystems | Functional, scalability, interoperability, explainability, reliability, security, governance requirements | **Fig. 5** Requirements Engineering Framework; **Table 4** Functional and Non-Functional Requirements Matrix | 600–1000 |
| 5. Python–Scala–Spark Ecosystem Architecture | Architecture integrating Python analytics, Scala services, Spark processing, autonomous agents | **Fig. 6** Python–Scala–Spark Ecosystem Architecture; **Fig. 7** Multi-Layer Analytics Stack | 800–1200 |
| 6. Agentic Programming Framework | Agents for ingestion, transformation, analytics, orchestration, optimization, monitoring, reporting | **Fig. 8** Agentic Programming Architecture; **Fig. 9** Multi-Agent Collaboration Workflow | 1000–1500 |
| 7. Autonomous Data Engineering Layer | Intelligent ETL/ELT, schema adaptation, data quality, metadata-driven programming, self-healing pipelines | **Fig. 10** Autonomous Data Engineering Pipeline; **Table 5** Data Engineering Agent Responsibilities | 1200–1800 |
| 8. Intelligent Analytics and Machine Learning Layer | Predictive analytics, automated feature engineering, AutoML, model selection, training, optimization | **Fig. 11** Intelligent Analytics Workflow; **Fig. 12** AutoML and Feature Engineering Architecture | 1200–1800 |
| 9. Apache Spark Distributed Processing Layer | Distributed analytics, cluster orchestration, parallel computing, fault tolerance, performance optimization | **Fig. 13** Distributed Spark Architecture; **Table 6** Spark Optimization Techniques; **Equation:** Resource Allocation Function | 1000–1500 |
| 10. Explainable and Trustworthy Analytics Layer | Explainable AI services, model transparency, decision traceability, fairness monitoring, trust assessment | **Fig. 14** Explainability Framework; **Fig. 15** Trustworthy Analytics Model; **Equation:** Trust Score Calculation | 1000–1500 |
| 11. Agentic Workflow Orchestration and Decision Intelligence | Autonomous decision-making, workflow scheduling, resource allocation, adaptive optimization | **Fig. 16** Agent Orchestration Workflow; **Fig. 17** Autonomous Decision Lifecycle | 1000–1500 |
| 12. Security, Privacy, and Governance Layer | Access control, privacy protection, secure execution, compliance monitoring, governance automation | **Fig. 18** Security and Governance Architecture; **Table 7** Security Controls and Governance Policies | 800–1200 |
| 13. Experimental Setup | Datasets, Spark clusters, Python and Scala environments, hardware, benchmark scenarios, agent settings | **Fig. 19** Experimental Architecture; **Table 8** Experimental Configuration | 700–1200 |
| 14. Evaluation Metrics | Performance, scalability, automation, trust, explainability, resource utilization, operational efficiency | **Fig. 20** Evaluation Framework; **Table 9** Metrics Definition Matrix | 500–800 |
| 15. Results and Findings | Performance, scalability, code automation efficiency, response time, analytics accuracy, resource utilization | **Fig. 21** Performance Benchmark Results; **Fig. 22** Scalability Analysis; **Fig. 23** Agent Efficiency Comparison; **Table 10** Experimental Results Summary | 1200–2000 |
| 16. Comparative Analysis | Against traditional Python analytics, Scala-only platforms, Spark-native environments, existing autonomous frameworks | **Fig. 24** Comparative Radar Chart; **Table 11** Comparative Performance Analysis | 800–1200 |
| 17. Explainability, Reliability, and Trustworthiness Analysis | Transparency, reproducibility, fault tolerance, reliability, governance effectiveness, trustworthiness | **Fig. 25** Reliability Assessment Model; **Table 12** Trustworthiness Evaluation Criteria | 700–1000 |
| 18. Industry Use Cases and Case Study Evaluation | Finance, healthcare, smart manufacturing, smart cities, cybersecurity, retail analytics | **Fig. 26** Smart Manufacturing Analytics Ecosystem; **Fig. 27** Healthcare Intelligence Platform; **Table 13** Domain-Specific Use Cases | 800–1500 |
| 19. Business Impact Assessment | Automation benefits, operational efficiency, developer productivity, infrastructure optimization, ROI | **Fig. 28** Business Impact Dashboard; **Table 14** Cost-Benefit Analysis | 500–1000 |
| 20. Challenges and Limitations | Technical complexity, interoperability, computational cost, skills, organizational challenges | **Fig. 29** Challenges and Limitations Roadmap | 600–1000 |
| 21. Future Research Directions | Autonomous software agents, LLM-assisted programming, self-evolving ecosystems, federated data science, cognitive programming | **Fig. 30** Future Research Roadmap; **Fig. 31** Next-Generation Agentic Analytics Ecosystem | 600–1200 |
| 22. Conclusion | Contributions, key findings, future opportunities | **Fig. 32** Research Contribution Framework | 500–800 |
| References | Agentic AI, Python, Scala, Spark, MLOps, AutoML, Explainable AI, Intelligent Analytics | none | variable |
| Appendix (Optional) | Algorithms, Spark configurations, agent workflows, benchmark settings, code templates, supplementary analyses | as required | 500–2000 |

**Publication statistics** (the file's own table): total words 10 000–15 000; figures 25–35;
tables 12–15; equations 5–10; references 70–120.

The plan as written names **32 figures, 14 tables and 2 equations** (the Resource Allocation
Function and the Trust Score Calculation), so it sits inside the figure and table ranges and
needs three to eight further equations to reach five.

**Core items, named by the file as such:**

- Core architecture figure — **Figure 6: Agentic Data Science Programming Architecture
  Integrating Python, Scala, and Apache Spark.** (The section table calls the same figure
  "Python–Scala–Spark Ecosystem Architecture"; the core-figure caption is the fuller one and is
  the one used.)
- Core validation figures — Fig. 21 Performance Benchmark Analysis, Fig. 22 Scalability
  Evaluation, Fig. 23 Agent Efficiency Comparison, Fig. 24 Comparative Benchmarking.
- Core research tables — Table 3 Literature Review Comparison, Table 5 Agent Responsibilities
  Matrix, Table 8 Experimental Setup Configuration, Table 10 Experimental Results Summary,
  Table 11 Comparative Performance Analysis.

Two captions differ slightly between the section table and the core lists (Fig. 21 "Results"
against "Analysis"; Table 5 "Data Engineering Agent Responsibilities" against "Agent
Responsibilities Matrix"). The section table is the more specific and is used for the caption;
the content satisfies both readings.

## 3. The report format, from `3_Consolidated_Template`

### Front matter, in this order

1. **Cover page.** "Case-Study Report On "<Title>" submitted as part of Programming for Data
   Science (MACSE502) by" four blocks of *Reg No* and *Name*, "To Dr M Rajasekhara Babu",
   School of Computer Science and Engineering, Vellore Institute of Technology, **October 2026**,
   and a **Team ID** box at top right.
2. **Index** — Sl. No., Section Topic, Page No.: Abstract, 1–22, References, Appendix
   (Optional), and four spare rows.
3. **List of Figures** — Fig. No., Figure Title, Page No.; 20 rows in the template, extended to
   the figure count.
4. **List of Tables** — Table. No., Table Title, Page No.; 20 rows.
5. **Contributions** — for each of Abstract, 1–22, References, Appendix: Section Topic, Reg. No.
   of Team Member contributed, No. of Words. Then a **Consolidated Report on contributions** —
   each student's total words — and the **Total number of words**.

### The paper body: IEEE Access style

Measured from the `.docx`: **A4** (21.0 × 29.7 cm); margins **2.54 cm** left and right,
**1.27 cm** top and bottom; front matter in one column; the paper body in **two columns with a
1.25 cm gap**; the closing section in one column; **Times New Roman**, Normal at 12 pt.

| Element | Rule, as the template states it |
| --- | --- |
| Title | Upper and lower case, not all capitals; no long formulas; no "(Invited)" |
| Authors | The template's author line is "Dr. M Rajasekhara Babu¹, Second B. Author², …", with **the faculty member first and as corresponding author** (mrajasekharababu@vit.ac.in) and VIT e-mails for the rest |
| Abstract | **150–250 words**, one paragraph, self-contained, no abbreviations, footnotes, references, displayed equations or tables; three or four keywords or phrases worked in |
| Index Terms | Keywords in **alphabetical order**, comma separated |
| Headings | Roman numerals and capitals for sections ("I. INTRODUCTION"); lettered italic subsections ("A. ABBREVIATIONS AND ACRONYMS") |
| Abbreviations | Defined at first use in the text even if defined in the abstract |
| Style | A zero before decimal points ("0.25"); "s" for seconds; ranges "7 to 9" or "7-9"; serial comma; no contractions; "data" is plural; "Fig." abbreviated even at the start of a sentence; "Table" never abbreviated |
| Equations | Microsoft Equation Editor or MathType; numbered consecutively, number in parentheses flush right; symbols defined at or just after the equation and italicized; cited as "(1)", or "Equation (1)" at the start of a sentence |
| Units | SI |
| Figures | Captions **below**; no caption inside the image, no text box, no border; axis labels in words with units in parentheses — "Magnetization (A/m)", never "A/m" alone or a ratio; multipart figures labelled (a) (b) (c), 8 pt Times New Roman, centred below |
| Tables | Numbered in **Roman numerals** (TABLE I); title **above**; table footnotes lettered |
| Figure sizing | One column 3.5 in (88 mm); page width 7.16 in (181 mm); maximum depth 8.5 in (216 mm) |
| Resolution | Photographs, colour and greyscale figures ≥ **300 dpi**; line art **including tables** ≥ **600 dpi** |
| Figure fonts | Times New Roman, Helvetica, Arial, Cambria or Symbol, embedded or outlined; 8–10 pt |
| Graphic formats | PS, EPS, TIFF, PDF or PNG |
| File naming | First five letters of the first author's surname plus sequence (`ander1.tif`); tables with `.t` inserted (`ander.t1.tif`) |
| References | IEEE style, numbered in square brackets on the line inside punctuation; initials before surname; *et al.* only when names are not given; abbreviated conference titles; volume, issue, pages, year and DOI where available; **one source per number**; only the first word of a paper title capitalized |
| Footnotes | Avoided except the receipt-date note |
| Conclusion | Must not replicate the abstract |
| Appendix | Before the acknowledgment |
| Author biographies | At the end, with a photograph 1 in × 1.25 in |

The template's sample content — the magnetization figure, the "Units for magnetic properties"
table, the reference examples, and the two fictitious sample biographies — is example material
and is removed, not adapted. The faculty member's own biography and photograph are kept, because
the template names him first author; the team's biographies and photographs are placeholders
until finalization.

## 4. The submission rules, from `1_Manuscript Guidelines.pdf`

| Area | Requirement, as stated |
| --- | --- |
| Template | Use the official template; all formatting (fonts, margins, headings) strictly follows it; insert all sections per the team's structure |
| Sections | Follow the suggested sections and word limits for the title; keep section numbering and titles consistent |
| Sources | Recent journal papers (2020–2025), conference proceedings, credible websites; prioritize IEEE, ACM, Springer, Elsevier |
| Diagrams | High resolution (**minimum 600 DPI**); **re-drawn in draw.io** for clarity and originality; a reused diagram is cited ("Adapted from [3]"); all diagrams saved in a **separate folder** named with serial number and title — `Fig 01 <Figure Name>` |
| Equations | **Editable**, created with **Microsoft Equation Editor**, numbered and referenced in the text |
| References | **Minimum 30–50**; **80% journals** (IEEE, ACM, Springer, Elsevier) and 20% conferences, symposia, articles and websites; include **2020–2025** |
| Citation | Summarize in own words; cite at the end of each sentence — `[1]`, or `[1][3][5]` for several |
| Plagiarism | Similarity index **≤ 10%**; above 10% is rejected |
| Deliverables | Complete Manuscript (**.Doc format**); Diagram Folder; Source code folder (preferably on GitHub, with link); Datasets folder; Execution videos folder |
| Data availability | One of five prescribed statement forms, the same statement in the manuscript and in the submission report; de-identified if needed |

## 5. Where the sources disagree, and the resolution

| Topic | Disagreement | Resolution |
| --- | --- | --- |
| Total length | The plan's per-section minimums sum to about 18 670 words, while its own statistics table gives a total of 10 000–15 000 | **Resolved by the team (08-10-2026): each section follows its own range**; a total above 15 000, and small overruns within a section, are acceptable and are noted in `MANUSCRIPT_COMPLIANCE_CHECKLIST.md` |
| Abstract length | Team plan: "Executive Summary and Abstract" 450–750 words. Template: abstract 150–250 words | Two parts. A **150–250-word abstract** that meets every IEEE rule, and an **executive summary** with Table 1, so the section as a whole is 450–750 words |
| Reference count | Team plan 70–120; guidelines "minimum 30–50" | The guidelines set the floor and the plan sets the target. Aim for 70 or more **only as far as real, relevant, verifiable sources exist**; never below 50. No citation is added to reach a number |
| Figure count | Plan 25–35 and names 32; template's List of Figures has 20 rows | The list is extended; the plan governs |
| Table numbering | Plan writes "Table 1"; template requires Roman numerals | **TABLE I … TABLE XIV** in the manuscript, mapped one-to-one to the plan's Table 1–14 |
| Diagram tool | Guidelines: draw.io at ≥ 600 DPI. Plots of measured data are not diagrams | Architecture and process diagrams authored as **draw.io files** and exported at 600 DPI; charts of experiment data generated from the stored results by code at 600 DPI, because redrawing measured data by hand is how a number drifts from its source |
| Equation format | Guidelines: Microsoft Equation Editor. The manuscript is generated, not typed | Equations written in LaTeX and converted to **native Word equations (OMML)**, which open and edit in Word's Equation Editor; numbered (1)…(n) flush right |
| Section 10 "Trust Score Calculation" | The team plan asks for the equation. The PRD defines no trust score, and the other three faculty files do not mention one | See *Determinations* |
| Section 9 "Resource Allocation Function" | The plan asks for the equation; the PRD has resource rules but no single named function | See *Determinations* |
| Section 18 use cases | The plan asks for six industry domains; TRIAD was built and measured on one | See *Determinations* |
| Section 19 ROI | The plan asks for ROI and cost-benefit; the PRD's manual baseline B1 needs engineer time sheets that do not exist | See *Determinations* |
| Paper authorship | Template puts the faculty member first and as corresponding author | Followed at finalization; author details are placeholders until then |

## 6. Determinations

**Trust score (Section 10, Table 12).** Searched for in all four faculty files: it appears only
in `26FSPDSCPMID4096.pdf`, as the equation for Section 10 and implicitly in Fig. 15 and
Table 12. The PRD defines none. It is therefore **required as a manuscript element and not as a
system requirement.** It is not invented as a product feature and not reported as something the
platform measures. It is *defined* in the manuscript as an explicit, weighted composite of
quantities TRIAD actually records — audit-chain verification, claim-check outcome, calibration
error against its threshold, validation-gate pass rate, and whether a promotion carried a
separate approver — and *computed* from stored run records by an analysis script, with the
weights stated as a design choice rather than a validated result. The manuscript says exactly
that.

**Resource allocation function (Section 9).** TRIAD has real resource-allocation logic: routing
rule LR-06 sends the Spark-to-Python hand-off to pandas only when the estimated in-memory size
fits the ML worker's budget, and the sandbox clamps every job to a container limit. The equation
is written from that code, so it describes what the system does rather than a textbook formula
it does not use.

**Industry use cases (Section 18).** TRIAD was implemented and measured on **one** case study:
e-commerce customer churn, on synthetic data (ADR-0002). Finance, healthcare, smart
manufacturing, smart cities and cybersecurity are written as **conceptual transfer analyses**,
each labelled as such, saying which TRIAD components carry over unchanged and which would need
new contracts, features and policies. None is presented as implemented or measured.

**Business impact and ROI (Section 19).** No ROI figure is reported, because the PRD's manual
baseline B1 is defined by engineer time sheets and no such record exists; inventing hours would
fabricate the central comparison. The section reports what *was* measured — wall-clock time,
manual steps, audit and decision records produced, failures recovered without a person — and
states the ROI calculation as a method with the inputs an adopting organization would supply.

**Data availability.** TRIAD's data are produced by its own seeded generator, and the generator,
its settings and the seeds are in the repository. The statement used is the first prescribed
form, naming the repository, plus the generator command that reproduces the data.

## 7. Placeholders until finalization

Not finalized now, and never invented:

```text
[TEAM MEMBER 1] … [TEAM MEMBER 4]                 names and registration numbers
[TEAM MEMBER PHOTOS — TO BE INSERTED AT FINALIZATION]
[FINAL AUTHOR DETAILS — TO BE CONFIRMED]          order, VIT e-mails
[CORRESPONDING AUTHOR — TO BE CONFIRMED]          the template names the faculty member
[CONTRIBUTIONS — WORD COUNTS PER MEMBER]
[PERSISTENT WEB LINK TO DATASETS — TO BE CONFIRMED AT FINALIZATION]   the repository's public URL
```

The registration numbers exist in `4_Teams_Titles_Links.pdf` row 1 and are inserted from there
at finalization, together with the names, which no faculty file contains.

## 8. What the manuscript may and may not claim

| It may say | Only if | It may never say |
| --- | --- | --- |
| A number is a **measured result** | it comes from a stored experiment artifact and the file is named | that a PRD `(EX)` value is a result |
| A capability is **implemented** | a test or a recorded run shows it | that the 10 GB, 100 GB or 1 TB tiers were run — they were not (ADR-0002) |
| A use case is **conceptual** | it is labelled so | that LLM repair was measured — no provider is configured (ADR-0003) |
| Something is **future work** | it is in the limitations | that a hypothesis held where its experiment says it did not |
