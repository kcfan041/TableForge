import { useEffect, useMemo, useState } from "react";

import { api, ApiError, can, localMode, type ModelView, type Session } from "../api";
import { Card, Page } from "../components";
import { useI18n, type MessageKey } from "../i18n";
import { urlParam } from "./analysis";
import { ACTIONS, CardFace, ReplayViewer, type Explanation, type Replay } from "./Replay";

const RULE_BOTS = ["tight", "call", "aggro", "random", "pushfold", "hunter", "pushfold_nash"];
const HUMAN = "human";

interface SeatChoice {
  kind: "human" | "rule" | "model";
  name: string;
  mode: string;
}

interface Turn {
  can_fold: boolean;
  can_check: boolean;
  call_amount: number | null;
  min_raise_to: number | null;
  max_raise_to: number | null;
  all_in_only: boolean;
  to_call: number;
  raise_sizes: Record<string, number>;
}

interface PublicAction {
  player: number;
  street: string;
  pot: number;
  to_call: number;
  stack: number;
  action: string;
  amount: number | null;
}

interface LiveHand {
  hand: number;
  level: number;
  small_blind: number;
  big_blind: number;
  ante: number;
  button: number;
  players: number[];
  street: string;
  board: string[];
  pot: number;
  current_bet: number;
  current_player: number | null;
  stacks: number[];
  street_bets: number[];
  folded: boolean[];
  all_in: boolean[];
  hole_cards: string[];
  actions: PublicAction[];
}

interface FinishedHand {
  hand: number;
  players: number[];
  hole_cards: string[][];
  board: string[];
  stacks_before: number[];
  stacks_after: number[];
  winners: number[];
  busted: number[];
  showdown: boolean;
  knockouts: { eliminated: number; collector: number; cash: number; added_to_bounty: number }[];
  actions: PublicAction[];
}

interface GameView {
  session_id: string;
  players: number;
  human: number;
  labels: string[];
  hints: boolean;
  config: { players: number; starting_stack_bb: number; payout: number[]; bounty?: unknown };
  hands_played: number;
  stacks: number[];
  bounties: number[];
  bounty_cash: number[];
  places: (number | null)[];
  complete: boolean;
  result: { places: number[]; prizes: number[]; bounties: number[]; results: number[] } | null;
  last_hand: FinishedHand | null;
  hand: LiveHand | null;
  turn: Turn | null;
  hint?: Explanation;
  practice?: boolean;
  coach?: string | null;
  feedback?: Review | null;
  reviews?: number;
}

/** The coach model's view of one of the person's decisions (practice mode). */
export interface Review {
  hand: number;
  street: string;
  position: string;
  situation: string;
  stack_bb: number;
  hole_cards: string[];
  board: string[];
  to_call: number;
  chosen: string;
  chosen_key: string;
  amount: number | null;
  recommended: string;
  probabilities: Record<string, number>;
  chosen_probability: number;
  values: Record<string, number>;
  value_unit: string;
  best_value_key: string | null;
  q_loss: number | null;
  mistake: boolean;
}

interface GroupSummary {
  decisions: number;
  agree: number | null;
  chosen_probability: number | null;
  q_loss_total: number;
  q_loss_mean: number | null;
  mistakes: number;
}

export interface PracticeReport {
  coach: string;
  unit: string;
  overall: GroupSummary;
  by: Record<string, Record<string, GroupSummary>>;
  worst: Review[];
  thresholds: { q_loss: number; probability: number };
}

interface SessionSummary {
  session_id: string;
  labels: string[];
  hands_played: number;
  complete: boolean;
  practice?: boolean;
}

type Where = "remote" | "local";

/** Talks to the cluster host (remote play) or to this computer (local play). */
function playApi(where: Where) {
  if (where === "remote") {
    return <T,>(action: string, body: object) => api.command<T>(action, body);
  }
  return async <T,>(action: string, body: object): Promise<T> => localCall<T>("POST", `/api/local/play/${action}`, body);
}

async function localCall<T>(method: string, path: string, body?: object): Promise<T> {
  const token = document.querySelector<HTMLMetaElement>('meta[name="tableforge-ui-token"]')?.content ?? "";
  const headers: Record<string, string> = { "X-TableForge-UI": token };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await response.text();
  const data: unknown = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = data && typeof data === "object" && "detail" in data ? String((data as { detail: unknown }).detail) : response.statusText;
    throw new ApiError(response.status, detail);
  }
  return data as T;
}

export { localCall };

