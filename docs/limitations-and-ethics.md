# Limitations and ethics

For anyone deciding what to conclude from this system, or whether to act on its output. It is
the document that says what TRIAD does **not** do.

## What this is

A case-study implementation of a governed, agentic analytics platform, built and measured on a
single machine with 8 cores and 5.27 GB of memory available to Docker (ADR-0002). It is complete
enough to run its whole loop end to end and to be wrong in ways that can be detected, which is
the property the case study is actually about.

## What the data is

**Synthetic, from this project's own generator** — not UCI Online Retail II, which the PRD
suggests. Online Retail II has transactions and nothing else: no customer table, no web
sessions, no support tickets, no returns. Two thirds of the feature catalogue would have nothing
to read and the churn definition could not be computed. The generator produces all eight tables
and carries every fault class the PRD names: null keys at 3%, duplicates at 1%, invalid prices,
and injection strings treated as data.

Nothing in this repository contains real customer data, and none has ever been processed by it.
The customer identifiers are generated; `mask_customer_ref` exists for the case where they would
not be.

**A synthetic dataset cannot validate a claim about real customers.** The model's PR-AUC of
around 0.78 is a measurement about generated behaviour. It says something about the pipeline and
nothing about retail churn.

## What was not measured

These are absent, not estimated, and the distinction is enforced by the experiment harness —
which refuses a verdict on a hypothesis when no measurement was recorded:

| Not measured | Why |
| --- | --- |
| The 10 GB and 100 GB tiers (NFR-PER-02/03, NFR-SCA-01, and the tiers EXP-2 and EXP-3 are really about) | 5.27 GB of Docker memory. Those workloads would fail for every configuration including the ones the hypotheses expect to finish: three failures and no information |
| B1, the manual baseline | It is defined as a person following a runbook and its metric is engineer hours from a time sheet. There is no second person here, and inventing hours would fabricate the central comparison |
| Anything depending on live LLM behaviour — EXP-1's "agent overhead dominates", EXP-8's repair rate | No provider is configured (ADR-0003). A measured zero would describe the missing provider, not the loop |
| Distributed Spark scaling | Two Compose workers on 8 shared cores would measure scheduling overhead, not scaling. `local[N]` varies the same knob honestly |
| Shuffle bytes and spill | They come from Spark event logs, which are disabled here |
| Container isolation as it runs on this machine | `docker` is absent from the dev container's PATH, so jobs fall back to `LocalRunner`. Every claim about container isolation describes `DockerRunner`, which is implemented and tested but is not the path this machine takes |

## What is implemented and does not work as intended

Two error classifications, both found by EXP-6 and both still open:

- An out-of-memory failure from a 256m driver is classified `UNKNOWN`, so the remedy that exists
  for it never fires.
- A coursier dependency-download failure is classified `SCALA_COMPILE_ERROR`, so the prescribed
  remedy is to regenerate code that was never the problem.

## What the model must not be used for

The churn model here predicts **who is likely to stop buying**, from behaviour. That is a narrow
claim and it is easy to stretch:

**It is association, never cause.** The top reasons shown per customer are the features the
model leans on. Nobody has shown that changing one changes the outcome. A retention campaign
built on them is an experiment, not an intervention with a known effect, and it needs its own
control group.

**It must not be used to set prices or terms per customer.** A churn probability is not a
willingness-to-pay estimate, and using it as one turns a retention tool into differential
pricing.

**It must not be used for anything about a person outside this commercial relationship.** Credit,
employment, insurance, housing — none of these is a use this model was built, validated or
governed for.

**A score is not a person.** `risk_band` is a bucket of a probability with two arbitrary
thresholds (0.66 and 0.33). It exists to sort a contact list.

## Fairness, and what the segment checks do and do not do

AP-6 compares recall across segments — `country`, `loyalty_tier`, `acquisition_channel` — and
escalates when the gap exceeds the policy's threshold. Three honest qualifications:

