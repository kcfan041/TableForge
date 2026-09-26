from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass

import uvicorn

from tableforge.web.app import WebContext, create_app


@dataclass
class RunningWebServer:
    server: uvicorn.Server
    thread: threading.Thread
    port: int

    def stop(self, timeout: float = 5.0) -> None:
        self.server.should_exit = True
        self.thread.join(timeout)


def parse_listen(listen: str) -> tuple[str, int]:
    host, separator, port = listen.rpartition(":")
    if not separator or not port.isdigit():
        raise ValueError(f"invalid listen address {listen!r}; expected host:port")
    return host.strip("[]") or "127.0.0.1", int(port)


def start_web_server(
    context: WebContext,
    listen: str,
    certificate: str | None = None,
    private_key: str | None = None,
) -> RunningWebServer:
    """Serve the web UI on a background thread and wait until it accepts connections."""
    host, port = parse_listen(listen)
    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    bound_port = int(sock.getsockname()[1])
    config = uvicorn.Config(
        create_app(context),
        log_level="warning",
        access_log=False,
        ssl_certfile=certificate,
        ssl_keyfile=private_key,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        timeout_graceful_shutdown=2,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, name="tableforge-web", daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError(f"web server failed to start on {listen}")
        time.sleep(0.02)
    return RunningWebServer(server, thread, bound_port)
