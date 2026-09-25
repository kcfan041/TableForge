from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tableforge.infrastructure.sql.database import Connection, Database


@dataclass
class Counters:
    _values: dict[tuple[str, tuple[tuple[str, str], ...]], float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def increment(self, name: str, amount: float = 1.0, **labels: str) -> None:
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def snapshot(self) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
        with self._lock:
            return dict(self._values)


COUNTERS = Counters()

_HELP = {
    "tableforge_jobs": "Jobs by status.",
    "tableforge_active_leases": "Active job leases.",
    "tableforge_expired_active_leases": "Active leases past expiry awaiting requeue.",
    "tableforge_nodes": "Registered nodes.",
    "tableforge_experiments": "Experiments by status.",
    "tableforge_checkpoints": "Checkpoints by status.",
    "tableforge_unpublished_events": "Outbox events not yet published.",
    "tableforge_job_failures": "Failed job attempts by error code.",
    "tableforge_rpc_requests_total": "Coordinator RPC calls by method and outcome.",
    "tableforge_rpc_seconds_total": "Cumulative coordinator RPC latency.",
    "tableforge_artifact_bytes_total": "Artifact bytes transferred by direction.",
    "tableforge_reconcile_total": "Reconciler iterations by outcome.",
}


def _query(
    connection: Connection, sql: str, parameters: tuple[object, ...] = ()
) -> list[tuple[object, ...]]:
    rows = connection.execute(sql, parameters).fetchall()
    return [tuple(row.values()) if isinstance(row, dict) else tuple(row) for row in rows]

def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _line(name: str, value: float, labels: tuple[tuple[str, str], ...] = ()) -> str:
    rendered = ",".join(f'{key}="{_escape(item)}"' for key, item in labels)
    suffix = f"{{{rendered}}}" if rendered else ""
    return f"{name}{suffix} {value:g}"


def render_metrics(database: Database, now: datetime, counters: Counters = COUNTERS) -> str:
    samples: dict[str, list[str]] = {}

    def add(name: str, value: float, **labels: str) -> None:
        samples.setdefault(name, []).append(_line(name, value, tuple(sorted(labels.items()))))

    with database.read() as connection:
        for status, count in _query(connection, "SELECT status, COUNT(*) FROM jobs GROUP BY 1"):
            add("tableforge_jobs", float(count), status=str(status))  # type: ignore[arg-type]
        for (count,) in _query(
            connection, "SELECT COUNT(*) FROM job_attempts WHERE active = 1"
        ):
            add("tableforge_active_leases", float(count))  # type: ignore[arg-type]
        for (count,) in _query(
            connection,
            "SELECT COUNT(*) FROM job_attempts WHERE active = 1 AND expires_at <= ?",
            (database.timestamp(now),),
        ):
            add("tableforge_expired_active_leases", float(count))  # type: ignore[arg-type]
        for (count,) in _query(connection, "SELECT COUNT(*) FROM nodes"):
            add("tableforge_nodes", float(count))  # type: ignore[arg-type]
        for status, count in _query(
            connection,
            """
            SELECT r.status, COUNT(*) FROM experiments e
            JOIN experiment_revisions r
              ON r.experiment_id = e.experiment_id AND r.revision = e.current_revision
            GROUP BY 1
            """,
        ):
            add("tableforge_experiments", float(count), status=str(status))  # type: ignore[arg-type]
        for status, count in _query(
            connection, "SELECT status, COUNT(*) FROM checkpoints GROUP BY 1"
        ):
            add("tableforge_checkpoints", float(count), status=str(status))  # type: ignore[arg-type]
        for (count,) in _query(
            connection, "SELECT COUNT(*) FROM events WHERE published_at IS NULL"
        ):
            add("tableforge_unpublished_events", float(count))  # type: ignore[arg-type]
        for code, count in _query(
            connection,
            "SELECT error_code, COUNT(*) FROM job_attempts WHERE error_code IS NOT NULL GROUP BY 1",
        ):
            add("tableforge_job_failures", float(count), code=str(code))  # type: ignore[arg-type]
    for (name, labels), value in sorted(counters.snapshot().items()):
        samples.setdefault(name, []).append(_line(name, value, labels))
    lines: list[str] = []
    for name in sorted(samples):
        kind = "counter" if name.endswith("_total") else "gauge"
        lines.append(f"# HELP {name} {_HELP.get(name, name)}")
        lines.append(f"# TYPE {name} {kind}")
        lines.extend(samples[name])
    return "\n".join(lines) + "\n"


@dataclass
class MetricsServer:
    server: ThreadingHTTPServer
    thread: threading.Thread

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def start_metrics_server(address: str, render: Callable[[], str]) -> MetricsServer:
    host, _, port = address.rpartition(":")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0] != "/metrics":
                self.send_error(404)
                return
            body = render().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer((host or "127.0.0.1", int(port)), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return MetricsServer(server, thread)
