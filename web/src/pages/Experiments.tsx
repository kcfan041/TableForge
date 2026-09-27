import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api, ApiError, type Preset, type Session } from "../api";
import { Card, Counts, LoadState, Page, ShortId, StatusBadge } from "../components";
import { useI18n, type MessageKey } from "../i18n";
import { useLiveData } from "../live";

export function canOperate(session: Session): boolean {
  return session.role === "operator" || session.role === "admin";
}

export function KindLabel({ kind }: { kind: string }) {
  const { t } = useI18n();
  const key = `kind.${kind}` as MessageKey;
  return <>{t(key) === key ? kind : t(key)}</>;
}

function presetInfo(preset: Preset, t: ReturnType<typeof useI18n>["t"], format: (n: number) => string) {
  if (preset.kind === "tournament_benchmark") {
    return t("experiments.presetTournament", {
      seeds: format(preset.hands),
      tournaments: format(preset.hands * preset.players),
      players: preset.players,
      stack: preset.starting_stack_bb ?? 0,
    });
  }
  let text = t("experiments.presetInfo", {
    hands: format(preset.hands),
    perJob: format(preset.hands_per_job),
    players: preset.players,
  });
  if (preset.rounds) text += t("experiments.presetRounds", { rounds: preset.rounds });
  if (preset.league) text += t("experiments.presetLeague");
  if (preset.learner_runtime === "cuda") text += t("experiments.presetGpu");
  if (preset.observation_schema === "poker-hunlhe-vector-v2") text += t("experiments.presetV2");
  return text;
}

function CreateExperiment() {
  const { t, formatNumber } = useI18n();
  const navigate = useNavigate();
  const [presets, setPresets] = useState<Preset[]>([]);
  const [selected, setSelected] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void api.presets().then((items) => {
      setPresets(items);
      setSelected((current) => current || items[0]?.key || "");
    });
  }, []);

  const create = async (event: FormEvent, start: boolean) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api.createExperiment(selected);
      if (start) await api.startExperiment(created.experiment_id);
      navigate(`/experiments/${created.experiment_id}`);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const current = presets.find((preset) => preset.key === selected);
  return (
    <Card title={t("experiments.create")}>
      <form className="inline-form" onSubmit={(event) => void create(event, true)}>
        <label>
          {t("experiments.preset")}
          <select value={selected} onChange={(event) => setSelected(event.target.value)}>
            {presets.map((preset) => (
              <option key={preset.key} value={preset.key}>
                {preset.key}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" className="primary" disabled={busy || !selected}>
          {t("experiments.createAndStart")}
        </button>
        <button type="button" disabled={busy || !selected} onClick={(event) => void create(event, false)}>
          {t("experiments.createButton")}
        </button>
      </form>
      {current ? (
        <p className="muted small">
          <KindLabel kind={current.kind} /> · {presetInfo(current, t, (n) => formatNumber(n))}
        </p>
      ) : null}
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
    </Card>
  );
}

export function Experiments({ session }: { session: Session }) {
  const { t, formatDate } = useI18n();
  const experiments = useLiveData(api.experiments);

  return (
    <Page title={t("experiments.title")}>
      {canOperate(session) ? <CreateExperiment /> : <p className="notice">{t("experiments.viewerNote")}</p>}
      <LoadState state={experiments}>
        {(data) =>
          data.length === 0 ? (
            <p className="muted">{t("experiments.empty")}</p>
          ) : (
            <div className="card table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("experiments.id")}</th>
                    <th>{t("experiments.preset")}</th>
                    <th>{t("experiments.kind")}</th>
                    <th>{t("experiments.players")}</th>
                    <th>{t("experiments.status")}</th>
                    <th>{t("experiments.jobs")}</th>
                    <th>{t("experiments.created")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.map((item) => (
                    <tr key={item.experiment_id}>
                      <td>
                        <Link to={`/experiments/${item.experiment_id}`}>
                          <ShortId id={item.experiment_id} />
                        </Link>
                      </td>
                      <td>{item.preset}</td>
                      <td>
                        <KindLabel kind={item.kind} />
                      </td>
                      <td>{item.players}</td>
                      <td>
                        <StatusBadge status={item.status} />
                      </td>
                      <td>
                        <Counts counts={item.job_counts} />
                      </td>
                      <td className="nowrap">{formatDate(item.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </LoadState>
    </Page>
  );
}
