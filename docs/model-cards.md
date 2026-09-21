# Model cards

For ML engineers and analysts. A model card is what an approver reads before deciding a
promotion, and what anyone reads later to find out what a deployed model actually claims.

## Where they are

A card is generated per run, not written by hand:

| Where | What |
| --- | --- |
| `reports/<run_key>/model_card.md` in object storage | The card itself, markdown |
| `model_versions.model_card_uri` | The pointer, per version |
| The approval evidence bundle | `model_card_uri`, which the governance policy lists as required evidence for `model.promote` |
| MLflow | The run and its parameters, metrics and artifacts, under the registered model |

```bash
docker compose -f docker/compose.yaml exec -T api \
    cat /data/triad/reports/<run_key>/model_card.md
```

## What is in one

`PRD 17.10`, rendered by `ModelCard.to_markdown`:

| Section | Contents |
| --- | --- |
| Header | Model name, version, algorithm, creation time, feature-set version |
| Intended use and limits | What the model is for, in a sentence, and the uses it is explicitly not for |
| Churn definition | The label definition and horizon, plus the training cutoffs |
| Candidate comparison | **Every** family that was screened with its validation PR-AUC, and which was selected |
| Metrics | The test-split values |
| Calibration | Method, expected calibration error, Brier skill, and the chosen threshold |
| Segment results | Per-segment metrics, where a segment had enough positives to report |
| Top drivers | Up to ten, **with the method named** — permutation importance and SHAP are not the same claim — and labelled as associations, not causes |
| Known limitations | Including the single test cutoff, and why importance is absent when it is |
| Approval | The decision, once one has been made |

The candidate comparison is the section that makes the card worth reading. A card listing only
the selected model says "this is the model"; a card listing every family that was screened and
lost says *why* it is the model, and lets a reader disagree.

## Two properties worth knowing

**The card is written where the evidence is, not where the approval is.** It is built at the end
of training, when the split, the candidates, the calibration and the importance are all still in
hand. Building it at the approval would mean reconstructing all of them, and a reconstruction is
a second chance to get it wrong.

**It used to not exist.** `ModelCard` and `write_model_card` were written, complete with this
renderer, and never called by anything — while `model_card_uri` was set to a path and the
governance policy listed the card among the evidence required to promote a model. An approver
following the link got nothing at all. The wiring is now covered by a test that asserts the
pipeline calls the writer, because the rendering was never the part that was missing.

## Reading one honestly

- The metrics are measured on **one test cutoff**. One cutoff is one season, and a model that
  looks better in March is not necessarily better.
- The intervals in the evaluation document (and on the Models page) are bootstrap intervals.
  Two overlapping intervals mean this test set cannot separate the versions, whatever the point
  estimates say.
- Drivers are what the model leans on. Nobody has shown that changing one changes the outcome.
- Everything in this deployment was trained on synthetic data (ADR-0002), so a card here
  describes the pipeline rather than retail churn. See
  [limitations and ethics](limitations-and-ethics.md).
