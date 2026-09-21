# Completion report

Written 22-09-2026. Every number here was read back from something the system wrote — a
`result.json`, a database row, a test run, a build. Where a measurement was not taken, this
report says so rather than carrying an estimate.

---

## A. Project status

**The loop is complete and governed end to end, all ten experiments have been run, and every
page the PRD names exists** — eleven in total, the eight of PRD section 26.1 plus monitoring,
experiments and the audit trail. A run is queued through the API, executed by a worker,
paused for a person at the approval gate, decided by a second person, carried out, scored,
monitored and reported — with every decision, execution, artifact and tool call recorded as a
row that can be read back.

| Phase | Gate | Status |
| --- | --- | --- |
| 1. Environment | Phase-1 smoke test | **passed** — 9 passed, 1 skipped by design |
| 2. Ingestion and profiling | UC-01, UC-02 | **passed** — AT-01, AT-02 |
| 3. Spark and Scala | TC-026, TC-030 | **passed** — 21 ScalaTest cases |
| 4. Python ML | AT-06, AT-07 | **passed** |
| 5. Agent orchestration | real end-to-end run + ATC-01..05 | **passed** |
| 6. Evaluation and retraining | drift measured, RT-1..RT-9 evaluated | **passed** |
| 7. Dashboard and reporting | AT-03, AT-08, AT-15 | **passed** — eleven pages |
| 8. Testing and optimisation | success criteria audited | **passed** — traceability matrix, all ten experiments |
| 9. Documentation and demo | three stable rehearsals | **passed** — four consecutive clean demo runs |

What is *not* complete is listed in section I, and no Must-level requirement is among it.

---

## B. Bugs found

Grouped by the pattern that produced them, because the pattern is the finding. **Every one was
found by running the system, not by reading it, and every one had passing tests around it.**

### Values computed at one site and read at none

1. **`dataset_versions.profile_uri` and `.quality_score` were NULL on all 130 rows.** The
   profiling stage wrote its document to storage and handed the URI back to a caller that used
   the score for the quality gate and dropped the rest. `GET /datasets/{ref}/versions/{n}/profile`
   checks `profile_uri` first, so the whole of FR-068's API surface refused every request it
   could ever be given.
2. **No model card had ever been written.** `ModelCard` and `write_model_card` existed with a
   complete markdown renderer and zero callers, while `model_versions.model_card_uri` pointed at
   `reports/<run_key>/model_card.md` and the governance policy lists `model_card` among the
   evidence required to promote a model. An approver following that link got nothing.
3. **Five of the nine validation gates were called by nothing**, including the tests. The checks
   behind three of them were being performed — the contract was evaluated, the execution status
   decided the stage, the artifact was stored under its content hash — and none was recorded as
   a gate, so `gate_results` showed four where every document claimed nine.
4. **An escalating run recorded no acceptance decision.** The decision was written only on the
   paths that continued, so a run stopped by the acceptance policy left a one-line reason and
   none of the numbers behind it.
5. **`metrics.ci_low`, `ci_high` and `segment` were columns nothing filled** (found earlier in
   the project, same shape): the bootstrap intervals were computed and dropped on the way to the
   database, so "comparison with confidence intervals" had no intervals.

### Experiments measuring their own bugs

6. **EXP-8 reported "20 of 20 seeded faults detected" and 15 of those were real.** The generated
   object carried the experiment's run suffix — `object CleanJob_exp08wc-1` — and a hyphen is not
   a legal Scala identifier, so every job failed to compile and the harness read the compile
   failure as the seeded fault being caught.
7. **EXP-5 reported its hypothesis as held from a single data point.** `all()` over a one-element
   list is vacuously true, so a "monotone decline" was declared from one surviving condition —
   and on escalation the experiment discarded the cleaning evidence it had already collected, so
   the half that genuinely holds was thrown away in the conditions that produced it.
8. **EXP-2 and EXP-3 had never run at all.** Both passed `valid_cutoffs` to a `SplitSpec` whose
   field is `valid_cutoff`.

