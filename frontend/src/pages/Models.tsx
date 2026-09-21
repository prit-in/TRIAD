import { useState } from "react";
import { api } from "../api";
import type { Evaluation, ModelDetail, ModelSummary, ModelVersion } from "../api";
import { BarChart, ConfusionMatrix, LineChart, UnitLineChart } from "../charts";
import { Empty, Panel, Problem, Tabs, num, pct, useLoad, when } from "../shared";

/** The metrics worth putting in front of someone deciding a promotion (PRD 17.5). */
const HEADLINE = [
  ["pr_auc", "PR-AUC"],
  ["roc_auc", "ROC-AUC"],
  ["recall", "Recall"],
  ["precision", "Precision"],
  ["brier_skill", "Brier skill"],
  ["ece", "Calibration error"],
  ["lift_at_10pct", "Lift at 10%"],
] as const;

export function Models() {
  const models = useLoad<ModelSummary[]>(() => api.models());
  const [open, setOpen] = useState<string | null>(null);

  return (
    <>
      <Problem error={models.problem} />
      {models.data?.length === 0 && <Empty what="registered models" />}

      {models.data?.map((model) => (
        <Panel
          key={model.id}
          title={model.name}
          provenance="Model output"
          actions={
            <button className="link" onClick={() => setOpen(open === model.name ? null : model.name)}>
              {open === model.name ? "Hide versions" : "All versions"}
            </button>
          }
        >
          <p className="muted">{model.target_definition}</p>
          <div className="aliases">
            <Aliased label="Champion" version={model.champion} />
            <Aliased label="Challenger" version={model.challenger} />
          </div>
          {open === model.name && <Versions name={model.name} />}
          <VersionComparison model={model} />
          {/* Both aliases, not just the champion. The challenger is the one whose curves
              someone needs while deciding a promotion (FR-066), and in practice it is also
              the version that has an evaluation document at all: a champion promoted before
              these documents existed has none, which is why this page showed no charts. */}
          <AliasEvaluations model={model} />
        </Panel>
      ))}
    </>
  );
}

