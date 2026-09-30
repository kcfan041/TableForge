import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  api,
  ApiError,
  can,
  type ExperimentDetail,
  type Interval,
  type Session,
  type TournamentSummary,
} from "../api";
import { Card, Counts, LoadState, Page, ShortId, StatusBadge } from "../components";
import { useI18n, type MessageKey } from "../i18n";
import { useLiveData } from "../live";
import { LatestBenchmark, TrainingCurve } from "./Curve";
import { canOperate, KindLabel } from "./Experiments";
import { MatrixHeatmap } from "./Matrix";
import { SaveModel } from "./Models";

function TournamentResults({ summary }: { summary: TournamentSummary }) {
  const { t, formatNumber } = useI18n();
  const money = (value: number) => formatNumber(value, 3);
  const percent = (value: number) => `${formatNumber(value * 100, 1)}%`;
  const rows: [string, Interval, (value: number) => string][] = [
    [t("tournament.roi"), summary.roi, percent],
    [t("tournament.totalEv"), summary.total_ev, money],
    [t("tournament.prizeEv"), summary.prize_ev, money],
    [t("tournament.bountyEv"), summary.bounty_ev, money],
    [t("tournament.itm"), summary.itm, percent],
    [t("tournament.averagePlace"), summary.average_place, (value) => formatNumber(value, 2)],
  ];
  const maxShare = Math.max(0.01, ...summary.place_share);
  return (
    <>
      <p className="muted small">
        {t("tournament.setup", {
          candidate: t(`bot.${summary.candidate}` as MessageKey),
          opponents: t(`field.${summary.opponents}` as MessageKey),
          stack: summary.starting_stack_bb,
          payout: summary.payout.map((share) => `${Math.round(share * 100)}%`).join(" / "),
          tournaments: formatNumber(summary.tournaments),
          hands: formatNumber(summary.average_hands, 1),
        })}
        {summary.bounty
          ? " " +
            t("tournament.pko", {
              bounty: `${formatNumber(summary.bounty.starting_bounty * 100, 0)}%`,
              cash: `${formatNumber(summary.bounty.cash_share * 100, 0)}%`,
            })
          : ""}
      </p>
      <table className="metrics">
        <thead>
          <tr>
            <th />
            <th>{t("tournament.mean")}</th>
            <th>{t("tournament.interval")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, interval, format]) => {
            const significant = interval.low > 0 || interval.high < 0;
            return (
              <tr key={label}>
                <th>{label}</th>
                <td className={significant ? (interval.mean > 0 ? "positive-text" : "negative-text") : ""}>
                  {format(interval.mean)}
                </td>
                <td className="muted small">
                  {format(interval.low)} – {format(interval.high)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="muted small">{t("tournament.moneyNote")}</p>
      <h3>{t("tournament.places")}</h3>
      <div className="bars">
        {summary.place_share.map((share, index) => (
          <div key={index} className="bar-row">
            <span className="bar-label">{t("tournament.place", { place: index + 1 })}</span>
            <span className="bar-track plain">
              <span
                className={`bar ${index < summary.payout.filter((value) => value > 0).length ? "positive" : "neutral"}`}
                style={{ width: `${(share / maxShare) * 100}%` }}
              />
            </span>
            <span className="bar-value">{percent(share)}</span>
          </div>
        ))}
      </div>
    </>
  );
}

function Results({ results, players }: { results: NonNullable<ExperimentDetail["results"]>; players: number }) {
  const { t, formatNumber } = useI18n();
  const maxAbs = Math.max(1, ...results.winnings.map((value) => Math.abs(value)));
  return (
    <>
      <dl className="facts">
        <dt>{t("experiment.hands")}</dt>
        <dd>{formatNumber(results.hands)}</dd>
        <dt>{t("experiment.actions")}</dt>
        <dd>{formatNumber(results.actions)}</dd>
        <dt>{t("experiment.showdowns")}</dt>
        <dd>{formatNumber(results.showdowns)}</dd>
        <dt>{t("experiment.folds")}</dt>
        <dd>{formatNumber(results.folds)}</dd>
      </dl>
      <h3>{t("experiment.winnings")}</h3>
      <div className="bars">
        {results.winnings.slice(0, Math.max(players, results.winnings.length)).map((value, seat) => (
          <div key={seat} className="bar-row">
            <span className="bar-label">{t("experiment.seat", { seat })}</span>
            <span className="bar-track">
              <span
                className={`bar ${value >= 0 ? "positive" : "negative"}`}
                style={{
                  width: `${(Math.abs(value) / maxAbs) * 50}%`,
                  marginLeft: value >= 0 ? "50%" : `${50 - (Math.abs(value) / maxAbs) * 50}%`,
                }}
              />
            </span>
            <span className="bar-value">{formatNumber(value)}</span>
          </div>
        ))}
      </div>
    </>
  );
}

interface PoolResult {
  members: string[];
  overall: { roi: { mean: number; low: number; high: number }; tournaments: number };
  matchups: Record<string, { roi: { mean: number; low: number; high: number }; tournaments: number }>;
}

function poolResult(data: ExperimentDetail): PoolResult | null {
  const points = data.curve ?? [];
  for (let index = points.length - 1; index >= 0; index -= 1) {
    const pool = (points[index]!.tournament as Record<string, unknown> | null | undefined)?.pool as PoolResult | undefined;
    if (pool && Object.keys(pool.matchups ?? {}).length > 0) return pool;
  }
  return null;
}

function PoolMatchups({ result }: { result: PoolResult }) {
  const { t, formatNumber } = useI18n();
  const percent = (value: number) => `${formatNumber(value * 100, 1)}%`;
  const rows: [string, { mean: number; low: number; high: number }, number][] = [
    [t("pool.overall"), result.overall.roi, result.overall.tournaments],
    ...Object.entries(result.matchups).map(
      ([name, item]) => [name.replace(/^model:/, "").replace(/^history:/, t("pool.history")), item.roi, item.tournaments] as [
        string,
        { mean: number; low: number; high: number },
        number,
      ],
    ),
  ];
  return (
    <>
      <p className="muted small">{t("pool.help")}</p>
      <table className="metrics">
        <thead>
          <tr>
            <th>{t("pool.opponent")}</th>
            <th>ROI</th>
            <th>{t("tournament.interval")}</th>
            <th>{t("pool.tournaments")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([name, roi, tournaments], index) => {
            const significant = roi.low > 0 || roi.high < 0;
            return (
              <tr key={name} className={index === 0 ? "selected" : ""}>
                <th>{name}</th>
                <td className={significant ? (roi.mean > 0 ? "positive-text" : "negative-text") : ""}>{percent(roi.mean)}</td>
                <td className="muted small">
                  {percent(roi.low)} – {percent(roi.high)}
                </td>
                <td>{formatNumber(tournaments)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
  );
}

function GroupCurve({ groupId, current }: { groupId: string; current: string }) {
  const { t } = useI18n();
  const group = useLiveData(async () => {
    const all = await api.experiments();
    const members = all.filter((item) => item.group_id === groupId);
    const details = await Promise.all(members.map((item) => api.experiment(item.experiment_id)));
    return details.sort((a, b) => (a.seed ?? 0) - (b.seed ?? 0));
  }, [groupId]);
  return (
    <LoadState state={group}>
      {(details) => {
        const series = details.map((item) => ({
          label: `seed ${item.seed ?? "?"}${item.experiment_id === current ? ` (${t("curve.this")})` : ""}`,
          points: item.curve ?? [],
          dashed: item.experiment_id !== current,
        }));
        return (
          <>
            <p className="muted small">{t("curve.group", { count: details.length })}</p>
            <TrainingCurve series={series} />
            <h3>{t("curve.latest")}</h3>
            <LatestBenchmark series={series} />
          </>
        );
      }}
    </LoadState>
  );
}

export function ExperimentPage({ session }: { session: Session }) {
  const { id = "" } = useParams();
  const { t, formatDate } = useI18n();
  const detail = useLiveData(() => api.experiment(id), [id]);
  const [error, setError] = useState<string | null>(null);

  const act = async (action: "start" | "stop") => {
    if (action === "stop" && !window.confirm(t("experiments.stopConfirm", { id }))) return;
    setError(null);
    try {
      await (action === "start" ? api.startExperiment(id) : api.stopExperiment(id));
      detail.reload();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : String(reason));
    }
  };

  return (
    <LoadState state={detail}>
      {(data) => (
        <Page
          title={t("experiment.title", { id: `${data.experiment_id.slice(0, 8)}…` })}
          actions={
            canOperate(session) ? (
              <>
                {data.status === "created" ? (
                  <button type="button" className="primary" onClick={() => void act("start")}>
                    {t("experiments.start")}
                  </button>
                ) : null}
                {data.status === "created" || data.status === "running" ? (
                  <button type="button" className="danger" onClick={() => void act("stop")}>
                    {t("experiments.stop")}
                  </button>
                ) : null}
              </>
            ) : null
          }
        >
          <p>
            <Link to="/experiments">← {t("common.back")}</Link>
          </p>
          {error ? (
            <p className="error" role="alert">
              {error}
            </p>
          ) : null}
          <div className="grid">
            <Card>
              <dl className="facts">
                <dt>{t("experiments.id")}</dt>
                <dd>
                  <code className="wrap">{data.experiment_id}</code>
                </dd>
                <dt>{t("experiments.preset")}</dt>
                <dd>{data.preset}</dd>
                <dt>{t("experiments.kind")}</dt>
                <dd>
                  <KindLabel kind={data.kind} />
                </dd>
                <dt>{t("experiments.players")}</dt>
                <dd>{data.players}</dd>
                <dt>{t("experiments.status")}</dt>
                <dd>
                  <StatusBadge status={data.status} />
                </dd>
                <dt>{t("experiments.jobs")}</dt>
                <dd>
                  <Counts counts={data.job_counts} />
                </dd>
                <dt>{t("experiments.created")}</dt>
                <dd>{formatDate(data.created_at)}</dd>
              </dl>
            </Card>
            <Card title={t("experiment.config")}>
              <pre className="config">{JSON.stringify(data.config, null, 2)}</pre>
            </Card>
          </div>
          {data.kind === "nfsp_iterative" && data.status === "completed" && can(session, "model:manage") ? (
            <SaveModel
              experimentId={data.experiment_id}
              defaultId={`${data.preset.replace(/-train$/, "")}-s${data.seed ?? 0}`}
            />
          ) : null}
          {data.kind === "nfsp_iterative" ? (
            <Card title={t("curve.title")}>
              {data.group_id ? (
                <GroupCurve groupId={data.group_id} current={data.experiment_id} />
              ) : (
                <TrainingCurve series={[{ label: `seed ${data.seed ?? ""}`, points: data.curve ?? [] }]} />
              )}
            </Card>
          ) : null}
          {poolResult(data) ? (
            <Card title={t("pool.title")}>
              <PoolMatchups result={poolResult(data)!} />
            </Card>
          ) : null}
          {data.tournament ? (
            <Card title={t("tournament.title")}>
              <TournamentResults summary={data.tournament} />
            </Card>
          ) : null}
          {data.results ? (
            <Card title={t("experiment.results")}>
              <Results results={data.results} players={data.players} />
            </Card>
          ) : null}
          {data.evaluation ? (
            <Card title={t("experiment.evaluation")}>
              <pre className="config">{JSON.stringify(data.evaluation, null, 2)}</pre>
            </Card>
          ) : null}
          {data.league ? (
            <Card title={t("experiment.matrix")}>
              <MatrixHeatmap experimentId={data.experiment_id} />
            </Card>
          ) : null}
          <Card title={t("experiment.jobs")}>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>{t("experiment.jobType")}</th>
                    <th>{t("experiment.payload")}</th>
                    <th>{t("experiments.status")}</th>
                    <th>{t("experiments.created")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.jobs.map((job) => (
                    <tr key={job.job_id}>
                      <td>
                        <ShortId id={job.job_id} />
                      </td>
                      <td>{job.type}</td>
                      <td className="small">{job.payload_type.replace("tableforge.v1.", "")}</td>
                      <td>
                        <StatusBadge status={job.status} />
                      </td>
                      <td className="nowrap">{formatDate(job.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </Page>
      )}
    </LoadState>
  );
}
