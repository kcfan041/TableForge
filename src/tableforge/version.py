from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

# Wire protocol between nodes/operators and the coordinator. Bump PROTOCOL_VERSION only for
# changes an older peer cannot handle, and raise MIN_PROTOCOL_VERSION when the coordinator
# stops accepting older clients. Additive protobuf fields do not require a bump.
PROTOCOL_VERSION = 1
MIN_PROTOCOL_VERSION = 1


def package_version() -> str:
    try:
        return version("tableforge")
    except PackageNotFoundError:
        return "unknown"


class IncompatibleVersionError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class VersionCheck:
    compatible: bool
    warning: str
    error: str


def check_compatibility(
    server_package: str,
    server_protocol: int,
    server_min_protocol: int,
    client_protocol: int = PROTOCOL_VERSION,
    client_min_protocol: int = MIN_PROTOCOL_VERSION,
    client_package: str | None = None,
) -> VersionCheck:
    """Compare this client with a coordinator.

    A coordinator that predates version reporting sends protocol 0; it is treated as
    protocol 1 since that is the protocol it spoke.
    """
    client_package = client_package or package_version()
    server_protocol = server_protocol or 1
    server_min_protocol = server_min_protocol or 1
    upgrade = "run `git pull` and then `uv sync --extra learning --extra gpu --extra s3`"
    if client_protocol < server_min_protocol:
        return VersionCheck(
            False,
            "",
            f"this TableForge ({client_package}, protocol {client_protocol}) is too old for the "
            f"coordinator ({server_package}, needs protocol >= {server_min_protocol}); {upgrade}",
        )
    if server_protocol < client_min_protocol:
        return VersionCheck(
            False,
            "",
            f"the coordinator ({server_package}, protocol {server_protocol}) is older than this "
            f"TableForge supports (protocol >= {client_min_protocol}); ask the admin to upgrade "
            "the coordinator",
        )
    warning = ""
    if server_package != client_package:
        warning = (
            f"coordinator runs {server_package}, this machine runs {client_package}; "
            "they are compatible, but updating both is recommended"
        )
    return VersionCheck(True, warning, "")
