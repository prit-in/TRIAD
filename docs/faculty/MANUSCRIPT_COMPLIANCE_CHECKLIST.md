# Manuscript compliance checklist

One row per requirement the faculty materials impose on the manuscript and its submission.
Sources: **T** = `26FSPDSCPMID4096.pdf` (team section plan), **L** = `4_Teams_Titles_Links.pdf`,
**C** = `3_Consolidated_Template` (.pdf/.docx), **G** = `1_Manuscript Guidelines.pdf`. The
reasoning behind each requirement is in `FACULTY_MATERIALS_ANALYSIS.md`.

Status: **DONE** (evidence named) · **PARTIAL** · **PENDING** (not started) · **FINAL** (deferred
by instruction to the finalization stage) · **N/A** (with the reason).

## Identity and front matter

| # | Requirement | Source | Where it is met | Status | Evidence |
| --- | --- | --- | --- | --- | --- |
| 1 | Team ID `26FSPDSCPMID4096` used exactly | L, C | cover page Team ID box; README; this folder | PARTIAL | analysis document; manuscript not yet assembled |
| 2 | Title verbatim: "Agentic Data Science Programming: Autonomous Python–Scala–Spark Ecosystems for Intelligent Analytics" | L, T | cover page, paper title | PENDING | |
| 3 | Course: Programming for Data Science (MACSE502) | L, C | cover page | PENDING | |
| 4 | Cover page per template: Reg No / Name ×4, "To Dr M Rajasekhara Babu", SCSE, VIT, October 2026 | C | cover page | FINAL | names and registration numbers deferred |
| 5 | Index table: Abstract, 1–22, References, Appendix, page numbers | C | front matter | PENDING | |
| 6 | List of Figures with page numbers | C | front matter | PENDING | |
| 7 | List of Tables with page numbers | C | front matter | PENDING | |
| 8 | Contributions table per section, and consolidated per member | C | front matter | FINAL | needs member names and allocation |

## Structure and length

| # | Requirement | Source | Where it is met | Status | Evidence |
| --- | --- | --- | --- | --- | --- |
| 9 | All 22 numbered sections, in order, with the faculty's titles | T, G | `docs/research/` one file per section | PENDING | |
| 10 | Executive Summary and Abstract 450–750 words, with Table 1 | T | `docs/research/executive_summary.md` | PENDING | |
| 11 | Abstract 150–250 words, one paragraph, no abbreviations/references/equations/tables | C | same file | PENDING | |
| 12 | Keywords 20–40 words; Index Terms alphabetical | T, C | same file | PENDING | |
| 13 | Each section within its word range | T | word counts recorded per file | PENDING | |
| 14 | Total 10 000–15 000 words | T | manuscript | PENDING | |
| 15 | Section numbering and titles consistent | G | manuscript | PENDING | |

## Figures

| # | Requirement | Source | Where it is met | Status | Evidence |
| --- | --- | --- | --- | --- | --- |
| 16 | 25–35 figures; the plan names Fig. 1–32 | T | `figures/` | PENDING | |
| 17 | Fig. 6 core architecture figure, captioned "Agentic Data Science Programming Architecture Integrating Python, Scala, and Apache Spark" | T | `figures/` | PENDING | |
| 18 | Fig. 21–24 core validation figures, from measured data | T | `figures/`, generated from `experiments/` | PENDING | |
| 19 | Diagrams redrawn in draw.io | G | `.drawio` sources beside each export | PENDING | |
| 20 | ≥ 600 DPI for line art and tables; ≥ 300 DPI for colour | G, C | export settings recorded per figure | PENDING | |
| 21 | Diagram folder, files named `Fig 01 <Figure Name>` | G | `figures/diagrams/` | PENDING | |
| 22 | Captions below figures; none inside the image; no borders | C | manuscript | PENDING | |
| 23 | Axis labels in words with units in parentheses | C | chart generator | PENDING | |
| 24 | Figure fonts Times New Roman/Arial/Helvetica, 8–10 pt | C | chart generator | PENDING | |
| 25 | "Fig." abbreviated everywhere, including sentence start | C | manuscript | PENDING | |
| 26 | A reused diagram is cited "Adapted from [n]" | G | captions | PENDING | none reused so far |