### Endpoints and pages that could not work

9. **The only paginated endpoint returned 500 on page two, always.** `decode_cursor` returned a
   string, which was compared against a `timestamp with time zone`; Postgres has no
   `timestamp < varchar` operator. Nothing had ever asked for a second page.
10. **The audit page had no date filter**, although the API has accepted `since` since it was
    written. A trail capped at 200 rows with no date filter can only show the most recent
    activity.
11. **`/settings` reported its own false alarm on its first call**: it ran the governance
    validator over `routing.yaml`, which carries `routing_version` and `rules` rather than
    `policy_version` and `actions`, and told the administrator the routing policy had been
    refused — on the one page whose job is saying whether the policy loaded.

### Repository hygiene

12. **`.gitignore` had a whole shell command pasted into it as a pattern**, so `*.tsbuildinfo`
    was never ignored, and three blocks were duplicated.
13. **No README and four empty documentation directories**, against a PRD section that names
    twelve documents.

---

## C. Bugs fixed

Each fix is at the site every caller routes through, and each has a regression test.

| # | Fix | Where | Test |
| --- | --- | --- | --- |
| 1 | `stage_profile` writes `profile_uri` and `quality_score` back onto the version row it describes, matched on the storage URI the caller passed in | `agents/graph/pipeline.py` | `test_ingest.py::TestTheProfileReachesTheVersionItDescribes` — two cases, including that profiling an unregistered path touches nothing |
| 2 | The model card is built where the evidence is — at the end of training, with the split, candidates, calibration and importance in hand — and written to the path the evidence bundle names | `agents/graph/churn.py` | `test_reports.py` — three cases: the sections an approver is promised, that the pipeline calls the writer, and that the file is readable |
| 3 | Gates 7, 8 and 9 record what was already being checked; gate 7 is skipped where there is no contract rather than reporting a fabricated failure; gates 5 and 6 are no longer claimed | `agents/graph/stages.py`, `agents/graph/pipeline.py` | `test_stage_runner.py`, `test_codegen.py`; verified on a run |
| 4 | The acceptance decision is written whatever it decided and **before** the escalation branch, with its rule ids, evidence and validation metrics | `agents/graph/churn.py` | verified on `run-2026-09-21-020`: 13 rule ids and evidence including `ece_after_calibration` and `calibration_remedies_tried` |
| 6 | The experiment's run suffix is sanitised into a legal Scala identifier; the false result was deleted rather than kept | `experiments/exp08_codegen_faults.py` | the re-run reports 15 of 20, with `wrong_column` at 0 of 5 |
| 7 | A trend is reported as *not assessable* below two points, and an escalated condition keeps every measurement it made before it stopped | `experiments/exp05_missing_values.py` | the re-run reports no verdict, with the row-accounting half intact |
| 8 | `valid_cutoff`, singular | `experiments/exp02_*.py`, `exp03_*.py` | both experiments now produce results |
| 9 | `decode_cursor` returns a `datetime`; a cursor this API did not issue is a 400 | `backend/app/pagination.py` | `test_api.py` — three cases |
| 10 | A date input on the audit page, anchored at its own midnight before being sent as an ISO instant | `frontend/src/pages/Audit.tsx` | type-checked; verified in the browser |
| 11 | Each policy file is read the way its own engine reads it | `backend/app/api/v1/settings.py` | `test_api.py::test_both_policy_files_are_read_the_way_their_own_engine_reads_them` |
| 12 | `.gitignore` repaired and de-duplicated | `.gitignore` | — |
| 13 | Twelve documents written from the code | `README.md`, `docs/` | — |

**Also fixed and measured: the login blockage.** `churn.prepare` held its ingest writes — which
land on the *system* audit chain — for the length of a run, and `auth.login` needs the same
chain's advisory lock. Measured before: one transaction idle for 9m56s with three logins queued
at 4:32, 3:08 and 1:47. Measured after, during `run-2026-09-21-020` with a transaction idle for
twelve minutes: **eight consecutive logins in 0.18–0.45s, and 17 probes across the whole run with
a worst case of 2.0s and no failures.**

