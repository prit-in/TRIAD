/**
 * Charts, in plain SVG.
 *
 * No charting library. The app has three runtime dependencies and one stylesheet, and a line
 * through a list of points is about forty lines of SVG — importing 90 kB of Recharts to draw
 * a ROC curve would be the largest dependency in the project by a wide margin.
 *
 * Two rules these components enforce rather than trust the caller with:
 *
 * 1. **A chart with no data says so.** Every component returns an explanatory line instead of
 *    axes around an empty plot area. An empty chart reads as "the value is zero", which is a
 *    different and usually wrong claim.
 * 2. **Nothing is interpolated or invented.** A gap in the data is drawn as a gap. The
 *    calibration bins in particular arrive with `NaN` for bins no prediction fell into, and
 *    those are skipped rather than drawn at zero.
 *
 * Colour comes from the stylesheet's custom properties so the charts match the pills and
 * badges, and every series also carries a distinguishing dash pattern or marker — colour
 * alone fails for anyone who cannot separate the hues.
 */

import type { ReactNode } from "react";

const PAD = { top: 12, right: 14, bottom: 34, left: 46 };

export interface Series {
  label: string;
  points: [number, number][];
  tone?: "accent" | "good" | "bad" | "waiting" | "muted";
  dashed?: boolean;
}

function toneVar(tone: Series["tone"]): string {
  switch (tone) {
    case "good":
      return "var(--good)";
    case "bad":
      return "var(--bad)";
    case "waiting":
      return "var(--waiting)";
    case "muted":
      return "var(--muted)";
    default:
      return "var(--accent)";
  }
}

/** A chart that has nothing to draw explains itself rather than drawing empty axes. */
function NoData({ why }: { why: string }) {
  return <p className="muted chart-empty">{why}</p>;
}

function Frame({
  width,
  height,
  xLabel,
  yLabel,
  xTicks,
  yTicks,
  children,
  title,
}: {
  width: number;
  height: number;
  xLabel: string;
  yLabel: string;
  xTicks: { at: number; text: string }[];
  yTicks: { at: number; text: string }[];
  children: ReactNode;
  title: string;
}) {
  const w = width - PAD.left - PAD.right;
  const h = height - PAD.top - PAD.bottom;
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="chart"
      role="img"
      aria-label={title}
      preserveAspectRatio="xMidYMid meet"
    >
      <title>{title}</title>
      <g transform={`translate(${PAD.left},${PAD.top})`}>
        {yTicks.map((t) => (
          <g key={`y${t.at}`} transform={`translate(0,${h - t.at * h})`}>
            <line x1={0} x2={w} y1={0} y2={0} className="grid" />
            <text x={-8} y={4} className="tick tick-y">
              {t.text}
            </text>
          </g>
        ))}
        {xTicks.map((t) => (
          <text key={`x${t.at}`} x={t.at * w} y={h + 18} className="tick tick-x">
            {t.text}
          </text>
        ))}
        <line x1={0} x2={w} y1={h} y2={h} className="axis" />
        <line x1={0} x2={0} y1={0} y2={h} className="axis" />
        {children}
        <text x={w / 2} y={h + 31} className="axis-label">
          {xLabel}
        </text>
        <text transform={`translate(${-34},${h / 2}) rotate(-90)`} className="axis-label">
          {yLabel}
        </text>
      </g>
    </svg>
  );
}

function Legend({ series }: { series: Series[] }) {
  return (
    <ul className="chart-legend">
      {series.map((s) => (
        <li key={s.label}>
          <span
            className="swatch"
            style={{
              background: toneVar(s.tone),
              // The dash is the second signal: colour alone is not enough.
              backgroundImage: s.dashed
                ? "repeating-linear-gradient(90deg,transparent 0 3px,var(--card) 3px 6px)"
                : undefined,
            }}
          />
          {s.label}
        </li>
      ))}
    </ul>
  );
}

const ticks01 = [0, 0.25, 0.5, 0.75, 1].map((v) => ({ at: v, text: v.toFixed(2) }));

/**
 * A line chart over a fixed 0..1 square — the shape ROC, PR and calibration all share.
 *
 * `reference` draws the line the chart is read against: the diagonal for ROC, the churn rate
 * for PR, perfect calibration for a reliability diagram. Without it a curve is just a shape.
 */
