/**
 * Drift, retraining and alerts (FR-073, FR-074, FR-080, FR-084).
 *
 * The drift reports carry a PSI per scoring run, so the trend over time is real stored data
 * and is drawn as one. The retraining picture is assembled from the alerts the policy raises
 * — `retraining parked`, `retraining started` — because that is where the decision is
 * recorded; nothing here infers a trigger that was not logged.
 */

import { useState } from "react";
import { api } from "../api";
import type { Alert, DriftReport } from "../api";
import { LineChart } from "../charts";
import {
  Empty,
  Panel,
  Problem,
  SearchBox,
  Tabs,
  useLoad,
  useToast,
  when,
} from "../shared";

// The feature threshold the drift module uses. Shown as the line a reader judges against,
// because a PSI without its threshold is a number with no meaning attached.
const PSI_FEATURE_THRESHOLD = 0.25;

export function Monitoring() {
  const drift = useLoad<DriftReport[]>(() => api.drift());
  const alerts = useLoad<Alert[]>(() => api.alerts("open"));
  const toast = useToast();
  const [query, setQuery] = useState("");

  const reports = [...(drift.data ?? [])].sort((a, b) =>
    (a.computed_at ?? "").localeCompare(b.computed_at ?? ""),
  );
  const measured = reports.filter((r) => r.max_psi !== null && Number.isFinite(r.max_psi));

  const retraining = (alerts.data ?? []).filter((a) => a.alert_type.includes("retrain"));
  const needle = query.trim().toLowerCase();
  const shownAlerts = (alerts.data ?? []).filter(
    (a) =>
      needle === "" ||
      [a.alert_type, a.message, a.severity].join(" ").toLowerCase().includes(needle),
  );

  return (
    <>
      <Problem error={drift.problem ?? alerts.problem} />

      <Panel title="Drift over time" provenance="Measured">
        {measured.length === 0 ? (
          <Empty what="drift reports with a measured PSI" />
        ) : (
          <>
            <LineChart
              title="Highest feature PSI per scoring run"
              xLabel="Scoring run, oldest first"
              yLabel="Max PSI"
              width={620}
              xFormat={(v) => `#${Math.round(v)}`}
              yFormat={(v) => v.toFixed(2)}
              series={[
                {
                  label: "Max PSI",
                  points: measured.map((r, i) => [i + 1, r.max_psi as number] as [number, number]),
                  tone: "accent",
                },
              ]}
              reference={{ label: `Flagging threshold (${PSI_FEATURE_THRESHOLD})`, value: PSI_FEATURE_THRESHOLD }}
            />
            <p className="footnote">
              PSI compares the scoring window against the training reference captured for that
              model version. A month-of-year feature is constant inside a single-cutoff window,
              so its PSI is enormous and says nothing about behaviour — the flag still fires,
              because a feature pinned by a broken source looks identical.
            </p>
          </>
        )}
      </Panel>

      <Panel
        title="Alerts"
        provenance="Measured"
        actions={
          <SearchBox label="Search alerts" value={query} onChange={setQuery}
                     placeholder="drift, retraining, severity…" />
        }
      >
        <Tabs
          label="Monitoring detail"
          tabs={[
            {
              id: "open",
              label: `Open (${shownAlerts.length})`,
              content:
                shownAlerts.length === 0 ? (
                  <Empty what="open alerts matching that" />
                ) : (
                  <table>
                    <thead>
                      <tr>
                        <th>Severity</th>
                        <th>Type</th>
                        <th>Message</th>
                        <th>Raised</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {shownAlerts.map((alert) => (
                        <tr key={alert.id}>
                          <td>
                            <span className={`pill ${alert.severity === "critical" || alert.severity === "warning" ? "bad" : "busy"}`}>
                              {alert.severity}
                            </span>
                          </td>
                          <td>{alert.alert_type.replaceAll("_", " ")}</td>
                          <td>{alert.message}</td>
                          <td>{when(alert.created_at)}</td>
                          <td>
                            <button
                              className="link"
                              onClick={() =>
                                api.acknowledgeAlert(alert.id).then(() => {
                                  toast("Alert acknowledged.");
                                  alerts.refresh();
                                })
                              }
                            >
                              Acknowledge
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ),
            },
            {
              id: "retraining",
              label: `Retraining (${retraining.length})`,
              content:
                retraining.length === 0 ? (
                  <p className="muted">
                    No retraining decision has been recorded. The triggers RT-1 to RT-9 are
                    evaluated after every scoring run; one that fires and is then held by the
                    validity gate is recorded as parked, with the reason.
                  </p>
                ) : (
                  <ul className="decisions">
                    {retraining.map((alert) => (
                      <li key={alert.id}>
                        <span className="pill busy">{alert.alert_type.replaceAll("_", " ")}</span>
                        <strong>{alert.message}</strong>
                        <span className="muted"> — {when(alert.created_at)}</span>
                      </li>
                    ))}
                  </ul>
                ),
            },
            {
              id: "reports",
              label: `Drift reports (${reports.length})`,
              content:
                reports.length === 0 ? (
                  <Empty what="drift reports" />
                ) : (
                  <table>
                    <thead>
                      <tr>
                        <th>Computed</th>
                        <th>Max PSI</th>
                        <th>Flagged features</th>
                        <th>Score drift</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...reports].reverse().map((report) => (
                        <tr key={report.id}>
                          <td>{when(report.computed_at)}</td>
                          <td>{report.max_psi === null ? "not measured" : report.max_psi.toFixed(3)}</td>
                          <td>
                            {report.flagged_features.length === 0 ? (
                              <span className="pill good">stable</span>
                            ) : (
                              report.flagged_features.join(", ")
                            )}
                          </td>
                          <td>
                            <span className={`pill ${report.score_flagged ? "bad" : "good"}`}>
                              {report.score_flagged ? "flagged" : "stable"}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ),
            },
          ]}
        />
      </Panel>
    </>
  );
}
