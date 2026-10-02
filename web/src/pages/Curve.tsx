import type { CurvePoint } from "../api";
import { useI18n, type MessageKey } from "../i18n";

const COLORS: Record<string, string> = {
  random: "#6b7280",
  call: "#2563eb",
  tight: "#dc2626",
  aggro: "#d97706",
  average: "#059669",
  best_response: "#7c3aed",
  sample: "#059669",
  greedy: "#7c3aed",
  pool: "#0f172a",
  target_average: "#0d9488",
  target_best_response: "#be185d",
};
const ORDER = ["random", "call", "tight", "aggro"];
const NFSP_MODES = ["average", "best_response"];
const PPO_MODES = ["sample", "greedy"];

/** The play modes a tournament curve reports: NFSP's (average, best response) or PPO's. */
export function tournamentModes(points: CurvePoint[]): string[] {
  return points.some((point) => point.benchmark.sample) ? PPO_MODES : NFSP_MODES;
}

/** Series names in a curve: rule bots (bb/100) or tournament play modes (ROI %). */
export function curveSeries(points: CurvePoint[]): string[] {
  const tournament = points.some((point) => point.unit === "roi_percent");
  if (!tournament) return ORDER;
  const modes = tournamentModes(points);
  if (points.some((point) => point.benchmark.target_best_response)) return ["target_best_response", "target_average", ...modes];
  return points.some((point) => point.benchmark.pool) ? [...modes, "pool"] : modes;
}

function isTournament(points: CurvePoint[]): boolean {
  return points.some((point) => point.unit === "roi_percent");
}

export interface Series {
  label: string;
  points: CurvePoint[];
  dashed?: boolean;
}

/** bb/100 against each rule bot after every round, with 95% interval bands. */
export function TrainingCurve({ series, opponents }: { series: Series[]; opponents?: string[] }) {
  const { t, formatNumber } = useI18n();
  const all = series.flatMap((item) => item.points);
  const tournament = isTournament(all);
  opponents = opponents ?? curveSeries(all);
  const label = (name: string) => (tournament ? t(`curve.mode.${name}` as MessageKey) : name);
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
                      {t(tournament ? "curve.pointTitleRoi" : "curve.pointTitle", {
                        label: item.label,
                        opponent: label(name),
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
            <span className="swatch" style={{ background: COLORS[name] ?? "#10b981" }} /> {label(name)}
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
      <p className="muted small">{t(tournament ? "curve.helpRoi" : "curve.help")}</p>
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
export function LatestBenchmark({ series, opponents }: { series: Series[]; opponents?: string[] }) {
  const { t, formatNumber } = useI18n();
  const all = series.flatMap((item) => item.points);
  const tournament = isTournament(all);
  opponents = opponents ?? curveSeries(all);
  if (tournament) return <LatestTournament series={series} />;
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

function LatestTournament({ series }: { series: Series[] }) {
  const { t, formatNumber } = useI18n();
  const percent = (value: number) => `${formatNumber(value * 100, 1)}%`;
  const cell = (interval: { mean: number; low: number; high: number } | undefined, format: (value: number) => string) => {
    if (!interval) return <td>–</td>;
    const significant = interval.low > 0 || interval.high < 0;
    return (
      <td
        className={significant ? (interval.mean > 0 ? "positive-text" : "negative-text") : ""}
        title={`${format(interval.low)} – ${format(interval.high)}`}
      >
        {format(interval.mean)}
      </td>
    );
  };
  return (
    <table className="metrics">
      <thead>
        <tr>
          <th />
          <th>{t("curve.round")}</th>
          <th>{t("curve.roiAverage")}</th>
          <th>{t("curve.roiBest")}</th>
          <th>{t("tournament.bountyEv")}</th>
          <th>{t("tournament.itm")}</th>
          <th>{t("curve.vsPrevious")}</th>
          {series.some((item) => item.points.some((point) => point.benchmark.pool)) ? <th>{t("curve.roiPool")}</th> : null}
        </tr>
      </thead>
      <tbody>
        {series.map((item) => {
          const last = item.points.slice().sort((a, b) => b.round - a.round)[0];
          const [defaultMode = "average", otherMode = "best_response"] = tournamentModes(item.points);
          const average = last?.tournament?.[defaultMode];
          const best = last?.tournament?.[otherMode];
          const head = last?.head_to_head;
          return (
            <tr key={item.label}>
              <th>{item.label}</th>
              <td>{last ? last.round : "–"}</td>
              {cell(average?.roi, percent)}
              {cell(best?.roi, percent)}
              {cell(average?.bounty_ev, (value) => formatNumber(value, 3))}
              {cell(average?.itm, percent)}
              {head
                ? cell(
                    { mean: head.bb_per_100 / 100, low: head.confidence_low / 100, high: head.confidence_high / 100 },
                    percent,
                  )
                : <td>–</td>}
              {last?.benchmark.pool
                ? cell(
                    {
                      mean: last.benchmark.pool.bb_per_100 / 100,
                      low: last.benchmark.pool.low / 100,
                      high: last.benchmark.pool.high / 100,
                    },
                    percent,
                  )
                : null}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
