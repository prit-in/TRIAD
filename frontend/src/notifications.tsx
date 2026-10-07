/**
 * In-app notifications (FR-084: "approvers see pending requests without refreshing").
 *
 * The bell polls the two lists the API already serves — pending approvals and open alerts —
 * every 15 seconds while the tab is visible, and once more when it becomes visible again. No
 * push channel: both lists exist, they are small, and a poll costs less than a socket held open
 * per signed-in user. The webhook half of FR-084 is not built.
 *
 * The number on the bell means "waiting on you": a request this role can decide, plus open
 * alerts that call for someone - warnings and critical ones. Informational alerts (a retraining
 * trigger held by its cooldown) are listed, not counted: on the live stack 21 alerts were open
 * and most were those, so a count of everything said nothing. A share of a two-approver request
 * that belongs to the other role is listed, not counted either - the server would refuse this
 * user's decision on it (`required_role`).
 */

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "./api";
import type { Alert, Approval, User } from "./api";
import { useToast, when } from "./shared";

const POLL_MS = 15_000;
// The roles `/approvals` admits as deciders (backend/app/security.py APPROVER_ROLES).
const APPROVER_ROLES = ["ml_engineer", "data_engineer", "admin"];
const SHOWN = 5;
const needsSomeone = (a: Alert) => a.severity !== "info";

export function Notifications({ user }: { user: User }) {
  const toast = useToast();
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [checkedAt, setCheckedAt] = useState<Date | null>(null);
  const [open, setOpen] = useState(false);
  const known = useRef<Set<string> | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const approver = APPROVER_ROLES.includes(user.role);

  const mine = (a: Approval) => !a.required_role || a.required_role === user.role;

  useEffect(() => {
    let live = true;
    async function poll() {
      if (document.visibilityState !== "visible") return;
      // A list that fails to load keeps its last value: a dropped poll is not "nothing pending".
      const [pending, openAlerts] = await Promise.all([
        approver ? api.approvals("pending").catch(() => null) : Promise.resolve([]),
        api.alerts("open").catch(() => null),
      ]);
      if (!live) return;
      if (pending) setApprovals(pending);
      if (openAlerts) setAlerts(openAlerts);
      setCheckedAt(new Date());

      // Announce what arrived since the last poll, never what was already there at sign-in.
      const ids = [...(pending ?? []).filter(mine).map((a) => a.id), ...(openAlerts ?? []).map((a) => a.id)];
      const seen = known.current;
      if (seen) {
        const fresh = (pending ?? []).filter((a) => mine(a) && !seen.has(a.id));
        const [request] = fresh;
        if (request) {
          toast(fresh.length === 1
            ? `New approval request: ${request.policy_action ?? request.action_type}`
            : `${fresh.length} new approval requests`);
        }
        const freshAlerts = (openAlerts ?? []).filter((a) => needsSomeone(a) && !seen.has(a.id));
        const [alert] = freshAlerts;
        if (alert) {
          toast(freshAlerts.length === 1 ? `Alert: ${alert.message}` : `${freshAlerts.length} new alerts`, "bad");
        }
      }
      known.current = new Set([...(seen ?? []), ...ids]);
    }
    void poll();
    const timer = window.setInterval(poll, POLL_MS);
    document.addEventListener("visibilitychange", poll);
    return () => {
      live = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", poll);
    };
    // `mine` and `toast` are stable for a given user; re-subscribing on them would re-poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [approver, user.role]);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === "Escape"
          : !box.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  const yours = approvals.filter(mine);
  const others = approvals.length - yours.length;
  const urgent = alerts.filter(needsSomeone);
  const count = yours.length + urgent.length;

  return (
    <div className="notify" ref={box}>
      <button
        type="button"
        className="bell"
        aria-expanded={open}
        aria-label={`Notifications: ${count} waiting on you`}
        onClick={() => setOpen((v) => !v)}
      >
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <path fill="currentColor" d="M12 22a2.5 2.5 0 0 0 2.45-2h-4.9A2.5 2.5 0 0 0 12 22Zm7-6V11a7 7 0 0 0-5.5-6.84V3a1.5 1.5 0 0 0-3 0v1.16A7 7 0 0 0 5 11v5l-2 2v1h18v-1l-2-2Z" />
        </svg>
        {count > 0 && <span className="badge">{count > 99 ? "99+" : count}</span>}
      </button>
      {open && (
        <div className="notify-panel" role="dialog" aria-label="Notifications">
          {approver && (
            <section>
              <h3>Waiting on you</h3>
              {yours.length === 0 ? (
                <p className="muted">No approval needs your role.</p>
              ) : (
                <ul>
                  {yours.slice(0, SHOWN).map((a) => (
                    <li key={a.id}>
                      <Link to="/approvals" onClick={() => setOpen(false)}>
                        {a.policy_action ?? a.action_type}
                      </Link>{" "}
                      <span className="muted">{a.target_ref} · {when(a.requested_at)}</span>
                    </li>
                  ))}
                </ul>
              )}
              {others > 0 && (
                <p className="muted">{others} more waiting on another role.</p>
              )}
            </section>
          )}
          <section>
            <h3>Open alerts</h3>
            {alerts.length === 0 ? (
              <p className="muted">None open.</p>
            ) : (
              <ul>
                {[...urgent, ...alerts.filter((a) => !needsSomeone(a))].slice(0, SHOWN).map((a) => (
                  <li key={a.id}>
                    <span className={`pill ${a.severity === "critical" ? "bad" : "busy"}`}>
                      {a.severity}
                    </span>{" "}
                    <Link to="/monitoring" onClick={() => setOpen(false)}>{a.message}</Link>
                  </li>
                ))}
              </ul>
            )}
            {alerts.length > SHOWN && (
              <p className="muted">
                <Link to="/monitoring" onClick={() => setOpen(false)}>
                  {alerts.length - SHOWN} more on the Monitoring page
                </Link>
              </p>
            )}
          </section>
          <p className="footnote">
            Checked every 15 seconds{checkedAt ? ` · last at ${checkedAt.toLocaleTimeString()}` : ""}
          </p>
        </div>
      )}
    </div>
  );
}
