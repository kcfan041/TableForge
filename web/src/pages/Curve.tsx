import type { CurvePoint } from "../api";
import { useI18n } from "../i18n";

const COLORS: Record<string, string> = {
  random: "#6b7280",
  call: "#2563eb",
  tight: "#dc2626",
  aggro: "#d97706",
};
const ORDER = ["random", "call", "tight", "aggro"];

export interface Series {
  label: string;
  points: CurvePoint[];
  dashed?: boolean;
}

/** bb/100 against each rule bot after every round, with 95% interval bands. */
export function TrainingCurve({ series, opponents = ORDER }: { series: Series[]; opponents?: string[] }) {
  const { t, formatNumber } = useI18n();
  const all = series.flatMap((item) => item.points);
  if (all.length === 0) return <p className="muted">{t("curve.empty")}</p>;
  const rounds = all.map((point) => point.round);
  const values = all.flatMap((point) =>
    opponents.flatMap((name) => {
      const cell = point.benchmark[name];
      return cell ? [cell.low, cell.high] : [];
    }),
  );
  if (values.length === 0) return <p className="muted">{t("curve.noBenchmark")}</p>;
  const width = 760;
  const height = 300;
  const pad = { left: 56, right: 16, top: 16, bottom: 36 };
  const minRound = Math.min(...rounds);
  const maxRound = Math.max(...rounds, minRound + 1);
  const low = Math.min(0, ...values);
  const high = Math.max(0, ...values);
  const span = high - low || 1;
  const x = (round: number) =>
    pad.left + ((round - minRound) / (maxRound - minRound)) * (width - pad.left - pad.right);
  const y = (value: number) => pad.top + ((high - value) / span) * (height - pad.top - pad.bottom);
  const ticks = niceTicks(low, high, 5);

  return (
    <div className="curve">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={t("curve.title")}>
        {ticks.map((tick) => (
          <g key={tick}>
            <line
              x1={pad.left}
              x2={width - pad.right}
              y1={y(tick)}
              y2={y(tick)}
              className={tick === 0 ? "axis-zero" : "grid-line"}
            />
            <text x={pad.left - 6} y={y(tick) + 4} textAnchor="end" className="tick">
              {formatNumber(tick)}
            </text>
          </g>
        ))}
        {Array.from(new Set(rounds))
          .sort((a, b) => a - b)
          .map((round) => (
            <text key={round} x={x(round)} y={height - pad.bottom + 18} textAnchor="middle" className="tick">
              {round}
            </text>
          ))}
        <text x={(width + pad.left) / 2} y={height - 4} textAnchor="middle" className="tick">
          {t("curve.round")}
        </text>
        {series.map((item) =>
          opponents.map((name) => {
            const points = item.points
              .filter((point) => point.benchmark[name])
              .sort((a, b) => a.round - b.round);
            if (points.length === 0) return null;
            const band =
              points.map((point) => `${x(point.round)},${y(point.benchmark[name]!.high)}`).join(" ") +
              " " +
              points
                .slice()
                .reverse()
                .map((point) => `${x(point.round)},${y(point.benchmark[name]!.low)}`)
                .join(" ");
            const line = points.map((point) => `${x(point.round)},${y(point.benchmark[name]!.bb_per_100)}`).join(" ");
            const color = COLORS[name] ?? "#10b981";
            return (
              <g key={`${item.label}-${name}`}>
                {series.length === 1 ? <polygon points={band} fill={color} opacity={0.12} /> : null}
                <polyline
                  points={line}
                  fill="none"
                  stroke={color}
                  strokeWidth={2}
                  strokeDasharray={item.dashed ? "5 4" : undefined}
                />
                {points.map((point) => (
                  <circle key={point.round} cx={x(point.round)} cy={y(point.benchmark[name]!.bb_per_100)} r={3} fill={color}>
                    <title>
                      {t("curve.pointTitle", {
                        label: item.label,
                        opponent: name,
                        round: point.round,
                        value: formatNumber(point.benchmark[name]!.bb_per_100),
                        low: formatNumber(point.benchmark[name]!.low),
                        high: formatNumber(point.benchmark[name]!.high),
                      })}
                    </title>
                  </circle>
                ))}
              </g>
            );
          }),
        )}
      </svg>
      <div className="legend">
        {opponents.map((name) => (
          <span key={name}>
            <span className="swatch" style={{ background: COLORS[name] ?? "#10b981" }} /> {name}
          </span>
        ))}
        {series.length > 1
          ? series.map((item) => (
              <span key={item.label} className="muted small">
                {item.dashed ? "- - -" : "——"} {item.label}
              </span>
            ))
          : null}
      </div>
      <p className="muted small">{t("curve.help")}</p>
    </div>
  );
}

function niceTicks(low: number, high: number, count: number): number[] {
  const raw = (high - low) / count || 1;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((factor) => factor * magnitude).find((value) => value >= raw) ?? raw;
  const ticks: number[] = [];
  for (let value = Math.ceil(low / step) * step; value <= high + 1e-9; value += step) {
    ticks.push(Math.round(value));
  }
  return ticks;
}

/** Summary table for the latest round of several experiments (e.g. one per seed). */
export function LatestBenchmark({ series, opponents = ORDER }: { series: Series[]; opponents?: string[] }) {
  const { t, formatNumber } = useI18n();
  return (
    <table className="metrics">
      <thead>
        <tr>
          <th />
          <th>{t("curve.round")}</th>
          {opponents.map((name) => (
            <th key={name}>{name}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {series.map((item) => {
          const last = item.points.slice().sort((a, b) => b.round - a.round)[0];
          return (
            <tr key={item.label}>
              <th>{item.label}</th>
              <td>{last ? last.round : "–"}</td>
              {opponents.map((name) => {
                const cell = last?.benchmark[name];
                if (!cell) return <td key={name}>–</td>;
                const significant = cell.low > 0 || cell.high < 0;
                return (
                  <td
                    key={name}
                    className={significant ? (cell.bb_per_100 > 0 ? "positive-text" : "negative-text") : ""}
                    title={`${formatNumber(cell.low)} – ${formatNumber(cell.high)}`}
                  >
                    {formatNumber(cell.bb_per_100)}
                  </td>
                );
              })}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
