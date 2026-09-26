import { useEffect, useState, type FormEvent } from "react";

import { Card } from "../components";
import { useI18n } from "../i18n";
import { local } from "./api";

type Choice = "none" | "create" | "join";

function CreateCluster({ onDone, onBack }: { onDone: () => void; onBack: () => void }) {
  const { t } = useI18n();
  const [tailscale, setTailscale] = useState<string | null | undefined>(undefined);
  const [network, setNetwork] = useState<"tailscale" | "local">("tailscale");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void local.setup().then((setup) => {
      setTailscale(setup.tailscale_ip);
      if (!setup.tailscale_ip) setNetwork("local");
    });
  }, []);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      await local.createCluster(network);
      onDone();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={t("setup.createTitle")}>
      <div className="stack">
        <p className="muted small">{t("setup.createHelp")}</p>
        {tailscale === undefined ? (
          <p className="muted">{t("setup.detecting")}</p>
        ) : (
          <>
            <label className="checkbox">
              <input
                type="radio"
                name="network"
                checked={network === "tailscale"}
                disabled={!tailscale}
                onChange={() => setNetwork("tailscale")}
              />
              <span>
                {t("setup.networkTailscale")}
                <div className="muted small">
                  {tailscale ? t("setup.tailscaleFound", { ip: tailscale }) : t("setup.tailscaleMissing")}
                </div>
              </span>
            </label>
            <label className="checkbox">
              <input
                type="radio"
                name="network"
                checked={network === "local"}
                onChange={() => setNetwork("local")}
              />
              <span>
                {t("setup.networkLocal")}
                <div className="muted small">{t("setup.networkLocalHelp")}</div>
              </span>
            </label>
          </>
        )}
        <div className="row">
          <button type="button" className="primary" disabled={busy || tailscale === undefined} onClick={() => void create()}>
            {busy ? t("setup.creating") : t("setup.createButton")}
          </button>
          <button type="button" onClick={onBack} disabled={busy}>
            {t("common.back")}
          </button>
        </div>
        {error ? (
          <p className="error" role="alert">
            {error}
          </p>
        ) : null}
      </div>
    </Card>
  );
}

function JoinCluster({ onDone, onBack }: { onDone: () => void; onBack: () => void }) {
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
      onDone();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={t("setup.joinTitle")}>
      <form className="stack" onSubmit={(event) => void submit(event)}>
        <p className="muted small">{t("local.joinHelp")}</p>
        <textarea
          aria-label={t("setup.joinTitle")}
          value={invite}
          onChange={(event) => setInvite(event.target.value)}
          rows={4}
          placeholder="tfinv1_…"
          spellCheck={false}
          required
        />
        <div className="row">
          <button type="submit" className="primary" disabled={busy || !invite.trim()}>
            {busy ? t("local.joining") : t("local.joinButton")}
          </button>
          <button type="button" onClick={onBack} disabled={busy}>
            {t("common.back")}
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

export function Setup({ onDone }: { onDone: () => void }) {
  const { t } = useI18n();
  const [choice, setChoice] = useState<Choice>("none");

  if (choice === "create") return <CreateCluster onDone={onDone} onBack={() => setChoice("none")} />;
  if (choice === "join") return <JoinCluster onDone={onDone} onBack={() => setChoice("none")} />;
  return (
    <div className="choices">
      <button type="button" className="choice" onClick={() => setChoice("create")}>
        <strong>{t("setup.create")}</strong>
        <span className="muted small">{t("setup.createDescription")}</span>
      </button>
      <button type="button" className="choice" onClick={() => setChoice("join")}>
        <strong>{t("setup.join")}</strong>
        <span className="muted small">{t("setup.joinDescription")}</span>
      </button>
    </div>
  );
}
