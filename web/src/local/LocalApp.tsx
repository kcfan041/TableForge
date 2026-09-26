import { useCallback, useEffect, useState, type FormEvent } from "react";

import { Card, StatusBadge } from "../components";
import { useI18n, type Locale, type MessageKey } from "../i18n";
import { local, type DoctorReport, type LocalStatus, type LogLine, type WorkerView } from "./api";

function usePolling<T>(load: () => Promise<T>, intervalMs: number): [T | null, string | null, () => void] {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let cancelled = false;
    const run = () =>
      load()
        .then((value) => {
          if (!cancelled) {
            setData(value);
            setError(null);
          }
        })
        .catch((reason: unknown) => {
          if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
        });
    void run();
    const timer = window.setInterval(() => void run(), intervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
    // load is stable for the lifetime of each caller
  }, [intervalMs, tick]);
  return [data, error, () => setTick((value) => value + 1)];
}

function Join({ onJoined }: { onJoined: () => void }) {
  const { t } = useI18n();
  const [invite, setInvite] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await local.join(invite.trim());
      setInvite("");
      onJoined();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={t("local.join")}>
      <form className="stack" onSubmit={(event) => void submit(event)}>
        <p className="muted small">{t("local.joinHelp")}</p>
        <textarea
          aria-label={t("local.join")}
          value={invite}
          onChange={(event) => setInvite(event.target.value)}
          rows={4}
          placeholder="tfinv1_…"
          spellCheck={false}
          required
        />
        <div>
          <button type="submit" className="primary" disabled={busy || !invite.trim()}>
            {busy ? t("local.joining") : t("local.joinButton")}
          </button>
        </div>
        {error ? (
          <p className="error" role="alert">
            {error}
          </p>
        ) : null}
      </form>
    </Card>
  );
}

