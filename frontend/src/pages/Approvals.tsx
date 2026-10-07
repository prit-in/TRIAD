import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiProblem, api } from "../api";
import type { Approval } from "../api";
import { Empty, Panel, Problem, useLoad, when } from "../shared";

export function Approvals() {
  const [status, setStatus] = useState("pending");
  const approvals = useLoad<Approval[]>(() => api.approvals(status), [status]);

  return (
    <Panel
      title="Approvals"
      provenance="Governed"
      actions={
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          {["pending", "approved", "rejected", "expired"].map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      }
    >
      <Problem error={approvals.problem} />
      {approvals.data && approvals.data.length > 0 ? (
        approvals.data.map((approval) => (
          <Request key={approval.id} approval={approval} onDecided={approvals.refresh} />
        ))
      ) : (
        <Empty what={`${status} requests`} />
      )}
    </Panel>
  );
}

function Request({ approval, onDecided }: { approval: Approval; onDecided: () => void }) {
  const [comment, setComment] = useState("");
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const [busy, setBusy] = useState(false);
  const pending = approval.status === "pending";

  async function decide(approved: boolean) {
    setBusy(true);
    setProblem(null);
    try {
      await api.decide(approval.id, approved, comment);
      onDecided();
    } catch (error) {
      // The refusals worth showing verbatim are here: self-approval, the wrong role, an
      // expired request. Rewording them would hide which rule actually applied.
      setProblem(error instanceof ApiProblem ? error : new ApiProblem(0, null));
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="approval">
      <header>
        <strong>{approval.action_type.replaceAll("_", " ")}</strong>
        <span className="mono">{approval.target_ref}</span>
        {approval.required_role && (
          <span className="pill busy">requires: {approval.required_role.replaceAll("_", " ")}</span>
        )}
        <span className={`pill ${approval.status === "approved" ? "good" : pending ? "waiting" : "bad"}`}>
          {approval.status}
        </span>
      </header>
      <p className="muted">
        requested {when(approval.requested_at)} by {approval.requested_by} · expires{" "}
        {when(approval.expires_at)} ·{" "}
        <Link to={`/runs/${approval.run_id}`}>the run</Link>
      </p>
      {approval.group_id && (
        <p className="muted">
          One of the shares of a decision that needs every share approved, each by a different
          person. A rejection of any share refuses the whole decision.
        </p>
      )}
      {approval.evidence_uri && (
        <p className="muted">Evidence bundle: <span className="mono">{approval.evidence_uri}</span></p>
      )}
      {approval.decision_note && <p>Decision note: {approval.decision_note}</p>}

      {pending && (
        <div className="decide">
          <label>
            Comment
            <input
              value={comment}
              placeholder="why this decision"
              onChange={(e) => setComment(e.target.value)}
            />
          </label>
          {/* The comment is required by the server too; disabling here only saves a round
              trip, it is not what enforces the rule. */}
          <button disabled={busy || comment.trim().length === 0} onClick={() => decide(true)}>
            Approve
          </button>
          <button
            className="secondary"
            disabled={busy || comment.trim().length === 0}
            onClick={() => decide(false)}
          >
            Reject
          </button>
        </div>
      )}
      {problem && (
        <p className="problem">
          <strong>{problem.title}.</strong> {problem.detail}
        </p>
      )}
    </article>
  );
}
