import { Link } from "react-router-dom";
import { api } from "../api";
import type { Alert, ModelSummary, RunSummary, Stats } from "../api";
import { Empty, Panel, Problem, State, Tile, num, pct, useLoad, when } from "../shared";

export function Dashboard() {
  const stats = useLoad<Stats>(() => api.stats());
  const runs = useLoad(() => api.runs("?limit=8"));
  const models = useLoad<ModelSummary[]>(() => api.models());
  const alerts = useLoad<Alert[]>(() => api.alerts("open"));

  const champion = models.data?.find((m) => m.champion)?.champion ?? null;

  return (
    <>
      <Problem error={stats.problem ?? runs.problem ?? models.problem ?? alerts.problem} />

      <div className="tiles">
        <Tile label="Runs today" value={stats.data?.runs_today ?? "…"} />
        <Tile
          label="Success rate"
          value={stats.data ? pct(stats.data.success_rate, 0) : "…"}
          note={stats.data ? `of ${stats.data.runs_finished} finished runs` : undefined}
        />
        <Tile label="Open alerts" value={stats.data?.open_alerts ?? "…"} />
        <Tile label="Pending approvals" value={stats.data?.pending_approvals ?? "…"} />
        <Tile
          label="Champion test PR-AUC"
          value={champion ? num(champion.metrics.pr_auc) : "no champion"}
          note={champion ? `${champion.algorithm}, v${champion.mlflow_version}` : undefined}
        />
      </div>

      <Panel title="Recent runs" provenance="Measured">
        {runs.data && runs.data.items.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>Run</th>
                <th>Pipeline</th>
                <th>State</th>
                <th>Started</th>
                <th>Finished</th>
              </tr>
            </thead>
            <tbody>
              {runs.data.items.map((run: RunSummary) => (
                <tr key={run.run_id}>
                  <td>
                    <Link to={`/runs/${run.run_id}`}>{run.run_id}</Link>
                  </td>
                  <td>{run.pipeline}</td>
                  <td>
                    <State value={run.state} />
                  </td>
                  <td>{when(run.started_at)}</td>
                  <td>{when(run.finished_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty what="runs" loading={runs.loading} />
        )}
      </Panel>

      <Panel title="Open alerts" provenance="Measured">
        {alerts.data && alerts.data.length > 0 ? (
          <ul className="alerts">
            {alerts.data.map((alert) => (
              <li key={alert.id}>
                <span className={`pill ${alert.severity === "critical" ? "bad" : "waiting"}`}>
                  {alert.severity}
                </span>
                <span className="alert-type">{alert.alert_type.replaceAll("_", " ")}</span>
                <span className="alert-message">{alert.message}</span>
                <button
                  className="link"
                  onClick={() =>
                    api.acknowledgeAlert(alert.id).then(() => {
                      // Both, because the tile and this list are two views of one number.
                      // Refreshing only the list left "Open alerts" reading 12 beside a list
                      // of 11 — the page contradicting itself after the user's own action.
                      alerts.refresh();
                      stats.refresh();
                    })
                  }
                >
                  Acknowledge
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <Empty what="open alerts" loading={alerts.loading} />
        )}
      </Panel>
    </>
  );
}
