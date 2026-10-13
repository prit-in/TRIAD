/**
 * The ten case-study experiments of PRD section 33.
 *
 * Read from the results the harness wrote to disk. Three things this page refuses to do,
 * because each would misrepresent the evidence:
 *
 * - It does not turn `partial` into a tick. An experiment that could not run the workload the
 *   PRD specifies is not a pass, and its limitations are shown next to its conclusion rather
 *   than folded away.
 * - It does not hide an experiment whose hypothesis failed, or one that reached no verdict
 *   because its fault never landed: both are results, and say more than a row of ticks.
 * - It shows an experiment that has never been run as exactly that, rather than omitting it
 *   and leaving a reader to count to ten themselves.
 */

import { useState } from "react";
import { api } from "../api";
import type { ExperimentResult, ExperimentRun } from "../api";
import { BarChart } from "../charts";
import { Empty, Panel, Problem, SearchBox, Tabs, useLoad, when } from "../shared";

const STATUS_TONE: Record<string, string> = {
  completed: "good",
  partial: "waiting",
  not_run: "busy",
  failed: "bad",
  incomplete: "bad",
};

/** A measurement as recorded: numbers and text as they are, lists joined, nothing as a dash. */
function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.length ? value.map(formatValue).join(", ") : "none";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function verdict(held: boolean | null): { text: string; tone: string } {
  if (held === true) return { text: "hypothesis held", tone: "good" };
  if (held === false) return { text: "hypothesis did not hold", tone: "bad" };
  return { text: "no verdict", tone: "busy" };
}

