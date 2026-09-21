/**
 * The pieces every page needs, including the two that the PRD's interface principles turn
 * into a rule: a number is shown with where it came from, and "no data" is said plainly
 * rather than drawn as a zero.
 */

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
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

/**
 * A bar chart built from div widths. A chart library for five bars is a dependency nobody
 * needs — but this is not SVG, whatever this comment used to claim, and the difference
 * matters to anyone looking for the `<svg>` that was never here.
 */
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

/**
 * Tabs, keyboard-navigable.
 *
 * The ARIA tab pattern expects arrow keys to move between tabs and only the active tab to be
 * in the natural tab order, so a keyboard user reaches the group once and then moves within
 * it. Getting that wrong makes a tab strip a trap, which is worse than no tabs at all.
 */
export function Tabs({
  label,
  tabs,
}: {
  label: string;
  tabs: { id: string; label: string; content: ReactNode }[];
}) {
  const [active, setActive] = useState(tabs[0]?.id ?? "");
  if (tabs.length === 0) return null;
  const index = Math.max(0, tabs.findIndex((t) => t.id === active));

  const move = (delta: number) => {
    const next = tabs[(index + delta + tabs.length) % tabs.length];
    if (next) {
      setActive(next.id);
      document.getElementById(`tab-${next.id}`)?.focus();
    }
  };

  return (
    <div className="tabs">
      <div role="tablist" aria-label={label} className="tablist">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            id={`tab-${tab.id}`}
            role="tab"
            type="button"
            aria-selected={tab.id === active}
            aria-controls={`panel-${tab.id}`}
            tabIndex={tab.id === active ? 0 : -1}
            className={tab.id === active ? "tab active" : "tab"}
            onClick={() => setActive(tab.id)}
            onKeyDown={(event) => {
              if (event.key === "ArrowRight") { event.preventDefault(); move(1); }
              if (event.key === "ArrowLeft") { event.preventDefault(); move(-1); }
              if (event.key === "Home") { event.preventDefault(); setActive(tabs[0]!.id); }
              if (event.key === "End") { event.preventDefault(); setActive(tabs[tabs.length - 1]!.id); }
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>
      {tabs.map((tab) => (
        <div
          key={tab.id}
          id={`panel-${tab.id}`}
          role="tabpanel"
          aria-labelledby={`tab-${tab.id}`}
          hidden={tab.id !== active}
          tabIndex={0}
        >
          {/* Mounted only when shown: each panel loads its own data, and mounting five at
              once would fire five requests for four panels nobody is looking at. */}
          {tab.id === active && tab.content}
        </div>
      ))}
    </div>
  );
}

/**
 * A modal confirmation, for actions that cannot be taken back.
 *
 * Cancelling a run used to fire on the first click with nothing in between, which for an
 * irreversible action is a misclick away from losing work. Built on `<dialog>` so the browser
 * supplies the focus trap, the backdrop and Escape-to-close rather than this file
 * reimplementing them badly.
 */
export function ConfirmButton({
  label,
  title,
  question,
  confirmLabel,
  onConfirm,
  className = "link",
  disabled = false,
}: {
  label: string;
  title: string;
  question: string;
  confirmLabel: string;
  onConfirm: () => Promise<unknown> | void;
  className?: string;
  disabled?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [busy, setBusy] = useState(false);

  return (
    <>
      <button type="button" className={className} disabled={disabled}
              onClick={() => ref.current?.showModal()}>
        {label}
      </button>
      <dialog ref={ref} className="confirm">
        <h2>{title}</h2>
        <p>{question}</p>
        <div className="confirm-actions">
          <button type="button" className="secondary" onClick={() => ref.current?.close()}>
            Keep it
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await onConfirm();
                ref.current?.close();
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </dialog>
    </>
  );
}

/**
 * Transient confirmations for actions that succeeded.
 *
 * Before this, the only sign an Approve or an Acknowledge had worked was a list quietly
 * re-rendering — indistinguishable from a click that missed. Announced politely so a screen
 * reader hears it without being interrupted mid-sentence.
 */
const ToastContext = createContext<(message: string, tone?: "good" | "bad") => void>(() => {});

export function useToast() {
  return useContext(ToastContext);
}

export function ToastHost({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<{ id: number; message: string; tone: string }[]>([]);
  const next = useRef(0);

  const push = useCallback((message: string, tone: "good" | "bad" = "good") => {
    const id = (next.current += 1);
    setToasts((current) => [...current, { id, message, tone }]);
    window.setTimeout(() => setToasts((c) => c.filter((t) => t.id !== id)), 5000);
  }, []);

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast ${toast.tone}`}>
            {toast.message}
            <button type="button" className="link" aria-label="Dismiss"
                    onClick={() => setToasts((c) => c.filter((t) => t.id !== toast.id))}>
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

/** A labelled search box that filters a list in the browser (FR-070). */
export function SearchBox({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="search">
      <span className="sr-only">{label}</span>
      <input type="search" value={value} placeholder={placeholder ?? label}
             aria-label={label} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

/** A determinate progress bar, for a budget or a share of a limit. */
export function Meter({ label, value, limit, format }: {
  label: string;
  value: number;
  limit: number | null | undefined;
  format?: (v: number) => string;
}) {
  const show = format ?? ((v: number) => String(v));
  if (limit === null || limit === undefined || limit <= 0) {
    // Without a limit there is no proportion to draw, and a full bar would be a lie.
    return <p className="meter-text">{label}: {show(value)} (no limit recorded)</p>;
  }
  const share = Math.min(1, value / limit);
  return (
    <div className="meter">
      <span className="meter-label">{label}</span>
      <progress value={value} max={limit} aria-label={`${label}: ${show(value)} of ${show(limit)}`} />
      <span className="meter-value">
        {show(value)} / {show(limit)} ({(share * 100).toFixed(0)}%)
      </span>
    </div>
  );
}
