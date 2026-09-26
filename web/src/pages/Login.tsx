import { useState, type FormEvent } from "react";

import { api, ApiError, type Session } from "../api";
import { useI18n } from "../i18n";

export function Login({ onSignedIn }: { onSignedIn: (session: Session) => void }) {
  const { t } = useI18n();
  const [credential, setCredential] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSignedIn(await api.login(credential.trim()));
      setCredential("");
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login">
      <form className="card login-card" onSubmit={(event) => void submit(event)}>
        <h1>{t("login.title")}</h1>
        <p className="muted">{t("login.help")}</p>
        <label htmlFor="credential" className="visually-hidden">
          {t("login.placeholder")}
        </label>
        <textarea
          id="credential"
          value={credential}
          onChange={(event) => setCredential(event.target.value)}
          placeholder={t("login.placeholder")}
          rows={4}
          autoComplete="off"
          spellCheck={false}
          required
        />
        {error ? (
          <p className="error" role="alert">
            {error}
          </p>
        ) : null}
        <button type="submit" className="primary" disabled={busy || !credential.trim()}>
          {busy ? t("login.submitting") : t("login.submit")}
        </button>
        <p className="muted small">{t("login.adminHint")}</p>
      </form>
    </main>
  );
}
