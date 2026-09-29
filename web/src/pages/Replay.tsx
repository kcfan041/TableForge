import { useEffect, useMemo, useState } from "react";

import type { Session } from "../api";
import { Card, Page } from "../components";
import { useI18n, type MessageKey } from "../i18n";
import { initialModel, urlParam, useAnalysis, useModels } from "./analysis";
import { canOperate } from "./Experiments";

interface Explanation {
  probabilities: Record<string, number>;
  details: { name: string; unit: string; per_action: Record<string, number>; value: number | null }[];
  raise_sizes: Record<string, number>;
}

interface ReplayAction {
  player: number;
  street: string;
  pot: number;
  to_call: number;
  stack: number;
  action: string;
  amount: number | null;
  explanation: Explanation | null;
}

interface ReplayHand {
  hand: number;
  level: number;
  small_blind: number;
  big_blind: number;
  ante: number;
  button: number;
  players: number[];
  stacks_before: number[];
  stacks_after: number[];
  bounties_before: number[];
  bounties_after: number[];
  icm_before: number[];
  icm_after: number[];
  hole_cards: string[][];
  board: string[];
  actions: ReplayAction[];
  busted: number[];
  knockouts: { eliminated: number; collector: number; cash: number; added_to_bounty: number }[];
  showdown: boolean;
  winners: number[];
}

interface Replay {
  seed: number;
  labels: string[];
  hands: ReplayHand[];
  result: { places: number[]; prizes: number[]; bounties: number[]; results: number[] };
  config: { players: number; payout: number[] };
}

const ACTIONS = ["fold", "check_call", "raise", "all_in"];
const SUIT: Record<string, string> = { s: "♠", h: "♥", d: "♦", c: "♣" };
type Filter = "all" | "model" | "allin" | "knockout" | "busted";

function CardFace({ card, hidden = false }: { card: string; hidden?: boolean }) {
  if (hidden) return <span className="playing-card back" />;
  const suit = card.slice(-1);
  const red = suit === "h" || suit === "d";
  return (
    <span className={`playing-card${red ? " red" : ""}`}>
      {card.slice(0, -1).replace("T", "10")}
      {SUIT[suit] ?? suit}
    </span>
  );
}

function streetBoard(hand: ReplayHand, street: string): string[] {
  const count = { preflop: 0, flop: 3, turn: 4, river: 5 }[street] ?? 5;
  return hand.board.slice(0, count);
}

function modelIndex(replay: Replay, modelId: string): number[] {
  return replay.labels.map((label, index) => (label === modelId ? index : -1)).filter((index) => index >= 0);
}

function matches(hand: ReplayHand, filter: Filter, model: number[]): boolean {
  const modelActions = hand.actions.filter((action) => model.includes(action.player));
  if (filter === "all") return true;
  if (filter === "model") return modelActions.some((action) => action.action !== "fold");
  if (filter === "allin") return hand.actions.some((action) => action.action === "all_in" && model.includes(action.player));
  if (filter === "knockout") return hand.knockouts.some((item) => model.includes(item.collector));
  return hand.busted.some((player) => model.includes(player));
}

