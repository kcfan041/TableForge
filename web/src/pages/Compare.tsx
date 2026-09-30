import { useEffect, useRef, useState } from "react";

import { api, ApiError, can, type Session } from "../api";
import { Card, Page } from "../components";
import { useI18n, type MessageKey } from "../i18n";
import { urlParam } from "./analysis";

interface Interval {
  mean: number;
  low: number;
  high: number;
}

interface Candidates {
  groups: { tournament: string; game: string; players: number; models: string[] }[];
  rule_bots: string[];
}

interface ComparisonResult {
  competitors: string[];
  seeds: number;
  tournaments_per_pair: number;
  rows_finished: number;
  matrix: Record<string, Record<string, { roi: Interval; tournaments: number }>>;
  ranking: { name: string; average_roi: Interval }[];
}

interface ComparisonAnswer {
  status: string;
  jobs: string[];
  result: ComparisonResult;
}

const DEFAULT_RULES = ["pushfold_nash", "tight", "hunter"];

function label(name: string, t: ReturnType<typeof useI18n>["t"]): string {
  if (name.startsWith("rule:")) return t(`bot.${name.slice(5)}` as MessageKey);
  return name.replace(/^model:/, "");
}

function significance(interval: Interval): string {
  if (interval.low > 0) return "positive-text";
  if (interval.high < 0) return "negative-text";
  return "";
}

