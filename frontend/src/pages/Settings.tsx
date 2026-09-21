/**
 * Settings (PRD section 26.1 "Settings"): accounts, the active policy, budgets, connections.
 *
 * Read-only, and that is the design rather than an unfinished state. The policy file is
 * reviewed and versioned like source (PRD 30.3); an edit box here would take the one document
 * the governance story rests on out of review. The page says so where a reader would look for
 * the missing button.
 *
 * Two things this page exists to make visible: whether the policy loaded at all — a policy
 * that failed to parse denies every gated action, and that should be legible before someone
 * wonders why every run escalates — and whether the budget ceilings in the pipeline config
 * agree with the ones the code falls back to.
 */

import { api } from "../api";
import type { SettingsView } from "../api";
import { Empty, Panel, Problem, Tabs, useLoad, when } from "../shared";

export function Settings() {
  const settings = useLoad<SettingsView>(() => api.settings());

  if (settings.problem) return <Problem error={settings.problem} />;
  if (!settings.data) return <p className="muted">Loading the active configuration…</p>;

  const s = settings.data;

  return (
    <>
      <Panel title={`Environment: ${s.environment}`} provenance="Calculated">
        <table>
          <thead>
            <tr>
              <th>Connection</th>
              <th>Target</th>
              <th>Used for</th>
            </tr>
          </thead>
          <tbody>
            {s.connections.map((c) => (
              <tr key={c.name}>
                <td>{c.name}</td>
                <td className="mono">{c.target}</td>
                <td>{c.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="footnote">
          Credentials are stripped before this reaches the browser: a target reads
          <span className="mono"> ***@host:port/db</span>, never a password. Nothing here is a
          live health check — it is the configuration this process loaded.
        </p>
      </Panel>

      <Panel title="Active policy files" provenance="Calculated">
        <Tabs
          label="Policy files"
          tabs={s.policies.map((policy) => ({
            id: policy.name,
            label: `${policy.name} (v${policy.version})`,
            content: (
              <>
                <dl className="metrics">
                  <div>
                    <dt>Version</dt>
                    <dd>{policy.version}</dd>
                  </div>
                  <div>
                    <dt>Loaded</dt>
                    <dd>
                      <span className={`pill ${policy.valid ? "good" : "bad"}`}>
                        {policy.valid ? "valid" : "refused"}
                      </span>
                    </dd>
                  </div>
                  <div>
                    {/* Governance covers actions; routing is a list of rules. Labelling both
                        "actions" reads as a governance claim about the wrong file. */}
                    <dt>{policy.name === "routing" ? "Routing rules" : "Actions covered"}</dt>
                    <dd>{policy.action_count}</dd>
                  </div>
                  <div>
                    <dt>File digest</dt>
                    <dd className="mono" title={policy.sha256 ?? ""}>
                      {policy.sha256 ? `${policy.sha256.slice(0, 16)}…` : "unreadable"}
                    </dd>
                  </div>
                </dl>
                {!policy.valid && (
                  <p className="problem">
                    <strong>This policy did not load.</strong> {policy.load_error}{" "}
                    {policy.name === "governance"
                      ? "Every gated action is refused while that is true — the engine fails closed."
                      : "The routing engine reads its thresholds from this file and raises rather than routing without one."}
                  </p>
                )}
                <p className="footnote">
                  {policy.path} — read-only here. Changing a policy is a reviewed commit, not a
                  form: the audit trail records which version decided each action, and a
                  version nobody reviewed would make that record meaningless.
                </p>
                <pre className="policy-text">{policy.text}</pre>
              </>
            ),
          }))}
        />
      </Panel>

      <Panel title="Budget ceilings" provenance="Calculated">
        {s.budgets.pipeline_config_error && (
          <p className="problem">
            <strong>The pipeline configuration could not be read.</strong>{" "}
            {s.budgets.pipeline_config_error}
          </p>
        )}
        <table>
          <thead>
            <tr>
              <th>Ceiling</th>
              <th>Pipeline configuration</th>
              <th>Code default</th>
            </tr>
          </thead>
          <tbody>
            {s.budgets.keys.map((key) => {
              const configured = s.budgets.pipeline_config[key];
              const fallback = s.budgets.code_default[key];
              // A ceiling defined in one place and not the other is the case worth seeing:
              // it is how a run ends up bounded by a number nobody chose.
              const disagrees =
                configured !== undefined && fallback !== undefined && configured !== fallback;
              return (
                <tr key={key}>
                  <td>{key.replace(/^max_/, "").replaceAll("_", " ")}</td>
                  <td className={disagrees ? "problem-cell" : undefined}>
                    {configured ?? "not set"}
                  </td>
                  <td>{fallback ?? "none"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="footnote">
          {s.budgets.pipeline_config_path} — the orchestrator enforces these, never the agent.
          A run stores the ceilings it started with, so changing them here would not change a
          run already in flight.
        </p>
      </Panel>

      <Panel title={`Accounts (${s.users.length})`} provenance="Calculated">
        {s.users.length === 0 ? (
          <Empty what="accounts" />
        ) : (
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Email</th>
                <th>Role</th>
                <th>Active</th>
                <th>Created</th>
                <th>Last signed in</th>
              </tr>
            </thead>
            <tbody>
              {s.users.map((u) => (
                <tr key={u.id}>
                  <td>{u.display_name}</td>
                  <td>{u.email}</td>
                  <td>
                    <span className="pill busy">{u.role.replaceAll("_", " ")}</span>
                  </td>
                  <td>
                    <span className={`pill ${u.is_active ? "good" : "bad"}`}>
                      {u.is_active ? "active" : "disabled"}
                    </span>
                  </td>
                  <td>{when(u.created_at)}</td>
                  <td>{when(u.last_login_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="footnote">
          Accounts are created by <span className="mono">scripts/seed_demo.py</span>, by someone
          with shell access to the deployment. There is deliberately no route that mints an
          administrator — an unauthenticated convenience like that survives into production.
        </p>
      </Panel>
    </>
  );
}
