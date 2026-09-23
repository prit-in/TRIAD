import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import type {
  Artifact,
  Decision,
  DecisionAlternative,
  LogLine,
  RunDetail,
} from "../api";
import {
  ConfirmButton,
  Empty,
  Meter,
  Panel,
  Problem,
  Pager,
  SearchBox,
  State,
  useLoad,
  usePages,
  useRunEvents,
  useToast,
  when,
} from "../shared";

export function Runs() {
  const [state, setState] = useState("");
  const pages = usePages();
  const runs = useLoad(
    () =>
      api.runs(
        `?limit=25${state ? `&state=${state}` : ""}${pages.cursor ? `&cursor=${encodeURIComponent(pages.cursor)}` : ""}`,
      ),
    [state, pages.cursor],
  );

  return (
    <Panel
      title="Runs"
      provenance="Measured"
      actions={
        <select
          value={state}
          onChange={(e) => {
            // A new filter is a new result set, so the cursor stack from the old one is
            // meaningless — keeping it would page into rows the filter excludes.
            pages.reset();
            setState(e.target.value);
          }}
        >
          <option value="">every state</option>
          {["RECEIVED", "APPROVAL_REQUIRED", "PUBLISHED", "COMPLETED", "ESCALATED", "CANCELLED"].map(
            (option) => (
              <option key={option} value={option}>
                {option.replaceAll("_", " ").toLowerCase()}
              </option>
            ),
          )}
        </select>
      }
    >
      <Problem error={runs.problem} />
      {runs.data && runs.data.items.length > 0 ? (
        <table>
          <thead>
            <tr>
              <th>Run</th>
              <th>Pipeline</th>
              <th>State</th>
              <th>Stage</th>
              <th>Started</th>
            </tr>
          </thead>
          <tbody>
            {runs.data.items.map((run) => (
              <tr key={run.run_id}>
                <td>
                  <Link to={`/runs/${run.run_id}`}>{run.run_id}</Link>
                </td>
                <td>{run.pipeline}</td>
                <td>
                  <State value={run.state} />
                </td>
                <td>{run.current_stage ?? "—"}</td>
                <td>{when(run.started_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Empty what="runs in this state" />
      )}
      <Pager
        pages={pages}
        nextCursor={runs.data?.next_cursor ?? null}
        shown={runs.data?.items.length ?? 0}
      />
    </Panel>
  );
}

export function RunDetailPage() {
  const toast = useToast();
  const [decisionQuery, setDecisionQuery] = useState("");
  const { runId = "" } = useParams();
  const run = useLoad<RunDetail>(() => api.run(runId), [runId]);
  const decisions = useLoad<Decision[]>(() => api.runDecisions(runId), [runId]);
  const artifacts = useLoad<Artifact[]>(() => api.runArtifacts(runId), [runId]);
  const logs = useLoad<LogLine[]>(() => api.runLogs(runId), [runId]);

  // Live progress (PRD 26.2: "stage changes appear within 5 seconds"). The backend has served
  // this stream since it was written and nothing subscribed, so a run in flight only moved
  // when someone reloaded the page. Subscribed only while the run is unfinished: a finished
  // run has nothing further to say and the server closes the stream anyway.
  const finished = ["COMPLETED", "PUBLISHED", "CANCELLED", "ABORTED", "REJECTED_INPUT"].includes(
    run.data?.state ?? "",
  );
  const liveStatus = useRunEvents(
    runId,
    () => {
      // The event carries what changed; the panels re-read rather than patching their own
      // copies, because a half-applied patch is worse than a round trip on a page this size.
      run.refresh();
      decisions.refresh();
      logs.refresh();
    },
    { enabled: Boolean(run.data) && !finished },
  );

  const decisionQueryLower = decisionQuery.trim().toLowerCase();
  const matchingDecisions = (decisions.data ?? []).filter((decision) =>
    decisionQueryLower === "" ||
    [decision.decision, decision.summary ?? "", decision.agent, decision.decision_type,
     decision.policy_rule_ids.join(" ")]
      .join(" ")
      .toLowerCase()
      .includes(decisionQueryLower),
  );

  return (
    <>
      <Problem error={run.problem} />
      <Panel
        title={runId}
        provenance="Measured"
        actions={
          run.data && !["COMPLETED", "PUBLISHED", "CANCELLED", "ABORTED"].includes(run.data.state) ? (
            // Cancelling is irreversible and used to fire on the first click, which for an
            // in-flight run is one misclick away from losing the work it has already done.
            <ConfirmButton
              label="Cancel"
              title={`Cancel ${runId}?`}
              question="Every queued, leased and running task is cancelled and the run stops where it is. Work already committed to the catalogue stays; work in flight is lost. This cannot be undone."
              confirmLabel="Cancel the run"
              onConfirm={() =>
                api.cancelRun(runId).then((result) => {
                  toast(`${runId} is now ${result.state.toLowerCase()}.`);
                  run.refresh();
                })
              }
            />
          ) : undefined
        }
      >
        {run.data && (
          <>
            <p>
              <State value={run.data.state} /> {run.data.pipeline} · {run.data.mode} · started{" "}
              {when(run.data.started_at)}
              {!finished && liveStatus === "live" && (
                <span className="pill busy live"> live</span>
              )}
              {!finished && liveStatus === "error" && (
                <span className="pill bad">
                  {" "}
                  not live — reload to refresh
                </span>
              )}
            </p>
            {run.data.error_summary && <p className="problem">{run.data.error_summary}</p>}
            {run.data.pending_approval && (
              <p className="waiting-note">
                Waiting on a decision for {run.data.pending_approval.target_ref} —{" "}
                <Link to="/approvals">go to approvals</Link>
              </p>
            )}
            <table>
              <thead>
                <tr>
                  <th>Stage</th>
                  <th>Status</th>
                  <th>Attempts</th>
                  <th>Duration</th>
                </tr>
              </thead>
              <tbody>
                {run.data.stages.map((stage, index) => (
                  <tr key={`${stage.name}-${index}`}>
                    <td>{stage.name}</td>
                    <td>
                      <State value={stage.status.toUpperCase()} />
                    </td>
                    <td>{stage.attempts}</td>
                    <td>{stage.duration_s === null ? "—" : `${stage.duration_s}s`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {/* PRD 26.1 asks for budget meters, not a sentence: a number without its
                limit does not say whether the run is near the edge. */}
            <Meter
              label="LLM calls"
              value={run.data.budget_used.llm_calls ?? 0}
              limit={run.data.budget_limits?.max_llm_calls}
            />
            <Meter
              label="LLM cost"
              value={run.data.budget_used.llm_cost_usd ?? 0}
              limit={run.data.budget_limits?.max_llm_cost_usd}
              format={(v) => `$${v.toFixed(2)}`}
            />
          </>
        )}
      </Panel>

      <Panel
        title="Agent decisions"
        provenance="Decision records"
        actions={
          <SearchBox
            label="Search decisions"
            value={decisionQuery}
            onChange={setDecisionQuery}
            placeholder="rule, agent, wording…"
          />
        }
      >
        {/* FR-070 asks for searchable decision records. The filter runs in the browser over
            the run's own records: the list is bounded by one run, and a server round trip per
            keystroke would be slower and no more correct. */}
        {matchingDecisions.length > 0 ? (
          <ul className="decisions">
            {matchingDecisions.map((decision) => (
              <li key={decision.id}>
                <span className="pill busy">{decision.decision_type}</span>
                <strong>{decision.decision}</strong>
                {decision.summary && <span className="muted"> — {decision.summary}</span>}
                {decision.policy_rule_ids.length > 0 && (
                  <span className="rules">{decision.policy_rule_ids.join(", ")}</span>
                )}
                {decision.alternatives.length > 0 && (
                  <RejectedOptions options={decision.alternatives} />
                )}
              </li>
            ))}
          </ul>
        ) : decisionQuery ? (
          <p className="muted">
            No decision matches “{decisionQuery}”. {decisions.data?.length ?? 0} recorded for
            this run.
          </p>
        ) : (
          <Empty what="decisions" />
        )}
      </Panel>

      <Panel title="Generated code" provenance="Measured">
        {artifacts.data && artifacts.data.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>Template</th>
                <th>Slot</th>
                <th>Hash</th>
                <th>Gates</th>
              </tr>
            </thead>
            <tbody>
              {artifacts.data.map((artifact) => (
                <tr key={artifact.id}>
                  <td>{artifact.template_id}</td>
                  <td>{artifact.slot_id ?? "—"}</td>
                  <td className="mono">{artifact.content_hash.slice(0, 12)}</td>
                  <td>
                    {(artifact.gate_results.gates ?? []).map((gate) => (
                      <span
                        key={gate.gate}
                        className={`gate ${gate.passed ? "good" : "bad"}`}
                        title={`${gate.name}: ${gate.detail}`}
                      >
                        {gate.gate}
                      </span>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty what="generated artifacts" />
        )}
      </Panel>

      <Panel title="Timeline" provenance="Measured, redacted">
        {logs.data && logs.data.length > 0 ? (
          <ul className="logs">
            {logs.data.map((line, index) => (
              <li key={index} className={line.level}>
                <span className="log-at">{when(line.at)}</span>
                <span className="log-stage">{line.stage ?? "—"}</span>
                <span className="log-message">{line.message}</span>
              </li>
            ))}
          </ul>
        ) : (
          <Empty what="recorded timeline" />
        )}
      </Panel>
    </>
  );
}


/** The options a decision refused, and why.
 *
 * `decision_records.alternatives` held `[]` on every row: the column existed, the argument that
 * fills it existed, and nothing passed one - so the run detail page showed which model was
 * chosen and never which were rejected. Reviewing a choice means seeing what it was chosen
 * over, so this renders each refused candidate with the rule that refused it and the two
 * numbers that decided it.
 */
function RejectedOptions({ options }: { options: DecisionAlternative[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="alternatives">
      <button
        type="button"
        className="link-button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {open ? "Hide" : "Show"} {options.length} refused{" "}
        {options.length === 1 ? "option" : "options"}
      </button>
      {open && (
        <table>
          <thead>
            <tr>
              <th>Option</th>
              <th>Refused because</th>
              <th>Rule</th>
              <th>Validation PR-AUC</th>
              <th>Calibration error</th>
            </tr>
          </thead>
          <tbody>
            {options.map((option, index) => (
              <tr key={`${option.family ?? "option"}-${index}`}>
                <td>{option.family ?? "—"}</td>
                <td>{option.reason ?? "—"}</td>
                <td className="rules">{option.rule ?? "—"}</td>
                <td>{metric(option.evidence?.valid_pr_auc)}</td>
                <td>{metric(option.evidence?.ece)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

/** A number the run recorded, or a statement that it did not record one. The project forbids
 *  substituting a placeholder value for a measurement that was never taken. */
function metric(value: number | null | undefined) {
  return typeof value === "number" ? value.toFixed(4) : "not measured";
}
