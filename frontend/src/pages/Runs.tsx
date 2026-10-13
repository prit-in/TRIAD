import { Fragment, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiProblem, api } from "../api";
import type {
  Artifact,
  DatasetSummary,
  Decision,
  DecisionAlternative,
  Execution,
  LogLine,
  RunDetail,
} from "../api";
import { useAuth } from "../auth";
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
    <>
    <StartRun />
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
        <Empty what="runs in this state" loading={runs.loading} />
      )}
      <Pager
        pages={pages}
        nextCursor={runs.data?.next_cursor ?? null}
        shown={runs.data?.items.length ?? 0}
      />
    </Panel>
    </>
  );
}

/** What the churn pipeline reads (pipelines/churn_v1.yaml). Transactions and customers are
 *  required; the others feed feature groups that are computed when their source exists and
 *  marked unavailable when it does not (FR-025). */
const CHURN_INPUTS = ["transactions", "customers", "products", "returns", "web_sessions",
                      "support_tickets", "promotions"];
// The roles `POST /pipelines/run` admits: STARTER_ROLES in backend/app/api/v1/pipelines.py, and
// admin, which `require_roles` always adds. Without it an admin never saw this form (DEC-53).
const STARTER_ROLES = ["data_engineer", "data_scientist", "ml_engineer", "admin"];
// `POST /pipelines/{id}/resume` admits these, and admin likewise (PRD 25.2).
const RESUME_ROLES = ["data_engineer", "ml_engineer", "admin"];

/**
 * Start a run from the dashboard, so a demonstration needs no shell. Each input resolves to its
 * latest registered version on the server when the run starts, which is also what the run then
 * records; the page only says which versions that will be. One run of a pipeline at a time is
 * the server's rule (DEC-18), and its refusal is shown in the dialog.
 */
function StartRun() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const datasets = useLoad<DatasetSummary[]>(() => api.datasets());
  if (!user || !STARTER_ROLES.includes(user.role)) return null;

  const latest = new Map((datasets.data ?? []).map((d) => [d.name, d.latest_version]));
  const inputs = CHURN_INPUTS.filter((name) => latest.get(name));
  const ready = inputs.includes("transactions") && inputs.includes("customers");
  return (
    <Panel
      title="Start a run"
      provenance="Governed"
      actions={
        <ConfirmButton
          label="Start a run of churn_v1"
          className="secondary"
          disabled={!ready}
          title="Start a governed run?"
          question={`churn_v1 on the latest version of ${inputs.length} datasets. It runs for tens of minutes - profiling, generated Spark jobs behind nine gates, training and the acceptance policy - and may stop for an approval or an escalation, which the run page will show.`}
          confirmLabel="Start the run"
          onConfirm={() =>
            api
              .startRun("churn_v1", Object.fromEntries(inputs.map((name) => [name, name])))
              .then((started) => navigate(`/runs/${started.run_id}`))
          }
        />
      }
    >
      <Problem error={datasets.problem} />
      {datasets.data && (
        <p className="muted">
          {ready
            ? `Inputs: ${inputs.map((name) => `${name} v${latest.get(name)}`).join(" · ")}`
            : "Transactions and customers must both be registered before a run can start."}
        </p>
      )}
    </Panel>
  );
}

/** States in which a run's model has been promoted. */
const PROMOTED_STATES = ["DEPLOYED", "PUBLISHED", "COMPLETED"];

