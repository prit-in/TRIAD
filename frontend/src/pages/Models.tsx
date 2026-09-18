import { useState } from "react";
import { api } from "../api";
import type { ModelSummary, ModelVersion } from "../api";
import { Empty, Panel, Problem, num, useLoad, when } from "../shared";

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
