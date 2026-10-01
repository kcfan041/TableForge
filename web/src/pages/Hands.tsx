import { useEffect, useState } from "react";

import { type ModelView } from "../api";
import { Card, Page } from "../components";
import { useI18n, type MessageKey } from "../i18n";
import { localCall, localModels, Report, type PracticeReport } from "./Play";
import { ReplayViewer, type Replay } from "./Replay";

interface ImportResult {
  format: string;
  verified_against_real_exports: boolean;
  hands: number;
  problems: string[];
  payout_mismatches: string[];
  hero: string[];
  hero_ids: number[];
  tournaments: string[];
  replay: Replay;
  coach: string | null;
  not_reviewed: number;
  report: PracticeReport | null;
}

/** Hand histories from GGPoker / Natural8, read and reviewed on this computer only. */
export function HandsPage() {
  const { t } = useI18n();
  const [text, setText] = useState("");
  const [models, setModels] = useState<ModelView[] | null>(null);
  const [coach, setCoach] = useState("");
  const [mode, setMode] = useState("average");
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    localModels
      .list()
      .then((value) => {
        setModels(value);
        if (value[0]) setCoach(value[0].model_id);
      })
      .catch(() => setModels([]));
  }, []);

  const readFiles = async (files: FileList | null) => {
    if (!files) return;
    const parts = await Promise.all(Array.from(files).map((file) => file.text()));
    setText(parts.join("\n\n\n"));
  };

  const run = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await localCall<ImportResult>("POST", "/api/local/hands/import", { text, coach: coach || undefined, mode }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const model = models?.find((item) => item.model_id === coach);
  return (
    <Page title={t("hands.title")}>
      <p className="notice">{t("hands.unverified")}</p>
      <p className="muted">{t("hands.help")}</p>
      <Card>
        <div className="stack">
          <label>
            {t("hands.files")}
            <input type="file" accept=".txt,text/plain" multiple onChange={(event) => void readFiles(event.target.files)} />
          </label>
          <label>
            {t("hands.paste")}
            <textarea rows={8} value={text} onChange={(event) => setText(event.target.value)} spellCheck={false} />
          </label>
        </div>
        <div className="inline-form">
          <label>
            {t("hands.coach")}
            <select value={coach} onChange={(event) => setCoach(event.target.value)}>
              <option value="">{t("hands.noCoach")}</option>
              {(models ?? []).map((item) => (
                <option key={item.model_id} value={item.model_id}>
                  {item.model_id}
                </option>
              ))}
            </select>
          </label>
          {model ? (
            <label>
              {t("replay.mode")}
              <select value={mode} onChange={(event) => setMode(event.target.value)}>
                {model.play_modes.map((item) => (
                  <option key={item.key} value={item.key}>
                    {t(`playMode.${item.key}` as MessageKey)}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <button type="button" className="primary" disabled={busy || !text.trim()} onClick={() => void run()}>
            {busy ? t("hands.reading") : t("hands.read")}
          </button>
        </div>
        {models && models.length === 0 ? <p className="muted small">{t("hands.noModels")}</p> : null}
        {error ? <p className="error">{error}</p> : null}
      </Card>
      {result ? (
        <Card title={t("hands.summary")}>
          <p>
            {t("hands.read.count", {
              hands: result.hands,
              tournaments: result.tournaments.length,
              hero: result.hero.join(", ") || "–",
            })}
          </p>
          {result.coach ? (
            <p className="small muted">
              {t("hands.coachNote", { coach: result.coach })}
              {result.not_reviewed ? ` ${t("hands.notReviewed", { count: result.not_reviewed })}` : ""}
            </p>
          ) : null}
          {result.problems.length > 0 ? (
            <details className="small" open={result.hands === 0}>
              <summary className="warning">{t("hands.problems", { count: result.problems.length })}</summary>
              <ul>
                {result.problems.slice(0, 50).map((item, index) => (
                  <li key={index}>
                    <code className="wrap">{item}</code>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
          {result.payout_mismatches.length > 0 ? (
            <p className="warning small">{t("hands.mismatches", { hands: result.payout_mismatches.slice(0, 10).join(", ") })}</p>
          ) : null}
        </Card>
      ) : null}
      {result?.report ? <Report report={result.report} /> : null}
      {result && result.hands > 0 ? <ReplayViewer replay={result.replay} focus={result.hero_ids} readUrl={false} /> : null}
    </Page>
  );
}