/** States a run cannot leave. FAILED is one: a failed task now fails its run (queue.fail). */
const FINISHED_STATES = ["COMPLETED", "PUBLISHED", "CANCELLED", "ABORTED", "REJECTED_INPUT", "FAILED"];

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
  const finished = FINISHED_STATES.includes(
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

  // A run that does not exist leaves every panel below with the same 404; one error and a way
  // back say it once (DEC-53).
  if (run.problem instanceof ApiProblem && run.problem.status === 404) {
    return (
      <>
        <Problem error={run.problem} />
        <p>
          <Link to="/runs">Back to the runs</Link>
        </p>
      </>
    );
  }

  return (
    <>
      <Problem error={run.problem} />
      <Panel
        title={runId}
        provenance="Measured"
        actions={
          run.data && !FINISHED_STATES.includes(run.data.state) ? (
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
            {run.data.override && PROMOTED_STATES.includes(run.data.state) ? (
              // Promoted despite a failed check: the reason it escalated is now the reason two
              // approvers accepted, not an error the run is still in.
              <p className="waiting-note">
                Promoted despite a failed check ({run.data.override.failed_rules.join(", ")}):{" "}
                {run.data.override.reason}. An ML Engineer and an Admin each approved it —{" "}
                <Link to="/approvals">see the decision</Link>.
              </p>
            ) : (
              run.data.error_summary && <p className="problem">{run.data.error_summary}</p>
            )}
            {run.data.pending_approval && (
              <p className="waiting-note">
                {run.data.state === "APPROVAL_REQUIRED" && run.data.override
                  ? `Promotion despite a failed check (${run.data.override.failed_rules.join(", ")}) is waiting for both approvers: an ML Engineer and an Admin must each approve their own share`
                  : `Waiting on a decision for ${run.data.pending_approval.target_ref}`}{" "}
                — <Link to="/approvals">go to approvals</Link>
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

      {run.data?.state === "ESCALATED" && run.data.override && (
        <OverrideRequest runId={runId} override={run.data.override} onRequested={run.refresh} />
      )}

      {run.data?.state === "ESCALATED" && <ResumeRun runId={runId} onResumed={run.refresh} />}

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
        {decisions.problem ? (
          <Problem error={decisions.problem} />
        ) : matchingDecisions.length > 0 ? (
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
          <Empty what="decisions" loading={decisions.loading} />
        )}
      </Panel>

      <SparkExecutions runId={runId} />

      <Panel title="Generated code" provenance="Measured">
        {artifacts.problem ? (
          <Problem error={artifacts.problem} />
        ) : artifacts.data && artifacts.data.length > 0 ? (
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
          <Empty what="generated artifacts" loading={artifacts.loading} />
        )}
      </Panel>

      <Panel title="Timeline" provenance="Measured, redacted">
        {logs.problem ? (
          <Problem error={logs.problem} />
        ) : logs.data && logs.data.length > 0 ? (
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
          <Empty what="recorded timeline" loading={logs.loading} />
        )}
      </Panel>
    </>
  );
}

/**
 * PRD section 30: promotion despite a failed check - two approvers, ML Engineer and Admin, and a
 * written justification. Offered only where the server says the candidate may be promoted anyway
 * (`run.override`: it failed a calibration or segment rule and beat the champion). Every rule
 * stated here is enforced by the server; the page only says them before anyone asks.
 */
function OverrideRequest({
  runId,
  override,
  onRequested,
}: {
  runId: string;
  override: NonNullable<RunDetail["override"]>;
  onRequested: () => void;
}) {
  const toast = useToast();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Error | null>(null);

  async function submit() {
    setBusy(true);
    setProblem(null);
    try {
      const shares = await api.requestOverride(runId, note.trim());
      toast(
        `Asked for ${shares.length} approvals: ${shares
          .map((share) => (share.required_role ?? "any approver").replaceAll("_", " "))
          .join(" and ")}.`,
      );
      onRequested();
    } catch (error) {
      // Shown verbatim: "already requested", the wrong role or a run that moved on are the
      // server's refusals, and rewording them would hide which rule applied.
      setProblem(error instanceof Error ? error : new Error(String(error)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Promotion despite a failed check" provenance="Governed">
      <p>
        The candidate failed <strong>{override.failed_rules.join(", ")}</strong>: {override.reason}.
        It is registered as a candidate, not promoted.
      </p>
      <p className="muted">
        Promoting it anyway needs two approvers, an ML Engineer and an Admin, each approving their
        own share. Neither may be the person who asks or whoever started this run, and one person
        cannot approve both shares. A rejection of either returns the run to escalated.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <label>
          Written justification (at least 20 characters)
          <textarea
            value={note}
            rows={4}
            maxLength={2000}
            onChange={(e) => setNote(e.target.value)}
          />
        </label>
        {/* The server refuses a shorter note too; disabling here only saves a round trip. */}
        <button type="submit" disabled={busy || note.trim().length < 20}>
          {busy ? "Working…" : "Request both approvals"}
        </button>
      </form>
      <Problem error={problem} />
    </Panel>
  );
}

/**
 * Resume an escalated run once its cause is fixed (FR-082; PRD 26.1, "resume after
 * escalation"). The API had the endpoint and nothing on screen called it, so an escalation
 * could only be cancelled from the dashboard. The run re-enters the stage it stopped in, with
 * the inputs it was given, and the fix note is recorded in the audit trail with the action.
 */
function ResumeRun({ runId, onResumed }: { runId: string; onResumed: () => void }) {
  const { user } = useAuth();
  const toast = useToast();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Error | null>(null);
  if (!user || !RESUME_ROLES.includes(user.role)) return null;

  async function submit() {
    setBusy(true);
    setProblem(null);
    try {
      const result = await api.resumeRun(runId, note.trim());
      toast(`${runId} is now ${result.state.toLowerCase()}: it resumes from the stage it stopped in.`);
      setNote("");
      onResumed();
    } catch (error) {
      setProblem(error instanceof Error ? error : new Error(String(error)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Resume after a fix" provenance="Governed">
      <p className="muted">
        Once the cause named in the escalation is fixed - a dataset corrected, an engine
        restarted, a setting changed - the run can re-enter the stage it stopped in, with the
        inputs it was given. The note is kept in the audit trail with the action.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <label>
          What was fixed (at least 10 characters)
          <textarea value={note} rows={3} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
        </label>
        {/* The server refuses a shorter note too; disabling here only saves a round trip. */}
        <button type="submit" disabled={busy || note.trim().length < 10}>
          {busy ? "Working…" : "Resume the run"}
        </button>
      </form>
      <Problem error={problem} />
    </Panel>
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

/**
 * FR-036: what Spark did in each job - its stages, tasks, shuffle, spill and task skew - as the
 * job's own listener measured them. Executions recorded before per-stage metrics existed (or
 * that ran no Spark job) are left out rather than shown as zeros.
 */
function SparkExecutions({ runId }: { runId: string }) {
  const executions = useLoad<Execution[]>(() => api.runExecutions(runId), [runId]);
  const [open, setOpen] = useState<string | null>(null);
  const measured = (executions.data ?? []).filter((e) => e.spark);

  return (
    <Panel title="Spark execution" provenance="Measured">
      {executions.problem ? (
        <Problem error={executions.problem} />
      ) : measured.length === 0 ? (
        <Empty what="Spark stage metrics" loading={executions.loading} />
      ) : (
        <table className="spark">
          <thead>
            <tr>
              <th>Job</th>
              <th>Ran on</th>
              <th>Wall clock</th>
              <th>Spark stages</th>
              <th>Tasks</th>
              <th>Shuffle read</th>
              <th>Shuffle write</th>
              <th>Spill</th>
              <th title="Slowest task over the median task, worst stage">Worst skew</th>
            </tr>
          </thead>
          <tbody>
            {measured.map((e) => {
              const t = e.spark!.totals;
              const spill = (t.memory_spill_bytes ?? 0) + (t.disk_spill_bytes ?? 0);
              return (
                <Fragment key={e.id}>
                  <tr>
                    <td>
                      <button className="link" aria-expanded={open === e.id}
                              onClick={() => setOpen(open === e.id ? null : e.id)}>
                        {e.stage}
                      </button>{" "}
                      {e.status !== "succeeded" && <State value={e.status.toUpperCase()} />}
                    </td>
                    <td className="mono"
                        title={e.routed_deploy_mode
                          ? `the router recommended ${e.routed_deploy_mode}` +
                            (e.log_kept ? "; the job's log is kept" : "")
                          : undefined}>
                      {e.master ? (e.master.startsWith("local") ? e.master : "cluster") : "—"}
                    </td>
                    <td>{e.duration_ms === null ? "—" : `${(e.duration_ms / 1000).toFixed(1)}s`}</td>
                    <td>{t.stages}</td>
                    <td>{t.tasks}</td>
                    <td>{bytes(t.shuffle_read_bytes)}</td>
                    <td>{bytes(t.shuffle_write_bytes)}</td>
                    <td className={spill > 0 ? "warn" : undefined}>{bytes(spill)}</td>
                    <td className={(t.max_task_skew ?? 1) >= 3 ? "warn" : undefined}>
                      {(t.max_task_skew ?? 1).toFixed(1)}×
                    </td>
                  </tr>
                  {open === e.id && (
                    <tr className="spark-stages">
                      <td colSpan={9}>
                        <table>
                          <thead>
                            <tr>
                              <th>Stage</th>
                              <th>Operation</th>
                              <th>Tasks</th>
                              <th>Duration</th>
                              <th>Shuffle read</th>
                              <th>Shuffle write</th>
                              <th>Spill</th>
                              <th>Skew</th>
                            </tr>
                          </thead>
                          <tbody>
                            {e.spark!.stages.map((st) => (
                              <tr key={`${st.stage_id}-${st.attempt}`}>
                                <td>{st.stage_id}{st.attempt > 0 ? ` (retry ${st.attempt})` : ""}</td>
                                <td className="mono" title={st.name}>{st.name.split(" at ")[0]}</td>
                                <td>{st.num_tasks}</td>
                                <td>{(st.duration_ms / 1000).toFixed(2)}s</td>
                                <td>{bytes(st.shuffle_read_bytes)}</td>
                                <td>{bytes(st.shuffle_write_bytes)}</td>
                                <td>{bytes(st.memory_spill_bytes + st.disk_spill_bytes)}</td>
                                <td>{st.task_skew.toFixed(1)}×</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

function bytes(value: number | undefined): string {
  if (!value) return "0";
  const units = ["B", "KB", "MB", "GB"];
  const power = Math.min(units.length - 1, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** power).toFixed(power === 0 ? 0 : 1)} ${units[power]}`;
}