function Aliased({ label, version }: { label: string; version: ModelVersion | null }) {
  if (!version) {
    return (
      <div className="aliased">
        <h3>{label}</h3>
        <p className="muted">none</p>
      </div>
    );
  }
  return (
    <div className="aliased">
      <h3>
        {label} <span className="pill good">v{version.mlflow_version ?? "unregistered"}</span>
      </h3>
      <p className="muted">
        {version.algorithm} · {version.feature_set_version ?? "no feature-set version"} ·
        promoted {when(version.promoted_at)}
      </p>
      <dl className="metrics">
        {HEADLINE.map(([key, label]) => (
          <div key={key}>
            <dt>{label}</dt>
            {/* A metric the pipeline never recorded is absent, not zero. */}
            <dd>{key in version.metrics ? num(version.metrics[key]) : "not measured"}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function Versions({ name }: { name: string }) {
  const detail = useLoad(() => api.model(name), [name]);
  if (!detail.data) return <Problem error={detail.problem} />;
  return (
    <table>
      <thead>
        <tr>
          <th>Version</th>
          <th>Algorithm</th>
          <th>Status</th>
          <th>Threshold</th>
          <th>Test PR-AUC</th>
          <th>Created</th>
        </tr>
      </thead>
      <tbody>
        {detail.data.versions.map((version) => (
          <tr key={version.id}>
            <td>{version.mlflow_version ?? "—"}</td>
            <td>{version.algorithm}</td>
            <td>{version.governance_status}</td>
            <td>{version.threshold === null ? "—" : num(version.threshold, 3)}</td>
            <td>{"pr_auc" in version.metrics ? num(version.metrics.pr_auc) : "not measured"}</td>
            <td>{when(version.created_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function AliasEvaluations({ model }: { model: ModelSummary }) {
  const aliases: { label: string; version: ModelVersion }[] = [];
  if (model.champion) aliases.push({ label: "Champion", version: model.champion });
  if (model.challenger) aliases.push({ label: "Challenger", version: model.challenger });

  if (aliases.length === 0) return null;
  return (
    <Tabs
      label={`${model.name} evaluation`}
      tabs={aliases.map(({ label, version }) => ({
        id: `${model.name}-${version.id}`,
        label: `${label} v${version.mlflow_version ?? "?"}`,
        content: <ModelEvaluation modelRef={model.name} version={version} />,
      }))}
    />
  );
}

/**
 * The charts FR-065 asks for, drawn from the evaluation document the run wrote.
 *
 * Every point here was computed during the run and persisted. Nothing is recomputed in the
 * browser, because the curves need the labels and the scores together and the API serves
 * neither to a client — so a chart drawn here from anything else would be a chart of invented
 * numbers, which is the one thing these pages must never show.
 */
function ModelEvaluation({ modelRef, version }: { modelRef: string; version: ModelVersion }) {
  const evaluation = useLoad<Evaluation>(
    () => api.evaluation(modelRef, version.id),
    [modelRef, version.id],
  );

  if (evaluation.problem) {
    // A version trained before evaluation documents existed has none. Worth saying plainly
    // rather than showing an error the reader cannot act on.
    return (
      <p className="muted">
        No stored curves for v{version.mlflow_version ?? "?"}. {evaluation.problem.message}
      </p>
    );
  }
  if (!evaluation.data) return <p className="muted">Loading the stored evaluation…</p>;

  const evaluated = evaluation.data;
  const test = evaluated.test;
  const curves = "roc" in test.curves ? test.curves : null;

  return (
    <Tabs
      label={`Evaluation of v${version.mlflow_version ?? "?"}`}
      tabs={[
        {
          id: "quality",
          label: "Quality",
          content: (
            <div className="chart-grid">
              <UnitLineChart
                title="ROC curve"
                xLabel="False positive rate"
                yLabel="True positive rate"
                series={
                  curves
                    ? [
                        {
                          label: `ROC (AUC ${num(test.values.roc_auc, 3)})`,
                          points: curves.roc.fpr.map(
                            (x, i) => [x, curves.roc.tpr[i] ?? 0] as [number, number],
                          ),
                        },
                      ]
                    : []
                }
                reference={{ label: "No better than chance", points: [[0, 0], [1, 1]] }}
              />
              <UnitLineChart
                title="Precision-recall curve"
                xLabel="Recall"
                yLabel="Precision"
                series={
                  curves
                    ? [
                        {
                          label: `PR (AUC ${num(test.values.pr_auc, 3)})`,
                          points: curves.pr.recall.map(
                            (x, i) => [x, curves.pr.precision[i] ?? 0] as [number, number],
                          ),
                          tone: "good",
                        },
                      ]
                    : []
                }
                reference={
                  curves
                    ? {
                        label: `Churn rate (${pct(curves.pr.baseline)})`,
                        points: [
                          [0, curves.pr.baseline],
                          [1, curves.pr.baseline],
                        ],
                      }
                    : undefined
                }
              />
              <div>
                <h3>Confusion matrix</h3>
                <ConfusionMatrix confusion={test.confusion} threshold={evaluated.threshold} />
              </div>
            </div>
          ),
        },
        {
          id: "calibration",
          label: "Calibration",
          content: (
            <div className="chart-grid">
              <UnitLineChart
                title="Reliability diagram"
                xLabel="Mean predicted probability"
                yLabel="Observed churn rate"
                series={
                  curves
                    ? [
                        {
                          label: `Observed (ECE ${num(test.values.ece, 3)})`,
                          // Bins no prediction fell into arrive as NaN and are dropped here:
                          // a gap in the data is a gap, not a point at zero.
                          points: curves.calibration
                            .filter((b) => b.n > 0)
                            .map((b) => [b.mean_predicted, b.observed_rate] as [number, number]),
                          tone: "waiting",
                        },
                      ]
                    : []
                }
                reference={{ label: "Perfectly calibrated", points: [[0, 0], [1, 1]] }}
              />
              <LineChart
                title="Lift and cumulative gain"
                xLabel="Share contacted, highest risk first"
                yLabel="Lift over the base rate"
                xFormat={(v) => `${(v * 100).toFixed(0)}%`}
                series={
                  curves
                    ? [
                        {
                          label: "Lift",
                          points: curves.lift.map((p) => [p.k, p.lift] as [number, number]),
                        },
                        {
                          label: "Cumulative gain",
                          points: curves.lift.map(
                            (p) => [p.k, p.cumulative_gain] as [number, number],
                          ),
                          tone: "good",
                          dashed: true,
                        },
                      ]
                    : []
                }
                reference={{ label: "No targeting benefit", value: 1 }}
              />
            </div>
          ),
        },
        {
          id: "candidates",
          label: "Candidates",
          content: (
            <div className="chart-grid">
              <BarChart
                title="Validation PR-AUC by family"
                yLabel="Validation PR-AUC"
                bars={evaluated.candidates.map((c) => ({
                  label: c.family,
                  value: c.valid_pr_auc,
                  tone: c.selected ? "good" : "muted",
                }))}
              />
              <BarChart
                title="Headline test metrics"
                yLabel="Value, with 95% intervals where measured"
                bars={(
                  [
                    ["pr_auc", "PR-AUC"],
                    ["roc_auc", "ROC-AUC"],
                    ["recall", "Recall"],
                    ["precision", "Precision"],
                  ] as const
                ).map(([key, label]) => ({
                  label,
                  value: test.values[key] ?? null,
                  interval: test.intervals[key],
                }))}
              />
            </div>
          ),
        },
        {
          id: "segments",
          label: `Segments (${evaluated.segments.length})`,
          content:
            evaluated.segments.length === 0 ? (
              <p className="muted">
                No per-segment metrics were recorded for this version. A segment needs enough
                rows and enough positives before its recall means anything, so thin segments
                are left out rather than reported as a number nobody should act on.
              </p>
            ) : (
              <BarChart
                title="Recall by segment"
                yLabel="Recall on the test split"
                width={620}
                bars={evaluated.segments.map((seg) => ({
                  label: seg.segment ?? "?",
                  value: seg.values.recall ?? null,
                }))}
              />
            ),
        },
        {
          id: "importance",
          label: "Importance",
          content: evaluated.feature_importance?.global?.length ? (
            <>
              <BarChart
                title="Global feature importance"
                yLabel={`Importance (${evaluated.feature_importance.method})`}
                width={620}
                bars={evaluated.feature_importance.global.slice(0, 10).map((f) => ({
                  label: f.feature,
                  value: f.value,
                }))}
              />
              <p className="footnote">{evaluated.feature_importance.note}</p>
            </>
          ) : (
            <p className="muted">
              No feature importance was stored. {evaluated.feature_importance?.note ?? ""}
            </p>
          ),
        },
      ]}
    />
  );
}


/**
 * Compare any two versions (PRD 26.1 "Model": *compare any two versions*; FR-066).
 *
 * Both sides come from stored evaluation documents, so a version trained before those existed
 * says so instead of showing blanks that read like zeros. The delta is arithmetic on two served
 * numbers; the intervals beside it are not, and they are what say whether the delta means
 * anything. Two overlapping 95% intervals mean this test set cannot separate the versions
 * whatever the point estimates do, and the table says so in words rather than leaving a reader
 * to compare four numbers in their head.
 */
const COMPARED = [
  ["pr_auc", "PR-AUC"],
  ["roc_auc", "ROC-AUC"],
  ["recall", "Recall"],
  ["precision", "Precision"],
  ["brier_skill", "Brier skill"],
  ["ece", "Calibration error"],
  ["lift_at_10pct", "Lift at 10%"],
] as const;

function VersionComparison({ model }: { model: ModelSummary }) {
  const detail = useLoad<ModelDetail>(() => api.model(model.name), [model.name]);
  const [left, setLeft] = useState<string | null>(null);
  const [right, setRight] = useState<string | null>(null);

  const versions = detail.data?.versions ?? [];
  const [first, second] = versions;
  if (!first || !second) {
    return first ? (
      <p className="muted">
        Only one version of this model is registered, so there is nothing to compare it with yet.
      </p>
    ) : null;
  }

  const a = left ?? model.champion?.id ?? first.id;
  const b = right ?? model.challenger?.id ?? versions.find((v) => v.id !== a)?.id ?? second.id;

  const picker = (label: string, value: string, onChange: (id: string) => void) => (
    <label className="search">
      <span className="sr-only">{label}</span>
      <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}>
        {versions.map((v) => (
          <option key={v.id} value={v.id}>
            v{v.mlflow_version ?? "?"} - {v.algorithm} - {v.governance_status}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <details className="compare">
      <summary>Compare two versions</summary>
      <div className="compare-pickers">
        {picker("First version to compare", a, setLeft)}
        <span className="muted">against</span>
        {picker("Second version to compare", b, setRight)}
      </div>
      {a === b ? (
        <p className="muted">Pick two different versions.</p>
      ) : (
        <ComparisonBody modelRef={model.name} a={a} b={b} versions={versions} />
      )}
    </details>
  );
}

function ComparisonBody({
  modelRef,
  a,
  b,
  versions,
}: {
  modelRef: string;
  a: string;
  b: string;
  versions: ModelVersion[];
}) {
  const left = useLoad<Evaluation>(() => api.evaluation(modelRef, a), [modelRef, a]);
  const right = useLoad<Evaluation>(() => api.evaluation(modelRef, b), [modelRef, b]);
  const name = (id: string) => {
    const version = versions.find((v) => v.id === id);
    return "v" + (version?.mlflow_version ?? "?");
  };

  const missing = [
    left.problem ? name(a) + ": " + left.problem.message : null,
    right.problem ? name(b) + ": " + right.problem.message : null,
  ].filter((m): m is string => m !== null);
  if (missing.length > 0) {
    return <p className="muted">No stored evaluation to compare. {missing.join(" ")}</p>;
  }
  if (!left.data || !right.data) return <p className="muted">Loading both evaluations...</p>;

  const l = left.data.test;
  const r = right.data.test;
  const curvesL = "roc" in l.curves ? l.curves : null;
  const curvesR = "roc" in r.curves ? r.curves : null;

  return (
    <>
      <table>
        <thead>
          <tr>
            <th>Metric</th>
            <th>{name(a)} (95% interval)</th>
            <th>{name(b)} (95% interval)</th>
            <th>Difference</th>
          </tr>
        </thead>
        <tbody>
          {COMPARED.map(([key, label]) => {
            const lv = l.values[key];
            const rv = r.values[key];
            const li = l.intervals[key];
            const ri = r.intervals[key];
            const overlap = li && ri ? li[0] <= ri[1] && ri[0] <= li[1] : null;
            const delta = lv !== undefined && rv !== undefined ? rv - lv : null;
            return (
              <tr key={key}>
                <td>{label}</td>
                <td>
                  {num(lv, 4)}
                  {li && (
                    <span className="muted">
                      {" "}
                      [{num(li[0], 3)}, {num(li[1], 3)}]
                    </span>
                  )}
                </td>
                <td>
                  {num(rv, 4)}
                  {ri && (
                    <span className="muted">
                      {" "}
                      [{num(ri[0], 3)}, {num(ri[1], 3)}]
                    </span>
                  )}
                </td>
                <td>
                  {delta === null ? (
                    "not measured"
                  ) : (
                    <>
                      {delta > 0 ? "+" : ""}
                      {delta.toFixed(4)}
                      {overlap === true && <span className="muted"> - intervals overlap</span>}
                      {overlap === false && <span className="pill good">separated</span>}
                    </>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="chart-grid">
        <UnitLineChart
          title="ROC, both versions"
          xLabel="False positive rate"
          yLabel="True positive rate"
          series={[
            curvesL && {
              label: name(a) + " (AUC " + num(l.values.roc_auc, 3) + ")",
              points: curvesL.roc.fpr.map(
                (x, i) => [x, curvesL.roc.tpr[i] ?? 0] as [number, number],
              ),
            },
            curvesR && {
              label: name(b) + " (AUC " + num(r.values.roc_auc, 3) + ")",
              points: curvesR.roc.fpr.map(
                (x, i) => [x, curvesR.roc.tpr[i] ?? 0] as [number, number],
              ),
              tone: "good" as const,
              dashed: true,
            },
          ].filter((s) => s !== null)}
          reference={{ label: "No better than chance", points: [[0, 0], [1, 1]] }}
        />
        <UnitLineChart
          title="Precision-recall, both versions"
          xLabel="Recall"
          yLabel="Precision"
          series={[
            curvesL && {
              label: name(a) + " (AUC " + num(l.values.pr_auc, 3) + ")",
              points: curvesL.pr.recall.map(
                (x, i) => [x, curvesL.pr.precision[i] ?? 0] as [number, number],
              ),
            },
            curvesR && {
              label: name(b) + " (AUC " + num(r.values.pr_auc, 3) + ")",
              points: curvesR.pr.recall.map(
                (x, i) => [x, curvesR.pr.precision[i] ?? 0] as [number, number],
              ),
              tone: "good" as const,
              dashed: true,
            },
          ].filter((s) => s !== null)}
        />
      </div>
      <p className="footnote">
        Both rows come from the test split of each version's own evaluation document. The two
        were scored on the same sealed test cutoff, which is what makes the difference column
        meaningful; the split is part of the stored document, so a version scored on a different
        one would not be comparable.
      </p>
    </>
  );
}
