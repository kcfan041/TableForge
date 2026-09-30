import { useState } from "react";

import { api, ApiError, can, type Session } from "../api";
import { formatBytes, LoadState, Page, ShortId, StatusBadge } from "../components";
import { useI18n } from "../i18n";
import { useLiveData } from "../live";

export function Nodes({ session }: { session: Session }) {
  const { t, formatDate } = useI18n();
  const nodes = useLiveData(api.nodes);
  const [message, setMessage] = useState<string | null>(null);

  const revoke = async (nodeId: string) => {
    if (!window.confirm(t("nodes.revokeConfirm", { id: nodeId }))) return;
    try {
      const result = await api.revokeNode(nodeId);
      setMessage(t("nodes.revoked", { count: result.revoked_sessions }));
    } catch (reason) {
      setMessage(reason instanceof ApiError ? reason.message : String(reason));
    }
  };

  return (
    <Page title={t("nodes.title")}>
      {message ? <p className="notice">{message}</p> : null}
      <LoadState state={nodes}>
        {(data) =>
          data.length === 0 ? (
            <p className="muted">{t("nodes.empty")}</p>
          ) : (
            <div className="card table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("nodes.id")}</th>
                    <th>{t("nodes.status")}</th>
                    <th>{t("nodes.system")}</th>
                    <th>{t("nodes.cpu")}</th>
                    <th>{t("nodes.memory")}</th>
                    <th>{t("nodes.gpu")}</th>
                    <th>{t("nodes.workers")}</th>
                    <th>{t("nodes.heartbeat")}</th>
                    {can(session, "admin:manage") ? <th /> : null}
                  </tr>
                </thead>
                <tbody>
                  {data.map((node) => (
                    <tr key={node.node_id}>
                      <td>
                        <ShortId id={node.node_id} />
                        {node.warnings.length > 0 ? (
                          <div className="warning small" title={node.warnings.join("\n")}>
                            ⚠ {node.warnings.length} {t("nodes.warnings")}
                          </div>
                        ) : null}
                      </td>
                      <td>
                        <StatusBadge status={node.online ? "online" : "offline"} />
                      </td>
                      <td>
                        {node.operating_system} {node.architecture}
                        <div className="muted small">
                          Python {node.python_version} · {node.roles.join(", ")}
                        </div>
                      </td>
                      <td>{node.logical_cpu_cores ?? "?"}</td>
                      <td className="nowrap">
                        {t("nodes.memoryValue", {
                          available: formatBytes(node.available_memory_bytes),
                          total: formatBytes(node.total_memory_bytes),
                        })}
                      </td>
                      <td>
                        {node.gpus.length === 0 ? (
                          <span className="muted">{t("common.none")}</span>
                        ) : (
                          node.gpus.map((gpu) => (
                            <div key={gpu.device_id}>
                              {gpu.model}
                              <div className="muted small">
                                {formatBytes(gpu.total_memory_bytes)}
                                {gpu.compute_capability ? ` · sm ${gpu.compute_capability}` : ""}
                                {gpu.driver_version ? ` · ${gpu.driver_version}` : ""}
                              </div>
                            </div>
                          ))
                        )}
                      </td>
                      <td>
                        {node.workers.length === 0 ? (
                          <span className="muted">{t("common.none")}</span>
                        ) : (
                          node.workers.slice(0, 4).map((worker) => (
                            <div key={worker.worker_id} className="small">
                              {worker.label || <ShortId id={worker.worker_id} />}
                            </div>
                          ))
                        )}
                      </td>
                      <td className="nowrap">{formatDate(node.last_heartbeat_at)}</td>
                      {can(session, "admin:manage") ? (
                        <td>
                          <button type="button" className="danger" onClick={() => void revoke(node.node_id)}>
                            {t("nodes.revoke")}
                          </button>
                        </td>
                      ) : null}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </LoadState>
    </Page>
  );
}