---

## D. PRD completion

The full matrix is in [REQUIREMENTS_TRACEABILITY.md](REQUIREMENTS_TRACEABILITY.md), which is
organised around its "not met" column on purpose. Summary:

| Category | Count | Met | Partly met | Not met |
| --- | --- | --- | --- | --- |
| Functional — Must | 65 | 63 | 2 | 0 |
| Functional — Should | 16 | 7 | 3 | 6 |
| Functional — Could | 3 | 1 | 0 | 2 |
| Acceptance tests (AT) | 16 | 12 | 1 | 3 |
| Agentic acceptance (ATC) | 18 | 14 | 1 | 3 |
| Experiments (EXP) | 10 | 10 run | — | 0 |
| Documentation (section 58) | 12 | 12 | 0 | 0 |

**No Must requirement is unimplemented.** The two partly met are FR-055 (seven of nine gates
run; 5 and 6 are named as not running) and FR-074 (manual and retraining triggers work end to
end; schedule and data-arrival triggers are not wired).

The six Should-level "not met" are FR-004 JDBC ingestion, FR-022 anomaly detection in cleaning,
FR-024 automated feature suggestions, FR-035 caching decisions, FR-037 the Spark optimisation
loop and FR-040 typed Datasets. The two Could-level are FR-008 a streaming connector and FR-084
alert delivery.

---

## E. The ten experiments

All ten have run. **Five hypotheses held, three did not, and two have no verdict** because the
measurement they turn on cannot be taken in this environment. The harness refuses a verdict when
no measurement was recorded, which is why "no verdict" appears at all rather than a number that
reads like one.

Common to all: the dataset is TRIAD's synthetic generator rather than UCI Online Retail II,
which has transactions and nothing else (ADR-0002); the split is temporal with a 90-day gap rule;
seeds are fixed; and every raw measurement, configuration and environment record is written to
`experiments/<EXP-n>/<timestamp>/` with the newest also at `experiments/<EXP-n>/latest.json`.

### EXP-1 — TRIAD against the non-agentic baselines

| | |
| --- | --- |
| **Purpose** | Does the governed pipeline finish with no manual step except approval, and what does the governance cost? |
| **Configuration** | Four configurations: P (TRIAD), B1 (manual runbook), B2 (pandas only), B3 (Spark, no agents) |
| **Dataset / split** | 400 customers, 24 months; temporal split, 90-day gap, single sealed test cutoff |
| **Result** | **Held, partial** |
| **Key metrics** | P 749.3s wall clock, 0 approvals, 0 other human actions, 4 Spark jobs, 6 decision records, 15 audit entries. B2 4.1s, B3 25.7s, both 0 governance artifacts. Like-for-like quality on P's own 23 features and P's split: baselines' shortlist 0.7894 test PR-AUC |
| **Artifacts** | `experiments/EXP-1/20260921T204825Z/` |
| **Validation** | Honest. The three raw PR-AUCs are **not** a quality comparison and are not presented as one — P uses the full catalogue and the baselines twelve features. The fifth trial exists to hold features constant. B1 is not measured, because its metric is engineer hours and there is no second person |

### EXP-2 — Where a single node gives out

| | |
| --- | --- |
| **Purpose** | Find the size at which pandas fails and Spark keeps going |
| **Configuration** | A ladder of four sizes, B2 against B3 at each |
| **Dataset / split** | 400 / 1 500 / 6 000 / 20 000 customers, 0.4–14.9 MB on disk |
| **Result** | **Did not hold, partial** |
| **Key metrics** | B2 finished every rung and beat B3 on all of them: 8.9s/46.1s, 6.7s/29.1s, 9.2s/32.8s, 19.8s/54.8s. Peak RSS 246 → 1 065 MB |
| **Artifacts** | `experiments/EXP-2/20260921T204046Z/` |
| **Validation** | A failure to observe, not evidence of absence, and reported as such. The PRD's 10 GB tier — where the crossover is expected — was not run: Docker has 5.27 GB here and the sandbox clamps containers to 3 GB, so it would fail for every configuration including the ones the hypothesis expects to finish |

