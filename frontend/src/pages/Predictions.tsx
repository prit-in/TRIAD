import { useState } from "react";
import { api, isAggregate } from "../api";
import type { DriftReport, ModelSummary, Prediction, PredictionAggregate } from "../api";
import { Bars, Empty, Panel, Problem, num, pct, useLoad, when } from "../shared";
import { useAuth } from "../auth";

export function Predictions() {
  const { user } = useAuth();
  const [band, setBand] = useState("");
  const [set, setSet] = useState("official");
  const [version, setVersion] = useState("");

  // Several runs score the same customers on the same day, each with its own model version.
  // Showing them together looks like one list of scores and is not, so a version is always
  // chosen: the champion's by default, because that is what "the predictions" means.
  const models = useLoad<ModelSummary[]>(() => api.models());
  const champion = models.data?.find((m) => m.champion)?.champion ?? null;
  const selected = version || champion?.id || "";

  const scores = useLoad(
    () =>
      selected
        ? api.predictions(
            `?prediction_set=${set}&model_version_id=${selected}` +
              `${band ? `&risk_band=${band}` : ""}&limit=100`,
          )
        : Promise.resolve([] as Prediction[]),
    [band, set, selected],
  );
  const drift = useLoad<DriftReport[]>(() => api.drift());

  const aggregate = scores.data && isAggregate(scores.data) ? scores.data : null;
  const rows = scores.data && !isAggregate(scores.data) ? scores.data : null;

  return (
    <>
      <Problem error={scores.problem} />

      <Panel
        title="Scores"
        provenance="Model output"
        actions={
          <>
            <select value={set} onChange={(e) => setSet(e.target.value)}>
              <option value="official">official</option>
              <option value="shadow">shadow</option>
            </select>
            <select value={band} onChange={(e) => setBand(e.target.value)}>
              <option value="">every band</option>
              <option value="high">high</option>
              <option value="medium">medium</option>
              <option value="low">low</option>
            </select>
            <select value={selected} onChange={(e) => setVersion(e.target.value)}>
              {(models.data ?? []).flatMap((model) =>
                [model.champion, model.challenger]
                  .filter((v): v is NonNullable<typeof v> => v !== null)
                  .map((v) => (
                    <option key={v.id} value={v.id}>
                      {model.name} v{v.mlflow_version} ({v.governance_status})
                    </option>
                  )),
              )}
            </select>
          </>
        }
      >
        {champion && (
          <p className="muted">
            Showing {selected === champion.id ? "the champion" : "a selected version"}:{" "}
            {champion.algorithm}, scored with feature set{" "}
            {champion.feature_set_version ?? "unrecorded"}.
          </p>
        )}
        {/* A Viewer is served aggregates by the server, so this branch is not a UI choice:
            the rows genuinely never arrive. */}
        {aggregate && <Aggregate data={aggregate} role={user?.role ?? ""} />}

        {rows && rows.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Customer</th>
                <th>Probability</th>
                <th>Band</th>
                <th>Scored</th>
                <th>Top associations</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row: Prediction) => (
                // The same customer is scored on the same day by more than one model
                // version, so the version has to be part of the identity.
                <tr key={`${row.model_version_id}-${row.customer_ref}-${row.score_date}`}>
                  <td className="mono">{row.customer_ref}</td>
                  <td>{num(row.churn_probability, 3)}</td>
                  <td>
                    <span className={`pill ${row.risk_band === "high" ? "bad" : "busy"}`}>
                      {row.risk_band}
                    </span>
                  </td>
                  <td>{row.score_date}</td>
                  <td className="muted">
                    {row.top_reasons.length === 0
                      ? "—"
                      : row.top_reasons.slice(0, 3).map((r) => r.feature).join(", ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {rows && rows.length === 0 && <Empty what="scores for this filter" />}
        <p className="muted footnote">
          These are associations between features and the model's score, not causes.
        </p>
      </Panel>

      <Panel title="Drift" provenance="Measured">
        <Problem error={drift.problem} />
        {drift.data && drift.data.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>Computed</th>
                <th>Highest PSI</th>
                <th>Flagged features</th>
                <th>Score distribution</th>
              </tr>
            </thead>
            <tbody>
              {drift.data.map((report) => (
                <tr key={report.id}>
                  <td>{when(report.computed_at)}</td>
                  <td>{num(report.max_psi, 3)}</td>
                  <td>
                    {report.flagged_features.length === 0
                      ? "none over the threshold"
                      : report.flagged_features.join(", ")}
                  </td>
                  <td>
                    {report.score_flagged === null ? (
                      "not measured"
                    ) : report.score_flagged ? (
                      <span className="pill bad">flagged</span>
                    ) : (
                      <span className="pill good">stable</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty what="drift reports" />
        )}
      </Panel>
    </>
  );
}

function Aggregate({ data, role }: { data: PredictionAggregate; role: string }) {
  return (
    <>
      <p className="muted">
        Your role ({role.replaceAll("_", " ")}) receives aggregates. Customer-level scores are
        not sent to the browser.
      </p>
      {data.rows === 0 ? (
        <Empty what="scores" />
      ) : (
        <>
          <Bars
            data={Object.entries(data.by_risk_band).map(([label, value]) => ({ label, value }))}
          />
          <p className="muted">
            {data.rows} scored · mean probability {num(data.mean_probability, 3)} ·{" "}
            {pct((data.by_risk_band.high ?? 0) / data.rows, 1)} high risk
          </p>
        </>
      )}
    </>
  );
}
