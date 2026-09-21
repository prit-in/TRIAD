# User guide

For anyone using the interface. Eight pages, one per question people actually ask.

Sign in at <http://localhost:5173>. Your role decides what you see; two pages are admin-only and
one page answers a coarser question for viewers rather than refusing them.

## Reading any number on any page

Every figure carries a **provenance badge**, and the three are not interchangeable:

| Badge | Means |
| --- | --- |
| **Calculated** | Measured from stored data by a deterministic computation — a count, a rate, a hash |
| **Model output** | Produced by a trained model: a probability, a metric, a ranking |
| **AI interpretation** | Written by a language model. Nothing in this deployment carries it, because no provider is configured |

Two phrases mean different things and are used precisely:

- **"not measured"** — the pipeline never recorded this. It is not zero.
- **"No data yet"** — there is nothing to show, said in words rather than drawn as an empty
  chart, because an empty chart reads as a value of zero.

A chart with nothing to draw explains itself instead of drawing axes around nothing. A gap in a
series is drawn as a gap and never interpolated.

## Dashboard

Runs today, success rate, open alerts, pending approvals, and the recent runs table. Every tile
is a query rather than a stored counter, so a tile can never drift from the thing it counts.

Start here to find a run, then open it.

## Datasets

The list, with each dataset's latest version and its quality score. Open a dataset for its
version history — row counts, checksums, and which versions were profiled — and then a version
for its profile:

- **Quality** — the four dimensions (completeness, validity, uniqueness, consistency) and the
  overall score, with duplicate counts on the declared grain.
- **Issues** — every issue the profiler raised, with its severity and the **evidence** it
  raised it on, because "quality issue" is not something anyone can act on and "233 rows have no
  customer_id (2.9%)" is.
- **Columns** — type, semantic type, null rate, distinct count, range and outliers per column.
  Select a column for its most frequent values, or, for a numeric column, a **quantile sketch**.
  That is deliberately not called a histogram: the profile stores p25, p50, p75, p95 and p99, so
  bin counts were never measured and are not drawn.
- **Compared with the previous version** — the schema diff the profiler recorded: columns added,
  removed or retyped, the row-count change, and any rename it *proposes*. A rename is always a
  proposal and always needs approval.

A version with no stored profile says so. So does a role that may not read one.

## Runs

Every run with its state, newest first, paged. Open one for:

- a **stepper** showing where it is, and a stage timeline with attempts and durations;
- **live updates** — stage changes arrive over the event stream rather than by refreshing. The
  "live" pill only appears when the stream is actually connected, because it is a claim;
- the **agent decision feed**: every routing, policy and acceptance decision with the rule ids
  and the evidence behind it;
- **generated code** with the nine gates it passed;
- **budget meters** — attempts, time and cost against the ceilings the run started with;
- **Cancel**, behind a confirmation dialog, and **Resume** after an escalation.

## Models

Champion and challenger side by side with their headline metrics, and every version underneath.

Per version, from the evaluation document the run wrote:

- **Quality** — ROC and precision-recall curves with a chance line and the base rate, and the
  confusion matrix at the chosen threshold.
- **Calibration** — a reliability diagram against the perfect diagonal, and lift and cumulative
  gain against "no targeting benefit". Bins no prediction fell into are dropped rather than
  drawn at zero.
- **Candidates** — validation PR-AUC by family, with the selected one marked, and the headline
  test metrics with their 95% intervals.
- **Segments** — recall by segment. A segment too thin for its recall to mean anything is left
  out rather than reported as a number nobody should act on.
- **Importance** — global feature importance with its **method named**, because permutation
  importance and SHAP are not the same claim.

**Compare two versions** opens a side-by-side table with both intervals and a difference column.
Where the two 95% intervals overlap it says so: a gap this test set cannot distinguish from none
should not be read off three decimal places.

A version trained before evaluation documents existed has none, and the page says that rather
than showing blanks.

## Predictions

The scored customers: probability distribution, risk bands, and a filterable table with the top
reasons per customer. Filter by risk band, score date, model version or minimum probability, and
export — the export is audited.

**Viewers see aggregates**: counts by band, a mean probability and the score dates, never
customer rows.

Reasons are labelled as association, not cause. A feature that ranks highly is one the model
leans on; it is not a lever anyone has shown moves the outcome.

## Reports

The generated business reports, each with its **claim check**. The check shows what was verified
and, more importantly, what it refused: numbers with no fact sheet entry, placeholders never
filled in, and causal phrases the evidence does not support.

A report that fails the check is never written, which is why a passing check is the normal case —
the interesting one stopped the run before it got here.

The document itself renders in the page, and downloads as HTML. PDF is not produced, and the page
says so where a PDF button would be rather than offering one that fails.

## Approvals

The queue, and the evidence page for each decision. One page per decision, on purpose: an
approver should never have to leave it to decide. It carries the metric deltas with intervals,
the segment checks, drift, the feature changes, the model card and the risk notes.

Deciding needs a **comment**, and it is mandatory. The approver must be a different person from
the requester — the API refuses otherwise, whatever the interface allows.

## Monitoring

Drift over time as the highest feature PSI per scoring run, drawn against the flagging threshold,
because a PSI without its threshold is a number with nothing attached. Underneath: open alerts
with acknowledgement, the retraining decisions the policy recorded, and every drift report.

One honest oddity is annotated on the page: a month-of-year feature is constant inside a
single-cutoff scoring window, so its PSI is enormous and says nothing about behaviour. The flag
still fires, because a feature pinned by a broken source looks exactly the same.

## Experiments

The ten case-study experiments as the harness wrote them: the hypothesis, the verdict, the
conditions, the measurements and the limitations. Experiments with no verdict say so.

## Audit (admin)

The hash-chained trail, filterable by action, actor, run and date. **Verify** recomputes every
chain and either confirms it or names the entry where it breaks.

## Settings (admin)

Read-only, and that is the design rather than an unfinished page:

- **Connections** — where this deployment points, with every credential stripped.
- **Policy files** — governance and routing, each with its version, a digest, whether it loaded,
  and its full text. A policy that did not load is the reason every gated action is being
  refused, and this is where that becomes visible.
- **Budget ceilings** — the pipeline configuration's numbers beside the code defaults, with any
  disagreement marked.
- **Accounts** — who exists, their roles, and when they last signed in.

There is no edit box. A policy change is a reviewed commit, because the audit trail records which
policy version decided each action and a version nobody reviewed would make that record
meaningless. Accounts are created by an administrator with shell access, because an
unauthenticated route that mints an administrator survives into production.

## Keyboard and accessibility

Tabs follow the ARIA pattern: reach the strip once, then move within it with the arrow keys.
Focus is always visible. Colour is never the only signal — a state pill carries its word, a
quality score carries its band, and every chart series carries a dash pattern as well as a
colour. Motion respects `prefers-reduced-motion`.