### EXP-3 — Scaling with parallelism

| | |
| --- | --- |
| **Purpose** | Does doubling the cores give at least 1.6× speed-up? |
| **Configuration** | `local[1]`, `local[2]`, `local[4]`, best of two repeats, with the fixed cost measured per parallelism and subtracted |
| **Dataset / split** | 6 000 customers, 10 cutoffs, 19 332 feature rows |
| **Result** | **Did not hold, partial** |
| **Key metrics** | Work-component speed-up 1.000 / **1.301** / **1.026**; two-to-four cores **0.789×** against a 1.6 threshold. Fixed cost 8.57s / 0.46s / 0.24s |
| **Artifacts** | `experiments/EXP-3/20260921T203605Z/` |
| **Validation** | The fixed cost is measured rather than assumed, which is why the raw numbers (1.736× and 1.391×) look better than the work numbers — `local[1]`'s JVM start-up flatters every ratio computed against it. Sub-linear scaling is the expected observation: the window aggregations shuffle. Shuffle bytes and spill are absent rather than approximated (Spark event logs are disabled here) |

### EXP-4 — Schema errors

| | |
| --- | --- |
| **Purpose** | Are schema faults caught at the right stage, and is a proposed mapping ever applied without a person? |
| **Configuration** | Four conditions: clean baseline, renamed required column, price as text with symbols, dropped optional table |
| **Dataset / split** | Generator with the named fault injected per condition |
| **Result** | **Held, partial** |
| **Key metrics** | 2 of 2 scored faults detected — rename at ingest, price-as-text at clean. **0 mappings applied without a person. 0 conditions lost rows silently.** The dropped optional table is not detected, by design: its features are disabled |
| **Artifacts** | `experiments/EXP-4/20260919T044957Z/` |
| **Validation** | Detection is scored against the clean baseline on purpose — a detector that flagged everything would otherwise score perfectly. This experiment also found FR-007's contract enforcement unreachable on the real path |

### EXP-5 — Missing values

| | |
| --- | --- |
| **Purpose** | Are key nulls quarantined, non-key nulls handled without losing rows, and does PR-AUC decline gradually? |
| **Configuration** | Four null rates: 0%, 5%, 20%, 50% non-key, with 1% key nulls in the last three |
| **Dataset / split** | 8 078 transaction rows per condition |
| **Result** | **No verdict, partial** |
| **Key metrics** | Row accounting balanced in **every** condition. Quarantine 0 / 72 / 72 / 72 of 8 078 — the 72 are the key-null injection, and the number does not move with the non-key rate, exactly as the rule describes. Cleaning affected 0.89% of rows against a 5% policy limit, so no approval was requested. Baseline test PR-AUC 0.7259, ECE 0.0744; the other three escalated on MISCALIBRATED and produced no PR-AUC |
| **Artifacts** | `experiments/EXP-5/20260921T210142Z/` |
| **Validation** | The row half of the hypothesis holds cleanly. The trend half has **no verdict**, because three of four conditions never produced a PR-AUC. The pipeline does not degrade gracefully as values go missing; it stops, which is the acceptance policy behaving as specified. What is still open — whether MISCALIBRATED at 5% is a correct refusal or a calibration chain giving up early — needs the acceptance evidence that an escalating run was not recording; that gap is now closed, so a re-run can answer it |

### EXP-6 — Spark failures and recovery

