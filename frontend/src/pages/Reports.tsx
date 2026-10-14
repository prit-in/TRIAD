/**
 * Generated reports and their claim check (PRD section 26.1 "Report", FR-071, FR-072).
 *
 * The claim check is the thing worth putting first. A report is refused publication if it
 * contains a digit that no fact sheet entry backs, a leftover placeholder, or a causal phrase
 * the evidence does not support — so the panel shows what was verified and what was refused,
 * not a green tick.
 *
 * The document itself is fetched with the bearer token and rendered in a sandboxed frame. It
 * cannot be an `<iframe src>`: the endpoint is authenticated and a frame sends no header. The
 * sandbox has no permissions at all — the report is static HTML and has no reason to run a
 * script, reach the network, or navigate the page that embeds it. PDF export prints the same
 * document from a second frame (`PdfButton`).
 */

import { useEffect, useState } from "react";
import { api } from "../api";
import type { Report } from "../api";
import { Empty, Panel, Problem, Tabs, useLoad, when } from "../shared";
import { Link } from "react-router-dom";

export function Reports() {
  const reports = useLoad<Report[]>(() => api.reports());
  const [open, setOpen] = useState<Report | null>(null);

  const rows = reports.data ?? [];
  const current = open && rows.some((r) => r.id === open.id) ? open : (rows[0] ?? null);

  return (
    <>
      <Problem error={reports.problem} />

      <Panel title="Reports" provenance="Model output">
        {!reports.data ? (
          reports.loading && <Empty what="generated reports" loading />
        ) : rows.length === 0 ? (
          <Empty what="generated reports" />
        ) : (
          <table>
            <thead>
              <tr>
                <th>Report</th>
                <th>Run</th>
                <th>Format</th>
                <th>Status</th>
                <th>Claim check</th>
                <th>Generated</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((report) => (
                <tr key={report.id} className={report.id === current?.id ? "selected" : undefined}>
                  <td>
                    <button className="link" onClick={() => setOpen(report)}>
                      {report.report_type} report
                    </button>
                  </td>
                  <td>
                    <Link to={`/runs/${report.run_id}`}>{report.run_id.slice(0, 8)}…</Link>
                  </td>
                  <td>{report.format.toUpperCase()}</td>
                  <td>{report.status}</td>
                  <td>
                    <span className={`pill ${report.claim_check_status === "passed" ? "good" : "bad"}`}>
                      {report.claim_check_status ?? "not checked"}
                    </span>
                  </td>
                  <td>{when(report.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      {current && <ReportDetail report={current} />}
    </>
  );
}

function ReportDetail({ report }: { report: Report }) {
  const check = report.claim_check_json ?? {};
  const refused = [
    ["Numbers with no fact sheet entry", check.unverified_numbers ?? []],
    ["Placeholders never filled in", check.unknown_placeholders ?? []],
    ["Causal phrases the evidence does not support", check.causal_phrases ?? []],
  ] as const;

  return (
    <Panel
      title={`${report.report_type} report — ${report.run_id.slice(0, 8)}…`}
      provenance="Model output"
      actions={
        <>
          <DownloadLink report={report} />
          {report.format === "html" && <PdfButton report={report} />}
        </>
      }
    >
      <Tabs
        label="Report detail"
        tabs={[
          {
            id: "document",
            label: "Document",
            content: <ReportDocument id={report.id} />,
          },
          {
            id: "claims",
            label: "Claim check",
            content: (
              <>
                <dl className="metrics">
                  <div>
                    <dt>Verdict</dt>
                    <dd>{report.claim_check_status ?? "not checked"}</dd>
                  </div>
                  <div>
                    <dt>Figures traced to a measurement</dt>
                    <dd>{check.facts_verified ?? "not recorded"}</dd>
                  </div>
                </dl>
                {refused.map(([label, items]) => (
                  <p key={label} className={items.length ? "problem" : "muted"}>
                    <strong>{label}:</strong>{" "}
                    {items.length ? items.join(", ") : "none"}
                  </p>
                ))}
                <p className="footnote">
                  The check runs before publication, not after. A report that fails it is not
                  written — which is why a passing check here is the normal case and an
                  interesting one would have stopped the run.
                </p>
              </>
            ),
          },
          {
            id: "provenance",
            label: "Provenance",
            content: (
              <dl className="metrics">
                <div>
                  <dt>Run</dt>
                  <dd>
                    <Link to={`/runs/${report.run_id}`}>{report.run_id}</Link>
                  </dd>
                </div>
                <div>
                  <dt>Stored at</dt>
                  <dd className="mono">{report.storage_uri}</dd>
                </div>
                <div>
                  <dt>Format</dt>
                  <dd>{report.format}</dd>
                </div>
                <div>
                  <dt>Generated</dt>
                  <dd>{when(report.created_at)}</dd>
                </div>
              </dl>
            ),
          },
        ]}
      />
    </Panel>
  );
}

/**
 * Save the document.
 *
 * A blob URL rather than a link straight at the API, for the same reason the frame uses
 * `srcDoc`: the download would arrive as a 401 page otherwise. The URL is revoked once the
 * click is done, so a page left open does not pin the document in memory.
 */
function DownloadLink({ report }: { report: Report }) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Error | null>(null);

  if (report.format !== "html") {
    // PDF is not produced by this pipeline. Saying so beats a button that 404s.
    return <span className="muted">{report.format.toUpperCase()} export is not produced.</span>;
  }

  return (
    <>
      <button
        type="button"
        className="secondary"
        disabled={busy}
        onClick={() => {
          setBusy(true);
          api
            .reportHtml(report.id)
            .then((html) => {
              const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
              const anchor = document.createElement("a");
              anchor.href = url;
              anchor.download = `${report.report_type}-report-${report.run_id.slice(0, 8)}.html`;
              anchor.click();
              URL.revokeObjectURL(url);
              setProblem(null);
            })
            .catch((error: Error) => setProblem(error))
            .finally(() => setBusy(false));
        }}
      >
        {busy ? "Fetching…" : "Download HTML"}
      </button>
      <Problem error={problem} />
    </>
  );
}

/**
 * Save as PDF (FR-072: "HTML and PDF export; exports match the on-screen report").
 *
 * The PDF is printed by the browser from the very document the page shows, so it cannot drift
 * from it - a second renderer on the server would be a second report to keep in step. Printing
 * needs a script, and the frame that displays the report runs none, so the print happens in a
 * separate, invisible frame: `allow-scripts allow-modals` and no `allow-same-origin`, which
 * leaves the document an opaque origin that cannot reach this page, its token or the API. The
 * only script in it is the one added here.
 */
function PdfButton({ report }: { report: Report }) {
  const [doc, setDoc] = useState<{ html: string; key: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Error | null>(null);

  return (
    <>
      <button
        type="button"
        className="secondary"
        disabled={busy}
        title="Opens the print dialog; choose Save as PDF"
        onClick={() => {
          setBusy(true);
          api
            .reportHtml(report.id)
            .then((html) => {
              const hook = "<script>addEventListener('load', () => print());</script>";
              const printable = html.includes("</body>")
                ? html.replace("</body>", `${hook}</body>`)
                : html + hook;
              setDoc({ html: printable, key: Date.now() });
              setProblem(null);
            })
            .catch((error: Error) => setProblem(error))
            .finally(() => setBusy(false));
        }}
      >
        {busy ? "Preparing…" : "Save as PDF"}
      </button>
      {doc && (
        <iframe
          key={doc.key}
          className="print-frame"
          title="Printable report"
          sandbox="allow-scripts allow-modals"
          srcDoc={doc.html}
        />
      )}
      <Problem error={problem} />
    </>
  );
}

function ReportDocument({ id }: { id: string }) {
  const [html, setHtml] = useState<string | null>(null);
  const [problem, setProblem] = useState<Error | null>(null);

  useEffect(() => {
    let live = true;
    setHtml(null);
    setProblem(null);
    api
      .reportHtml(id)
      .then((text) => live && setHtml(text))
      .catch((error: Error) => live && setProblem(error));
    return () => {
      live = false;
    };
  }, [id]);

  if (problem) return <Problem error={problem} />;
  if (html === null) return <p className="muted">Fetching the document…</p>;
  return (
    <iframe
      className="report-frame"
      title="Report document"
      // No `allow-scripts`, no `allow-same-origin`: a report is static HTML, and a frame that
      // has both is not sandboxed at all.
      sandbox=""
      srcDoc={html}
    />
  );
}