export function ComparePage({ session }: { session: Session }) {
  const { t, formatNumber } = useI18n();
  const [candidates, setCandidates] = useState<Candidates | null>(null);
  const [group, setGroup] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [rules, setRules] = useState<string[]>(DEFAULT_RULES);
  const [seeds, setSeeds] = useState("200");
  const [comparisonId, setComparisonId] = useState(urlParam("comparison") ?? "");
  const [answer, setAnswer] = useState<ComparisonAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    api
      .query<Candidates>("comparison_candidates")
      .then((value) => {
        setCandidates(value);
        const first = value.groups.find((item) => item.models.length >= 2) ?? value.groups[0];
        if (first) {
          setGroup(first.tournament);
          setModels(first.models.slice(0, 6));
        }
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason)));
    return () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, []);

  useEffect(() => {
    if (!comparisonId) return;
    let cancelled = false;
    const poll = () =>
      api
        .query<ComparisonAnswer>("comparison", comparisonId)
        .then((value) => {
          if (cancelled) return;
          setAnswer(value);
          if (value.status === "running") timer.current = window.setTimeout(poll, 3000);
        })
        .catch((reason: unknown) => !cancelled && setError(reason instanceof Error ? reason.message : String(reason)));
    void poll();
    return () => {
      cancelled = true;
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, [comparisonId]);

  const current = candidates?.groups.find((item) => item.tournament === group);
  const toggle = (list: string[], item: string) => (list.includes(item) ? list.filter((value) => value !== item) : [...list, item]);

  const start = async () => {
    setBusy(true);
    setError(null);
    setAnswer(null);
    try {
      const created = await api.command<{ comparison_id: string }>("request_comparison", {
        models,
        rules,
        seeds: Number(seeds || 200),
      });
      setComparisonId(created.comparison_id);
      const url = new URL(window.location.href);
      url.searchParams.set("comparison", created.comparison_id);
      window.history.replaceState(null, "", url);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const result = answer?.result;
  const total = models.length + rules.length;
  const done = answer ? answer.jobs.filter((status) => status === "succeeded").length : 0;

  return (
    <Page title={t("compare.title")}>
      <p className="muted">{t("compare.help")}</p>
      {candidates ? (
        <Card>
          {candidates.groups.length === 0 ? <p className="muted">{t("models.empty")}</p> : null}
          <div className="inline-form">
            <label>
              {t("compare.tournament")}
              <select
                value={group}
                onChange={(event) => {
                  setGroup(event.target.value);
                  setModels(candidates.groups.find((item) => item.tournament === event.target.value)?.models.slice(0, 6) ?? []);
                }}
              >
                {candidates.groups.map((item) => (
                  <option key={item.tournament} value={item.tournament}>
                    {t("compare.group", { game: item.game, count: item.models.length })}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("compare.seeds")}
              <input className="narrow" value={seeds} onChange={(event) => setSeeds(event.target.value.replace(/\D/g, ""))} />
            </label>
          </div>
          {current ? (
            <div className="stack">
              <div>
                <strong className="small">{t("compare.models")}</strong>
                <div className="row">
                  {current.models.map((item) => (
                    <label key={item} className="checkbox small">
                      <input type="checkbox" checked={models.includes(item)} onChange={() => setModels(toggle(models, item))} />
                      {item}
                    </label>
                  ))}
                </div>
              </div>
              <div>
                <strong className="small">{t("compare.rules")}</strong>
                <div className="row">
                  {candidates.rule_bots.map((item) => (
                    <label key={item} className="checkbox small">
                      <input type="checkbox" checked={rules.includes(item)} onChange={() => setRules(toggle(rules, item))} />
                      {t(`bot.${item}` as MessageKey)}
                    </label>
                  ))}
                </div>
              </div>
            </div>
          ) : null}
          <p className="muted small">
            {t("compare.cost", {
              pairs: total * (total - 1),
              tournaments: formatNumber(total * (total - 1) * Number(seeds || 0) * (current?.players ?? 6)),
            })}
          </p>
          <button
            type="button"
            className="primary"
            disabled={!can(session, "analysis:run") || busy || models.length === 0 || total < 2 || total > 10}
            onClick={() => void start()}
          >
            {t("compare.start")}
          </button>
          {!can(session, "analysis:run") ? <p className="muted small">{t("replay.viewerNote")}</p> : null}
          {total > 10 ? <p className="error small">{t("compare.tooMany")}</p> : null}
        </Card>
      ) : null}
      {error ? <p className="error">{error}</p> : null}
      {answer && result ? (
        <Card title={t("compare.result")}>
          <p className="muted small">
            {answer.status === "running"
              ? t("compare.running", { done, total: answer.jobs.length })
              : answer.status === "failed"
                ? t("compare.failed")
                : t("compare.finished", { tournaments: formatNumber(result.tournaments_per_pair) })}
          </p>
          <h3>{t("compare.ranking")}</h3>
          <table className="metrics">
            <thead>
              <tr>
                <th>#</th>
                <th>{t("compare.competitor")}</th>
                <th>{t("compare.averageRoi")}</th>
                <th>{t("tournament.interval")}</th>
              </tr>
            </thead>
            <tbody>
              {result.ranking.map((item, index) => (
                <tr key={item.name}>
                  <td>{index + 1}</td>
                  <th>{label(item.name, t)}</th>
                  <td className={significance(item.average_roi)}>{formatNumber(item.average_roi.mean * 100, 1)}%</td>
                  <td className="small muted">
                    {formatNumber(item.average_roi.low * 100, 1)}% – {formatNumber(item.average_roi.high * 100, 1)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <h3>{t("compare.matrix")}</h3>
          <p className="muted small">{t("compare.matrixHelp")}</p>
          <div className="table-wrap">
            <table className="metrics">
              <thead>
                <tr>
                  <th>{t("compare.rowVsField")}</th>
                  {result.competitors.map((name) => (
                    <th key={name} className="small">
                      {label(name, t)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.competitors.map((row) => (
                  <tr key={row}>
                    <th className="small">{label(row, t)}</th>
                    {result.competitors.map((column) => {
                      const cell = result.matrix[row]?.[column];
                      if (row === column) return <td key={column} className="muted">–</td>;
                      if (!cell) return <td key={column} className="muted">…</td>;
                      return (
                        <td
                          key={column}
                          className={significance(cell.roi)}
                          title={`${formatNumber(cell.roi.low * 100, 1)}% – ${formatNumber(cell.roi.high * 100, 1)}%`}
                        >
                          {formatNumber(cell.roi.mean * 100, 1)}%
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted small">{t("compare.note")}</p>
        </Card>
      ) : null}
    </Page>
  );
}
