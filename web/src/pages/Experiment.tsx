import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, ApiError, type ExperimentDetail, type Session } from "../api";
import { Card, Counts, LoadState, Page, ShortId, StatusBadge } from "../components";
import { useI18n } from "../i18n";
import { useLiveData } from "../live";
import { canOperate, KindLabel } from "./Experiments";
import { MatrixHeatmap } from "./Matrix";

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
