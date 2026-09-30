import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api, ApiError, can, type ModelView, type Session } from "../api";
import { Card, LoadState, Page } from "../components";
import { useI18n } from "../i18n";
import { useLiveData } from "../live";

type Interval = { mean: number; low: number; high: number };

function evaluationRoi(model: ModelView): Interval | null {
  const evaluation = model.evaluation as Record<string, unknown>;
  const average = evaluation.average_policy as { roi?: Interval } | undefined;
  if (average?.roi) return average.roi;
  const curve = evaluation.curve as { tournament?: { average?: { roi?: Interval } } } | null | undefined;
  return curve?.tournament?.average?.roi ?? null;
}

export const models = {
  list: () => api.query<ModelView[]>("models"),
  save: (experimentId: string, modelId: string, description: string) =>
    api.command<ModelView>("save_model", {
      experiment_id: experimentId,
      model_id: modelId,
      description,
    }),
  remove: (modelId: string) => api.command<{ ok: boolean }>("remove_model", { model_id: modelId }),
};

export function Models({ session }: { session: Session }) {
  const { t, formatDate, formatNumber } = useI18n();
  const list = useLiveData(models.list);
  const [error, setError] = useState<string | null>(null);

  const navigate = useNavigate();
  const exploit = async (id: string) => {
    if (!window.confirm(t("models.confirmExploit", { id }))) return;
    try {
      const created = (await api.createExperiment({
        preset: "6max-pko-10bb-exploit",
        settings: { exploit_target: id },
      })) as { experiment_id: string };
      await api.startExperiment(created.experiment_id);
      navigate(`/experiments/${encodeURIComponent(created.experiment_id)}`);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : String(reason));
    }
  };

  const remove = async (id: string) => {
    if (!window.confirm(t("models.confirmRemove", { id }))) return;
    try {
      await models.remove(id);
      list.reload();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : String(reason));
    }
  };

  return (
    <Page title={t("models.title")}>
      <p className="muted">{t("models.help")}</p>
      {error ? <p className="error">{error}</p> : null}
      <LoadState state={list}>
        {(data) =>
          data.length === 0 ? (
            <p className="muted">{t("models.empty")}</p>
          ) : (
            <div className="card table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("models.id")}</th>
                    <th>{t("models.game")}</th>
                    <th>{t("models.algorithm")}</th>
                    <th>{t("models.roi")}</th>
                    <th>{t("models.description")}</th>
                    <th>{t("models.created")}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {data.map((model) => {
                    const roi = evaluationRoi(model);
                    return (
                      <tr key={model.model_id}>
                        <td className="nowrap">
                          <strong>{model.model_id}</strong>
                        </td>
                        <td>{model.game}</td>
                        <td>{model.algorithm.toUpperCase()}</td>
                        <td
                          className={roi && roi.low > 0 ? "positive-text" : ""}
                          title={roi ? `${formatNumber(roi.low * 100, 1)}% – ${formatNumber(roi.high * 100, 1)}%` : ""}
                        >
                          {roi ? `${formatNumber(roi.mean * 100, 1)}%` : "–"}
                        </td>
                        <td className="small">{model.description}</td>
                        <td className="nowrap">{formatDate(model.created_at)}</td>
                        <td className="nowrap">
                          <Link to={`/replay?model=${encodeURIComponent(model.model_id)}`}>{t("models.replay")}</Link>
                          {" · "}
                          <Link to={`/strategy?model=${encodeURIComponent(model.model_id)}`}>{t("models.strategy")}</Link>
                          {can(session, "experiment:control") && model.algorithm === "nfsp" ? (
                            <>
                              {" · "}
                              <button type="button" className="link" onClick={() => void exploit(model.model_id)}>
                                {t("models.exploit")}
                              </button>
                            </>
                          ) : null}
                          {can(session, "play:remote") ? (
                            <>
                              {" · "}
                              <Link to="/play">{t("models.play")}</Link>
                            </>
                          ) : null}
                          {can(session, "admin:manage") ? (
                            <>
                              {" · "}
                              <button type="button" className="link danger-text" onClick={() => void remove(model.model_id)}>
                                {t("models.remove")}
                              </button>
                            </>
                          ) : null}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        }
      </LoadState>
    </Page>
  );
}

export function SaveModel({ experimentId, defaultId }: { experimentId: string; defaultId: string }) {
  const { t } = useI18n();
  const [modelId, setModelId] = useState(defaultId);
  const [description, setDescription] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const saved = await models.save(experimentId, modelId.trim(), description);
      setMessage(t("models.saved", { id: saved.model_id }));
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={t("models.saveTitle")}>
      <p className="muted small">{t("models.saveHelp")}</p>
      <div className="inline-form">
        <label>
          {t("models.id")}
          <input value={modelId} onChange={(event) => setModelId(event.target.value)} />
        </label>
        <label>
          {t("models.description")}
          <input value={description} onChange={(event) => setDescription(event.target.value)} />
        </label>
        <button type="button" className="primary" disabled={busy || !modelId.trim()} onClick={() => void save()}>
          {t("models.save")}
        </button>
      </div>
      {message ? (
        <p className="notice">
          {message} <Link to="/models">{t("models.title")}</Link>
        </p>
      ) : null}
      {error ? <p className="error">{error}</p> : null}
    </Card>
  );
}
