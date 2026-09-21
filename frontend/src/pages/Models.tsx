import { useState } from "react";
import { api } from "../api";
import type { Evaluation, ModelSummary, ModelVersion } from "../api";
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
