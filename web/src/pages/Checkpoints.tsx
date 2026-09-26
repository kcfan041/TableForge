import { Link } from "react-router-dom";

import { api } from "../api";
import { LoadState, Page, ShortId, StatusBadge } from "../components";
import { useI18n } from "../i18n";
import { useLiveData } from "../live";

export function Checkpoints() {
  const { t, formatDate } = useI18n();
  const checkpoints = useLiveData(api.checkpoints);

  return (
    <Page title={t("checkpoints.title")}>
      <LoadState state={checkpoints}>
        {(data) =>
          data.length === 0 ? (
            <p className="muted">{t("checkpoints.empty")}</p>
          ) : (
            <div className="card table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("checkpoints.id")}</th>
                    <th>{t("checkpoints.agent")}</th>
                    <th>{t("experiments.id")}</th>
                    <th>{t("checkpoints.round")}</th>
                    <th>{t("checkpoints.status")}</th>
                    <th>{t("checkpoints.reason")}</th>
                    <th>{t("checkpoints.created")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.map((item) => (
                    <tr key={item.checkpoint_id}>
                      <td>
                        <ShortId id={item.checkpoint_id} />
                      </td>
                      <td>
                        <ShortId id={item.agent_id} />
                      </td>
                      <td>
                        <Link to={`/experiments/${item.experiment_id}`}>
                          <ShortId id={item.experiment_id} />
                        </Link>
                      </td>
                      <td>{item.round_index}</td>
                      <td>
                        <StatusBadge status={item.status} />
                      </td>
                      <td className="small">{item.decision_reason}</td>
                      <td className="nowrap">{formatDate(item.created_at)}</td>
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
