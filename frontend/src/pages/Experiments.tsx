/**
 * The ten case-study experiments of PRD section 33.
 *
 * Read from the results the harness wrote to disk. Three things this page refuses to do,
 * because each would misrepresent the evidence:
 *
 * - It does not turn `partial` into a tick. An experiment that could not run the workload the
 *   PRD specifies is not a pass, and its limitations are shown next to its conclusion rather
 *   than folded away.
 * - It does not hide the experiment whose hypothesis failed. EXP-6 is the most informative
 *   result in the set precisely because it did not hold.
 * - It shows an experiment that has never been run as exactly that, rather than omitting it
 *   and leaving a reader to count to ten themselves.
 */

import { useState } from "react";
import { api } from "../api";
import type { ExperimentResult } from "../api";
import { BarChart } from "../charts";
import { Empty, Panel, Problem, SearchBox, Tabs, useLoad, when } from "../shared";

const STATUS_TONE: Record<string, string> = {
  completed: "good",
  partial: "waiting",
  not_run: "busy",
  failed: "bad",
};

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
      </Panel>

      {shown.length === 0 && !experiments.problem && <Empty what="experiments matching that" />}

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
                            <span className={`pill ${trial.ok ? "good" : "bad"}`}>
                              {trial.ok ? "as expected" : "not as expected"}
                            </span>
                          </td>
                          <td>
                            {trial.detail || (
                              <span className="muted">
                                {(trial.measurements ?? [])
                                  .slice(0, 4)
                                  .map((m) => `${m.name} ${m.value}`)
                                  .join(", ") || "—"}
                              </span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ),
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
