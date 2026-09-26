import { api } from "../api";
import { Card, LoadState, Page } from "../components";
import { useI18n } from "../i18n";
import { useLiveData } from "../live";

export function Diagnostics() {
  const { t } = useI18n();
  const diagnostics = useLiveData(api.diagnostics);

  return (
    <Page title={t("diagnostics.title")}>
      <LoadState state={diagnostics}>
        {(data) => (
          <div className="grid">
            <Card>
              <dl className="facts">
                <dt>{t("diagnostics.version")}</dt>
                <dd>{data.version}</dd>
                <dt>{t("diagnostics.protocol")}</dt>
                <dd>{t("diagnostics.protocolValue", { protocol: data.protocol, min: data.min_protocol })}</dd>
                <dt>{t("diagnostics.schema")}</dt>
                <dd>{data.schema_version}</dd>
                <dt>{t("diagnostics.metadata")}</dt>
                <dd>{data.metadata_backend}</dd>
                <dt>{t("diagnostics.artifacts")}</dt>
                <dd>{data.artifact_backend}</dd>
                <dt>{t("diagnostics.directTransfer")}</dt>
                <dd>{data.direct_transfer ? t("common.yes") : t("common.no")}</dd>
                <dt>{t("diagnostics.grpc")}</dt>
                <dd>
                  <code>{data.grpc_listen}</code>
                </dd>
                <dt>{t("diagnostics.http")}</dt>
                <dd>
                  <code>{data.http_listen}</code>
                </dd>
                <dt>{t("diagnostics.nodeTtl")}</dt>
                <dd>{t("diagnostics.nodeTtlValue", { seconds: data.node_ttl_seconds })}</dd>
                <dt>{t("diagnostics.unpublished")}</dt>
                <dd>{data.unpublished_events}</dd>
              </dl>
            </Card>
            <Card title={t("diagnostics.certificate")}>
              {data.certificate.present ? (
                <dl className="facts">
                  <dt>{t("diagnostics.hosts")}</dt>
                  <dd>
                    {(data.certificate.hosts ?? []).map((host) => (
                      <code key={host} className="tag">
                        {host}
                      </code>
                    ))}
                  </dd>
                  <dt>{t("diagnostics.fingerprint")}</dt>
                  <dd>
                    <code className="wrap small">{data.certificate.fingerprint}</code>
                  </dd>
                </dl>
              ) : (
                <p className="warning">{t("diagnostics.certificateMissing")}</p>
              )}
            </Card>
          </div>
        )}
      </LoadState>
    </Page>
  );
}
