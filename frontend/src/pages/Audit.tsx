/**
 * The audit trail and its integrity check (FR-078, ATC-15, admin only).
 *
 * Two things a reader needs and neither is a list of rows on its own: whether the chain still
 * verifies, and what happened. The verification runs server-side over every entry, and this
 * page reports its answer rather than restating it — a green tick drawn without asking would
 * be exactly the failure the chain exists to make impossible.
 *
 * The trail is one hash chain per run plus a `system` chain, so `chains_checked` is a count of
 * runs and not a sign of fragmentation.
 */

import { useState } from "react";
import { api } from "../api";
import type { AuditEntry, ChainStatus } from "../api";
import { Empty, Panel, Problem, SearchBox, Tabs, useLoad, when } from "../shared";

export function Audit() {
  const chain = useLoad<ChainStatus>(() => api.chainStatus());
  const [action, setAction] = useState("");
  // `since` is a filter the API has accepted since it was written and nothing ever sent. A
  // trail capped at 200 rows without one can only show the most recent activity, which is the
  // opposite of what an investigation needs.
  const [since, setSince] = useState("");
  const entries = useLoad<AuditEntry[]>(() => {
    const params = new URLSearchParams({ limit: "200" });
    if (action) params.set("action", action);
    // A date input gives a local calendar day; the column is a timestamp with a zone, so the
    // day is anchored at its own midnight rather than sent as a bare date.
    if (since) params.set("since", new Date(`${since}T00:00:00`).toISOString());
    return api.auditLogs(`?${params.toString()}`);
  }, [action, since]);
  const [query, setQuery] = useState("");

  const needle = query.trim().toLowerCase();
  const shown = (entries.data ?? []).filter(
    (e) =>
      needle === "" ||
      [e.action, e.actor_id, e.resource_type, e.resource_id, e.outcome]
        .join(" ")
        .toLowerCase()
        .includes(needle),
  );

  // The distinct actions present, so the filter offers what is actually there rather than a
  // hardcoded list that drifts from the code.
  const actions = [...new Set((entries.data ?? []).map((e) => e.action))].sort();

  return (
    <>
      <Problem error={chain.problem ?? entries.problem} />

      <Panel title="Chain integrity" provenance="Governed">
        {!chain.data ? (
          <p className="muted">Verifying the chain…</p>
        ) : (
          <>
            <p>
              <span className={`pill ${chain.data.ok ? "good" : "bad"}`}>
                {chain.data.ok ? "verified" : "BROKEN"}
              </span>{" "}
              {chain.data.entries_checked} entries across {chain.data.chains_checked} chains
              recomputed and compared.
            </p>
            {!chain.data.ok && (
              <p className="problem">
                <strong>The chain does not verify.</strong> First broken entry:{" "}
                {chain.data.broken_at ?? "unknown"} in chain{" "}
                {chain.data.broken_scope ?? "unknown"}. {chain.data.detail ?? ""}
              </p>
            )}
            <p className="footnote">
              One chain per run, plus a <code>system</code> chain, so the number of chains is
              the number of runs that have written an entry — appending under a per-chain lock
              is what lets concurrent runs write without forking a shared chain. A whole chain
              being removed is not detected by this check; that needs a periodic seal over the
              chain heads, which is recorded as a known limit rather than implied here.
            </p>
          </>
        )}
      </Panel>

      <Panel
        title="Audit entries"
        provenance="Governed"
        actions={
          <>
            <select value={action} onChange={(e) => setAction(e.target.value)}>
              <option value="">every action</option>
              {actions.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>{" "}
            <label className="search">
              <span className="sr-only">Entries on or after</span>
              <input type="date" value={since} aria-label="Entries on or after"
                     onChange={(e) => setSince(e.target.value)} />
            </label>{" "}
            {since && (
              <button type="button" className="link" onClick={() => setSince("")}>
                Clear date
              </button>
            )}{" "}
            <SearchBox label="Search the trail" value={query} onChange={setQuery}
                       placeholder="actor, resource, outcome…" />
          </>
        }
      >
        <Tabs
          label="Audit detail"
          tabs={[
            {
              id: "entries",
              label: `Entries (${shown.length})`,
              content:
                shown.length === 0 ? (
                  <Empty what="audit entries matching that" />
                ) : (
                  <table>
                    <thead>
                      <tr>
                        <th>When</th>
                        <th>Actor</th>
                        <th>Action</th>
                        <th>Resource</th>
                        <th>Outcome</th>
                        <th>Entry hash</th>
                      </tr>
                    </thead>
                    <tbody>
                      {shown.map((entry) => (
                        <tr key={entry.id}>
                          <td>{when(entry.ts)}</td>
                          <td>
                            <span className="pill busy">{entry.actor_type}</span>{" "}
                            {entry.actor_id}
                          </td>
                          <td className="mono">{entry.action}</td>
                          <td>
                            {entry.resource_type}{" "}
                            <span className="mono">{entry.resource_id.slice(0, 18)}</span>
                          </td>
                          <td>
                            <span
                              className={`pill ${
                                entry.outcome === "ok" ? "good"
                                : ["denied", "refused", "rejected"].includes(entry.outcome) ? "bad"
                                : "busy"
                              }`}
                            >
                              {entry.outcome}
                            </span>
                          </td>
                          <td className="mono">{entry.entry_hash.slice(0, 12)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ),
            },
            {
              id: "refusals",
              label: "Refusals",
              content: (() => {
                const refused = shown.filter((e) => e.outcome !== "ok");
                return refused.length === 0 ? (
                  <p className="muted">
                    Nothing was refused in these {shown.length} entries. Every governance
                    refusal — a denied policy action, a self-approval, a tool an agent may not
                    call — lands in this trail, so an empty list here means none occurred, not
                    that none was recorded.
                  </p>
                ) : (
                  <ul className="decisions">
                    {refused.map((entry) => (
                      <li key={entry.id}>
                        <span className="pill bad">{entry.outcome}</span>
                        <strong>{entry.action}</strong>
                        <span className="muted">
                          {" "}
                          — {entry.actor_id} on {entry.resource_type} · {when(entry.ts)}
                        </span>
                        {entry.details?.error ? (
                          <span className="rules">{String(entry.details.error).slice(0, 200)}</span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                );
              })(),
            },
          ]}
        />
      </Panel>
    </>
  );
}