export function ReplayPage({ session }: { session: Session }) {
  const { t, formatNumber } = useI18n();
  const { models, error: modelsError } = useModels();
  const [modelId, setModelId] = useState("");
  const [mode, setMode] = useState("");
  const [seed, setSeed] = useState(urlParam("seed") ?? "1");
  const [seat, setSeat] = useState(urlParam("seat") ?? "0");
  const [opponents, setOpponents] = useState(urlParam("opponents") ?? "mixed");
  const [autoRun, setAutoRun] = useState(urlParam("run") === "1");
  const [handIndex, setHandIndex] = useState(0);
  const [step, setStep] = useState(0);
  const [filter, setFilter] = useState<Filter>("all");
  const [showCards, setShowCards] = useState(true);
  const { state, request } = useAnalysis<Replay>("request_replay");

  useEffect(() => {
    if (!modelId && models && models.length > 0) setModelId(initialModel(models));
  }, [models, modelId]);
  const model = models?.find((item) => item.model_id === modelId);
  useEffect(() => {
    if (!model) return;
    const wanted = urlParam("mode");
    setMode(model.play_modes.some((item) => item.key === wanted) ? (wanted as string) : (model.play_modes[0]?.key ?? ""));
  }, [model]);
  useEffect(() => {
    if (!autoRun || !model || !mode) return;
    setAutoRun(false);
    void request({ model_id: model.model_id, mode, seed: Number(seed || 0), seat: Number(seat), opponents });
  }, [autoRun, model, mode]); // eslint-disable-line react-hooks/exhaustive-deps

  const replay = state.result;
  const modelSeats = useMemo(() => (replay ? modelIndex(replay, modelId) : []), [replay, modelId]);
  const visible = useMemo(
    () => (replay ? replay.hands.map((hand, index) => [hand, index] as const).filter(([hand]) => matches(hand, filter, modelSeats)) : []),
    [replay, filter, modelSeats],
  );
  useEffect(() => {
    const wanted = Number(urlParam("hand") ?? "1") - 1;
    setHandIndex(replay && wanted > 0 && wanted < replay.hands.length ? wanted : 0);
    setStep(Number(urlParam("step") ?? "0"));
  }, [replay]);

  const hand = replay?.hands[handIndex];
  const actions = hand?.actions ?? [];
  const current = step > 0 ? actions[step - 1] : null;
  const street = current ? current.street : "preflop";

  const stacksNow = useMemo(() => {
    if (!hand) return new Map<number, number>();
    const map = new Map(hand.players.map((player, index) => [player, hand.stacks_before[index]!]));
    return map;
  }, [hand]);

  const folded = new Set(actions.slice(0, step).filter((action) => action.action === "fold").map((action) => action.player));
  const lastAction = new Map(actions.slice(0, step).map((action) => [action.player, action]));
  const finished = hand !== undefined && step >= actions.length;

  const go = (next: number) => {
    setHandIndex(Math.max(0, Math.min((replay?.hands.length ?? 1) - 1, next)));
    setStep(0);
  };

  const actionLabel = (action: ReplayAction) =>
    `${t(`replay.action.${action.action}` as MessageKey)}${action.amount && action.action !== "fold" && action.action !== "check_call" ? ` ${formatNumber(action.amount)}` : ""}`;

  return (
    <Page title={t("replay.title")}>
      <p className="muted">{t("replay.help")}</p>
      {modelsError ? <p className="error">{modelsError}</p> : null}
      {models && models.length === 0 ? <p className="notice">{t("models.empty")}</p> : null}
      {models && models.length > 0 ? (
        <Card>
          <div className="inline-form">
            <label>
              {t("replay.model")}
              <select value={modelId} onChange={(event) => setModelId(event.target.value)}>
                {models.map((item) => (
                  <option key={item.model_id} value={item.model_id}>
                    {item.model_id}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("replay.mode")}
              <select value={mode} onChange={(event) => setMode(event.target.value)}>
                {model?.play_modes.map((item) => (
                  <option key={item.key} value={item.key} title={item.description}>
                    {t(`playMode.${item.key}` as MessageKey)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("replay.opponents")}
              <select value={opponents} onChange={(event) => setOpponents(event.target.value)}>
                {["mixed", "self", "tight", "call", "aggro", "random", "pushfold", "hunter"].map((item) => (
                  <option key={item} value={item}>
                    {t(`replay.field.${item}` as MessageKey)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("replay.seat")}
              <select value={seat} onChange={(event) => setSeat(event.target.value)}>
                {[0, 1, 2, 3, 4, 5].map((item) => (
                  <option key={item} value={String(item)}>
                    {item + 1}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("replay.seed")}
              <input className="narrow" value={seed} onChange={(event) => setSeed(event.target.value.replace(/\D/g, ""))} />
            </label>
            <button
              type="button"
              className="primary"
              disabled={!canOperate(session) || state.busy || !modelId}
              onClick={() =>
                void request({ model_id: modelId, mode, seed: Number(seed || 0), seat: Number(seat), opponents })
              }
            >
              {state.busy ? t("replay.running") : t("replay.play")}
            </button>
            <button type="button" disabled={state.busy} onClick={() => setSeed(String(Math.floor(Math.random() * 1_000_000)))}>
              {t("replay.randomSeed")}
            </button>
          </div>
          {!canOperate(session) ? <p className="muted small">{t("replay.viewerNote")}</p> : null}
          {state.error ? <p className="error">{state.error}</p> : null}
          {state.busy ? <p className="muted small">{t("replay.waiting", { status: state.status ?? "" })}</p> : null}
        </Card>
      ) : null}
      {replay && hand ? (
        <>
          <Card title={t("replay.summary")}>
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
                {replay.labels.map((label, player) => (
                  <tr key={player} className={modelSeats.includes(player) ? "selected" : ""}>
                    <th>
                      {player + 1}. {label}
                    </th>
                    <td>{replay.result.places[player]}</td>
                    <td>{formatNumber(replay.result.prizes[player]!, 3)}</td>
                    <td>{formatNumber(replay.result.bounties[player]!, 3)}</td>
                    <td className={replay.result.results[player]! > 0 ? "positive-text" : replay.result.results[player]! < 0 ? "negative-text" : ""}>
                      {formatNumber(replay.result.results[player]!, 3)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted small">{t("replay.summaryHelp", { hands: replay.hands.length })}</p>
          </Card>
          <div className="replay-layout">
            <Card title={t("replay.hands")}>
              <label className="small">
                {t("replay.filter")}
                <select value={filter} onChange={(event) => setFilter(event.target.value as Filter)}>
                  {(["all", "model", "allin", "knockout", "busted"] as Filter[]).map((item) => (
                    <option key={item} value={item}>
                      {t(`replay.filter.${item}` as MessageKey)}
                    </option>
                  ))}
                </select>
              </label>
              <ol className="hand-list">
                {visible.map(([item, index]) => (
                  <li key={index}>
                    <button type="button" className={`link${index === handIndex ? " active" : ""}`} onClick={() => go(index)}>
                      #{item.hand} · {formatNumber(item.small_blind)}/{formatNumber(item.big_blind)}
                      {item.knockouts.some((knockout) => modelSeats.includes(knockout.collector)) ? " 🎯" : ""}
                      {item.busted.some((player) => modelSeats.includes(player)) ? " ✖" : ""}
                    </button>
                  </li>
                ))}
              </ol>
            </Card>
            <Card title={t("replay.handTitle", { hand: hand.hand, level: hand.level, sb: hand.small_blind, bb: hand.big_blind, ante: hand.ante })}>
              <div className="row">
                <button type="button" onClick={() => go(handIndex - 1)} disabled={handIndex === 0}>
                  ⏮ {t("replay.previousHand")}
                </button>
                <button type="button" onClick={() => setStep(Math.max(0, step - 1))} disabled={step === 0}>
                  ◀ {t("replay.back")}
                </button>
                <button type="button" className="primary" onClick={() => setStep(Math.min(actions.length, step + 1))} disabled={finished}>
                  {t("replay.next")} ▶
                </button>
                <button type="button" onClick={() => setStep(actions.length)} disabled={finished}>
                  {t("replay.toEnd")}
                </button>
                <button type="button" onClick={() => go(handIndex + 1)} disabled={handIndex >= replay.hands.length - 1}>
                  {t("replay.nextHand")} ⏭
                </button>
                <label className="checkbox small">
                  <input type="checkbox" checked={showCards} onChange={(event) => setShowCards(event.target.checked)} />
                  {t("replay.showCards")}
                </label>
              </div>
              <div className="table-felt">
                <div className="board">
                  {streetBoard(hand, finished ? "river" : street).map((card) => (
                    <CardFace key={card} card={card} />
                  ))}
                  <div className="pot">
                    {t("replay.pot")} {formatNumber(current ? current.pot : 0)}
                  </div>
                </div>
                <div className="seats">
                  {hand.players.map((player, index) => {
                    const isModel = modelSeats.includes(player);
                    const acting = current?.player === player;
                    const last = lastAction.get(player);
                    const stack = finished ? hand.stacks_after[index]! : (acting ? current!.stack : stacksNow.get(player)!);
                    return (
                      <div
                        key={player}
                        className={`seat${isModel ? " model" : ""}${acting ? " acting" : ""}${folded.has(player) ? " folded" : ""}`}
                      >
                        <div className="seat-name">
                          {player === hand.button ? <span className="dealer">D</span> : null}
                          {player + 1}. {replay.labels[player]}
                        </div>
                        <div className="seat-cards">
                          {hand.hole_cards[index]!.map((card) => (
                            <CardFace key={card} card={card} hidden={!showCards && !isModel} />
                          ))}
                        </div>
                        <div className="small">
                          {formatNumber(stack)} ({formatNumber(stack / hand.big_blind, 1)} BB)
                        </div>
                        {hand.bounties_before.length > 0 ? (
                          <div className="small muted">
                            {t("replay.bounty")} {formatNumber(hand.bounties_before[index]!, 3)}
                          </div>
                        ) : null}
                        {last ? <div className="seat-action">{actionLabel(last)}</div> : null}
                        {finished && hand.winners.includes(player) ? <div className="seat-action positive-text">{t("replay.won")}</div> : null}
                        {finished && hand.busted.includes(player) ? <div className="seat-action negative-text">{t("replay.busted")}</div> : null}
                      </div>
                    );
                  })}
                </div>
              </div>
              {current ? (
                <DecisionPanel action={current} label={replay.labels[current.player]!} actionLabel={actionLabel(current)} />
              ) : (
                <p className="muted small">{t("replay.startHelp")}</p>
              )}
              {finished ? (
                <div className="small">
                  {hand.knockouts.map((knockout, index) => (
                    <p key={index}>
                      {t("replay.knockout", {
                        collector: `${knockout.collector + 1}. ${replay.labels[knockout.collector]}`,
                        eliminated: `${knockout.eliminated + 1}. ${replay.labels[knockout.eliminated]}`,
                        cash: formatNumber(knockout.cash, 3),
                        added: formatNumber(knockout.added_to_bounty, 3),
                      })}
                    </p>
                  ))}
                </div>
              ) : null}
            </Card>
          </div>
        </>
      ) : null}
    </Page>
  );
}

function DecisionPanel({ action, label, actionLabel }: { action: ReplayAction; label: string; actionLabel: string }) {
  const { t, formatNumber } = useI18n();
  const explanation = action.explanation;
  return (
    <div className="decision">
      <p>
        <strong>
          {action.player + 1}. {label}
        </strong>{" "}
        · {t(`replay.street.${action.street}` as MessageKey)} · {t("replay.toCall")} {formatNumber(action.to_call)} ·{" "}
        <strong>{actionLabel}</strong>
      </p>
      {explanation ? (
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
                const value = explanation.probabilities[name] ?? 0;
                return (
                  <td key={name} className={name === action.action ? "chosen" : ""}>
                    <span className="prob-bar" style={{ width: `${Math.round(value * 60)}px` }} />
                    {formatNumber(value * 100, 1)}%
                  </td>
                );
              })}
            </tr>
            {explanation.details.map((detail) => (
              <tr key={detail.name}>
                <th>
                  {detail.name}
                  <span className="muted small"> ({detail.unit})</span>
                </th>
                {ACTIONS.map((name) => {
                  const values = Object.entries(detail.per_action).filter(([key]) => key === name || key.startsWith(`${name}_`));
                  return (
                    <td key={name} className="small">
                      {values.length === 0 ? "–" : values.map(([key, value]) => (
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
      ) : (
        <p className="muted small">{t("replay.noExplanation")}</p>
      )}
    </div>
  );
}
