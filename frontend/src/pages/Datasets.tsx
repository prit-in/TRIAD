/**
 * Datasets, versions and the data quality report (PRD section 26.1 "Dataset", FR-068).
 *
 * Every number here comes out of the profile document the profiling stage wrote for that
 * version. Nothing is recomputed in the browser and nothing is filled in: a column whose
 * distinct count was approximated says so, a dataset with no stored profile says that rather
 * than showing an empty table, and the version comparison is the `schema_diff` the profiler
 * recorded against the previous version rather than a diff invented here from two summaries.
 *
 * One honest limitation is on the page itself. The PRD asks for histograms; the profile stores
 * quantiles and top values, not bin counts, so what is drawn is a quantile sketch and a
 * top-value chart, both labelled as such. Binning five quantiles into a histogram would be
 * drawing a distribution nobody measured.
 */

import { useState } from "react";
import { ApiProblem, api } from "../api";
import type { DatasetSummary, DatasetVersionSummary, Profile, ProfileColumn } from "../api";
import { BarChart, LineChart } from "../charts";
import { Empty, Panel, Problem, SearchBox, Tabs, num, pct, useLoad, when } from "../shared";

export function Datasets() {
  const datasets = useLoad<DatasetSummary[]>(() => api.datasets());
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const needle = query.trim().toLowerCase();
  const shown = (datasets.data ?? []).filter(
    (d) =>
      needle === "" ||
      [d.name, d.entity_type ?? "", d.source_type ?? ""].join(" ").toLowerCase().includes(needle),
  );

  return (
    <>
      <Problem error={datasets.problem} />

      <Panel
        title="Datasets"
        provenance="Calculated"
        actions={
          <SearchBox label="Search datasets" value={query} onChange={setQuery}
                     placeholder="name, entity, source…" />
        }
      >
        {datasets.data && shown.length === 0 ? (
          <Empty what={needle ? "datasets matching that" : "registered datasets"} />
        ) : (
          <table>
            <thead>
              <tr>
                <th>Dataset</th>
                <th>Entity</th>
                <th>Source</th>
                <th>Latest version</th>
                <th>Quality score</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((dataset) => (
                <tr key={dataset.id}>
                  <td>
                    <button
                      className="link"
                      aria-expanded={selected === dataset.name}
                      onClick={() => setSelected(selected === dataset.name ? null : dataset.name)}
                    >
                      {dataset.name}
                    </button>
                  </td>
                  <td>{dataset.entity_type ?? "—"}</td>
                  <td>{dataset.source_type ?? "—"}</td>
                  <td>{dataset.latest_version === null ? "never ingested" : `v${dataset.latest_version}`}</td>
                  <td><QualityPill score={dataset.latest_quality_score} /></td>
                  <td>{when(dataset.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      {selected && <DatasetDetail name={selected} />}
    </>
  );
}

/**
 * The quality score with a word beside it.
 *
 * The number alone means nothing to a reader who does not know the gate, and colour alone
 * fails for anyone who cannot separate the hues — so the band is written out.
 */
function QualityPill({ score }: { score: number | null }) {
  if (score === null || score === undefined) return <span className="muted">not profiled</span>;
  const tone = score >= 0.9 ? "good" : score >= 0.7 ? "busy" : "bad";
  const word = score >= 0.9 ? "good" : score >= 0.7 ? "acceptable" : "below the gate";
  return (
    <span className={`pill ${tone}`}>
      {score.toFixed(2)} · {word}
    </span>
  );
}

function DatasetDetail({ name }: { name: string }) {
  const versions = useLoad<DatasetVersionSummary[]>(() => api.datasetVersions(name), [name]);
  const [versionNo, setVersionNo] = useState<number | null>(null);

  const rows = versions.data ?? [];
  const current = versionNo ?? rows[0]?.version_no ?? null;

  return (
    <Panel
      title={`${name} — versions and profile`}
      provenance="Calculated"
      actions={
        rows.length > 1 && (
          <label className="search">
            <span className="sr-only">Version to show</span>
            <select
              aria-label="Version to show"
              value={current ?? ""}
              onChange={(e) => setVersionNo(Number(e.target.value))}
            >
              {rows.map((v) => (
                <option key={v.version_no} value={v.version_no}>
                  v{v.version_no} · {when(v.created_at)}
                </option>
              ))}
            </select>
          </label>
        )
      }
    >
      <Problem error={versions.problem} />
      {versions.data && rows.length === 0 && <Empty what="versions of this dataset" />}

      {rows.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Version</th>
              <th>Rows</th>
              <th>Quality</th>
              <th>Registered</th>
              <th>Checksum</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((v) => (
              <tr key={v.version_no} className={v.version_no === current ? "selected" : undefined}>
                <td>v{v.version_no}</td>
                <td>{v.row_count === null ? "not counted" : v.row_count.toLocaleString()}</td>
                <td><QualityPill score={v.quality_score} /></td>
                <td>{when(v.created_at)}</td>
                {/* The whole digest is what makes two versions comparable; the first twelve
                    characters are enough to read and the title carries the rest. */}
                <td title={v.checksum ?? ""} className="mono">
                  {v.checksum ? `${v.checksum.slice(0, 12)}…` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {current !== null && <ProfileView name={name} versionNo={current} />}
    </Panel>
  );
}

function ProfileView({ name, versionNo }: { name: string; versionNo: number }) {
  const profile = useLoad<Profile>(() => api.datasetProfile(name, versionNo), [name, versionNo]);
  const [column, setColumn] = useState<string | null>(null);

  if (profile.problem) {
    // PRD 26.3: a forbidden section names the role it needs and shows nothing else. "No
    // profile" would be a different and false statement — the profile exists, this account
    // may not read it.
    const forbidden = profile.problem instanceof ApiProblem && profile.problem.status === 403;
    return (
      <p className="muted">
        {forbidden
          ? "Column statistics are limited to the data roles — data engineer, data scientist or ML engineer."
          : `No stored profile for v${versionNo}. ${profile.problem.message}`}
      </p>
    );
  }
  if (!profile.data) return <p className="muted">Loading the stored profile…</p>;

  const p = profile.data;
  const dimensions = Object.entries(p.quality_dimensions);
  const selectedColumn = p.columns.find((c) => c.name === column) ?? p.columns[0] ?? null;
  const diff = p.schema_diff ?? {};
  const hasDiff =
    (diff.added_columns?.length ?? 0) +
      (diff.removed_columns?.length ?? 0) +
      (diff.retyped_columns?.length ?? 0) >
    0 || diff.row_count_delta !== undefined;

  return (
    <Tabs
      label={`${name} v${versionNo} profile`}
      tabs={[
        {
          id: "quality",
          label: "Quality",
          content: (
            <div className="chart-grid">
              <BarChart
                title="Quality dimensions"
                yLabel="Share passing (0–1)"
                yFormat={(v) => v.toFixed(2)}
                bars={dimensions.map(([key, value]) => ({
                  label: key,
                  value,
                  tone: value >= 0.9 ? "good" : value >= 0.7 ? "accent" : "bad",
                }))}
              />
              <dl className="metrics">
                <div>
                  <dt>Overall score</dt>
                  <dd>{num(p.quality_score, 3)}</dd>
                </div>
                <div>
                  <dt>Rows</dt>
                  <dd>{p.row_count.toLocaleString()}</dd>
                </div>
                <div>
                  <dt>Columns</dt>
                  <dd>{p.column_count}</dd>
                </div>
                <div>
                  <dt>Duplicate rows</dt>
                  <dd>{p.duplicate_rows.toLocaleString()}</dd>
                </div>
                <div>
                  <dt>Duplicates on {p.key_columns.join(" + ") || "the key"}</dt>
                  <dd>{p.key_duplicate_rows.toLocaleString()}</dd>
                </div>
                <div>
                  <dt>Profiled</dt>
                  <dd>{when(p.computed_at)}</dd>
                </div>
              </dl>
              {p.sample_fraction !== null && p.sample_fraction < 1 && (
                <p className="footnote">
                  Profiled on a {pct(p.sample_fraction)} sample, seeded so the same version
                  profiles the same way twice. Counts above are scaled from that sample.
                </p>
              )}
            </div>
          ),
        },
        {
          id: "issues",
          label: `Issues (${p.issues.length})`,
          content:
            p.issues.length === 0 ? (
              <p className="muted">
                The profiler raised nothing on this version. That is a statement about the
                rules it applies, not a guarantee about the data.
              </p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Severity</th>
                    <th>Column</th>
                    <th>What was found</th>
                    <th>Evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {p.issues.map((issue) => (
                    <tr key={issue.id}>
                      <td>
                        <span
                          className={`pill ${
                            issue.severity === "blocking" || issue.severity === "error"
                              ? "bad"
                              : issue.severity === "warning"
                                ? "waiting"
                                : "busy"
                          }`}
                        >
                          {issue.severity}
                        </span>
                      </td>
                      <td>{issue.column ?? "—"}</td>
                      <td>{issue.message}</td>
                      {/* The evidence is what turns "quality issue" into something a data
                          engineer can go and look at, so it is shown, not summarised. */}
                      <td className="mono">
                        {Object.entries(issue.evidence)
                          .map(([k, v]) => `${k}=${String(v)}`)
                          .join(", ")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ),
        },
        {
          id: "columns",
          label: `Columns (${p.columns.length})`,
          content: (
            <>
              <table>
                <thead>
                  <tr>
                    <th>Column</th>
                    <th>Type</th>
                    <th>Semantic</th>
                    <th>Nulls</th>
                    <th>Distinct</th>
                    <th>Range</th>
                    <th>Outliers</th>
                  </tr>
                </thead>
                <tbody>
                  {p.columns.map((c) => (
                    <tr key={c.name} className={c.name === selectedColumn?.name ? "selected" : undefined}>
                      <td>
                        <button className="link" onClick={() => setColumn(c.name)}>
                          {c.name}
                        </button>
                      </td>
                      <td className="mono">{c.physical_type}</td>
                      <td>{c.semantic_type}</td>
                      <td>
                        {c.null_count.toLocaleString()}
                        <span className="muted"> ({pct(c.null_rate, 2)})</span>
                      </td>
                      <td>
                        {(c.exact_distinct ?? c.approx_distinct).toLocaleString()}
                        {c.exact_distinct === null && <span className="muted"> approx.</span>}
                      </td>
                      <td className="mono">
                        {c.min_value === null && c.max_value === null
                          ? "—"
                          : `${String(c.min_value)} … ${String(c.max_value)}`}
                      </td>
                      <td>{c.outlier_count === null ? "—" : c.outlier_count.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {selectedColumn && <ColumnDetail column={selectedColumn} />}
            </>
          ),
        },
        {
          id: "diff",
          label: "Compared with the previous version",
          content: !hasDiff ? (
            <p className="muted">
              No comparison was stored for v{versionNo}. The profiler records a schema diff
              against the version before it, so the first version of a dataset has none.
            </p>
          ) : (
            <dl className="metrics">
              <div>
                <dt>Row count change</dt>
                <dd>
                  {diff.row_count_delta === undefined
                    ? "not recorded"
                    : diff.row_count_delta.toLocaleString(undefined, { signDisplay: "exceptZero" })}
                </dd>
              </div>
              <div>
                <dt>Columns added</dt>
                <dd>{diff.added_columns?.join(", ") || "none"}</dd>
              </div>
              <div>
                <dt>Columns removed</dt>
                <dd>{diff.removed_columns?.join(", ") || "none"}</dd>
              </div>
              <div>
                <dt>Columns retyped</dt>
                <dd>
                  {diff.retyped_columns?.length
                    ? diff.retyped_columns.map((r) => `${r.column}: ${r.from} → ${r.to}`).join("; ")
                    : "none"}
                </dd>
              </div>
              <div>
                <dt>Possible renames</dt>
                {/* A proposal, never an applied change: a rename needs approval (ATC-06). */}
                <dd>
                  {diff.rename_candidates?.length
                    ? `${diff.rename_candidates.map((r) => `${r.from} → ${r.to}`).join("; ")} (proposed, needs approval)`
                    : "none proposed"}
                </dd>
              </div>
            </dl>
          ),
        },
      ]}
    />
  );
}

function ColumnDetail({ column }: { column: ProfileColumn }) {
  const quantiles = Object.entries(column.quantiles)
    .map(([label, value]) => [Number(label.replace("p", "")), value] as [number, number])
    .filter(([q, v]) => Number.isFinite(q) && Number.isFinite(v))
    .sort((a, b) => a[0] - b[0]);

  return (
    <div className="chart-grid">
      <h3>{column.name}</h3>
      {column.top_values.length > 0 && (
        <BarChart
          title="Most frequent values"
          yLabel="Rows"
          width={620}
          yFormat={(v) => v.toLocaleString()}
          bars={column.top_values.map((t) => ({
            label: t.value === null ? "(null)" : String(t.value),
            value: t.count,
          }))}
        />
      )}
      {quantiles.length > 1 && (
        <>
          <LineChart
            title="Quantile sketch"
            xLabel="Percentile"
            yLabel={column.name}
            width={620}
            xFormat={(v) => `p${Math.round(v)}`}
            series={[{ label: column.name, points: quantiles, tone: "accent" }]}
          />
          <p className="footnote">
            A quantile sketch, not a histogram: the profile stores p25, p50, p75, p95 and p99,
            so bin counts were never measured and are not drawn. Mean {num(column.mean, 3)},
            standard deviation {num(column.stddev, 3)}.
          </p>
        </>
      )}
      {column.top_values.length === 0 && quantiles.length <= 1 && (
        <p className="muted">
          Neither top values nor quantiles were stored for {column.name}. Top values are
          collected for string columns below a thousand distinct values, quantiles for numeric
          ones — an identifier gets neither, by design.
        </p>
      )}
    </div>
  );
}