export function Experiments() {
  const experiments = useLoad<ExperimentResult[]>(() => api.experiments());
  const [query, setQuery] = useState("");

  const all = experiments.data ?? [];
  const needle = query.trim().toLowerCase();
  const shown = all.filter(
    (e) =>
      needle === "" ||
      [e.exp_id, e.title ?? "", e.conclusion ?? "", e.status].join(" ").toLowerCase().includes(needle),
  );

  const run = all.filter((e) => e.status !== "not_run");
  const held = run.filter((e) => e.hypothesis_held === true).length;
  const failed = run.filter((e) => e.hypothesis_held === false).length;

  return (
    <>
      <Problem error={experiments.problem} />

      <Panel
        title="Experiments"
        provenance="Measured"
        actions={
          <SearchBox label="Search experiments" value={query} onChange={setQuery}
                     placeholder="EXP-6, drift, leakage…" />
        }
      >
        <p className="muted">
          {run.length} of {all.length} run · {held} held · {failed} did not hold ·{" "}
          {all.length - run.length} not yet run. A <strong>partial</strong> result ran and
          measured something but could not run the workload the PRD specifies; its limitations
          say why.
        </p>
        <BarChart
          title="Experiments by status"
          yLabel="Experiments"
          width={520}
          yFormat={(v) => v.toFixed(0)}
          bars={(["completed", "partial", "failed", "not_run"] as const).map((status) => ({
            label: status.replace("_", " "),
            value: all.filter((e) => e.status === status).length,
            tone: STATUS_TONE[status] as "good" | "waiting" | "bad" | "muted",
          }))}
        />
        <table>
          <caption className="muted">At a glance</caption>
          <thead>
            <tr>
              <th>Experiment</th>
              <th>Status</th>
              <th>Verdict</th>
              <th>Last run</th>
            </tr>
          </thead>
          <tbody>
            {all.map((e) => {
              const outcome = verdict(e.hypothesis_held);
              return (
                <tr key={e.exp_id}>
                  <td>
                    <span className="mono">{e.exp_id}</span> {e.title}
                  </td>
                  <td>
                    <span className={`pill ${STATUS_TONE[e.status] ?? "busy"}`}>
                      {e.status.replace("_", " ")}
                    </span>
                  </td>
                  <td>
                    <span className={`pill ${outcome.tone}`}>{outcome.text}</span>
                  </td>
                  <td>{e.started_at ? when(e.started_at) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Panel>

      {shown.length === 0 && !experiments.problem && <Empty what="experiments matching that" loading={experiments.loading} />}

      {shown.map((experiment) => (
        <ExperimentCard key={experiment.exp_id} experiment={experiment} />
      ))}
    </>
  );
}

function ExperimentCard({ experiment }: { experiment: ExperimentResult }) {
  const outcome = verdict(experiment.hypothesis_held);
  const trials = experiment.trials ?? [];

  return (
    <Panel
      title={`${experiment.exp_id}${experiment.title ? ` — ${experiment.title}` : ""}`}
      provenance="Measured"
      actions={
        <>
          <span className={`pill ${STATUS_TONE[experiment.status] ?? "busy"}`}>
            {experiment.status.replace("_", " ")}
          </span>{" "}
          <span className={`pill ${outcome.tone}`}>{outcome.text}</span>
        </>
      }
    >
      {experiment.hypothesis && <p className="muted">{experiment.hypothesis}</p>}

      {experiment.status === "not_run" ? (
        <p className="muted">{experiment.conclusion}</p>
      ) : (
        <Tabs
          label={`${experiment.exp_id} detail`}
          tabs={[
            {
              id: `${experiment.exp_id}-conclusion`,
              label: "Conclusion",
              content: (
                <>
                  <p>{experiment.conclusion}</p>
                  {(experiment.independent_variable || experiment.dependent_measures) && (
                    <p className="muted">
                      {experiment.independent_variable &&
                        `Varied: ${experiment.independent_variable}. `}
                      {experiment.dependent_measures &&
                        `Measured: ${experiment.dependent_measures.join(", ")}.`}
                    </p>
                  )}
                  {experiment.run_dir && (
                    <p className="muted">
                      Raw outputs:{" "}
                      <span className="mono">
                        experiments/{experiment.exp_id}/{experiment.run_dir}/
                      </span>
                    </p>
                  )}
                  {experiment.started_at && (
                    <p className="muted">
                      Run {when(experiment.started_at)}
                      {experiment.duration_s ? ` · took ${Math.round(experiment.duration_s)}s` : ""}
                    </p>
                  )}
                </>
              ),
            },
            {
              id: `${experiment.exp_id}-trials`,
              label: `Trials (${trials.length})`,
              content:
                trials.length === 0 ? (
                  <Empty what="trials" />
                ) : (
                  <table>
                    <thead>
                      <tr>
                        <th>Condition</th>
                        <th>Outcome</th>
                        <th>What was measured</th>
                      </tr>
                    </thead>
                    <tbody>
                      {trials.map((trial) => (
                        <tr key={trial.condition}>
                          <td className="mono">{trial.condition}</td>
                          <td>
                            {/* Each experiment decides what its trials' flag means; the
                                detail beside it says what happened. */}
                            <span className={`pill ${trial.ok ? "good" : "bad"}`}
                                  title="The experiment's own flag for this condition">
                              {trial.ok ? "ok" : "not ok"}
                            </span>
                          </td>
                          <td>
                            {trial.detail || <span className="muted">—</span>}
                            {(trial.measurements ?? []).length > 0 && (
                              <details className="evidence">
                                <summary>
                                  All {(trial.measurements ?? []).length} measurements
                                </summary>
                                <table>
                                  <thead>
                                    <tr>
                                      <th>Measure</th>
                                      <th>Value</th>
                                      <th>Note</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {(trial.measurements ?? []).map((m, i) => (
                                      <tr key={`${m.name}-${i}`}>
                                        <td className="mono">{m.name}</td>
                                        <td>
                                          {formatValue(m.value)}
                                          {m.unit && m.value !== null ? ` ${m.unit}` : ""}
                                        </td>
                                        <td className="muted">{m.note}</td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </details>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ),
            },
            {
              id: `${experiment.exp_id}-history`,
              label: "History",
              content: <RunHistory expId={experiment.exp_id} />,
            },
            {
              id: `${experiment.exp_id}-limits`,
              label: `Limitations (${(experiment.limitations ?? []).length})`,
              content:
                (experiment.limitations ?? []).length === 0 ? (
                  <p className="muted">
                    This experiment ran the workload the PRD specifies, so it records no
                    limitation.
                  </p>
                ) : (
                  <ul className="limitations">
                    {experiment.limitations.map((limit, i) => (
                      <li key={i}>{limit}</li>
                    ))}
                  </ul>
                ),
            },
          ]}
        />
      )}
    </Panel>
  );
}

/**
 * Every recorded run, newest first - including runs that were interrupted or superseded, with
 * the note that says why. A result that was re-run should show that it was.
 */
function RunHistory({ expId }: { expId: string }) {
  const runs = useLoad<ExperimentRun[]>(() => api.experimentRuns(expId));
  if (runs.problem) return <Problem error={runs.problem} />;
  if (!runs.data) return <p className="muted">Loading…</p>;
  if (runs.data.length === 0) return <Empty what="recorded runs" />;
  return (
    <table>
      <thead>
        <tr>
          <th>Run</th>
          <th>Status</th>
          <th>Verdict</th>
          <th>Took</th>
        </tr>
      </thead>
      <tbody>
        {runs.data.map((run) => {
          const outcome = verdict(run.hypothesis_held ?? null);
          return (
            <tr key={run.run_dir}>
              <td>
                <span className="mono">{run.run_dir}</span>
                {run.started_at && <span className="muted"> · {when(run.started_at)}</span>}
                {run.note && (
                  <details className="evidence">
                    <summary>Note</summary>
                    <pre className="note">{run.note}</pre>
                  </details>
                )}
              </td>
              <td>
                {run.status ? (
                  <span className={`pill ${STATUS_TONE[run.status] ?? "busy"}`}>
                    {run.status.replace("_", " ")}
                  </span>
                ) : (
                  <span className="muted">no result</span>
                )}
              </td>
              <td>{run.status ? <span className={`pill ${outcome.tone}`}>{outcome.text}</span> : "—"}</td>
              <td>{run.duration_s ? `${Math.round(run.duration_s)}s` : "—"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
