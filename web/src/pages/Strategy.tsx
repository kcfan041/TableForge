import { useEffect, useMemo, useState } from "react";

import { can, type Session } from "../api";
import { Card, Page } from "../components";
import { useI18n, type MessageKey } from "../i18n";
import { initialModel, urlParam, useAnalysis, useModels } from "./analysis";

interface StrategyResult {
  mode: string;
  hands: string[];
  actions: string[];
  scenarios?: string[];
  players?: number;
  starting_stack_bb?: number;
  level?: number;
  blinds?: { small_blind: number; big_blind: number; ante: number };
  open_raise_bb?: number;
  positions: string[];
  stacks: number[];
  bounties: number[];
  value_unit: string;
  columns: string[];
  tables: Record<string, (number | null)[][] | Record<string, (number | null)[]>>;
  /** ICM Push/Fold Nash baseline (all-in or fold only); absent in older results. */
  pushfold_nash?: {
    name: string;
    max_stack_bb: number;
    tables: Record<string, Record<string, number>>;
    gap: Record<string, number>;
  };
}

type View = "model" | "baseline" | "difference";

/** The model's aggressive probability that the baseline also measures: shove when opening,
 * call when facing an all-in. */
function modelAggression(values: (number | null)[], scenario: string, actions: string[]): number {
  if (scenario === "call") return values[actions.indexOf("check_call")] ?? 0;
  return values[actions.indexOf("all_in")] ?? 0;
}

function differenceStyle(value: number): React.CSSProperties {
  // Red: the model is more aggressive than the baseline; blue: less aggressive.
  const strength = Math.min(1, Math.abs(value));
  const [r, g, b] = value >= 0 ? [220, 38, 38] : [37, 99, 235];
  return { background: `rgba(${r}, ${g}, ${b}, ${0.05 + strength * 0.9})`, color: strength > 0.55 ? "white" : undefined };
}

function baselineStyle(value: number, scenario: string): React.CSSProperties {
  const [r, g, b] = ACTION_COLORS[scenario === "call" ? "check_call" : "all_in"]!;
  return { background: `rgba(${r}, ${g}, ${b}, ${0.08 + value * 0.85})`, color: value > 0.55 ? "white" : undefined };
}

const RANKS = "AKQJT98765432";
const ACTION_COLORS: Record<string, [number, number, number]> = {
  fold: [148, 163, 184],
  check_call: [37, 99, 235],
  raise: [217, 119, 6],
  all_in: [220, 38, 38],
};

function cellName(row: number, column: number): string {
  const first = RANKS[row]!;
  const second = RANKS[column]!;
  if (row === column) return first + second;
  return column > row ? `${first}${second}s` : `${second}${first}o`;
}

/** Background colour: the aggressive action (all-in, raise, call) mixed by its share. */
function cellStyle(values: (number | null)[], focus: string, actions: string[]): React.CSSProperties {
  const probabilities = actions.map((_, index) => values[index] ?? 0);
  if (focus !== "mix") {
    const share = probabilities[actions.indexOf(focus)] ?? 0;
    const [r, g, b] = ACTION_COLORS[focus]!;
    return { background: `rgba(${r}, ${g}, ${b}, ${0.08 + share * 0.85})`, color: share > 0.55 ? "white" : undefined };
  }
  let offset = 0;
  const stops: string[] = [];
  actions.forEach((action, index) => {
    const share = probabilities[index]!;
    if (share <= 0) return;
    const [r, g, b] = ACTION_COLORS[action]!;
    stops.push(`rgb(${r}, ${g}, ${b}) ${offset * 100}% ${(offset + share) * 100}%`);
    offset += share;
  });
  return { background: `linear-gradient(to top, ${stops.join(", ")})`, color: "white", textShadow: "0 0 2px #000" };
}