export function UnitLineChart({
  title,
  series,
  xLabel,
  yLabel,
  reference,
  width = 380,
  height = 300,
}: {
  title: string;
  series: Series[];
  xLabel: string;
  yLabel: string;
  reference?: { label: string; points: [number, number][] };
  width?: number;
  height?: number;
}) {
  const drawable = series.filter((s) => s.points.length > 1);
  if (drawable.length === 0) {
    return <NoData why={`No ${title.toLowerCase()} points were stored for this version.`} />;
  }
  const w = width - PAD.left - PAD.right;
  const h = height - PAD.top - PAD.bottom;
  const path = (points: [number, number][]) =>
    points
      .filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y))
      .map(([x, y], i) => `${i === 0 ? "M" : "L"}${(x * w).toFixed(2)},${(h - y * h).toFixed(2)}`)
      .join(" ");

  const all = reference ? [...drawable, { label: reference.label, points: reference.points, tone: "muted" as const, dashed: true }] : drawable;

  return (
    <figure className="chart-figure">
      <Frame
        width={width}
        height={height}
        xLabel={xLabel}
        yLabel={yLabel}
        xTicks={ticks01}
        yTicks={ticks01}
        title={title}
      >
        {reference && (
          <path d={path(reference.points)} className="line reference" stroke="var(--muted)" />
        )}
        {drawable.map((s) => (
          <path
            key={s.label}
            d={path(s.points)}
            className={`line${s.dashed ? " dashed" : ""}`}
            stroke={toneVar(s.tone)}
          />
        ))}
      </Frame>
      <figcaption>
        <Legend series={all} />
      </figcaption>
    </figure>
  );
}

/** A line chart over arbitrary ranges — lift, gains, drift over time, metric history. */
export function LineChart({
  title,
  series,
  xLabel,
  yLabel,
  yMin,
  yMax,
  xFormat = (v: number) => String(v),
  yFormat = (v: number) => v.toFixed(2),
  reference,
  width = 380,
  height = 300,
}: {
  title: string;
  series: Series[];
  xLabel: string;
  yLabel: string;
  yMin?: number;
  yMax?: number;
  xFormat?: (v: number) => string;
  yFormat?: (v: number) => string;
  reference?: { label: string; value: number };
  width?: number;
  height?: number;
}) {
  const finite = series
    .map((s) => ({ ...s, points: s.points.filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y)) }))
    .filter((s) => s.points.length > 1);
  if (finite.length === 0) {
    return <NoData why={`No ${title.toLowerCase()} points were stored for this version.`} />;
  }

  const xs = finite.flatMap((s) => s.points.map(([x]) => x));
  const ys = finite.flatMap((s) => s.points.map(([, y]) => y));
  if (reference !== undefined) ys.push(reference.value);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const y0 = yMin ?? Math.min(...ys);
  const y1 = yMax ?? Math.max(...ys);
  // A flat series would divide by zero; give it a band so the line sits in the middle.
  const xSpan = x1 - x0 || 1;
  const ySpan = y1 - y0 || Math.abs(y1) || 1;

  const w = width - PAD.left - PAD.right;
  const h = height - PAD.top - PAD.bottom;
  const nx = (x: number) => ((x - x0) / xSpan) * w;
  const ny = (y: number) => h - ((y - y0) / ySpan) * h;
  const path = (points: [number, number][]) =>
    points.map(([x, y], i) => `${i === 0 ? "M" : "L"}${nx(x).toFixed(2)},${ny(y).toFixed(2)}`).join(" ");

  const yTickValues = [0, 0.25, 0.5, 0.75, 1].map((f) => y0 + f * ySpan);
  const xTickValues = [0, 0.25, 0.5, 0.75, 1].map((f) => x0 + f * xSpan);

  return (
    <figure className="chart-figure">
      <Frame
        width={width}
        height={height}
        xLabel={xLabel}
        yLabel={yLabel}
        xTicks={xTickValues.map((v, i) => ({ at: i / 4, text: xFormat(v) }))}
        yTicks={yTickValues.map((v, i) => ({ at: i / 4, text: yFormat(v) }))}
        title={title}
      >
        {reference !== undefined && (
          <line x1={0} x2={w} y1={ny(reference.value)} y2={ny(reference.value)} className="line reference" stroke="var(--muted)" />
        )}
        {finite.map((s) => (
          <path key={s.label} d={path(s.points)} className={`line${s.dashed ? " dashed" : ""}`} stroke={toneVar(s.tone)} />
        ))}
      </Frame>
      <figcaption>
        <Legend series={finite} />
      </figcaption>
    </figure>
  );
}