1. **Those attributes are joined for evaluation only and never trained on.** A segment check
   asks whether a model built from *behaviour* treats groups differently; a model handed
   `country` as an input answers a different question. Getting this wrong once moved every
   family's PR-AUC, which is how it was caught.
2. **A thin segment is left out rather than reported.** A recall computed on a handful of
   positives is a number nobody should act on, so it is not shown.
3. **Recall parity is one fairness definition among several, and they are not simultaneously
   satisfiable.** Choosing recall parity is a choice, made in the acceptance policy where it can
   be reviewed, not a solution to fairness.

The segments available are the ones the data has. Nothing here measures fairness across
attributes the data does not carry, and their absence is not evidence of their irrelevance.

## Privacy and data handling

- Customer identifiers can be masked before reaching a prompt (`mask_customer_ref`), and
  redaction is applied to logs and stored prompts.
- Content that came from data is fenced before it reaches a prompt, and scanned for injection —
  including chat-template control tokens, which the scanner originally read straight past
  (EXP-10).
- Passwords are argon2 hashes and never leave the database. `GET /settings` strips every
  credential from the connection targets it shows, and a test asserts the whole payload carries
  none.
- The sandbox that runs Spark jobs has no route to the internet, the LLM, governance, unrelated
  secrets, or the database.
- **The audit trail is not anonymised.** It records who did what, deliberately, and it is
  admin-only. Any deployment holding real data needs a retention policy for it — and pruning it
  by row breaks the chain, so whole chains must be archived rather than rows deleted.

## Governance limits

- **The chain does not detect a whole run's chain being removed.** The design is one chain per
  run, which is what makes concurrent writes correct; detecting a removed chain needs a periodic
  seal over the chain heads, which is not built.
- **Separation of duties is between accounts, not people.** Two accounts one person controls
  satisfy every check here. That is an operational control, not a technical one.
- **An approval expires but a promoted model does not.** Nothing forces re-approval of a
  champion that has been in production for a year.
- **The policy file is the root of trust.** Anyone who can commit to it can widen autonomy. That
  is why `policy.change` is forbidden to agents and why the file is reviewed like source — but
  it is a process control.

## Reproducibility, and its cost

A content hash over a data directory is the multiset of its rows, because Spark's part-file names
carry a per-job UUID and row-to-file assignment varies with scheduling. This makes ATC-18 — a
replay with no LLM calls producing identical outputs — actually satisfiable, and it has been
verified end to end. The cost is a read-back on commit, O(rows). The cheaper alternative, a
deterministic sort before write, is recorded in the code as the trade it is.

Seeds are fixed and recorded. The toolchain is pinned and read from the image. `input_versions`
records exactly which dataset versions a run read, so a replay reads the same bytes rather than
"the current version".

## Attribution

- Apache Spark, PostgreSQL, MLflow, scikit-learn, XGBoost, Optuna, FastAPI, React and the rest
  are used under their own licences.
- The PRD this implements is `TRIAD_PRD_Agentic_Data_Science_Case_Study.md` in the repository
  root.
- The dataset is generated by `datasets/synthetic/generator.py`. No third-party dataset is
  redistributed here, and large datasets are deliberately not committed to git.

## How to read a claim in this repository

Every number in these documents was read back from something the system wrote — a `result.json`,
a database row, a run summary. Where a measurement was not taken, the documents say so.

Three experiment results were **deleted after the fact** rather than kept, and each is described
in the [experiment log](implementation/EXPERIMENTS.md) with what it actually measured: EXP-8's
"20 of 20 faults detected" was fifteen real detections and five jobs failing to compile on the
experiment's own invalid Scala identifier; EXP-5's "hypothesis held" was a monotone trend
asserted from a single surviving data point; EXP-9's first run measured a generator in which a
correct model and a leaking one are indistinguishable.

A requirement marked *met* in the [traceability matrix](implementation/REQUIREMENTS_TRACEABILITY.md)
means an execution produced the evidence, not that a test asserted the intention. The distinction
is not pedantic: most of the defects this project found had passing tests around them.
