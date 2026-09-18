/**
 * The pieces every page needs, including the two that the PRD's interface principles turn
 * into a rule: a number is shown with where it came from, and "no data" is said plainly
 * rather than drawn as a zero.
 */

import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { ApiProblem } from "./api";

export function useLoad<T>(load: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [problem, setProblem] = useState<ApiProblem | Error | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(() => {
    setLoading(true);
    load()
      .then((value) => {
        setData(value);
        setProblem(null);
      })
      .catch((error: Error) => setProblem(error))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(refresh, [refresh]);
  return { data, problem, loading, refresh };
}

export function Panel({
  title,
  children,
  actions,
  provenance,
}: {
  title: string;
  children: ReactNode;
  actions?: ReactNode;
  provenance?: string;
}) {
  return (
    <section className="card">
      <div className="card-head">
        <h2>{title}</h2>
        {provenance && <span className="provenance">{provenance}</span>}
        {actions}
      </div>
      {children}
    </section>
  );
}

export function Problem({ error }: { error: ApiProblem | Error | null }) {
  if (!error) return null;
  const title = error instanceof ApiProblem ? error.title : "Could not reach the server";
  return (
    <p className="problem">
      <strong>{title}.</strong> {error.message}
    </p>
  );
}

/** Says there is nothing, rather than drawing an empty chart that looks like a zero. */
export function Empty({ what }: { what: string }) {
  return <p className="muted">No {what} yet.</p>;
}

export function Tile({
  label,
  value,
  note,
}: {
  label: string;
  value: number | string | null;
  note?: string;
}) {
  return (
    <div className="tile">
      <span className="tile-label">{label}</span>
      <span className="tile-value">{value === null ? "not measured" : value}</span>
      {note && <span className="tile-note">{note}</span>}
    </div>
  );
}

export function State({ value }: { value: string }) {
  const tone =
    value === "PUBLISHED" || value === "COMPLETED"
      ? "good"
      : value === "ESCALATED" || value === "FAILED"
        ? "bad"
        : value === "APPROVAL_REQUIRED"
          ? "waiting"
          : "busy";
  return <span className={`pill ${tone}`}>{value.replaceAll("_", " ").toLowerCase()}</span>;
}

export function when(value: string | null): string {
  if (!value) return "—";
  const at = new Date(value);
  return Number.isNaN(at.getTime()) ? value : at.toLocaleString();
}

export function pct(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? "not measured" : `${(value * 100).toFixed(digits)}%`;
}

export function num(value: number | null | undefined, digits = 4): string {
  return value === null || value === undefined ? "not measured" : value.toFixed(digits);
}

/** A bar chart in plain SVG. A chart library for five bars is a dependency nobody needs. */
export function Bars({ data }: { data: { label: string; value: number }[] }) {
  const highest = Math.max(...data.map((d) => d.value), 1);
  return (
    <div className="bars">
      {data.map((d) => (
        <div className="bar-row" key={d.label}>
          <span className="bar-label">{d.label}</span>
          <span className="bar-track">
            <span className="bar-fill" style={{ width: `${(d.value / highest) * 100}%` }} />
          </span>
          <span className="bar-value">{d.value}</span>
        </div>
      ))}
    </div>
  );
}
