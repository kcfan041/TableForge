import { useCallback, useEffect, useState, type ReactNode } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";

import { api, localMode, setCsrfToken, setUnauthorizedHandler, type Session } from "./api";
import { useI18n, type Locale, type MessageKey } from "./i18n";
import { LiveProvider, useLive } from "./live";
import { Checkpoints } from "./pages/Checkpoints";
import { Diagnostics } from "./pages/Diagnostics";
import { Events } from "./pages/Events";
import { ExperimentPage } from "./pages/Experiment";
import { Experiments } from "./pages/Experiments";
import { Invites } from "./pages/Invites";
import { Login } from "./pages/Login";
import { Nodes } from "./pages/Nodes";
import { Overview } from "./pages/Overview";

function LiveIndicator() {
  const { connected } = useLive();
  const { t } = useI18n();
  return (
    <span className={`live ${connected ? "on" : "off"}`} title={connected ? t("common.live") : t("common.offline")}>
      <span className="dot" aria-hidden="true" />
      {connected ? t("common.live") : t("common.offline")}
    </span>
  );
}

export interface ShellProps {
  /** Cluster management identity; null when this computer has no management access. */
  session: Session | null;
  /** True while the identity is still being fetched; unknown pages are not redirected yet. */
  sessionPending?: boolean;
  onSignOut?: () => void;
  /** Extra first page (the local "this computer" page inside `tableforge ui`). */
  machinePage?: ReactNode;
}

export function Shell({ session, sessionPending = false, onSignOut, machinePage }: ShellProps) {
  const { t, locale, setLocale } = useI18n();
  const links: [string, MessageKey][] = [];
  if (machinePage) links.push(["/", "nav.machine"]);
  if (session) {
    links.push(
      [machinePage ? "/overview" : "/", "nav.overview"],
      ["/nodes", "nav.nodes"],
      ["/experiments", "nav.experiments"],
      ["/checkpoints", "nav.checkpoints"],
      ["/events", "nav.events"],
    );
    if (session.role === "admin") links.push(["/invites", "nav.invites"]);
    links.push(["/diagnostics", "nav.diagnostics"]);
  }

  const content = (
    <div className="shell">
      <nav className="sidebar" aria-label="main">
        <div className="brand">{t("app.title")}</div>
        <ul>
          {links.map(([to, key]) => (
            <li key={to}>
              <NavLink to={to} end={to === "/"}>
                {t(key)}
              </NavLink>
            </li>
          ))}
        </ul>
        <div className="sidebar-footer">
          {session ? <LiveIndicator /> : null}
          {session ? (
            <div className="small">
              {t("nav.signedInAs", {
                label: session.label || session.operator_id.slice(0, 8),
                role: t(`role.${session.role}` as MessageKey),
              })}
            </div>
          ) : null}
          <select
            aria-label="language"
            value={locale}
            onChange={(event) => setLocale(event.target.value as Locale)}
          >
            <option value="zh-Hant">繁體中文</option>
            <option value="en">English</option>
          </select>
          {onSignOut ? (
            <button type="button" onClick={onSignOut}>
              {t("nav.signOut")}
            </button>
          ) : null}
        </div>
      </nav>
      <main className="content">
        <Routes>
          {machinePage ? <Route path="/" element={machinePage} /> : null}
          {session ? (
            <>
              <Route path={machinePage ? "/overview" : "/"} element={<Overview />} />
              <Route path="/nodes" element={<Nodes session={session} />} />
              <Route path="/experiments" element={<Experiments session={session} />} />
              <Route path="/experiments/:id" element={<ExperimentPage session={session} />} />
              <Route path="/checkpoints" element={<Checkpoints />} />
              <Route path="/events" element={<Events />} />
              <Route path="/invites" element={<Invites session={session} />} />
              <Route path="/diagnostics" element={<Diagnostics />} />
            </>
          ) : null}
          <Route
            path="*"
            element={
              sessionPending ? (
                <p className="muted">{t("common.loading")}</p>
              ) : (
                <Navigate to="/" replace />
              )
            }
          />
        </Routes>
      </main>
    </div>
  );
  return session ? <LiveProvider>{content}</LiveProvider> : content;
}

export function App() {
  const { t } = useI18n();
  const [session, setSession] = useState<Session | null>(null);
  const [checking, setChecking] = useState(true);

  const accept = useCallback((next: Session | null) => {
    setCsrfToken(next?.csrf_token ?? null);
    setSession(next);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => accept(null));
    api
      .session()
      .then(accept)
      .catch(() => accept(null))
      .finally(() => setChecking(false));
  }, [accept]);

  if (localMode) throw new Error("App is the coordinator entry; use LocalApp in tableforge ui");
  if (checking) return <p className="muted center">{t("common.loading")}</p>;
  if (!session) return <Login onSignedIn={accept} />;
  return (
    <Shell
      session={session}
      onSignOut={() => {
        void api.logout().finally(() => accept(null));
      }}
    />
  );
}
