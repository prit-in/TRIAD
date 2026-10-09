import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiProblem, api } from "../api";
import type { Approval, EvidenceBundle } from "../api";
import { Empty, Panel, Problem, num, pct, useLoad, when } from "../shared";

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
      {approval.evidence_uri && <Evidence approvalId={approval.id} />}
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

/**
 * The evidence bundle, read on the page (PRD 30.5: "what an approver sees on one page").
 *
 * The page used to print the bundle's storage path, which no approver can open from a browser,
 * so a decision was made on the target name alone. Fetched when opened: a queue of requests
 * should not fetch every bundle to show a list.
 */
function Evidence({ approvalId }: { approvalId: string }) {
  const [bundle, setBundle] = useState<EvidenceBundle | null>(null);
  const [problem, setProblem] = useState<Error | null>(null);

  function load(open: boolean) {
    if (!open || bundle || problem) return;
    api.approvalEvidence(approvalId).then(setBundle).catch((error: Error) => setProblem(error));
  }

  const delta = bundle?.metrics_vs_champion;
  return (
    <details className="evidence" onToggle={(e) => load(e.currentTarget.open)}>
      <summary>Evidence</summary>
      <Problem error={problem} />
      {!bundle && !problem && <p className="muted">Fetching the evidence…</p>}
      {bundle && (
        <>
          {bundle.override_note && (
            <blockquote className="override-note">
              <strong>Justification:</strong> {bundle.override_note}
            </blockquote>
          )}
          {bundle.risk_notes.length > 0 && (
            <ul className="risk-notes">
              {bundle.risk_notes.map((note) => <li key={note}>{note}</li>)}
            </ul>
          )}
          {/* A schema change has no model behind it: its notes above name each proposed
              mapping, and the impact says what approving does. */}
          {bundle.impact_estimate?.effect && (
            <p>
              {bundle.impact_estimate.rows ?? "An unknown number of"} rows. Approving{" "}
              {bundle.impact_estimate.effect}.
            </p>
          )}
          {bundle.action_type !== "schema.change" && (
          <dl className="metrics">
            <div>
              <dt>PR-AUC against the champion (AP-8)</dt>
              <dd>
                {delta?.mean === undefined
                  ? (delta?.note ?? "not measured")
                  : `${delta.mean >= 0 ? "+" : ""}${num(delta.mean, 3)} (95% interval ${num(delta.lower_95, 3)} to ${num(delta.upper_95, 3)})`}
              </dd>
            </div>
            {Object.entries(bundle.calibration)
              .filter(([, value]) => typeof value === "number" || typeof value === "string")
              .map(([key, value]) => (
                <div key={key}>
                  <dt>{key.replaceAll("_", " ")}</dt>
                  <dd>{typeof value === "number" ? num(value, 4) : String(value)}</dd>
                </div>
              ))}
          </dl>
          )}
          {bundle.segment_checks.map((check) => (
            <SegmentCheck key={check.rule} check={check} />
          ))}
        </>
      )}
    </details>
  );
}

function SegmentCheck({ check }: { check: EvidenceBundle["segment_checks"][number] }) {
  const review = check.threshold_review;
  return (
    <section className="segment-check">
      <h4>
        {check.rule} <span className={`pill ${check.result === "failed" ? "bad" : "good"}`}>{check.result}</span>
      </h4>
      {check.recall_gaps && (
        <p>
          Recall gap within each segmentation:{" "}
          {Object.entries(check.recall_gaps)
            .sort(([, a], [, b]) => b - a)
            .map(([name, gap]) => `${name.replaceAll("_", " ")} ${num(gap, 3)}`)
            .join(" · ")}
        </p>
      )}
      {review?.segments && (
        <>
          <p>
            Segment-aware thresholds for <strong>{review.segmentation?.replaceAll("_", " ")}</strong> (PRD
            19.4, first remedy): the gap would be {num(review.gap_at_segment_thresholds, 3)}, flagging{" "}
            {pct(review.flagged_share_segment_aware)} of customers instead of {pct(review.flagged_share_global)}.
          </p>
          <table>
            <thead>
              <tr>
                <th>Segment</th>
                <th>Rows</th>
                <th>Churners</th>
                <th>Recall, one threshold</th>
                <th>Own threshold</th>
                <th>Recall, own threshold</th>
                <th>Flagged</th>
              </tr>
            </thead>
            <tbody>
              {review.segments.map((seg) => (
                <tr key={seg.segment}>
                  <td>{seg.segment.split("=").pop()}</td>
                  <td>{seg.rows}</td>
                  <td>{seg.positives}</td>
                  <td>{pct(seg.recall_at_global_threshold)}</td>
                  {seg.insufficient_data ? (
                    // DEC-02: fewer than 50 rows or 30 churners is too thin to set a threshold on.
                    <td colSpan={3} className="muted">too few rows or churners for its own threshold</td>
                  ) : (
                    <>
                      <td>{num(seg.segment_threshold, 3)}</td>
                      <td>{pct(seg.recall_at_segment_threshold)}</td>
                      <td>{pct(seg.flagged_share)}</td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          {review.note && <p className="footnote">{review.note}</p>}
        </>
      )}
    </section>
  );
}