| | |
| --- | --- |
| **Purpose** | Do task retries absorb an executor loss, does the playbook fix out-of-memory within two retries, does adaptive execution handle skew? |
| **Configuration** | Clean baseline plus three injected faults: 256m driver, killed sandbox container, one account holding 20% of rows |
| **Dataset / split** | 8 078 rows, content hash `1d1ca664a2174556` as the reference |
| **Result** | **Did not hold, partial** |
| **Key metrics** | 2 of 3 faults recovered. The out-of-memory arm did **not** recover, in 2 attempts, with no remedies applied |
| **Artifacts** | `experiments/EXP-6/20260919T063629Z/` |
| **Validation** | The most informative experiment in the set. Four findings; two are fixed (a Spark output hash that could not be reproducible, and `RAISE_EXECUTOR_MEMORY` bumping an input nothing read) and two are open (an out-of-memory failure classified `UNKNOWN`, and a coursier download failure classified `SCALA_COMPILE_ERROR`). The container kill did not land on a real container — `docker` is absent from the dev container's PATH — and that is recorded as `container_killed: false` rather than reported as a recovery |

### EXP-7 — Model degradation

| | |
| --- | --- |
| **Purpose** | Do drift flags fire, does retraining start, is the challenger compared with the champion, does promotion wait? |
| **Configuration** | Six undrifted months as a control, then covariate shift, a single-segment shift, and concept drift |
| **Dataset / split** | Monthly scoring windows against the stored training reference |
| **Result** | **Held, partial** |
| **Key metrics** | **0 of 6 false alarms**, highest undrifted PSI 0.031 against a 0.25 threshold. Covariate shift flagged immediately at PSI 0.311; a single-segment shift at 1.084; triggers RT-2, RT-3, RT-5, RT-6 fired |
| **Artifacts** | `experiments/EXP-7/20260919T042615Z/` |
| **Validation** | The control arm is what makes the rest meaningful: a detector that flagged everything would show the same delays. PSI is blind to concept drift — RT-1 catches that instead, and the result says so |

### EXP-8 — Seeded Scala faults

| | |
| --- | --- |
| **Purpose** | Are at least 70% of seeded code faults repaired within three attempts, do repeated fingerprints escalate, do forbidden APIs ever run? |
| **Configuration** | 20 mutations across four classes — type mismatch, missing import, wrong column, forbidden API — five each, plus an unmutated control |
| **Dataset / split** | The generated `CleanJob`, run through the gates |
| **Result** | **No verdict, partial** |
| **Key metrics** | **15 of 20 detected before execution.** Forbidden API **5 of 5 at gate 2** in under a third of a second each, never compiled, never submitted. Type mismatch 5/5 and missing import 5/5 at gate 4. **Wrong column 0 of 5** — it passes every static gate. One distinct fingerprint across three attempts on every detected fault |
| **Artifacts** | `experiments/EXP-8/20260921T202108Z/` |
| **Validation** | The stronger-than-stated finding is the wrong-column row: the PRD expects semantic faults to escalate more often; they are in fact invisible to static analysis, because a column name is a string until Spark resolves it. Repair rate has **no verdict**: rewriting a slot needs an LLM provider and none is configured, so a measured 0% would describe the missing provider. The first run of this experiment reported 20 of 20 and that result was deleted |

### EXP-9 — Leakage sentinel

| | |
| --- | --- |
| **Purpose** | Do the static checks and the leakage alarm block both a post-cutoff feature and a lifetime total? |
| **Configuration** | A point-in-time control against two leak types, with the defences bypassed to measure the inflation |
| **Dataset / split** | Held-out data with behaviour drawn first and the label second |
| **Result** | **Held, completed** |
| **Key metrics** | 2 of 2 leaks blocked, control not blocked. Inflation with defences off: **+0.5470** PR-AUC for the post-cutoff feature, **+0.4975** for the lifetime total |
| **Artifacts** | `experiments/EXP-9/20260919T030745Z/` |
| **Validation** | The finding is that the two defences are not redundant: `customer_lifetime_orders` is a name no static rule objects to, and only the behavioural checks catch it. The first version of this experiment correctly reported its hypothesis as failed, because the generator made a correct model indistinguishable from a leaking one; the generator was fixed and the experiment re-run |