## Tables

| # | Requirement | Source | Where it is met | Status | Evidence |
| --- | --- | --- | --- | --- | --- |
| 27 | 12–15 tables; the plan names Table 1–14 | T | `tables/` | PENDING | |
| 28 | Core tables 3, 5, 8, 10, 11 present | T | `tables/` | PENDING | |
| 29 | Result tables generated from stored experiment data, not typed | prompt, G | `tables/` generator | PENDING | |
| 30 | Roman numerals (TABLE I…); title above; lettered footnotes | C | manuscript | PENDING | |

## Equations

| # | Requirement | Source | Where it is met | Status | Evidence |
| --- | --- | --- | --- | --- | --- |
| 31 | 5–10 meaningful equations | T | manuscript | PENDING | |
| 32 | Resource Allocation Function (Section 9), from TRIAD's actual allocation logic | T | `docs/research/spark_layer.md` | PENDING | |
| 33 | Trust Score Calculation (Section 10), computed from recorded evidence | T | `docs/research/explainability.md` + analysis script | PENDING | |
| 34 | Editable, Microsoft Equation Editor format | G | native Word (OMML) equations | PENDING | |
| 35 | Numbered (1)…(n) flush right; symbols defined; cited as "(1)" | C, G | manuscript | PENDING | |

## References and integrity

| # | Requirement | Source | Where it is met | Status | Evidence |
| --- | --- | --- | --- | --- | --- |
| 36 | At least 30–50 references; target 70–120 where real sources exist | G, T | `docs/research/references.md` | PENDING | |
| 37 | 80% journals (IEEE, ACM, Springer, Elsevier), 20% other | G | reference audit | PENDING | |
| 38 | Sources from 2020–2025 | G | reference audit | PENDING | |
| 39 | Every reference real and verified; none invented | prompt | verification record per reference | PENDING | |
| 40 | IEEE reference format; one source per number; cited in order | C | manuscript | PENDING | |
| 41 | Cite at the end of each sentence, `[1]` or `[1][3][5]` | G | manuscript | PENDING | |
| 42 | Own words; similarity ≤ 10% | G | written from TRIAD's evidence, not from sources | PENDING | a similarity check is run by the submitter's tool |
| 43 | Measured results distinguished from PRD `(EX)` illustrations | prompt | every number traced to an artifact | PENDING | |

## Format

| # | Requirement | Source | Where it is met | Status | Evidence |
| --- | --- | --- | --- | --- | --- |
| 44 | Built on the official template; fonts, margins, headings unchanged | G, C | the `.docx` is used as the base document | PENDING | geometry measured: A4, 2.54/1.27 cm, two columns, 1.25 cm gap |
| 45 | Paper body in two columns | C | manuscript | PENDING | |
| 46 | Section headings in Roman numerals and capitals | C | manuscript | PENDING | |
| 47 | Abbreviations defined at first use | C | manuscript | PENDING | |
| 48 | Template sample content removed | C | manuscript | PENDING | |
| 49 | Author biographies with photographs | C | end of manuscript | FINAL | photographs deferred |

## Deliverables

| # | Requirement | Source | Where it is met | Status | Evidence |
| --- | --- | --- | --- | --- | --- |
| 50 | Complete manuscript in Word format | G | `docs/final/` | PENDING | |
| 51 | Diagram folder | G | `figures/diagrams/` | PENDING | |
| 52 | Source code folder, preferably GitHub with link | G | this repository | PARTIAL | repository exists; publication is the team's decision |
| 53 | Datasets folder | G | generator + seeds; large data not committed | PENDING | |
| 54 | Execution videos folder | G | `videos/` | PENDING | needs a person to record; the demo script is prepared for it |
| 55 | Data availability statement, same in manuscript and submission | G | manuscript, `docs/faculty/` | PENDING | form chosen in the analysis |
