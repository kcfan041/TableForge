import { Link } from "react-router-dom";

import { api } from "../api";
import { Card, Counts, LoadState, Page } from "../components";
import { useI18n } from "../i18n";
import { useLive, useLiveData } from "../live";
import { EventRow } from "./Events";

export function Overview() {
  const { t } = useI18n();
  const overview = useLiveData(api.overview);
  const { latest } = useLive();
  const recent = useLiveData(() => api.events(10));
  const events = latest.length > 0 ? latest.slice(0, 10) : (recent.data ?? []);

  return (
    <Page title={t("overview.title")}>
      <LoadState state={overview}>
        {(data) => (
          <div className="grid">
            <Card title={t("overview.nodes")}>
              <p className="big">
                <Link to="/nodes">
                  {t("overview.nodesValue", { online: data.nodes.online, total: data.nodes.total })}
                </Link>
              </p>
            </Card>
            <Card title={t("overview.gpus")}>
              <p className="big">{data.gpus}</p>
            </Card>
            <Card title={t("overview.experiments")}>
              {Object.keys(data.experiments).length === 0 ? (
                <p className="muted">{t("overview.noExperiments")}</p>
              ) : (
                <Link to="/experiments">
                  <Counts counts={data.experiments} />
                </Link>
              )}
            </Card>
            <Card title={t("overview.jobs")}>
              <Counts counts={data.jobs} />
            </Card>
            <Card title={t("overview.cluster")}>
              <code className="wrap">{data.cluster_id}</code>
              <p className="muted small">
                {t("overview.version")}: {data.version}
              </p>
            </Card>
          </div>
        )}
      </LoadState>
      <Card title={t("overview.recent")}>
        {events.length === 0 ? (
          <p className="muted">{t("events.empty")}</p>
        ) : (
          <table>
            <tbody>
              {events.map((event) => (
                <EventRow key={event.sequence} event={event} />
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </Page>
  );
}
