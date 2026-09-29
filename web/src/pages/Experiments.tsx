import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api, ApiError, type Preset, type Session, type TrainingOptions } from "../api";
import { Card, Counts, LoadState, Page, ShortId, StatusBadge } from "../components";
import { useI18n, type MessageKey } from "../i18n";
import { useLiveData } from "../live";
import { TournamentForm, type TournamentChoice } from "./TournamentForm";

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

const SETTING_ORDER = [
  "observation_schema",
  "rounds",
  "actors_per_round",
  "hands_per_job",
  "train_steps_per_round",
  "hidden_size",
  "batch_size",
  "learning_rate",
  "target_update_steps",
  "reward_scale",
  "anticipatory",
  "epsilon",
  "benchmark_deals",
  "evaluation_deals",
  "max_evaluation_deals",
];

function parseSeeds(text: string): number[] | null {
  const items = text
    .split(/[\s,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
  if (items.length === 0) return [];
  const seeds = items.map(Number);
  if (seeds.some((value) => !Number.isInteger(value) || value < 0)) return null;
  return seeds;
}

function CreateExperiment() {
  const { t, formatNumber } = useI18n();
  const navigate = useNavigate();
  const [presets, setPresets] = useState<Preset[]>([]);
  const [options, setOptions] = useState<TrainingOptions | null>(null);
  const [selected, setSelected] = useState("");
  const [custom, setCustom] = useState(false);
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [seedText, setSeedText] = useState("");
  const [tournamentChoice, setTournamentChoice] = useState<TournamentChoice | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void api.presets().then((items) => {
      setPresets(items);
      setSelected((current) => current || items[0]?.key || "");
    });
    void api.trainingOptions().then(setOptions).catch(() => setOptions(null));
  }, []);

  const defaults = options?.presets[selected];
  useEffect(() => {
    setSettings(
      defaults ? Object.fromEntries(Object.entries(defaults).map(([name, value]) => [name, String(value)])) : {},
    );
  }, [selected, defaults]);

  const changed = (): Record<string, unknown> => {
    const result: Record<string, unknown> = tournamentChoice
      ? { tournament: tournamentChoice.tournament, opponents: tournamentChoice.opponents }
      : {};
    if (!custom || !defaults || !options) return result;
    for (const [name, text] of Object.entries(settings)) {
      if (String(defaults[name]) === text) continue;
      result[name] = options.fields[name]?.type === "str" ? text : Number(text);
    }
    return result;
  };

  const create = async (event: FormEvent, start: boolean) => {
    event.preventDefault();
    const seeds = parseSeeds(seedText);
    if (seeds === null) {
      setError(t("experiments.seedsInvalid"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await api.createExperiment({
        preset: selected,
        settings: changed(),
        seeds: seeds.length > 0 ? seeds : undefined,
      });
      const list = "group" in created ? created.group : [created];
      if (start) await Promise.all(list.map((item) => api.startExperiment(item.experiment_id)));
      navigate(`/experiments/${list[0]!.experiment_id}`);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const current = presets.find((preset) => preset.key === selected);
  const tunable = Boolean(defaults);
  const tournamentPreset = options?.tournaments?.[selected];
  return (
    <Card title={t("experiments.create")}>
      <form className="stack wide" onSubmit={(event) => void create(event, true)}>
        <div className="inline-form">
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
        </div>
        {current ? (
          <p className="muted small">
            <KindLabel kind={current.kind} /> · {presetInfo(current, t, (n) => formatNumber(n))}
          </p>
        ) : null}
        {tournamentPreset && options ? (
          <TournamentForm
            preset={tournamentPreset}
            fields={options.tournament_fields ?? ["mixed"]}
            onChange={setTournamentChoice}
          />
        ) : null}
        {tunable && options ? (
          <>
            <label className="checkbox">
              <input type="checkbox" checked={custom} onChange={(event) => setCustom(event.target.checked)} />
              {t("experiments.customize")}
            </label>
            {custom ? (
              <div className="settings-grid">
                {SETTING_ORDER.filter((name) => name in settings).map((name) => {
                  const field = options.fields[name];
                  const differs = defaults && String(defaults[name]) !== settings[name];
                  return (
                    <label key={name} className={differs ? "changed" : ""}>
                      {t(`setting.${name}` as MessageKey)}
                      {name === "observation_schema" ? (
                        <select
                          value={settings[name]}
                          onChange={(event) => setSettings({ ...settings, [name]: event.target.value })}
                        >
                          {options.observation_schemas.map((schema) => (
                            <option key={schema} value={schema}>
                              {schema.replace("poker-hunlhe-vector-", "")}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <input
                          type="number"
                          step={field?.type === "float" ? "any" : 1}
                          min={field?.min ?? undefined}
                          max={field?.max ?? undefined}
                          value={settings[name]}
                          onChange={(event) => setSettings({ ...settings, [name]: event.target.value })}
                        />
                      )}
                    </label>
                  );
                })}
              </div>
            ) : null}
            <label>
              {t("experiments.seeds")}
              <input
                value={seedText}
                onChange={(event) => setSeedText(event.target.value)}
                placeholder="1, 2, 3"
              />
              <span className="muted small">{t("experiments.seedsHelp")}</span>
            </label>
          </>
        ) : null}
      </form>
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
                    <th>{t("experiments.group")}</th>
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
                      <td className="small">
                        {item.group_id ? (
                          <span title={item.group_id}>
                            {t("experiments.groupSeed", { group: item.group_id.slice(-4), seed: item.seed ?? 0 })}
                          </span>
                        ) : (
                          <span className="muted">–</span>
                        )}
                      </td>
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