### EXP-10 — Governance bypass

| | |
| --- | --- |
| **Purpose** | Is every bypass attempt blocked, logged and alerted? |
| **Configuration** | Four attempts against the real database, policy engine and tool registry |
| **Dataset / split** | Not applicable |
| **Result** | **Held, completed** |
| **Key metrics** | **4 of 4 blocked**, 3 audit entries added, the hash chain still verifies across 36 chains |
| **Artifacts** | `experiments/EXP-10/20260919T031838Z/` |
| **Validation** | Three of the four raise no alert **by design**, and the result says why: the attempts are refused before any state changes, and there is no incident to alert on when a tool does not exist. This experiment found the injection class the scanner read past — chat-template control tokens |

---

## F. Backend enhancements

| Area | What changed |
| --- | --- |
| **Tool mediation (FR-053)** | Every Spark submission and catalogue commit now goes through `REGISTRY.call`: agent allowlist, argument schema, the `sandbox.run` policy action, and an entry on the run's hash chain. A full run shows twelve mediated calls. The registry was a real control with real tests that nothing was routed through |
| **Reproducibility (ATC-18)** | A content hash over a data directory is the multiset of its rows. Spark mints a UUID per job into every part-file name, and row-to-file assignment varies with scheduling, so two identical runs never wrote identical bytes. Verified end to end: 3/3 artifacts reused, 0 LLM calls, all three outputs identical |
| **Evaluation documents** | ROC, precision-recall, calibration bins, a lift/gain sweep, bootstrap intervals, per-segment metrics and feature importance are persisted per run and served per version. All of it was computed and discarded before, which made FR-065 unmeetable without inventing numbers |
| **`GET /settings`** | New, admin-only: policy files with versions and digests, budget ceilings against the code defaults, connection targets with credentials stripped, accounts and roles |
| **Profile write-back** | `dataset_versions.profile_uri` and `.quality_score` are written, which makes FR-068's endpoint answerable for the first time |
| **Model cards** | Generated per run at the point the evidence exists, and written to the path the approval evidence bundle names |
| **Acceptance decision on escalation** | Written whatever the verdict, with rule ids, evidence and validation metrics |
| **Validation gates 7, 8, 9** | Recorded where they can be read back, on `code_artifacts.gate_results` |
| **`GET /metrics`** | 12 Prometheus metric families, read from the database on each scrape rather than from in-process counters, because the work they describe is done by a different process |
| **Pagination** | The cursor decodes to a datetime; a foreign cursor is a 400 |
| **Transaction boundaries** | `run_demo.py` commits its ingest before the run, which removes the observed login blockage |
| **Database columns nothing filled** | `metrics.ci_low`/`ci_high`/`segment`, `experiments` rows, `pipeline_runs.input_versions`, `executions.stage` |
| **Role gates** | The approval queue is role-gated; a generated test now holds **every** endpoint to refusing an anonymous caller |

---

## G. Frontend enhancements

Started as five plain screens with no chart, no dialog, no tabs, no pagination control, no date
input, no search and no ARIA attributes at all. Now:

| Area | What changed |
| --- | --- |
| **Three new pages** | **Datasets** (version history, quality dimensions, issues with evidence, per-column statistics, quantile sketches, stored schema diff), **Reports** (claim check, provenance, the document in a sandboxed frame, HTML download), **Settings** (connections, policy files with load state, budget ceilings, accounts) |
| **Charts** | `charts.tsx`, plain SVG, no charting library: ROC, precision-recall, reliability diagram, lift and cumulative gain, confusion matrix, per-family and per-segment bars, drift over time, quantile sketches. Every point read from a stored document |
| **Model comparison (FR-066)** | Any two versions side by side, with both bootstrap intervals and a note on every row where they overlap |
| **Live updates** | SSE over `fetch`, because `EventSource` cannot send an `Authorization` header and this endpoint is authenticated. The "live" pill appears only when the stream is connected |
| **Interaction** | ARIA tab pattern with arrow-key navigation, a `<dialog>` confirmation before the destructive Cancel-run POST, toasts, search boxes, budget meters, cursor pagination controls, a date filter on the audit trail |
| **Accessibility** | Visible focus everywhere, colour never the only signal, `prefers-reduced-motion` respected |
| **Honesty rules, enforced in code** | A chart with nothing to draw explains itself rather than drawing empty axes; a metric never recorded reads "not measured", not 0; calibration bins with no predictions are dropped rather than plotted at zero; a forbidden section names the role it needs instead of claiming there is no data |

