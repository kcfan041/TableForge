import { useState, type ReactNode } from "react";

import type { Loaded } from "./live";
import { useI18n, type MessageKey } from "./i18n";

export function StatusBadge({ status }: { status: string }) {
  const { t } = useI18n();
  const key = `status.${status}` as MessageKey;
  const label = t(key) === key ? status : t(key);
  return <span className={`badge badge-${status}`}>{label}</span>;
}

export function Page({
  title,
  actions,
  children,
}: {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="page">
      <header className="page-header">
        <h1>{title}</h1>
        {actions ? <div className="page-actions">{actions}</div> : null}
      </header>
      {children}
    </section>
  );
}

export function Card({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <div className="card">
      {title ? <h2>{title}</h2> : null}
      {children}
    </div>
  );
}

export function LoadState<T>({
  state,
  children,
}: {
  state: Loaded<T>;
  children: (data: T) => ReactNode;
}) {
  const { t } = useI18n();
  if (state.data !== null) {
    return (
      <>
        {state.error ? (
          <p className="error" role="alert">
            {t("common.error", { message: state.error })}
          </p>
        ) : null}
        {children(state.data)}
      </>
    );
  }
  if (state.error) {
    return (
      <div className="error" role="alert">
        {t("common.error", { message: state.error })}{" "}
        <button type="button" onClick={state.reload}>
          {t("common.retry")}
        </button>
      </div>
    );
  }
  return <p className="muted">{t("common.loading")}</p>;
}

export function CopyButton({ text }: { text: string }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 2000);
        });
      }}
    >
      {copied ? t("common.copied") : t("common.copy")}
    </button>
  );
}

export function ShortId({ id }: { id: string }) {
  return (
    <code className="id" title={id}>
      {id.slice(0, 8)}…{id.slice(-4)}
    </code>
  );
}

export function Counts({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).filter(([, value]) => value > 0);
  const { t } = useI18n();
  if (entries.length === 0) return <span className="muted">{t("common.none")}</span>;
  return (
    <span className="counts">
      {entries.map(([status, value]) => (
        <span key={status} className="count">
          <StatusBadge status={status} /> {value}
        </span>
      ))}
    </span>
  );
}

export function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return "?";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}