export function StrategyPage({ session }: { session: Session }) {
  const { t, formatNumber } = useI18n();
  const { models, error: modelsError } = useModels();
  const [modelId, setModelId] = useState("");
  const [mode, setMode] = useState("");
  const [scenario, setScenario] = useState(urlParam("scenario") ?? "open");
  const [hero, setHero] = useState(urlParam("hero") ?? "BTN");
  const [shover, setShover] = useState(urlParam("shover") ?? "BTN");
  const [stack, setStack] = useState(urlParam("stack") ?? "");
  const [level, setLevel] = useState(urlParam("level") ?? "1");
  const [bounty, setBounty] = useState(urlParam("bounty") ?? "1");
  const [autoRun, setAutoRun] = useState(urlParam("run") === "1");
  const [focus, setFocus] = useState("mix");
  const [view, setView] = useState<View>((urlParam("view") as View | null) ?? "model");
  const [hover, setHover] = useState<string | null>(null);
  const { state, request } = useAnalysis<StrategyResult>("request_strategy");

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
    void request({ model_id: model.model_id, mode, level: Number(level) });
  }, [autoRun, model, mode]); // eslint-disable-line react-hooks/exhaustive-deps

  const result = state.result;
  const positions = result?.positions ?? ["UTG", "HJ", "CO", "BTN", "SB", "BB"];
  const scenarios = result?.scenarios ?? ["open", "call"];
  const heroes = scenario === "open" ? positions.slice(0, -1) : positions.slice(1);
  useEffect(() => {
    if (!result) return;
    const wanted = Number(stack);
    if (!result.stacks.includes(wanted)) {
      const start = result.starting_stack_bb ?? result.stacks[0]!;
      const closest = result.stacks.reduce((best, item) => (Math.abs(item - start) < Math.abs(best - start) ? item : best));
      setStack(String(closest));
    }
    if (!heroes.includes(hero)) setHero(heroes[heroes.length - 1]!);
  }, [result]); // eslint-disable-line react-hooks/exhaustive-deps
  const shovers = positions.slice(0, positions.indexOf(hero));
  useEffect(() => {
    if (!heroes.includes(hero)) setHero(heroes[heroes.length - 1]!);
  }, [scenario]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (scenario !== "open" && !shovers.includes(shover)) setShover(shovers[shovers.length - 1] ?? positions[0]!);
  }, [hero, scenario, result]); // eslint-disable-line react-hooks/exhaustive-deps

  const key = `${scenario}|${hero}|${scenario === "open" ? "" : shover}|${stack}|${scenario === "call" ? bounty : "1"}`;
  const table = result?.tables[key] as Record<string, (number | null)[]> | undefined;
  const actions = result?.actions ?? ["fold", "check_call", "raise", "all_in"];
  const shownActions = scenario === "call" ? ["fold", "check_call"] : actions;

  const totals = useMemo(() => {
    if (!table) return null;
    // Share of all 1,326 starting combinations: pairs 6, suited 4, offsuit 12.
    const weights = Object.fromEntries(
      Object.keys(table).map((name) => [name, name.length === 2 ? 6 : name.endsWith("s") ? 4 : 12]),
    );
    return actions.map((_, index) =>
      Object.entries(table).reduce((sum, [name, values]) => sum + (values[index] ?? 0) * weights[name]!, 0) / 1326,
    );
  }, [table, actions]);

  const exportCsv = () => {
    if (!result) return;
    const header = ["scenario", "hero", "shover", "stack_bb", "bounty_multiple", "hand", ...result.columns];
    const lines = [header.join(",")];
    for (const [tableKey, rows] of Object.entries(result.tables)) {
      const [spot, heroName, shoverName, stackBb, multiple] = tableKey.split("|");
      for (const [hand, values] of Object.entries(rows as Record<string, (number | null)[]>)) {
        lines.push([spot, heroName, shoverName, stackBb, multiple, hand, ...values.map((value) => (value === null ? "" : value))].join(","));
      }
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `strategy-${modelId}-${result.mode}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  };

  const hovered = hover && table ? table[hover] : null;
  const baseline = scenario === "raise" ? undefined : result?.pushfold_nash?.tables[key];
  const baselineGap = result?.pushfold_nash?.gap[key];
  const shownView: View = baseline ? view : "model";
  const baselineWeights = (name: string) => (name.length === 2 ? 6 : name.endsWith("s") ? 4 : 12);
  const comparison = useMemo(() => {
    if (!table || !baseline) return null;
    let modelTotal = 0;
    let modelPlays = 0;
    let baselineTotal = 0;
    let absolute = 0;
    const deviations: [string, number][] = [];
    for (const [name, values] of Object.entries(table)) {
      const weight = baselineWeights(name) / 1326;
      const mine = modelAggression(values, scenario, actions);
      const theirs = baseline[name] ?? 0;
      modelTotal += mine * weight;
      modelPlays += (1 - (values[actions.indexOf("fold")] ?? 0)) * weight;
      baselineTotal += theirs * weight;
      absolute += Math.abs(mine - theirs) * weight;
      deviations.push([name, mine - theirs]);
    }
    deviations.sort((left, right) => Math.abs(right[1]) - Math.abs(left[1]));
    return { modelTotal, modelPlays, baselineTotal, absolute, top: deviations.slice(0, 8) };
  }, [table, baseline, scenario, actions]);

  const cellFor = (name: string, values: (number | null)[]): React.CSSProperties => {
    if (shownView === "baseline" && baseline) return baselineStyle(baseline[name] ?? 0, scenario);
    if (shownView === "difference" && baseline) {
      return differenceStyle(modelAggression(values, scenario, actions) - (baseline[name] ?? 0));
    }
    return cellStyle(values, focus, actions);
  };

  return (
    <Page title={t("strategy.title")}>
      <p className="muted">{t("strategy.help")}</p>
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
              {t("strategy.level")}
              <input className="narrow" value={level} onChange={(event) => setLevel(event.target.value.replace(/\D/g, ""))} />
            </label>
            <button
              type="button"
              className="primary"
              disabled={!can(session, "analysis:run") || state.busy || !modelId}
              onClick={() => void request({ model_id: modelId, mode, level: Number(level || 1) })}
            >
              {state.busy ? t("strategy.running") : t("strategy.build")}
            </button>
            {result ? (
              <button type="button" onClick={exportCsv}>
                {t("strategy.csv")}
              </button>
            ) : null}
          </div>
          {!can(session, "analysis:run") ? <p className="muted small">{t("replay.viewerNote")}</p> : null}
          {state.error ? <p className="error">{state.error}</p> : null}
          {state.busy ? <p className="muted small">{t("strategy.waiting")}</p> : null}
        </Card>
      ) : null}
      {result ? (
        <Card>
          <div className="inline-form">
            <label>
              {t("strategy.scenario")}
              <select value={scenario} onChange={(event) => setScenario(event.target.value)}>
                {scenarios.map((item) => (
                  <option key={item} value={item}>
                    {t(`strategy.scenario.${item}` as MessageKey)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("strategy.hero")}
              <select value={hero} onChange={(event) => setHero(event.target.value)}>
                {heroes.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>
            {scenario !== "open" ? (
              <label>
                {t(scenario === "call" ? "strategy.shover" : "strategy.opener")}
                <select value={shover} onChange={(event) => setShover(event.target.value)}>
                  {shovers.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            <label>
              {t("strategy.stack")}
              <select value={stack} onChange={(event) => setStack(event.target.value)}>
                {result.stacks.map((item) => (
                  <option key={item} value={String(item)}>
                    {item} BB
                  </option>
                ))}
              </select>
            </label>
            {scenario === "call" && result.bounties.length > 1 ? (
              <label>
                {t("strategy.bounty")}
                <select value={bounty} onChange={(event) => setBounty(event.target.value)}>
                  {result.bounties.map((item) => (
                    <option key={item} value={String(item)}>
                      {item}×
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            {result.pushfold_nash && scenario !== "raise" ? (
              <label>
                {t("strategy.view")}
                <select value={shownView} onChange={(event) => setView(event.target.value as View)} disabled={!baseline}>
                  <option value="model">{t("strategy.view.model")}</option>
                  <option value="baseline">{t("strategy.view.baseline")}</option>
                  <option value="difference">{t("strategy.view.difference")}</option>
                </select>
              </label>
            ) : null}
            <label>
              {t("strategy.color")}
              <select value={focus} disabled={shownView !== "model"} onChange={(event) => setFocus(event.target.value)}>
                <option value="mix">{t("strategy.color.mix")}</option>
                {shownActions.map((action) => (
                  <option key={action} value={action}>
                    {t(`replay.action.${action}` as MessageKey)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {shownView === "model" ? (
            <div className="legend">
              {shownActions.map((action) => (
                <span key={action}>
                  <span className="swatch" style={{ background: `rgb(${ACTION_COLORS[action]!.join(",")})` }} />{" "}
                  {t(`replay.action.${action}` as MessageKey)}
                  {totals ? ` ${formatNumber(totals[actions.indexOf(action)]! * 100, 1)}%` : ""}
                </span>
              ))}
            </div>
          ) : (
            <div className="legend">
              {shownView === "difference" ? (
                <>
                  <span>
                    <span className="swatch" style={{ background: "rgb(220, 38, 38)" }} /> {t(`strategy.diff.more.${scenario === "call" ? "call" : "open"}` as MessageKey)}
                  </span>
                  <span>
                    <span className="swatch" style={{ background: "rgb(37, 99, 235)" }} /> {t(`strategy.diff.less.${scenario === "call" ? "call" : "open"}` as MessageKey)}
                  </span>
                </>
              ) : (
                <span>
                  <span className="swatch" style={{ background: `rgb(${ACTION_COLORS[scenario === "call" ? "check_call" : "all_in"]!.join(",")})` }} />{" "}
                  {t(`strategy.baselineAction.${scenario === "call" ? "call" : "open"}` as MessageKey)}
                  {comparison ? ` ${formatNumber(comparison.baselineTotal * 100, 1)}%` : ""}
                </span>
              )}
            </div>
          )}
          {result.pushfold_nash && scenario !== "raise" ? (
            <div className="notice small baseline-note">
              <strong>{t("strategy.baselineName")}</strong> {t("strategy.baselineDisclaimer")}
              {baseline && comparison ? (
                <div>
                  {t(`strategy.compare.${scenario === "call" ? "call" : "open"}` as MessageKey, {
                    model: formatNumber(comparison.modelTotal * 100, 1),
                    baseline: formatNumber(comparison.baselineTotal * 100, 1),
                    distance: formatNumber(comparison.absolute * 100, 1),
                  })}{" "}
                  {baselineGap !== undefined ? t("strategy.baselineGap", { gap: formatNumber(baselineGap, 5) }) : null}
                  {scenario === "open" && comparison.modelPlays - comparison.modelTotal > 0.05 ? (
                    <div className="warning">
                      {t("strategy.notPushFold", {
                        plays: formatNumber(comparison.modelPlays * 100, 1),
                        shoves: formatNumber(comparison.modelTotal * 100, 1),
                      })}
                    </div>
                  ) : null}
                  <div className="muted">
                    {t("strategy.largestDeviations")}{" "}
                    {comparison.top
                      .map(([name, value]) => `${name} ${value > 0 ? "+" : ""}${formatNumber(value * 100, 0)}%`)
                      .join(" · ")}
                  </div>
                </div>
              ) : (
                <div className="muted">{t("strategy.baselineMissing", { max: result.pushfold_nash.max_stack_bb })}</div>
              )}
            </div>
          ) : null}
          {table ? (
            <div className="strategy-layout">
              <div className="range-grid" onMouseLeave={() => setHover(null)}>
                {Array.from({ length: 13 }, (_, row) =>
                  Array.from({ length: 13 }, (_, column) => {
                    const name = cellName(row, column);
                    const values = table[name] ?? [];
                    return (
                      <div
                        key={name}
                        className={`range-cell${hover === name ? " hovered" : ""}`}
                        style={cellFor(name, values)}
                        onMouseEnter={() => setHover(name)}
                      >
                        {name}
                      </div>
                    );
                  }),
                )}
              </div>
              <div className="range-detail">
                {hovered && hover ? (
                  <>
                    <h3>{hover}</h3>
                    <table className="metrics">
                      <thead>
                        <tr>
                          <th />
                          <th>{t("replay.probability")}</th>
                          <th>{t("strategy.value", { unit: result.value_unit })}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {actions.map((action, index) => (
                          <tr key={action}>
                            <th>{t(`replay.action.${action}` as MessageKey)}</th>
                            <td>{formatNumber((hovered[index] ?? 0) * 100, 1)}%</td>
                            <td>{hovered[index + actions.length] === null ? "–" : formatNumber(hovered[index + actions.length]!, 3)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {baseline ? (
                      <p className="small">
                        {t(`strategy.baselineAction.${scenario === "call" ? "call" : "open"}` as MessageKey)} ({t("strategy.baselineName")}):{" "}
                        <strong>{formatNumber((baseline[hover] ?? 0) * 100, 1)}%</strong>
                        <br />
                        {t("strategy.view.model")}: {formatNumber(modelAggression(hovered, scenario, actions) * 100, 1)}%
                      </p>
                    ) : null}
                  </>
                ) : (
                  <p className="muted small">{t("strategy.hover")}</p>
                )}
              </div>
            </div>
          ) : (
            <p className="muted">{t("strategy.noSpot")}</p>
          )}
          <p className="muted small">
            {t("strategy.spotInfo", {
              players: result.players ?? 6,
              level: result.level ?? 1,
              sb: result.blinds?.small_blind ?? 10,
              bb: result.blinds?.big_blind ?? 20,
              ante: result.blinds?.ante ?? 20,
              raise: result.open_raise_bb ?? 2.5,
            })}{" "}
            {t("strategy.note")}
          </p>
        </Card>
      ) : null}
    </Page>
  );
}