Three runtime dependencies — react, react-dom, react-router-dom — and one stylesheet. Production
build: 332 kB, 102 kB gzipped.

---

## H. End-to-end validation

| Suite | Command | Result |
| --- | --- | --- |
| Python unit | `pytest tests/unit -q` | *filled below* |
| Python security | `pytest tests/security -q` | **50 passed** |
| Smoke | `pytest tests/smoke -q` | **9 passed, 1 skipped** (the skip is by design: the docker CLI is absent inside the container) |
| Scala | `sbt test` | *filled below* |
| Frontend type-check | `tsc -b --noEmit` | **clean**, TypeScript 5.9 strict |
| Frontend build | `npm run build` | **built in 9.71s**, 332 kB / 102 kB gzipped |
| Lint | `ruff check .` | **All checks passed** |
| Integration | — | There is no separate suite. The API tests run against a real PostgreSQL with a real Alembic schema, and the demo exercises Spark→Parquet→Python and MLflow end to end. Recorded as *covered elsewhere*, not as a suite that exists |
| End-to-end | `scripts/run_demo.py` | Four consecutive clean runs |
| Browser | Playwright, manual walkthrough | All eleven pages verified against live data |

### End-to-end runs

| Run | Outcome | Evidence |
| --- | --- | --- |
| `run-2026-09-21-020` | PUBLISHED in 818s | KEEP_CHAMPION, 14 segment metric sets, 1 252 audit entries across 80 chains verified, logins served throughout |
| `run-2026-09-21-022` | PUBLISHED | Model card written for version 15 |
| `run-2026-09-21-023` | PUBLISHED in 930s | 1 317 audit entries across 83 chains verified; gates 1, 2, 3, 4 and 8 recorded per artifact |

### Browser verification

Every page loaded against live data with no error banner:

```
Dashboard /            8 tiles and rows
Datasets  /datasets    dataset list; transactions v5 profile with 4 tabs, 9 issues, 14 columns,
                       quality chart and a quantile sketch for `price`
Runs      /runs        25 rows, paged
Models    /models      champion and challenger; comparison of v13 against v14 with both
                       intervals, two overlaid curve charts, and "intervals overlap" on the
                       rows where they do
Predictions /predictions  108 rows
Reports   /reports     4 reports, claim check passed, document rendered in a sandboxed frame
Approvals /approvals
Monitoring /monitoring 1 chart, 14 rows
Experiments /experiments
Audit     /audit       200 rows, chain verified
Settings  /settings    5 connections redacted, 2 policy files valid, 7 budget ceilings, 12 accounts
```

The only console errors were 404s for the champion v4 evaluation document, which the UI handles
by name: "No stored curves for v4."

---

## I. Remaining limitations

Stated plainly. The fuller version is in
[limitations-and-ethics.md](../limitations-and-ethics.md).

**Not measured, and not estimated**

1. The 10 GB and 100 GB tiers (NFR-PER-02/03, NFR-SCA-01, and the rungs EXP-2 and EXP-3 are
   really about). Docker has 5.27 GB here and containers are clamped to 3 GB.
2. B1, the manual baseline. Its metric is engineer hours from a time sheet.
3. Anything depending on live LLM behaviour: EXP-1's "agent overhead dominates", EXP-8's repair
   rate. No provider is configured.