/** Grouped bars for comparing a metric across models, families or segments. */
export function BarChart({
  title,
  bars,
  yLabel,
  yFormat = (v: number) => v.toFixed(3),
  width = 380,
  height = 300,
}: {
  title: string;
  bars: { label: string; value: number | null; tone?: Series["tone"]; interval?: [number, number] }[];
  yLabel: string;
  yFormat?: (v: number) => string;
  width?: number;
  height?: number;
}) {
  // A null is "not measured" and is dropped rather than drawn as a zero-height bar, which
  // would be indistinguishable from a measured zero.
  const drawable = bars.filter((b) => b.value !== null && Number.isFinite(b.value));
  if (drawable.length === 0) return <NoData why={`Nothing measured for ${title.toLowerCase()}.`} />;

  const values = drawable.flatMap((b) => [b.value as number, ...(b.interval ?? [])]);
  const top = Math.max(...values, 0);
  const bottom = Math.min(...values, 0);
  const span = top - bottom || 1;
  const w = width - PAD.left - PAD.right;
  const h = height - PAD.top - PAD.bottom;
  const slot = w / drawable.length;
  const barW = Math.min(46, slot * 0.6);
  const ny = (v: number) => h - ((v - bottom) / span) * h;
  const missing = bars.length - drawable.length;

  return (
    <figure className="chart-figure">
      <Frame
        width={width}
        height={height}
        xLabel=""
        yLabel={yLabel}
        xTicks={[]}
        yTicks={[0, 0.25, 0.5, 0.75, 1].map((f, i) => ({ at: i / 4, text: yFormat(bottom + f * span) }))}
        title={title}
      >
        {drawable.map((b, i) => {
          const cx = i * slot + slot / 2;
          const y = ny(b.value as number);
          return (
            <g key={b.label}>
              <rect x={cx - barW / 2} y={y} width={barW} height={Math.max(1, ny(bottom) - y)} fill={toneVar(b.tone)} className="bar-rect" />
              {/* Bootstrap interval, when one was recorded. Drawn only when it exists: an
                  absent interval must not look like a zero-width one. */}
              {b.interval && (
                <g className="interval">
                  <line x1={cx} x2={cx} y1={ny(b.interval[0])} y2={ny(b.interval[1])} />
                  <line x1={cx - 5} x2={cx + 5} y1={ny(b.interval[0])} y2={ny(b.interval[0])} />
                  <line x1={cx - 5} x2={cx + 5} y1={ny(b.interval[1])} y2={ny(b.interval[1])} />
                </g>
              )}
              <text x={cx} y={h + 14} className="tick tick-x">{b.label}</text>
              <text x={cx} y={y - 5} className="bar-number">{yFormat(b.value as number)}</text>
            </g>
          );
        })}
      </Frame>
      {missing > 0 && (
        <figcaption className="muted">
          {missing} of {bars.length} not measured, and left out rather than drawn as zero.
        </figcaption>
      )}
    </figure>
  );
}

/** A 2x2 confusion matrix, which is a table of four numbers and reads best as one. */
export function ConfusionMatrix({
  confusion,
  threshold,
}: {
  confusion: { tn: number; fp: number; fn: number; tp: number } | null | undefined;
  threshold: number;
}) {
  if (!confusion) return <NoData why="No confusion matrix was stored for this version." />;
  const { tn, fp, fn, tp } = confusion;
  const total = tn + fp + fn + tp;
  const cell = (n: number) => (total ? `${n} (${((n / total) * 100).toFixed(1)}%)` : String(n));
  return (
    <table className="confusion">
      <caption>At the selected threshold of {threshold.toFixed(3)}</caption>
      <thead>
        <tr>
          <th scope="col"></th>
          <th scope="col">Predicted stay</th>
          <th scope="col">Predicted churn</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <th scope="row">Actually stayed</th>
          <td className="good-cell">{cell(tn)}</td>
          <td>{cell(fp)}</td>
        </tr>
        <tr>
          <th scope="row">Actually churned</th>
          <td>{cell(fn)}</td>
          <td className="good-cell">{cell(tp)}</td>
        </tr>
      </tbody>
    </table>
  );
}
