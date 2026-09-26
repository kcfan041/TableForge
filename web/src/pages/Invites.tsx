import { useEffect, useState, type FormEvent } from "react";

import { api, ApiError, type CreatedInvite, type Role, type Session } from "../api";
import { Card, CopyButton, LoadState, Page, ShortId, StatusBadge } from "../components";
import { useI18n, type MessageKey } from "../i18n";
import { useLiveData } from "../live";

function CreateInvite({ onCreated }: { onCreated: () => void }) {
  const { t, formatDate } = useI18n();
  const [address, setAddress] = useState("");
  const [node, setNode] = useState(true);
  const [role, setRole] = useState<Role | "">("operator");
  const [ttl, setTtl] = useState(24);
  const [label, setLabel] = useState("");
  const [confirmAdmin, setConfirmAdmin] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<CreatedInvite | null>(null);
  const isAdmin = role === "admin";

  useEffect(() => {
    void api.diagnostics().then((data) => {
      if (data.advertised_address) {
        setAddress((current) => current || data.advertised_address || "");
      }
    });
  }, []);

  useEffect(() => {
    if (isAdmin && ttl > 1) setTtl(1);
    if (!isAdmin) setConfirmAdmin(false);
  }, [isAdmin, ttl]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!node && !role) {
      setError(t("invites.needScope"));
      return;
    }
    if (isAdmin && !confirmAdmin) {
      setError(t("invites.adminConfirmNeeded"));
      return;
    }
    setBusy(true);
    setError(null);
    setCreated(null);
    try {
      setCreated(
        await api.createInvite({
          address: address.trim(),
          node,
          role: role || null,
          ttl_hours: ttl,
          label: label.trim(),
          confirm_admin: confirmAdmin,
        }),
      );
      onCreated();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={t("invites.create")}>
      <form className="stack" onSubmit={(event) => void submit(event)}>
        <label className="checkbox">
          <input type="checkbox" checked={node} onChange={(event) => setNode(event.target.checked)} />
          <span>
            {t("invites.node")}
            <div className="muted small">{t("invites.nodeHelp")}</div>
          </span>
        </label>
        <label>
          {t("invites.role")}
          <select value={role} onChange={(event) => setRole(event.target.value as Role | "")}>
            <option value="">{t("invites.roleNone")}</option>
            <option value="viewer">{t("role.viewer")}</option>
            <option value="operator">{t("role.operator")}</option>
            <option value="admin">{t("role.admin")}</option>
          </select>
          <span className="muted small">{t(`invites.roleHelp.${role || "none"}` as MessageKey)}</span>
        </label>
        {isAdmin ? (
          <label className="checkbox warning-box">
            <input
              type="checkbox"
              checked={confirmAdmin}
              onChange={(event) => setConfirmAdmin(event.target.checked)}
            />
            <span>{t("invites.adminConfirm")}</span>
          </label>
        ) : null}
        <div className="row">
          <label>
            {t("invites.ttl")}
            <input
              type="number"
              min={1}
              max={isAdmin ? 1 : 720}
              value={ttl}
              onChange={(event) => setTtl(Number(event.target.value))}
            />
          </label>
          <label className="grow">
            {t("invites.label")}
            <input value={label} onChange={(event) => setLabel(event.target.value)} maxLength={200} />
          </label>
        </div>
        <details>
          <summary className="small muted">{t("invites.advanced")}</summary>
          <label>
            {t("invites.address")}
            <input value={address} onChange={(event) => setAddress(event.target.value)} />
            <span className="muted small">{t("invites.addressHelp")}</span>
          </label>
        </details>
        <div>
          <button type="submit" className="primary" disabled={busy || (isAdmin && !confirmAdmin)}>
            {t("invites.submit")}
          </button>
        </div>
      </form>
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
      {created ? (
        <div className="invite-result">
          <p>{t("invites.result")}</p>
          <div className="code-box">
            <code className="wrap">{created.invite}</code>
            <CopyButton text={created.invite} />
          </div>
          <p className="muted small">
            {created.scope} · {t("invites.expires")} {formatDate(created.expires_at)}
          </p>
          {created.warnings.map((warning) => (
            <p key={warning} className="warning small">
              ⚠ {warning}
            </p>
          ))}
          <p className="muted small">{t("invites.howToUse")}</p>
        </div>
      ) : null}
    </Card>
  );
}

export function Invites({ session }: { session: Session }) {
  const { t, formatDate } = useI18n();
  const invites = useLiveData(api.invites);
  const operators = useLiveData(api.operators);
  const [error, setError] = useState<string | null>(null);

  if (session.role !== "admin") {
    return (
      <Page title={t("invites.title")}>
        <p className="notice">{t("invites.adminOnly")}</p>
      </Page>
    );
  }

  const run = async (action: () => Promise<unknown>) => {
    setError(null);
    try {
      await action();
      invites.reload();
      operators.reload();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : String(reason));
    }
  };

  return (
    <Page title={t("invites.title")}>
      <CreateInvite onCreated={invites.reload} />
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
      <Card title={t("invites.list")}>
        <LoadState state={invites}>
          {(data) =>
            data.length === 0 ? (
              <p className="muted">{t("invites.empty")}</p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>{t("invites.scope")}</th>
                      <th>{t("invites.label")}</th>
                      <th>{t("experiments.status")}</th>
                      <th>{t("invites.expires")}</th>
                      <th>{t("invites.redeemedBy")}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {data.map((invite) => (
                      <tr key={invite.invite_id}>
                        <td>
                          <ShortId id={invite.invite_id} />
                        </td>
                        <td>
                          <code>{invite.scope}</code>
                        </td>
                        <td>{invite.label}</td>
                        <td>
                          <StatusBadge status={invite.state} />
                        </td>
                        <td className="nowrap">{formatDate(invite.expires_at)}</td>
                        <td>{invite.redeemed_by ? <ShortId id={invite.redeemed_by} /> : null}</td>
                        <td>
                          {invite.state === "open" ? (
                            <button
                              type="button"
                              className="danger"
                              onClick={() => {
                                if (window.confirm(t("invites.revokeConfirm")))
                                  void run(() => api.revokeInvite(invite.invite_id));
                              }}
                            >
                              {t("invites.revoke")}
                            </button>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          }
        </LoadState>
      </Card>
      <Card title={t("invites.operators")}>
        <p className="muted small">{t("invites.operatorsHelp")}</p>
        <LoadState state={operators}>
          {(data) => (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>{t("invites.role")}</th>
                    <th>{t("invites.label")}</th>
                    <th>{t("experiments.created")}</th>
                    <th>{t("invites.expires")}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {data.map((operator) => (
                    <tr key={operator.operator_id}>
                      <td>
                        <ShortId id={operator.operator_id} />
                      </td>
                      <td>{t(`role.${operator.role}` as MessageKey)}</td>
                      <td>
                        {operator.label} {operator.current ? <span className="muted">{t("invites.current")}</span> : null}
                      </td>
                      <td className="nowrap">{formatDate(operator.created_at)}</td>
                      <td className="nowrap">{formatDate(operator.expires_at)}</td>
                      <td>
                        {operator.current ? null : (
                          <button
                            type="button"
                            className="danger"
                            onClick={() => {
                              if (window.confirm(t("invites.revokeOperatorConfirm", { label: operator.label })))
                                void run(() => api.revokeOperator(operator.operator_id));
                            }}
                          >
                            {t("invites.revoke")}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </LoadState>
      </Card>
    </Page>
  );
}