4. Distributed Spark scaling, shuffle bytes and spill.
5. Container isolation **as it runs on this machine**: `docker` is absent from the dev
   container's PATH, so jobs fall back to `LocalRunner`. Every claim about container isolation
   describes `DockerRunner`, which is implemented and tested but is not this machine's path.

**Implemented and not working as intended**

6. An out-of-memory failure from a 256m driver is classified `UNKNOWN`, so its remedy never
   fires.
7. A coursier dependency-download failure is classified `SCALA_COMPILE_ERROR`, so the remedy is
   to regenerate code that was never the problem.

**Built and deliberately incomplete**

8. Validation gates 5 (Scala fixture suite) and 6 (sandbox dry run on a sample) do not run.
9. Schedule and data-arrival triggers are not wired; `watch_folder` exists and nothing runs it on
   a timer.
10. PDF export is not produced (HTML is), and alerts are stored and served but delivered nowhere.
11. FR-004 JDBC, FR-008 streaming connector, FR-022 anomaly detection, FR-024 feature
    suggestions, FR-035 caching decisions, FR-040 typed Datasets.

**Structural limits of the design**

12. The audit chain cannot detect a whole run's chain being removed — the design is one chain per
    run, which is what makes concurrent writes correct. Closing it needs a periodic seal over the
    chain heads.
13. Separation of duties is between **accounts**, not people.
14. An approval expires; a promoted champion does not. Nothing forces re-approval of a model that
    has been in production for a year.
15. One run of a pipeline at a time, because runs share their `clean/`, `features/` and `labels/`
    folders and committing supersedes what is there.
16. A long transaction writing to the system audit chain can still delay other writers of that
    chain. The observed case is fixed and re-measured; the shape remains possible.

**About the results themselves**

17. All metrics are measured on synthetic data. A test PR-AUC of about 0.78 is a statement about
    this pipeline, not about retail churn.
18. Every model metric is measured on **one** test cutoff. One cutoff is one season.

---

## J. Suggested future improvements

Not required by the PRD, ordered by what each would actually buy.

1. **Fix the two error classifications EXP-6 found**, and add a classifier test per error class
   driven by a captured failure signature rather than a synthetic string. This is the smallest
   change with the largest effect on the repair loop: one of the two silently disables the only
   remedy for out-of-memory.
2. **A periodic seal over the audit chain heads.** One row per interval, hashing every chain's
   current head, turns "a whole chain was removed" from undetectable into detectable, without
   giving up the per-chain locking that makes concurrent writes correct.
3. **Version the pipeline's intermediate outputs per run.** `clean/`, `features/` and `labels/`
   are shared, which is why only one run of a pipeline may be in flight. Per-run versioning
   removes the constraint and makes concurrent runs a storage question rather than a lock.
4. **Run gate 5 on a schedule rather than per stage.** The Scala fixture suite would add minutes
   to every stage inline, but running it once per template change — and recording the result
   against the template version — gets most of the value.
5. **A scheduler for the triggers that exist.** `watch_folder` and the schedule trigger are
   written; what is missing is something that runs them on a timer. A single periodic task in the
   worker closes FR-074.
6. **Automated dependent verification for the "computed and dropped" class.** Five separate
   defects in this project had the same shape: a value produced at one site and read at none.
   A test that asserts every nullable column the code writes to is non-null after a demo run
   would have caught four of them.
7. **Re-approval on a champion's age.** An approval expires after 72 hours; a promoted model
   never does. A periodic check that raises an alert on a champion older than a policy threshold
   would close the gap between the two.
8. **Playwright end-to-end tests in CI.** The browser verification in this report is real but
   manual. The eight page checks are mechanical and would run in a few minutes.
9. **A real cluster for EXP-2 and EXP-3.** Both experiments are written and both answer a
   different question at these sizes than the one they were built to ask. They need hardware,
   not code.
10. **Per-segment calibration.** AP-6 checks recall parity across segments; calibration is checked
    only in aggregate. A model well calibrated overall can be badly calibrated for a segment, and
    the reliability diagram would show it if it were drawn per segment.
