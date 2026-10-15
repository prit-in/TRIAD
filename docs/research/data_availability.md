# Data Availability

The datasets generated and analysed during the current study are available in the TRIAD
repository, https://github.com/pritdadhania27/TRIAD. All of them are
synthetic: the repository's seeded generator produces every table, so the command
`python -m datasets.synthetic.generator --out <folder> --customers <n> --seed <seed>` regenerates
any of them exactly, and the size, seed, and fault settings of each experiment are recorded beside
its results in `experiments/`. No personal data were collected or used.

---

## Evidence (not part of the manuscript)

- Form: the first of the five statement forms in `1_Manuscript Guidelines.pdf` (repository and
  link), chosen in `docs/faculty/FACULTY_MATERIALS_ANALYSIS.md` section 6; the same statement goes
  into the submission report.
- Generator command: `datasets/synthetic/generator.py` `main()`; per-experiment settings in each
  `experiments/EXP-n/<timestamp>/config.json`.
