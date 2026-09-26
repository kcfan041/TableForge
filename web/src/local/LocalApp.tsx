import { useCallback, useEffect, useState } from "react";

import { api, type Session } from "../api";
import { Shell } from "../App";
import { Card, StatusBadge } from "../components";
import { useI18n, type Locale, type MessageKey } from "../i18n";
import { local, type DoctorReport, type LocalStatus, type LogLine, type WorkerView } from "./api";
import { Setup } from "./Setup";

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

function ClusterService({ status, refresh }: { status: LocalStatus; refresh: () => void }) {
  const { t, formatDate } = useI18n();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hosting = status.hosting;

  const act = async (action: () => Promise<unknown>, confirmText?: string) => {
    if (confirmText && !window.confirm(confirmText)) return;
    setBusy(true);
    setError(null);
    try {
      await action();
      refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const state = hosting.running ? "service_running" : hosting.starting ? "service_starting" : "service_stopped";
  return (
    <Card title={t("service.title")}>
      <p className="muted small">{t("service.help")}</p>
      <dl className="facts">
        <dt>{t("experiments.status")}</dt>
        <dd>
          <StatusBadge status={state} />
          {hosting.running && hosting.started_at ? (
            <span className="muted small"> {t("service.since", { date: formatDate(hosting.started_at) })}</span>
          ) : null}
        </dd>
        <dt>{t("service.address")}</dt>
        <dd>
          <code>{hosting.address}</code>
          <div className="muted small">{t("service.addressHelp")}</div>
        </dd>
      </dl>
      <div className="row">
        {hosting.running ? (
          <button
            type="button"
            className="danger"
            disabled={busy}
            onClick={() => void act(local.stopService, t("service.stopConfirm"))}
          >
            {t("service.stop")}
          </button>
        ) : (
          <button type="button" className="primary" disabled={busy} onClick={() => void act(local.startService)}>
            {busy ? t("service.starting") : t("service.start")}
          </button>
        )}
      </div>
      {error ? <pre className="error small config">{error}</pre> : null}
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


function MachinePage({ status, refresh }: { status: LocalStatus; refresh: () => void }) {
  const { t, formatDate } = useI18n();
  return (
    <section className="page">
      <header className="page-header">
        <h1>{t("nav.machine")}</h1>
      </header>
      <Card title={t("local.thisMachine")}>
        <dl className="facts">
          <dt>{t("local.cluster")}</dt>
          <dd>
            {status.node ? (
              <>
                <StatusBadge status="joined" />{" "}
                {status.hosting.hosted ? t("local.clusterOwn") : t("local.clusterOther")}
                <details className="small muted">
                  <summary>{t("local.details")}</summary>
                  <div>
                    {t("local.detailCluster")}: <code>{status.node.cluster_id}</code>
                  </div>
                  <div>
                    {t("local.detailCoordinator")}: <code>{status.node.coordinator}</code>
                  </div>
                  {status.node.session_expires_at ? (
                    <div>
                      {t("local.sessionExpires", { date: formatDate(status.node.session_expires_at) })}
                    </div>
                  ) : null}
                  <div>
                    {t("local.dataDir")}: <code className="wrap">{status.data_dir}</code>
                  </div>
                  <div>TableForge {status.version}</div>
                </details>
              </>
            ) : (
              <span className="muted">
                {status.operator ? t("local.managementOnly") : t("local.notJoined")}
              </span>
            )}
          </dd>
          <dt>{t("local.webAccess")}</dt>
          <dd>
            {status.operator
              ? t("local.webAccessRole", { role: t(`role.${status.operator.role}` as MessageKey) })
              : t("local.webAccessNone")}
          </dd>
        </dl>
      </Card>
      {status.hosting.hosted ? <ClusterService status={status} refresh={refresh} /> : null}
      {status.joined ? (
        <>
          <Workers status={status} refresh={refresh} />
          <Doctor />
        </>
      ) : null}
    </section>
  );
}

export function LocalApp() {
  const { t, locale, setLocale } = useI18n();
  const [status, error, refresh] = usePolling(local.status, 2000);
  const [session, setSession] = useState<Session | null>(null);
  const [sessionPending, setSessionPending] = useState(true);
  const operatorKey = status?.operator ? `${status.operator.cluster_id}:${status.operator.role}` : "";
  const serviceUp = status ? !status.hosting.hosted || Boolean(status.hosting.running) : false;

  useEffect(() => {
    if (status === null) return;
    if (!operatorKey || !serviceUp) {
      setSession(null);
      setSessionPending(false);
      return;
    }
    let cancelled = false;
    setSessionPending(true);
    api
      .session()
      .then((value) => {
        if (!cancelled) setSession(value);
      })
      .catch(() => {
        if (!cancelled) setSession(null);
      })
      .finally(() => {
        if (!cancelled) setSessionPending(false);
      });
    return () => {
      cancelled = true;
    };
  }, [status === null, operatorKey, serviceUp]);

  if (status === null) {
    return (
      <main className="local">
        {error ? <p className="error">{t("local.panelError", { message: error })}</p> : null}
        <p className="muted">{t("common.loading")}</p>
      </main>
    );
  }
  if (!status.joined && !status.hosting.hosted && !status.operator) {
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
        <Setup onDone={refresh} />
      </main>
    );
  }
  return (
    <>
      {error ? <p className="error banner">{t("local.panelError", { message: error })}</p> : null}
      <Shell
        session={session}
        sessionPending={sessionPending}
        machinePage={<MachinePage status={status} refresh={refresh} />}
      />
    </>
  );
}
