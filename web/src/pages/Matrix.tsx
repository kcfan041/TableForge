import { api, type MatrixCell } from "../api";
import { LoadState } from "../components";
import { useI18n } from "../i18n";
import { useLiveData } from "../live";

function cellColor(cell: MatrixCell, scale: number): string {
  const strength = Math.min(1, Math.abs(cell.bb_per_100) / scale);
  const alpha = 0.12 + strength * 0.68;
  return cell.bb_per_100 >= 0 ? `rgba(22, 163, 74, ${alpha})` : `rgba(220, 38, 38, ${alpha})`;
}

export function MatrixHeatmap({ experimentId }: { experimentId: string }) {
  const { t, formatNumber } = useI18n();
  const matrix = useLiveData(() => api.matrix(experimentId), [experimentId]);

  return (
    <LoadState state={matrix}>
      {(data) => {
        if (!data.available || !data.players || !data.cells) {
          return (
            <p className="muted">
              {data.reason === "not_league" ? t("matrix.notLeague") : t("matrix.noMatchups")}
            </p>
          );
        }
        const cells = data.cells;
        const players = data.players;
        const values = players.flatMap((row) =>
          players.map((column) => Math.abs(cells[row]?.[column]?.bb_per_100 ?? 0)),
        );
        const scale = Math.max(50, ...values);
        return (
          <>
            <p className="muted small">{t("matrix.help")}</p>
            <div className="table-wrap">
              <table className="heatmap">
                <thead>
                  <tr>
                    <th />
                    {players.map((column) => (
                      <th key={column} className="rotate" title={column}>
                        <span>{column}</span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {players.map((row) => (
                    <tr key={row}>
                      <th className="row-label" title={row}>
                        {row}
                      </th>
                      {players.map((column) => {
                        const cell = cells[row]?.[column];
                        if (!cell || row === column) {
                          return <td key={column} className="heat-empty" />;
                        }
                        const significant = cell.confidence_low > 0 || cell.confidence_high < 0;
                        const title = t("matrix.cellTitle", {
                          row,
                          column,
                          value: formatNumber(cell.bb_per_100, 1),
                          low: formatNumber(cell.confidence_low, 1),
                          high: formatNumber(cell.confidence_high, 1),
                          hands: formatNumber(cell.hands),
                        });
                        return (
                          <td
                            key={column}
                            className={`heat ${significant ? "" : "uncertain"}`}
                            style={{ backgroundColor: cellColor(cell, scale) }}
                            title={title}
                            aria-label={`${title} (${significant ? t("matrix.significant") : t("matrix.notSignificant")})`}
                          >
                            {formatNumber(cell.bb_per_100, 0)}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        );
      }}
    </LoadState>
  );
}