export const localModels = {
  list: () => localCall<ModelView[]>("GET", "/api/local/models"),
  download: (modelId: string, replace = false) =>
    localCall<ModelView>("POST", "/api/local/models/download", { model_id: modelId, replace }),
  remove: (modelId: string) => localCall<{ ok: boolean }>("DELETE", `/api/local/models/${encodeURIComponent(modelId)}`),
};

function message(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

function download(name: string, text: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

export function PlayPage({ session }: { session: Session | null }) {
  const { t } = useI18n();
  const remoteAllowed = session !== null && can(session, "play:remote");
  const wanted = urlParam("where");
  const [where, setWhere] = useState<Where>(
    wanted === "local" && localMode ? "local" : wanted === "remote" || remoteAllowed || !localMode ? "remote" : "local",
  );
  const [models, setModels] = useState<ModelView[] | null>(null);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [game, setGame] = useState<GameView | null>(null);
  const [replay, setReplay] = useState<Replay | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<SessionSummary[]>([]);
  const [report, setReport] = useState<PracticeReport | null>(null);
  const call = useMemo(() => playApi(where), [where]);

  useEffect(() => {
    let cancelled = false;
    setModels(null);
    setModelsError(null);
    setGame(null);
    setReplay(null);
    const load = where === "remote" ? api.query<ModelView[]>("models") : localModels.list();
    load
      .then((value) => !cancelled && setModels(value))
      .catch((reason: unknown) => !cancelled && setModelsError(message(reason)));
    call<SessionSummary[]>("play_list", {})
      .then((value) => !cancelled && setSaved(value))
      .catch(() => !cancelled && setSaved([]));
    const linked = urlParam("session");
    if (linked) {
      call<GameView>("play_state", { session_id: linked })
        .then((value) => !cancelled && setGame(value))
        .catch((reason: unknown) => !cancelled && setError(message(reason)));
      if (urlParam("report") === "1") {
        call<PracticeReport>("play_report", { session_id: linked })
          .then((value) => !cancelled && setReport(value))
          .catch(() => undefined);
      }
    }
    return () => {
      cancelled = true;
    };
  }, [where, call]);

  const run = async (operation: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await operation();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  };

  const act = (action: string, amount?: number) =>
    run(async () => setGame(await call<GameView>("play_act", { session_id: game!.session_id, action, amount })));

  const showReplay = () =>
    run(async () => setReplay(await call<Replay>("play_replay", { session_id: game!.session_id })));

  const saveHistory = () =>
    run(async () => {
      const history = await call<{ text: string }>("play_history", { session_id: game!.session_id });
      download(`tableforge-${game!.session_id}.txt`, history.text, "text/plain");
    });

  const saveReplay = () =>
    run(async () => {
      const document = await call<Replay>("play_replay", { session_id: game!.session_id });
      download(`tableforge-replay-${game!.session_id}.json`, JSON.stringify(document, null, 1), "application/json");
    });

  const resume = (sessionId: string) =>
    run(async () => {
      setReplay(null);
      setReport(null);
      setGame(await call<GameView>("play_state", { session_id: sessionId }));
    });

  const showReport = () =>
    run(async () => setReport(await call<PracticeReport>("play_report", { session_id: game!.session_id })));

  const saveReport = () =>
    run(async () => {
      const document = await call<PracticeReport>("play_report", { session_id: game!.session_id });
      download(`tableforge-practice-${game!.session_id}.json`, JSON.stringify(document, null, 1), "application/json");
    });

  const choices: [Where, boolean][] = [
    ["remote", session !== null],
    ["local", localMode],
  ];

  return (
    <Page title={t("play.title")}>
      <p className="muted">{t("play.help")}</p>
      {choices.filter(([, shown]) => shown).length > 1 ? (
        <div className="row">
          {choices
            .filter(([, shown]) => shown)
            .map(([key]) => (
              <label key={key} className="checkbox">
                <input type="radio" name="where" checked={where === key} onChange={() => setWhere(key)} />
                {t(`play.where.${key}` as MessageKey)}
              </label>
            ))}
        </div>
      ) : null}
      <p className="muted small">{t(`play.whereHelp.${where}` as MessageKey)}</p>
      {where === "remote" && !remoteAllowed ? <p className="notice">{t("play.needRemote")}</p> : null}
      {where === "local" ? <LocalModels session={session} models={models} onChange={setModels} /> : null}
      {modelsError ? <p className="error">{modelsError}</p> : null}
      {game === null && models !== null && (where === "local" || remoteAllowed) ? (
        <NewGame models={models} busy={busy} onStart={(body) => run(async () => setGame(await call<GameView>("play_start", body)))} />
      ) : null}
      {game === null && saved.length > 0 ? (
        <Card title={t("play.saved")}>
          <ul>
            {saved.map((item) => (
              <li key={item.session_id}>
                <button type="button" className="link" onClick={() => void resume(item.session_id)}>
                  {item.labels.map((name) => (name === "You" ? t("play.you") : name)).join(" · ")} — {t("play.handsPlayed", { hands: item.hands_played })}
                  {item.practice ? ` · ${t("practice.label")}` : ""}
                  {item.complete ? ` (${t("play.over")})` : ""}
                </button>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
      {error ? <p className="error">{error}</p> : null}
      {game ? (
        <>
          {game.practice && game.feedback ? <Feedback review={game.feedback} /> : null}
          <Table game={game} busy={busy} onAct={(action, amount) => void act(action, amount)} />
          <div className="row">
            <button type="button" onClick={() => { setGame(null); setReplay(null); setReport(null); }}>
              {t("play.newGame")}
            </button>
            {game.practice ? (
              <>
                <button type="button" className="primary" disabled={busy || !game.reviews} onClick={() => void showReport()}>
                  {t("practice.showReport")}
                </button>
                <button type="button" disabled={busy || !game.reviews} onClick={() => void saveReport()}>
                  {t("practice.saveReport")}
                </button>
              </>
            ) : null}
            <button type="button" disabled={busy || !game.complete} onClick={() => void showReplay()}>
              {t("play.toReplay")}
            </button>
            <button type="button" disabled={busy || !game.complete} onClick={() => void saveReplay()}>
              {t("play.saveReplay")}
            </button>
            <button type="button" disabled={busy || game.hands_played === 0} onClick={() => void saveHistory()}>
              {t("play.saveHistory")}
            </button>
          </div>
          {!game.complete ? <p className="muted small">{t("play.replayAfter")}</p> : null}
        </>
      ) : null}
      {report ? <Report report={report} /> : null}
      {replay && game ? <ReplayViewer replay={replay} focus={[game.human]} readUrl={false} /> : null}
    </Page>
  );
}

function actionName(t: ReturnType<typeof useI18n>["t"], name: string): string {
  if (name.startsWith("raise_")) return `${t("replay.action.raise")} (${t(`practice.size.${name.slice(6)}` as MessageKey)})`;
  return t(`replay.action.${name}` as MessageKey);
}

/** Practice mode: what the coach thought of the person's last decision. */
function Feedback({ review }: { review: Review }) {
  const { t, formatNumber } = useI18n();
  const agrees = review.chosen === review.recommended;
  const tone = review.mistake ? "error" : agrees ? "positive-text" : "warning";
  return (
    <Card title={t("practice.feedbackTitle", { hand: review.hand })}>
      <p className={tone}>
        <strong>
          {review.mistake ? t("practice.mistake") : agrees ? t("practice.agree") : t("practice.differs")}
        </strong>{" "}
        {t("practice.youChose", {
          chosen: actionName(t, review.chosen) + (review.amount && review.chosen !== "check_call" && review.chosen !== "fold" ? ` ${formatNumber(review.amount)}` : ""),
          recommended: actionName(t, review.recommended),
          probability: formatNumber(review.chosen_probability * 100, 1),
        })}
        {review.q_loss !== null ? ` ${t("practice.qLoss", { loss: formatNumber(review.q_loss, 3), unit: review.value_unit })}` : ""}
      </p>
      <p className="small muted">
        {t("practice.spot", {
          position: review.position,
          stack: formatNumber(review.stack_bb, 1),
          situation: t(`practice.situation.${review.situation}` as MessageKey),
          street: t(`replay.street.${review.street}` as MessageKey),
        })}{" "}
        {review.hole_cards.map((card) => (
          <CardFace key={card} card={card} />
        ))}
        {review.board.length > 0 ? " · " : ""}
        {review.board.map((card) => (
          <CardFace key={card} card={card} />
        ))}
      </p>
      <table className="metrics">
        <thead>
          <tr>
            <th />
            {ACTIONS.map((name) => (
              <th key={name} className={name === review.chosen ? "chosen" : ""}>
                {t(`replay.action.${name}` as MessageKey)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr>
            <th>{t("replay.probability")}</th>
            {ACTIONS.map((name) => (
              <td key={name} className={name === review.chosen ? "chosen" : ""}>
                <span className="prob-bar" style={{ width: `${Math.round((review.probabilities[name] ?? 0) * 60)}px` }} />
                {formatNumber((review.probabilities[name] ?? 0) * 100, 1)}%
              </td>
            ))}
          </tr>
          <tr>
            <th>
              Q <span className="muted small">({review.value_unit})</span>
            </th>
            {ACTIONS.map((name) => {
              const entries = Object.entries(review.values).filter(([key]) => key === name || key.startsWith(`${name}_`));
              return (
                <td key={name} className="small">
                  {entries.length === 0
                    ? "–"
                    : entries.map(([key, value]) => (
                        <div key={key} className={key === review.best_value_key ? "positive-text" : key === review.chosen_key ? "chosen" : ""}>
                          {key !== name ? <span className="muted">{t(`practice.size.${key.slice(6)}` as MessageKey)}: </span> : null}
                          {formatNumber(value, 3)}
                        </div>
                      ))}
                </td>
              );
            })}
          </tr>
        </tbody>
      </table>
      <p className="muted small">{t("practice.feedbackNote")}</p>
    </Card>
  );
}

const DIMENSIONS = ["position", "stack", "situation", "street"];

export function Report({ report }: { report: PracticeReport }) {
  const { t, formatNumber } = useI18n();
  const percent = (value: number | null) => (value === null ? "–" : `${formatNumber(value * 100, 0)}%`);
  const loss = (value: number | null) => (value === null ? "–" : formatNumber(value, 3));
  const label = (dimension: string, name: string) =>
    dimension === "situation"
      ? t(`practice.situation.${name}` as MessageKey)
      : dimension === "street"
        ? t(`replay.street.${name}` as MessageKey)
        : dimension === "stack"
          ? `${name} BB`
          : name;
  return (
    <Card title={t("practice.reportTitle")}>
      <p className="muted small">{t("practice.reportHelp", { coach: report.coach, unit: report.unit })}</p>
      <dl className="facts">
        <dt>{t("practice.decisions")}</dt>
        <dd>{report.overall.decisions}</dd>
        <dt>{t("practice.agreeRate")}</dt>
        <dd>{percent(report.overall.agree)}</dd>
        <dt>{t("practice.lossTotal", { unit: report.unit })}</dt>
        <dd>
          {loss(report.overall.q_loss_total)} <span className="muted small">({t("practice.perDecision", { value: loss(report.overall.q_loss_mean) })})</span>
        </dd>
        <dt>{t("practice.mistakes")}</dt>
        <dd>{report.overall.mistakes}</dd>
      </dl>
      <div className="report-grid">
        {DIMENSIONS.map((dimension) => {
          const rows = Object.entries(report.by[dimension] ?? {}).sort(([, a], [, b]) => b.q_loss_total - a.q_loss_total);
          return (
            <div key={dimension}>
              <h3>{t(`practice.by.${dimension}` as MessageKey)}</h3>
              <table className="metrics">
                <thead>
                  <tr>
                    <th />
                    <th>{t("practice.decisions")}</th>
                    <th>{t("practice.agreeRate")}</th>
                    <th>{t("practice.lossShort")}</th>
                    <th>{t("practice.mistakes")}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(([name, item]) => (
                    <tr key={name}>
                      <th>{label(dimension, name)}</th>
                      <td>{item.decisions}</td>
                      <td>{percent(item.agree)}</td>
                      <td className={item.q_loss_total > report.thresholds.q_loss * 3 ? "negative-text" : ""}>{loss(item.q_loss_total)}</td>
                      <td>{item.mistakes}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        })}
      </div>
      <h3>{t("practice.worst")}</h3>
      <table className="metrics">
        <thead>
          <tr>
            <th>#</th>
            <th>{t("practice.spotShort")}</th>
            <th>{t("practice.cards")}</th>
            <th>{t("practice.chosen")}</th>
            <th>{t("practice.recommended")}</th>
            <th>{t("practice.lossShort")}</th>
          </tr>
        </thead>
        <tbody>
          {report.worst.map((item, index) => (
            <tr key={index} className={item.mistake ? "selected" : ""}>
              <td>{item.hand}</td>
              <td className="small">
                {item.position} · {formatNumber(item.stack_bb, 1)} BB · {t(`practice.situation.${item.situation}` as MessageKey)}
              </td>
              <td>
                {item.hole_cards.map((card) => (
                  <CardFace key={card} card={card} />
                ))}
              </td>
              <td>
                {actionName(t, item.chosen)} <span className="muted small">({formatNumber(item.chosen_probability * 100, 0)}%)</span>
              </td>
              <td>{actionName(t, item.recommended)}</td>
              <td className="negative-text">{loss(item.q_loss)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">{t("practice.reportNote", { loss: report.thresholds.q_loss, probability: formatNumber(report.thresholds.probability * 100, 0) })}</p>
    </Card>
  );
}

function LocalModels({
  session,
  models,
  onChange,
}: {
  session: Session | null;
  models: ModelView[] | null;
  onChange: (models: ModelView[]) => void;
}) {
  const { t, formatNumber } = useI18n();
  const [cluster, setCluster] = useState<ModelView[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const allowed = session !== null && can(session, "model:download");

  useEffect(() => {
    if (!session || !can(session, "model:read")) return;
    api
      .query<ModelView[]>("models")
      .then(setCluster)
      .catch(() => setCluster(null));
  }, [session]);

  const have = new Map((models ?? []).map((item) => [item.model_id, item]));

  const fetchModel = async (modelId: string, replace: boolean) => {
    setBusy(modelId);
    setError(null);
    try {
      await localModels.download(modelId, replace);
      onChange(await localModels.list());
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(null);
    }
  };

  const remove = async (modelId: string) => {
    if (!window.confirm(t("play.confirmRemoveLocal", { id: modelId }))) return;
    try {
      await localModels.remove(modelId);
      onChange(await localModels.list());
    } catch (reason) {
      setError(message(reason));
    }
  };

  return (
    <Card title={t("play.localModels")}>
      <p className="muted small">{t("play.localModelsHelp")}</p>
      {models && models.length === 0 ? <p className="muted">{t("play.noLocalModels")}</p> : null}
      {models && models.length > 0 ? (
        <ul>
          {models.map((item) => (
            <li key={item.model_id}>
              <strong>{item.model_id}</strong> <span className="muted small">{item.game} · {formatNumber(item.size_bytes / 1024 / 1024, 1)} MB</span>{" "}
              <button type="button" className="link danger-text" onClick={() => void remove(item.model_id)}>
                {t("models.remove")}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {cluster && cluster.length > 0 ? (
        <>
          <h3>{t("play.clusterModels")}</h3>
          {!allowed ? <p className="notice small">{t("play.needDownload")}</p> : null}
          <table>
            <tbody>
              {cluster.map((item) => {
                const local = have.get(item.model_id);
                const same = local?.sha256 === item.sha256;
                return (
                  <tr key={item.model_id}>
                    <td className="nowrap">
                      <strong>{item.model_id}</strong>
                    </td>
                    <td className="small">{item.game}</td>
                    <td className="small">{formatNumber(item.size_bytes / 1024 / 1024, 1)} MB</td>
                    <td>
                      {same ? (
                        <span className="muted small">{t("play.downloaded")}</span>
                      ) : (
                        <button
                          type="button"
                          disabled={!allowed || busy !== null}
                          onClick={() => void fetchModel(item.model_id, local !== undefined)}
                        >
                          {busy === item.model_id ? t("play.downloading") : local ? t("play.update") : t("play.download")}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      ) : null}
      {error ? <p className="error">{error}</p> : null}
    </Card>
  );
}

function NewGame({
  models,
  busy,
  onStart,
}: {
  models: ModelView[];
  busy: boolean;
  onStart: (body: Record<string, unknown>) => void;
}) {
  const { t } = useI18n();
  const first = models[0]?.model_id ?? "";
  const [players, setPlayers] = useState(6);
  const [stack, setStack] = useState("");
  const [seed, setSeed] = useState("");
  const [hints, setHints] = useState(false);
  const [practice, setPractice] = useState(urlParam("practice") === "1");
  const [coach, setCoach] = useState("");
  const [seats, setSeats] = useState<SeatChoice[]>([]);

  const gameOf = (modelId: string) => models.find((item) => item.model_id === modelId)?.game ?? "";
  useEffect(() => {
    const match = /^(\d)max-.*?-(\d+(?:\.\d+)?)bb/.exec(gameOf(first));
    if (match) setPlayers(Number(match[1]));
  }, [first]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    setSeats((current) =>
      Array.from({ length: players }, (_, index) =>
        current[index] ??
        (index === 0
          ? { kind: HUMAN, name: "", mode: "" }
          : first
            ? { kind: "model", name: first, mode: "" }
            : { kind: "rule", name: RULE_BOTS[index % RULE_BOTS.length]!, mode: "" }),
      ),
    );
  }, [players, first]);

  const setSeat = (index: number, value: SeatChoice) =>
    setSeats((current) =>
      current.map((item, position) => {
        if (position === index) return value;
        if (value.kind === HUMAN && item.kind === HUMAN) return first ? { kind: "model", name: first, mode: "" } : { kind: "rule", name: "call", mode: "" };
        return item;
      }),
    );

  const firstModel = seats.find((item) => item.kind === "model");
  const start = () => {
    const tournament: Record<string, unknown> = { players };
    if (stack) tournament.starting_stack_bb = Number(stack);
    const trained = firstModel ? /^(\d)max-.*?-(\d+(?:\.\d+)?)bb/.exec(gameOf(firstModel.name)) : null;
    const sameAsModel = trained && Number(trained[1]) === players && (!stack || Number(trained[2]) === Number(stack));
    onStart({
      seats,
      hints,
      practice,
      ...((hints || practice) && (coach || first) ? { hint_model: coach || firstModel?.name || first } : {}),
      ...(seed ? { seed: Number(seed) } : {}),
      ...(sameAsModel ? {} : { tournament }),
    });
  };

  return (
    <Card title={t("play.newGame")}>
      <div className="inline-form">
        <label>
          {t("tournamentForm.players")}
          <select value={players} onChange={(event) => setPlayers(Number(event.target.value))}>
            {[2, 3, 4, 5, 6].map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("tournamentForm.stack")}
          <input className="narrow" placeholder={t("play.stackDefault")} value={stack} onChange={(event) => setStack(event.target.value.replace(/[^\d.]/g, ""))} />
        </label>
        <label>
          {t("replay.seed")}
          <input className="narrow" placeholder={t("play.seedRandom")} value={seed} onChange={(event) => setSeed(event.target.value.replace(/\D/g, ""))} />
        </label>
        <label className="checkbox">
          <input type="checkbox" checked={hints} disabled={models.length === 0} onChange={(event) => setHints(event.target.checked)} />
          {t("play.hints")}
        </label>
        <label className="checkbox">
          <input type="checkbox" checked={practice} disabled={models.length === 0} onChange={(event) => setPractice(event.target.checked)} />
          {t("practice.toggle")}
        </label>
        {hints || practice ? (
          <label>
            {t("practice.coach")}
            <select value={coach || firstModel?.name || first} onChange={(event) => setCoach(event.target.value)}>
              {models.map((item) => (
                <option key={item.model_id} value={item.model_id}>
                  {item.model_id}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>
      <p className="muted small">{t("play.tournamentHelp")}</p>
      {practice ? <p className="muted small">{t("practice.help")}</p> : null}
      <table>
        <tbody>
          {seats.map((seat, index) => {
            const model = models.find((item) => item.model_id === seat.name);
            const value = seat.kind === HUMAN ? HUMAN : `${seat.kind}:${seat.name}`;
            return (
              <tr key={index}>
                <th>{t("play.seat", { seat: index + 1 })}</th>
                <td>
                  <select
                    value={value}
                    onChange={(event) => {
                      const [kind, ...rest] = event.target.value.split(":");
                      setSeat(index, { kind: kind as SeatChoice["kind"], name: rest.join(":"), mode: "" });
                    }}
                  >
                    <option value={HUMAN}>{t("play.you")}</option>
                    {models.map((item) => (
                      <option key={item.model_id} value={`model:${item.model_id}`}>
                        {item.model_id}
                      </option>
                    ))}
                    {RULE_BOTS.map((item) => (
                      <option key={item} value={`rule:${item}`}>
                        {t(`play.bot.${item}` as MessageKey)}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  {seat.kind === "model" && model ? (
                    <select value={seat.mode || model.play_modes[0]?.key} onChange={(event) => setSeat(index, { ...seat, mode: event.target.value })}>
                      {model.play_modes.map((item) => (
                        <option key={item.key} value={item.key} title={item.description}>
                          {t(`playMode.${item.key}` as MessageKey)}
                        </option>
                      ))}
                    </select>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <button type="button" className="primary" disabled={busy || seats.length !== players} onClick={start}>
        {busy ? t("play.starting") : t("play.start")}
      </button>
    </Card>
  );
}

function Table({ game, busy, onAct }: { game: GameView; busy: boolean; onAct: (action: string, amount?: number) => void }) {
  const { t, formatNumber } = useI18n();
  const hand = game.hand;
  const turn = game.turn;
  const [amount, setAmount] = useState("");
  useEffect(() => {
    setAmount(turn?.min_raise_to != null ? String(turn.raise_sizes.half_pot ?? turn.min_raise_to) : "");
  }, [turn?.min_raise_to, turn?.raise_sizes.half_pot, hand?.hand, hand?.actions.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const label = (player: number) => (player === game.human ? t("play.you") : `${player + 1}. ${game.labels[player]}`);
  const actionText = (action: PublicAction) =>
    `${t(`replay.action.${action.action}` as MessageKey)}${action.amount && action.action !== "fold" && action.action !== "check_call" ? ` ${formatNumber(action.amount)}` : ""}`;
  const bb = hand?.big_blind ?? 1;

  if (game.complete && game.result) {
    const place = game.result.places[game.human]!;
    return (
      <Card title={t("play.finished")}>
        <p className={place === 1 ? "positive-text" : ""}>
          <strong>{t("play.yourPlace", { place, players: game.players })}</strong>{" "}
          {t("play.yourResult", { result: formatNumber(game.result.results[game.human]!, 3) })}
        </p>
        <table className="metrics">
          <thead>
            <tr>
              <th />
              <th>{t("replay.place")}</th>
              <th>{t("tournament.prizeEv")}</th>
              <th>{t("tournament.bountyEv")}</th>
              <th>{t("replay.net")}</th>
            </tr>
          </thead>
          <tbody>
            {game.labels.map((_, player) => (
              <tr key={player} className={player === game.human ? "selected" : ""}>
                <th>{label(player)}</th>
                <td>{game.result!.places[player]}</td>
                <td>{formatNumber(game.result!.prizes[player]!, 3)}</td>
                <td>{formatNumber(game.result!.bounties[player]!, 3)}</td>
                <td>{formatNumber(game.result!.results[player]!, 3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {game.last_hand ? <LastHand game={game} label={label} actionText={actionText} /> : null}
      </Card>
    );
  }
  if (!hand) return <p className="muted">{t("common.loading")}</p>;

  const lastByPlayer = new Map(hand.actions.map((action) => [action.player, action]));
  return (
    <Card title={t("replay.handTitle", { hand: hand.hand, level: hand.level, sb: hand.small_blind, bb: hand.big_blind, ante: hand.ante })}>
      <div className="table-felt">
        <div className="board">
          {hand.board.map((card) => (
            <CardFace key={card} card={card} />
          ))}
          <div className="pot">
            {t("replay.pot")} {formatNumber(hand.pot)}
          </div>
        </div>
        <div className="seats">
          {hand.players.map((player, seat) => {
            const me = player === game.human;
            const last = lastByPlayer.get(player);
            return (
              <div
                key={player}
                className={`seat${me ? " model" : ""}${hand.current_player === player ? " acting" : ""}${hand.folded[seat] ? " folded" : ""}`}
              >
                <div className="seat-name">
                  {player === hand.button ? <span className="dealer">D</span> : null}
                  {label(player)}
                </div>
                <div className="seat-cards">
                  {me ? hand.hole_cards.map((card) => <CardFace key={card} card={card} />) : (
                    <>
                      <CardFace card="" hidden />
                      <CardFace card="" hidden />
                    </>
                  )}
                </div>
                <div className="small">
                  {formatNumber(hand.stacks[seat]!)} ({formatNumber(hand.stacks[seat]! / bb, 1)} BB)
                  {hand.all_in[seat] ? ` · ${t("replay.action.all_in")}` : ""}
                </div>
                {hand.street_bets[seat]! > 0 ? (
                  <div className="small">
                    {t("play.bet")} {formatNumber(hand.street_bets[seat]!)}
                  </div>
                ) : null}
                {game.bounties.length > 0 ? (
                  <div className="small muted">
                    {t("replay.bounty")} {formatNumber(game.bounties[player]!, 3)}
                  </div>
                ) : null}
                {last && last.street === hand.street ? <div className="seat-action">{actionText(last)}</div> : null}
              </div>
            );
          })}
        </div>
      </div>
      {turn ? (
        <div className="decision">
          <p>
            <strong>{t("play.yourTurn")}</strong> · {t(`replay.street.${hand.street}` as MessageKey)} · {t("replay.toCall")} {formatNumber(turn.to_call)}
          </p>
          <div className="row">
            {turn.can_fold ? (
              <button type="button" disabled={busy} onClick={() => onAct("fold")}>
                {t("replay.action.fold")}
              </button>
            ) : null}
            <button type="button" className="primary" disabled={busy} onClick={() => onAct("check_call")}>
              {turn.can_check ? t("play.check") : `${t("play.call")} ${formatNumber(turn.call_amount ?? 0)}`}
            </button>
            {turn.min_raise_to != null && turn.max_raise_to != null && !turn.all_in_only ? (
              <>
                {(["min", "half_pot", "pot"] as const).map((size) =>
                  turn.raise_sizes[size] != null ? (
                    <button key={size} type="button" className="link" disabled={busy} onClick={() => setAmount(String(turn.raise_sizes[size]))}>
                      {t(`play.size.${size}` as MessageKey)}
                    </button>
                  ) : null,
                )}
                <input
                  className="narrow"
                  aria-label={t("play.raiseTo")}
                  value={amount}
                  onChange={(event) => setAmount(event.target.value.replace(/\D/g, ""))}
                />
                <button
                  type="button"
                  disabled={busy || !amount || Number(amount) < turn.min_raise_to || Number(amount) > turn.max_raise_to}
                  onClick={() => onAct("raise", Number(amount))}
                >
                  {t("play.raiseTo")} {amount ? formatNumber(Number(amount)) : ""}
                </button>
                <span className="muted small">
                  {formatNumber(turn.min_raise_to)}–{formatNumber(turn.max_raise_to)}
                </span>
              </>
            ) : null}
            {turn.max_raise_to != null ? (
              <button type="button" className="danger" disabled={busy} onClick={() => onAct("all_in")}>
                {t("replay.action.all_in")} {formatNumber(turn.max_raise_to)}
              </button>
            ) : null}
          </div>
          {game.hint ? <Hint hint={game.hint} /> : null}
        </div>
      ) : (
        <p className="muted small">{t("play.waiting")}</p>
      )}
      {hand.actions.length > 0 ? (
        <details className="small">
          <summary>{t("play.actionsThisHand", { count: hand.actions.length })}</summary>
          <ol>
            {hand.actions.map((action, index) => (
              <li key={index}>
                {t(`replay.street.${action.street}` as MessageKey)} · {label(action.player)}: {actionText(action)}
              </li>
            ))}
          </ol>
        </details>
      ) : null}
      {game.last_hand ? <LastHand game={game} label={label} actionText={actionText} /> : null}
    </Card>
  );
}

function LastHand({
  game,
  label,
  actionText,
}: {
  game: GameView;
  label: (player: number) => string;
  actionText: (action: PublicAction) => string;
}) {
  const { t, formatNumber } = useI18n();
  const last = game.last_hand!;
  return (
    <details className="small" open>
      <summary>{t("play.lastHand", { hand: last.hand })}</summary>
      <p>
        {last.board.length > 0 ? (
          <>
            {t("play.board")}{" "}
            {last.board.map((card) => (
              <CardFace key={card} card={card} />
            ))}{" "}
          </>
        ) : null}
        {t("play.winners", { names: last.winners.map(label).join(", ") })}
      </p>
      <ul>
        {last.players.map((player, seat) =>
          last.hole_cards[seat]!.length > 0 || last.stacks_after[seat] !== last.stacks_before[seat] ? (
            <li key={player}>
              {label(player)}{" "}
              {last.hole_cards[seat]!.map((card) => (
                <CardFace key={card} card={card} />
              ))}{" "}
              {formatNumber(last.stacks_before[seat]!)} → {formatNumber(last.stacks_after[seat]!)}
              {last.busted.includes(player) ? ` · ${t("replay.busted")}` : ""}
            </li>
          ) : null,
        )}
      </ul>
      <p className="muted">{last.actions.map((action) => `${label(action.player)} ${actionText(action)}`).join(" · ")}</p>
    </details>
  );
}

function Hint({ hint }: { hint: Explanation }) {
  const { t, formatNumber } = useI18n();
  return (
    <div className="hint">
      <p className="small muted">{t("play.hintTitle")}</p>
      <table className="metrics">
        <thead>
          <tr>
            <th />
            {ACTIONS.map((name) => (
              <th key={name}>{t(`replay.action.${name}` as MessageKey)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr>
            <th>{t("replay.probability")}</th>
            {ACTIONS.map((name) => {
              const value = hint.probabilities[name] ?? 0;
              return (
                <td key={name}>
                  <span className="prob-bar" style={{ width: `${Math.round(value * 60)}px` }} />
                  {formatNumber(value * 100, 1)}%
                </td>
              );
            })}
          </tr>
          {hint.details.map((detail) => (
            <tr key={detail.name}>
              <th>
                {detail.name}
                <span className="muted small"> ({detail.unit})</span>
              </th>
              {ACTIONS.map((name) => {
                const values = Object.entries(detail.per_action).filter(([key]) => key === name || key.startsWith(`${name}_`));
                return (
                  <td key={name} className="small">
                    {values.length === 0
                      ? "–"
                      : values.map(([key, value]) => (
                          <div key={key}>
                            {key !== name ? <span className="muted">{key.replace(`${name}_`, "")}: </span> : null}
                            {formatNumber(value, 3)}
                          </div>
                        ))}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