function Doctor() {
  const { t } = useI18n();
  const [report, setReport] = useState<DoctorReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setReport(await local.doctor());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={t("local.doctor")}>
      <p className="muted small">{t("local.doctorHelp")}</p>
      <button type="button" onClick={() => void run()} disabled={busy}>
        {busy ? t("local.doctorRunning") : t("local.doctorButton")}
      </button>
      {error ? <p className="error">{error}</p> : null}
      {report ? (
        <table>
          <tbody>
            {report.checks.map((check) => (
              <tr key={check.name}>
                <td>
                  <StatusBadge status={check.ok ? "succeeded" : "failed"} />
                </td>
                <td>
                  <code>{check.name}</code>
                </td>
                <td>
                  {check.detail}
                  {check.hint ? <div className="warning small">→ {check.hint}</div> : null}
                </td>
                <td className="muted small nowrap">{check.seconds.toFixed(2)} s</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {report ? (
        <p className={report.ok ? "" : "error"}>{report.ok ? t("local.doctorOk") : t("local.doctorFailed")}</p>
      ) : null}
    </Card>
  );
}

function describe(line: LogLine, t: ReturnType<typeof useI18n>["t"]): string {
  const event = line.event ?? "";
  const key = `local.event.${event}` as MessageKey;
  if (event && t(key) !== key) {
    const extra = line.status ? ` (${String(line.status)})` : line.detail ? `：${String(line.detail)}` : "";
    return t(key) + extra;
  }
  if (event) {
    const fields = Object.entries(line)
      .filter(([name]) => !["timestamp", "event", "component", "node_id"].includes(name))
      .map(([name, value]) => `${name}=${typeof value === "object" ? JSON.stringify(value) : String(value)}`)
      .join(" ");
    return `${event} ${fields}`;
  }
  return line.message ?? JSON.stringify(line);
}

function WorkerLog({ worker }: { worker: WorkerView }) {
  const { t, formatDate } = useI18n();
  const load = useCallback(() => local.log(worker.worker), [worker.worker]);
  const [log] = usePolling(load, 2000);
  const lines = log?.lines ?? [];
  return (
    <div className="log">
      {lines.length === 0 ? (
        <p className="muted small">{t("local.logEmpty")}</p>
      ) : (
        lines
          .slice()
          .reverse()
          .map((line, index) => (
            <div key={index} className="log-line">
              <span className="muted">{line.timestamp ? formatDate(line.timestamp) : ""}</span>{" "}
              {describe(line, t)}
            </div>
          ))
      )}
    </div>
  );
}

function Workers({ status, refresh }: { status: LocalStatus; refresh: () => void }) {
  const { t } = useI18n();
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const running = status.workers.filter((worker) => worker.running);

  const act = async (action: () => Promise<unknown>) => {
    setError(null);
    try {
      await action();
      refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const shown = open ?? status.workers[status.workers.length - 1]?.worker ?? null;
  return (
    <Card title={t("local.workers")}>
      <p className="muted small">{status.use_gpu ? t("local.workersHelpGpu") : t("local.workersHelpCpu")}</p>
      <div className="row">
        <button type="button" className="primary" onClick={() => void act(local.startWorker)}>
          {t("local.startWorker")}
        </button>
        {running.length > 0 ? (
          <span className="muted">{t("local.runningCount", { count: running.length })}</span>
        ) : null}
      </div>
      {error ? <p className="error">{error}</p> : null}
      {status.workers.length > 0 ? (
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>{t("experiments.status")}</th>
              <th>{t("local.jobs")}</th>
              <th>{t("local.lastError")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {status.workers
              .slice()
              .reverse()
              .map((worker) => (
                <tr key={worker.worker} className={worker.worker === shown ? "selected" : ""}>
                  <td>
                    <button type="button" className="link" onClick={() => setOpen(worker.worker)}>
                      {t("local.workerName", { number: worker.worker })}
                    </button>
                  </td>
                  <td>
                    <StatusBadge status={`worker_${worker.state}`} />
                    {worker.exit_code !== null && worker.exit_code !== 0 ? (
                      <span className="muted small"> exit {worker.exit_code}</span>
                    ) : null}
                  </td>
                  <td>{worker.jobs}</td>
                  <td className="small warning">{worker.last_error}</td>
                  <td>
                    {worker.running ? (
                      <button
                        type="button"
                        className="danger"
                        onClick={() => void act(() => local.stopWorker(worker.worker))}
                      >
                        {t("local.stopWorker")}
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      ) : null}
      {shown !== null ? (
        <>
          <h3>{t("local.log", { number: shown })}</h3>
          <WorkerLog worker={status.workers.find((item) => item.worker === shown) ?? status.workers[0]!} />
        </>
      ) : null}
    </Card>
  );
}

export function LocalApp() {
  const { t, locale, setLocale, formatDate } = useI18n();
  const [status, error, refresh] = usePolling(local.status, 2000);

  return (
    <main className="local">
      <header className="page-header">
        <h1>{t("local.title")}</h1>
        <select aria-label="language" value={locale} onChange={(event) => setLocale(event.target.value as Locale)}>
          <option value="zh-Hant">繁體中文</option>
          <option value="en">English</option>
        </select>
      </header>
      {error ? <p className="error">{t("local.panelError", { message: error })}</p> : null}
      {status === null ? (
        <p className="muted">{t("common.loading")}</p>
      ) : (
        <div className="page">
          <Card title={t("local.thisMachine")}>
            <dl className="facts">
              <dt>{t("local.dataDir")}</dt>
              <dd>
                <code className="wrap">{status.data_dir}</code>
              </dd>
              <dt>{t("local.version")}</dt>
              <dd>{status.version}</dd>
              <dt>{t("local.cluster")}</dt>
              <dd>
                {status.node ? (
                  <>
                    <StatusBadge status="joined" /> <code>{status.node.coordinator}</code>
                    <div className="muted small">
                      {status.node.cluster_id} · storage {status.node.storage_endpoint}
                      {status.node.session_expires_at
                        ? ` · ${t("local.sessionExpires", { date: formatDate(status.node.session_expires_at) })}`
                        : ""}
                    </div>
                  </>
                ) : (
                  <span className="muted">{t("local.notJoined")}</span>
                )}
              </dd>
              <dt>{t("local.webAccess")}</dt>
              <dd>
                {status.operator
                  ? t("local.webAccessRole", {
                      role: t(`role.${status.operator.role}` as MessageKey),
                    })
                  : t("local.webAccessNone")}
              </dd>
            </dl>
          </Card>
          {status.joined ? (
            <>
              <Workers status={status} refresh={refresh} />
              <Doctor />
            </>
          ) : (
            <Join onJoined={refresh} />
          )}
        </div>
      )}
    </main>
  );
}
