import { useState } from "react";

import { api, type DomainEvent } from "../api";
import { LoadState, Page, ShortId } from "../components";
import { useI18n } from "../i18n";
import { useLiveData } from "../live";

export function EventRow({ event }: { event: DomainEvent }) {
  const { formatDate } = useI18n();
  const payload = Object.entries(event.payload)
    .map(([key, value]) => `${key}=${typeof value === "object" ? JSON.stringify(value) : String(value)}`)
    .join(" ");
  return (
    <tr>
      <td className="muted">{event.sequence}</td>
      <td className="nowrap">{formatDate(event.occurred_at)}</td>
      <td>
        <code>{event.type}</code>
      </td>
      <td className="nowrap">
        {event.aggregate_type} <ShortId id={event.aggregate_id} />
      </td>
      <td className="muted small">{payload}</td>
    </tr>
  );
}

export function Events() {
  const { t } = useI18n();
  const events = useLiveData(() => api.events(300));
  const [filter, setFilter] = useState("");

  return (
    <Page
      title={t("events.title")}
      actions={
        <input
          type="search"
          value={filter}
          placeholder={t("events.filter")}
          onChange={(event) => setFilter(event.target.value)}
        />
      }
    >
      <LoadState state={events}>
        {(data) => {
          const needle = filter.trim().toLowerCase();
          const shown = needle
            ? data.filter((event) =>
                `${event.type} ${event.aggregate_type} ${event.aggregate_id}`
                  .toLowerCase()
                  .includes(needle),
              )
            : data;
          if (shown.length === 0) return <p className="muted">{t("events.empty")}</p>;
          return (
            <div className="card table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("events.sequence")}</th>
                    <th>{t("events.time")}</th>
                    <th>{t("events.type")}</th>
                    <th>{t("events.target")}</th>
                    <th>{t("events.details")}</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((event) => (
                    <EventRow key={event.sequence} event={event} />
                  ))}
                </tbody>
              </table>
            </div>
          );
        }}
      </LoadState>
    </Page>
  );
}
