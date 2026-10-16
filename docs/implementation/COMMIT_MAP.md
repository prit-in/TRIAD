# Commit map

Before the repository was first published (16-10-2026), its history was rewritten at the team's
request, so that GitHub lists one contributor, the team's account `prit-in`:

- the `Co-Authored-By` trailer was removed from every commit message;
- the author and committer e-mail was set to the account's noreply address.

Nothing else changed: each commit keeps its files, author name, dates and message text, and the
last commit's tree is the same object (`290ac8d`) before and after. Every commit ID changed, though.

The experiment records (`experiments/*/environment.json`, the `NOTE.md` files) and some
implementation notes were written before the rewrite and cite the original IDs. They are records and
stay as written. This table pairs each original ID with the published one, in order.

| # | Original | Published | Subject |
|---|---|---|---|
| 1 | `37f4366` | `0ce13ce` | Phase 1: pinned toolchain, core library and a passing smoke test |
| 2 | `6c78ac3` | `6685e1c` | Phase 2: schema migration, ingestion and Spark profiling |
| 3 | `078c20c` | `dc2995c` | Stop tracking .claude-flow tooling artefacts |
| 4 | `437f550` | `4f88c05` | Phase 3: Scala fixtures prove leakage safety and row accounting |
| 5 | `7e3a579` | `958bd49` | Phase 4: leakage-safe splits, candidate registry and evidence-based selection |
| 6 | `7a4c97a` | `f7b2cd4` | Phase 5a: template slots, nine gates, task queue and sandbox runner |
| 7 | `61a7850` | `6b64b81` | Phase 5b: governance, model registry and the bounded stage loop |
| 8 | `0892388` | `4fa2556` | Phase 5c: deterministic routing, pipeline stages and claim-checked reporting |
| 9 | `151acd7` | `1a32036` | Phase 5d: predictions, explanations and the end-to-end demo driver |
| 10 | `37d85c9` | `1814785` | Phases 5 and 6: the end-to-end governed run passes, plus drift and retraining |
| 11 | `d22e75f` | `d39e39c` | Remove a stray file and state the backend's real state |
| 12 | `b5a47f0` | `5cc4a09` | Phase 7a: the REST API, 37 endpoints under /api/v1 |
| 13 | `627e3b5` | `eb44808` | The orchestrator worker, and a real fork in the audit chain |
| 14 | `9c3e059` | `c1cbd94` | Consolidate the ad-hoc probes into a deployment smoke check |
| 15 | `79550ce` | `f12e51b` | Tidy the smoke check after the consolidation |
| 16 | `8d1ebb2` | `aa975ee` | Phase 7b: the dashboard, five pages against the real API |
| 17 | `5ed6595` | `c18a115` | Ignore browser-automation scratch |
| 18 | `84b1c70` | `d8b54bc` | Tests scoped to their own queue stage, and the status brought up to date |
| 19 | `b3c8fc9` | `c8eed97` | Phase 8a: the hyper-parameter search the selection procedure asked for |
| 20 | `5e19eae` | `230b790` | Phase 8b: the prompt-injection test, and a card number it found in the clear |
| 21 | `19c17f8` | `65dc69d` | Phase 6 completed: a fired trigger now starts a run |
| 22 | `70785cf` | `c6af0c9` | Phase 8c: the requirements traceability matrix |
| 23 | `dcceff3` | `a2696c0` | Audit fix: an abandoned run blocked its pipeline for ever |
| 24 | `c8b415b` | `036abdf` | FR-053: the tool registry now mediates the runtime path |
| 25 | `5646069` | `1bbc141` | EXP-9 and EXP-10, and the injection class EXP-10 found |
| 26 | `9c12e5e` | `60dee00` | EXP-7, and three defects the experiments found by running the thing |
| 27 | `3f77761` | `eb62217` | EXP-4 complete, EXP-1 baselines, and two remedies that were calculated and ignored |
| 28 | `222add9` | `b419663` | EXP-2, EXP-3 and EXP-6: the three scale and resilience experiments |
| 29 | `a2c3f60` | `7c6e88d` | Replay mode, /metrics, curve data, and three more columns nothing filled |
| 30 | `97bf5ee` | `81a7448` | Two defects the browser walkthrough found: an empty stage timeline and a stale tile |
| 31 | `2780f30` | `c1c87e9` | Charts from stored data, the reproducibility fix, and an import guard |
| 32 | `e7e988e` | `1217d9a` | Stop tracking the TypeScript build cache |
| 33 | `6f40edb` | `f3da40c` | The fairness gate was vacuous, and the reproducibility fix needed a second half |
| 34 | `73c1756` | `e2a85b4` | A run in progress blocked every login, and my own fairness fix leaked into the model |
| 35 | `6f6dd60` | `cfdf58c` | ATC-18 passes end to end, and AP-6 has 14 segment sets where it had none |
| 36 | `27787fa` | `1b4d6a2` | Model charts render, and a mistyped key made one of them silently empty |
| 37 | `35ac322` | `1d3703e` | Cursor pagination, and the long-transaction pattern in two more places |
| 38 | `6878ff5` | `15bf5e3` | Two experiments were measuring their own bugs |
| 39 | `e667039` | `f453b2c` | Three more reads of keys that were never there |
| 40 | `1e0f27a` | `ce8db36` | The only paginated endpoint had always returned 500 on page two |
| 41 | `58babd8` | `9347c23` | EXP-5 threw away half its evidence and claimed a trend from one point |
| 42 | `d63186f` | `aaa2bd2` | A live run now updates itself, over fetch because EventSource cannot authenticate |
| 43 | `0d471ce` | `ef9d3e6` | EXP-8 now counts 15, EXP-3 and EXP-2 ran at all |
| 44 | `15e701d` | `390b073` | The three PRD pages that were never built, and two more columns nothing filled |
| 45 | `2d0b1fd` | `a039cdc` | EXP-5 stopped three times and recorded the number for none of them |
| 46 | `40bce8d` | `3a3fe52` | The documents now say what was run, and .gitignore stops ignoring a shell command |
| 47 | `ed4e604` | `bf0747d` | The twelve documents PRD section 58 asks for, and the model card nothing wrote |
| 48 | `c58f3ad` | `7d09ac0` | Four of the nine gates ran, and the documents said nine |
| 49 | `2591623` | `c184724` | Endpoint authorisation and secret scanning, generated rather than listed |
| 50 | `c343b24` | `05db7a7` | The completion report, with every number read back rather than recalled |
| 51 | `226cd41` | `6eb2a9c` | A segment's metric was the model's headline metric, and a refusal left no trace |
| 52 | `0ec5ea0` | `f08ebae` | An upload named ../../../../tmp/x.csv wrote to /tmp |
| 53 | `cbf86a3` | `bf6aba7` | The audit trail was append-only in the documents and writable in the database |
| 54 | `b46d8f5` | `7b6e347` | EXP-5's open question, answered by measurement |
| 55 | `a37a049` | `7175922` | FR-060's other half: the options a decision refused |
| 56 | `04a1049` | `01f7847` | A guard for the bug class that stopped two experiments from ever running |
| 57 | `e4a6c70` | `c20d536` | Five tests the last three commits broke, and a claim one of them made that was false |
| 58 | `b6b52f2` | `54698a6` | Satisfy SIM102 in the call-signature guard |
| 59 | `24e1be0` | `c7f3eea` | The four faculty documents, read and reconciled with the PRD |
| 60 | `499aa8d` | `6e42b67` | EXP-6's two misclassifications, fixed where they started |
| 61 | `9f89a24` | `6bfda2c` | The second audit: 19 confirmed defects, three Musts completed, every fix tested on the live path |
| 62 | `88a3d72` | `f61248e` | Spark jobs run in containers now; and four defects the first end-to-end run in two weeks found |
| 63 | `95f506a` | `9c48870` | Six end-to-end runs in containers, what they found, and the research manuscript begun |
| 64 | `0f00551` | `598f2a2` | Promotion despite a failed check (PRD 30), the AP-6 threshold review (PRD 19.4), and an enforced state machine |
| 65 | `b11b6b0` | `a0cfd66` | The override path, tested and in the dashboard; cancellation allowed from any state |
| 66 | `36038cb` | `cf340e2` | Spark stage metrics, data anomalies, approver evidence, notifications and PDF export; four defects run 9 found |
| 67 | `d8e6363` | `15098cc` | Configuration optimisation loop (FR-037), experiment protocols for the re-run, and the manuscript brought up to date |
| 68 | `f22340b` | `62e1d73` | Manuscript sections 3, 18, 20, 21 and 23 brought up to date and within their word ranges |
| 69 | `006f250` | `4a162b9` | Charts for Figs 21-25 and 28 from stored outputs at 600 DPI; trust score covers override approvals |
| 70 | `2f34c7e` | `af9c12c` | Experiment re-run outputs so far, the batch runner, and the 08-10-2026 handoff |
| 71 | `b21f6cd` | `13fb273` | Toasts and the override note without side-stripe accents |
| 72 | `d4a3a32` | `b18718e` | Memory caps that hold, an escalated EXP-1 that still measures, and ceilings for isolated runs |
| 73 | `46dd494` | `3f6a1eb` | Prices fixed by try_cast and quarantine, rollback that restores only the previous champion, and a browser-only demo |
| 74 | `e250e05` | `f3ec779` | Front matter, reliability and trust, and conclusion drafted; results sections for EXP-3, EXP-7 to EXP-10 and the noise floors |
| 75 | `6e8f508` | `bdae77c` | Generator builds columns and writes one table at a time, with the same draws; EXP-2 names every outcome |
| 76 | `b78259c` | `a99b204` | Manuscript assembler on the faculty template, with Word's page numbers and PDF; sections brought to their word ranges |
| 77 | `335869b` | `961f245` | EXP-8 runs the fixture gate on faults the static gates miss, with an unmutated control (DEC-38) |
| 78 | `4925cf6` | `a51cb28` | Charts and Table 10 read the fixture gate and every EXP-2 outcome; the README says how the manuscript is assembled and counted |
| 79 | `e820f9d` | `7f70636` | EXP-2 complete: B3 did not finish at 100 000 customers while B2 did, so the hypothesis is refuted on this node |
| 80 | `43a996e` | `ee714d8` | Diagrams redrawn so no edge crosses a box; all 26 exported at 630-706 DPI; Section 15.2 written from EXP-2 |
| 81 | `29556a9` | `6479f3a` | CleanJob compiles when generated; templates now held to the generated code's no-warnings rule |
| 82 | `da30279` | `505c92b` | Figures and text made to agree: review findings fixed in both directions |
| 83 | `bf5a409` | `ec64040` | Trust score of a run whose generated job failed its gates: 0.67, beside the published run's 1.00 |
| 84 | `550a392` | `1db7c08` | EXP-1's like-for-like refit uses P's column roles; the cold-cache run kept as a record |
| 85 | `e1e4f2a` | `9c1a9c0` | EXP-1 complete: P 989 s with gate 5 reused, B3 71 s, B2 5.5 s; like-for-like 0.776 on P's table |
| 86 | `842df6f` | `cf6058f` | EXP-1 written into Sections 15.1, 16.1, 19, 22, the executive summary and EXPERIMENTS.md |
| 87 | `e577213` | `497c66e` | A renamed required column is proposed as a mapping and applied only once approved |
| 88 | `d4dc449` | `139a270` | A cleaning plan over its limit pauses for a Data Engineer instead of escalating |
| 89 | `4e895d0` | `44e1867` | EXP-4 complete: every fault detected and handled, no mapping applied without a person |
| 90 | `79a5d4c` | `9ea4540` | Cancelling a run withdraws its open approvals; EXP-5 measures its own first claim |
| 91 | `7f99d42` | `be3c41b` | An out-of-memory in a sandboxed Spark job can now be repaired |
| 92 | `8f612a8` | `cea8442` | A killed sandbox job says who killed it, so the right remedy follows |
| 93 | `0a82914` | `93f6936` | Test doubles match the runner's real interfaces; who-killed-it is tested |
| 94 | `3fe646f` | `d805ef5` | EXP-6 checks that each fault landed, and measures skew where it can act |
| 95 | `598e125` | `49eb9f7` | DEC-39 to DEC-41: mapping proposals, the cleaning pause, and a repairable out-of-memory |
| 96 | `5e0a2e1` | `830860c` | Table 10 joins Section 15; EXP-5's 09-10 attempt kept as a record |
| 97 | `25aef18` | `aa554c5` | Section 15.4 opens with EXP-4: every schema fault detected and handled |
| 98 | `54000db` | `1ddd3d9` | EXPERIMENTS.md: EXP-4 completed and held, with the runs that shaped its protocol |
| 99 | `73d036e` | `ff0c501` | Approvers come from the action's terms; a mapped file is asked about once |
| 100 | `7bb592c` | `5eb8f83` | EXP-5, EXP-6 and EXP-8 measure what they claim; the memory remedy obeys the allowlist |
| 101 | `b0c7c8a` | `d4b8fda` | Traceability: ATC-06 met (mapping proposals); FR-007 notes the contract row checks have no caller |
| 102 | `459d663` | `531236b` | EXP-6's memory arm runs where memory matters; experiments get production's JVM heaps |
| 103 | `6503afa` | `83c8e74` | IMPLEMENTATION_STATUS: 10-10-2026 progress, the review, and the running batch |
| 104 | `2b02ec8` | `42f5dca` | EXP-9 and EXP-10 re-run on the current code: both completed and held, same measurements |
| 105 | `ab6e357` | `9bf9823` | EXP-9 and EXP-10 cited from their 10-10 re-runs (143 audit chains verified) |
| 106 | `2892452` | `be2657a` | Charts and Table 10 read EXP-6 and EXP-8 as they now measure |
| 107 | `7da4ce3` | `268fe59` | EXP-5 completed: nulls are quarantined or harmless as claimed; PR-AUC did not decline |
| 108 | `884abc3` | `aaacd03` | EXP-5 written into Section 15.4 and EXPERIMENTS.md: completed, did not hold |
| 109 | `16766fa` | `8a07b4b` | Section 13: Table 8 and the protocol rules describe the experiments as they now run |
| 110 | `a303cb9` | `28b208d` | EXP-6 partial, no verdict: the kill is absorbed identically; no out-of-memory lands |
| 111 | `210071d` | `ae9f1ee` | sbt and coursier use the cache volumes; EXP-8's failed attempt kept as a record (DEC-43) |
| 112 | `1c2c0fd` | `fe0ad7d` | EXP-4 re-run on the reviewed code: completed and held, the same outcomes |
| 113 | `3b4c91c` | `2e3203b` | Frontend: upload a dataset version; approvers see a mapping's and a cleaning plan's evidence |
| 114 | `223e777` | `f74bcba` | EXP-6's feature-table difference explained: last-bit floating-point sums under spill |
| 115 | `fe29b8d` | `85704b4` | EXP-6 written into Section 15.4 and EXPERIMENTS.md: partial, no verdict |
| 116 | `3b86d6d` | `a697822` | Section 22: the data-fault finding from EXP-4, EXP-5 and EXP-6 |
| 117 | `76c5edc` | `8fbab92` | Section 20.6: the faults this node cannot produce, and reproducibility under one plan |
| 118 | `5d64152` | `7b694aa` | EXP-8 partial: 19 of 20 faults stopped before any data is read; the breaker trips at attempt 2 |
| 119 | `db6e47d` | `86de997` | EXP-8 written into the manuscript: 19 of 20 stopped before data; fixtures catch 4 of 5 wrong columns |
| 120 | `f6f1bc7` | `2760a63` | EXPERIMENTS.md: EXP-8 with gate 5 and the measured breaker, and how it got there |
| 121 | `6826ecd` | `e2d9c95` | Traceability: ATC-10 met and measured (EXP-8); ATC-07 partly met (no out-of-memory lands here) |
| 122 | `0e48f87` | `dcce01f` | EXP-1 re-run on warm build caches: P 831 s, gates about 395 s; the same models and verdict |
| 123 | `91b3dcc` | `0605cec` | EXP-1's governance cost corrected to the warm-cache run: 831 s, gates about 395 s |
| 124 | `79defd8` | `13d9360` | Figs 21-25 and 28, Tables 10 and 11 rebuilt from the 10-10 experiment results |
| 125 | `3e992e0` | `537e775` | EXP-5's interrupted start of 09-10 kept as a record, with a note |
| 126 | `5560e40` | `f2bcafb` | IMPLEMENTATION_STATUS: every experiment run on the current code, the day's fixes, next steps |
| 127 | `c6e6565` | `9e584e9` | B3 ends its Spark JVM before modelling; P runs from one module for EXP-1 and EXP-2 (DEC-44) |
| 128 | `2a3a837` | `fd3510a` | EXP-7 runs drift to approval on the platform itself (DEC-44, DEC-45) |
| 129 | `04b043c` | `62612ed` | Cluster path made correct and exercised; configs no longer shared; EXP-7 completed (DEC-46, DEC-47) |
| 130 | `7eb3cd1` | `7c5474d` | EXP-6's cluster arm piloted to a real executor loss mid-task (DEC-46) |
| 131 | `0004fd5` | `5e162e7` | Fixes from the review of 11-10: retrains follow their parent's data, verdicts say untested (DEC-48) |
| 132 | `ae05586` | `9b9beb6` | Results of the overnight batch of 11-10-2026 on the reviewed code |
| 133 | `fed0143` | `74fa6c9` | EXP-7 reads the comparison from the acceptance decision; B3 hands off through parquet; a failing baseline is its result (DEC-49, DEC-50) |
| 134 | `ebd0b36` | `7387c4d` | Re-runs of EXP-7, EXP-1 and EXP-2 on DEC-49 and DEC-50 |
| 135 | `0aa308a` | `a1771f0` | The sandbox outlasts a stalled container engine; an engine outage is resubmitted, not regenerated; records carry their own time (DEC-51, DEC-52) |
| 136 | `dce4ed9` | `0124958` | The worker releases its idle JVM heap before the clean job too (DEC-52) |
| 137 | `bf2f6d9` | `06a24c8` | The dashboard and API close the gaps against PRD 25-26: correlation ids, readable errors, resume, audited idempotent export (DEC-53) |
| 138 | `8cda4b9` | `3242922` | EXP-1's run on a busy host and EXP-2's unfinished run of 12-10-2026, kept with notes |
| 139 | `4ddde4f` | `442dcc8` | The report's Index and Contributions tables follow the course's reference layout; appendix tables captioned |
| 140 | `de09c40` | `a796a6d` | EXP-1 re-run on a quiet host: held, 0.957 of P's time beyond B3's |
| 141 | `772f86a` | `4da2f34` | Section 15 rewritten on the final runs (EXP-2 subsection pending) |
| 142 | `3442102` | `3421c65` | A refused workspace write while generating is a classified, retried stage failure (DEC-54) |
| 143 | `39625b0` | `dfa3c02` | A run's generated Scala is built, submitted and diagnosed as its own (DEC-55) |
| 144 | `9b3999b` | `959c3b3` | Sections 16, 19, 20 and 22 on the final EXP-1, EXP-3 and EXP-8 runs; EXP-2 statements marked pending |
| 145 | `51fd724` | `4c720b4` | The experiment batch waits out a busy engine and reads only its own run's result |
| 146 | `c632abc` | `2c40277` | Status of 15-10-2026: EXP-2's third re-run in progress, DEC-55, orchestrator stopped for it |
| 147 | `222d7d6` | `0f9822b` | EXP-2's third run kept with a note: the VM froze under P at 200 000 customers; EXP-2 concluded on three attempts |
| 148 | `d4e573b` | `e4278a0` | EXP-2 written up: B2 fails first at 200 000 customers, B3 finishes, P does not on this node |
| 149 | `dfa3cbe` | `f004f55` | Charts and tables rebuilt on the final runs; Fig. 22 draws P; thousands grouped as the text groups them |
| 150 | `6330b37` | `fe35040` | The assembled report's front matter matches the course's reference layout |
| 151 | `03e73e5` | `7b26f4b` | Chart text prints at 8 to 10 pt, as the template asks; abbreviations defined at first use |
| 152 | `493b14e` | `f0b4f8c` | Compliance checklist verified against the built report: 46 done, 4 partial, 3 awaiting the team, 1 pending |
| 153 | `91c5913` | `5a1ee49` | The dashboard's success rate counts only runs that ended on their own |
| 154 | `fd31b7f` | `967110e` | Status of 15-10-2026: complete except what needs the team's details |
| 155 | `d919d44` | `0efd804` | The report carries the team: cover, author line, contributions, biographies and repository link |
