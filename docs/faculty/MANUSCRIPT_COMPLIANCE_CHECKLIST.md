# Manuscript compliance checklist

One row per requirement the faculty materials impose on the manuscript and its submission.
Sources: **T** = `26FSPDSCPMID4096.pdf` (team section plan), **L** = `4_Teams_Titles_Links.pdf`,
**C** = `3_Consolidated_Template` (.pdf/.docx), **G** = `1_Manuscript Guidelines.pdf`. The
reasoning behind each requirement is in `FACULTY_MATERIALS_ANALYSIS.md`.

Status: **DONE** (evidence named) · **PARTIAL** · **PENDING** (not started) · **FINAL** (deferred
by instruction to the finalization stage) · **N/A** (with the reason).

**State on 15-10-2026:** 46 DONE, 4 PARTIAL, 3 FINAL (the team's names, numbers, photographs and allocation), 1 PENDING (the execution videos), 1 N/A.

## Identity and front matter

| # | Requirement | Source | Where it is met | Status | Evidence |
| --- | --- | --- | --- | --- | --- |
| 1 | Team ID `26FSPDSCPMID4096` used exactly | L, C | cover Team ID box; README | DONE | box reads `Team ID: 26FSPDSCPMID4096` on one line (build of 15-10-2026) |
| 2 | Title verbatim: "Agentic Data Science Programming: Autonomous Python–Scala–Spark Ecosystems for Intelligent Analytics" | L, T | cover; paper title | DONE | both read the title verbatim, en dashes included |
| 3 | Course: Programming for Data Science (MACSE502) | L, C | cover | DONE | the template's own text, kept |
| 4 | Cover page per template: Reg No / Name ×4, "To Dr M Rajasekhara Babu", SCSE, VIT, October 2026 | C | cover | FINAL | faculty name, SCSE, VIT and October 2026 in place; the four Reg No / Name boxes await the team's details |
| 5 | Index table: Abstract, 1–22, References, Appendix, page numbers | C | front matter, page 2 | DONE | Abstract, Executive Summary, 1-22, Data Availability, Appendix, References; Word fills the page numbers (PAGEREF) |
| 6 | List of Figures with page numbers | C | front matter, page 3 | DONE | 32 rows, numbered 1. to 32.; page numbers by Word |
| 7 | List of Tables with page numbers | C | front matter, page 4 | DONE | 15 rows; page numbers by Word |
| 8 | Contributions table per section, and consolidated per member | C | front matter, pages 5-6 | FINAL | per-section word counts and the consolidated total are generated; Reg. Nos. and the allocation await the team |

## Structure and length

| # | Requirement | Source | Where it is met | Status | Evidence |
| --- | --- | --- | --- | --- | --- |
| 9 | All 22 numbered sections, in order, with the faculty's titles | T, G | `docs/research/01`-`22` | DONE | sections I-XXII with the plan's titles |
| 10 | Executive Summary and Abstract 450–750 words, with Table 1 | T | `00_front_matter.md` | DONE | 634 words, with Table I |
| 11 | Abstract 150–250 words, one paragraph, no abbreviations/references/equations/tables | C | `00_front_matter.md` | DONE | 225 words, one paragraph, no abbreviation, reference or equation |
| 12 | Keywords 20–40 words; Index Terms alphabetical | T, C | `00_front_matter.md` | DONE | 32 words, alphabetical |
| 13 | Each section within its word range | T | `docs/final/manuscript_report.json` | DONE | all 25 parts within their ranges (Results 1 995 of 2 000, Conclusion 798 of 800, Challenges 998 of 1 000) |
| 14 | Total 10 000–15 000 words | T | manuscript | DONE | 22 818 words; the plan's own section minimums sum to about 18 670, so the team resolved (08-10-2026) that each section follows its own range (FACULTY_MATERIALS_ANALYSIS.md) |
| 15 | Section numbering and titles consistent | G | manuscript | DONE | Roman section numbers I-XXII, lettered subsections, the same titles in the Index |

## Figures

| # | Requirement | Source | Where it is met | Status | Evidence |
| --- | --- | --- | --- | --- | --- |
| 16 | 25–35 figures; the plan names Fig. 1–32 | T | `docs/research/figures/` | DONE | 32 figures, Fig. 1-32 as the plan names them |
| 17 | Fig. 6 core architecture figure, captioned "Agentic Data Science Programming Architecture Integrating Python, Scala, and Apache Spark" | T | Section V | DONE | Fig. 6 carries the caption verbatim |
| 18 | Fig. 21–24 core validation figures, from measured data | T | `figures/build_charts.py`, `charts_provenance.json` | DONE | Figs. 21-24 drawn from the experiments' result files; provenance recorded per chart |
| 19 | Diagrams redrawn in draw.io | G | `docs/research/figures/drawio/` | DONE | 26 diagrams as .drawio sources, exported by `export_diagrams.py` |
| 20 | ≥ 600 DPI for line art and tables; ≥ 300 DPI for colour | G, C | `figures/export_manifest.json`; `build_charts.py` | DONE | diagrams at 706 DPI at page width; charts at 600 DPI |
| 21 | Diagram folder, files named `Fig 01 <Figure Name>` | G | `docs/research/figures/{drawio,svg,png}/` | DONE | every file named `Fig NN <Figure Name>` |
| 22 | Captions below figures; none inside the image; no borders | C | manuscript | DONE | captions below each figure; no title inside a chart; no borders |
| 23 | Axis labels in words with units in parentheses | C | `build_charts.py` | DONE | e.g. Wall clock (s), Peak resident memory (MiB) |
| 24 | Figure fonts Times New Roman/Arial/Helvetica, 8–10 pt | C | `build_charts.py` | DONE | Times New Roman; drawn at the 6.27 in text width, printing 8.0 to 10.0 pt as placed (15-10-2026) |
| 25 | "Fig." abbreviated everywhere, including sentence start | C | manuscript | DONE | no 'Figure n' in running text; 40 'Fig.' references; captions in the template's FIGURE n. style |
| 26 | A reused diagram is cited "Adapted from [n]" | G | captions | N/A | no diagram is reused from a source |

## Tables

| # | Requirement | Source | Where it is met | Status | Evidence |
| --- | --- | --- | --- | --- | --- |
| 27 | 12–15 tables; the plan names Table 1–14 | T | `docs/research/tables/` | DONE | 15 tables |
| 28 | Core tables 3, 5, 8, 10, 11 present | T | Sections III, VII, XIII, XV, XVI | DONE | III Comparative Review, V Data Engineering Agent Responsibilities, VIII Experimental Configurations, X Experimental Results Summary, XI Comparative Performance Analysis |
| 29 | Result tables generated from stored experiment data, not typed | prompt, G | `docs/research/tables/build_tables.py` | DONE | Tables X and XI generated from the experiments' result files |
| 30 | Roman numerals (TABLE I…); title above; lettered footnotes | C | manuscript | DONE | TABLE I-XV, title above each table; no table needs a footnote |

## Equations

| # | Requirement | Source | Where it is met | Status | Evidence |
| --- | --- | --- | --- | --- | --- |
| 31 | 5–10 meaningful equations | T | manuscript | DONE | 10 numbered equations |
| 32 | Resource Allocation Function (Section 9), from TRIAD's actual allocation logic | T | Section IX.B | DONE | the resource allocation function, from the router and the sandbox profiles |
| 33 | Trust Score Calculation (Section 10), computed from recorded evidence | T | Section X.E, Eq. (6) | DONE | trust score from recorded evidence, computed by an analysis script (Fig. 25b) |
| 34 | Editable, Microsoft Equation Editor format | G | manuscript | DONE | native Word equations (OMML, via MML2OMML.XSL) |
| 35 | Numbered (1)…(n) flush right; symbols defined; cited as "(1)" | C, G | manuscript | DONE | numbered (1)-(10) flush right in Word's numbered-equation form; symbols defined beneath; cited as (n) |

## References and integrity

| # | Requirement | Source | Where it is met | Status | Evidence |
| --- | --- | --- | --- | --- | --- |
| 36 | At least 30–50 references; target 70–120 where real sources exist | G, T | `docs/research/references/bibliography.json` | DONE | 118 references, every one cited |
| 37 | 80% journals (IEEE, ACM, Springer, Elsevier), 20% other | G | reference audit | DONE | 105 journal articles (89%), 11 conference papers, 1 book, 1 report |
| 38 | Sources from 2020–2025 | G | reference audit | PARTIAL | 87 of 118 (74%) from 2020-2025; the rest are the foundational methods the work uses (e.g. the Brier score, random forests) |
| 39 | Every reference real and verified; none invented | prompt | `references/verify_references.py` | DONE | every entry resolved by its DOI on Crossref with its title words checked; metadata from Crossref, none typed from memory |
| 40 | IEEE reference format; one source per number; cited in order | C | manuscript | DONE | IEEE style, numbered in order of first citation |
| 41 | Cite at the end of each sentence, `[1]` or `[1][3][5]` | G | manuscript | DONE | citations close their sentences; none inside a word |
| 42 | Own words; similarity ≤ 10% | G | written from TRIAD's evidence | PARTIAL | own words throughout; the similarity score needs the submitter's tool |
| 43 | Measured results distinguished from PRD `(EX)` illustrations | prompt | Evidence blocks under every section | DONE | every number traced to an experiment or run record; no PRD (EX) value used as a result |

## Format

| # | Requirement | Source | Where it is met | Status | Evidence |
| --- | --- | --- | --- | --- | --- |
| 44 | Built on the official template; fonts, margins, headings unchanged | G, C | `docs/faculty/source/3_Consolidated_Template.docx` | DONE | the template is the base document; A4, 2.54/1.27 cm, two columns, 1.25 cm gap |
| 45 | Paper body in two columns | C | manuscript | DONE | paper body in two columns; wide figures and tables span both |
| 46 | Section headings in Roman numerals and capitals | C | manuscript | DONE | e.g. XV. RESULTS AND FINDINGS |
| 47 | Abbreviations defined at first use | C | manuscript | DONE | checked 15-10-2026: LLM, API, PSI, ECE, PR-AUC, ROC-AUC, JVM, UDF and JAR defined at first use |
| 48 | Template sample content removed | C | manuscript | DONE | no sample text left ('J. K. Author', '<Title>', 'TITLE OF THE PAPER' all absent) |
| 49 | Author biographies with photographs | C | end of manuscript | FINAL | the faculty biography is in place; the four team biographies and photographs await the team |

## Deliverables

| # | Requirement | Source | Where it is met | Status | Evidence |
| --- | --- | --- | --- | --- | --- |
| 50 | Complete manuscript in Word format | G | `docs/final/TRIAD_Manuscript_26FSPDSCPMID4096.docx` (.pdf beside it) | DONE | 55 pages; Word fills the page references; every page numbered |
| 51 | Diagram folder | G | `docs/research/figures/` | DONE | draw.io sources, SVG and PNG exports |
| 52 | Source code folder, preferably GitHub with link | G | this repository | PARTIAL | complete; the public link is the team's to publish |
| 53 | Datasets folder | G | `datasets/synthetic/` | DONE | seeded generator; each experiment's sizes and seeds stored with its results; no data committed (by rule) |
| 54 | Execution videos folder | G | `videos/` | PENDING | needs a person to record; `scripts/run_demo.py` runs the demonstration scenario end to end |
| 55 | Data availability statement, same in manuscript and submission | G | `docs/research/data_availability.md` | PARTIAL | statement written in the repository form; the persistent link awaits the team |
