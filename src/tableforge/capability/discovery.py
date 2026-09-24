from __future__ import annotations

import os
import platform
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tableforge.domain.capabilities import (
    NodeCapability,
    RuntimeCapability,
    StorageCapability,
)
from tableforge.domain.ids import NodeId


@dataclass(frozen=True, slots=True)
class LocalCapabilityDiscovery:
    node_id: NodeId
    roles: tuple[str, ...]
    storage_path: Path

    def discover(self) -> NodeCapability:
        warnings: list[str] = []
        total_memory, available_memory = _memory_bytes(warnings)
        disk = shutil.disk_usage(self.storage_path)
        logical_cores = os.cpu_count()
        if logical_cores is None:
            warnings.append("CPU_LOGICAL_CORES_UNKNOWN")
        return NodeCapability(
            schema_version="1.0",
            node_id=self.node_id,
            revision=1,
            operating_system=platform.system().lower(),
            architecture=platform.machine().lower(),
            logical_cpu_cores=logical_cores,
            physical_cpu_cores=None,
            total_memory_bytes=total_memory,
            available_memory_bytes=available_memory,
            gpus=(),
            storage=(
                StorageCapability(
                    volume_id="local-data",
                    available_bytes=disk.free,
                    storage_class="unknown",
                ),
            ),
            runtime=RuntimeCapability(
                python_version=platform.python_version(),
                runtimes=("cpu",),
            ),
            enabled_roles=self.roles,
            captured_at=datetime.now(UTC),
            warnings=tuple(warnings),
        )


def _memory_bytes(warnings: list[str]) -> tuple[int | None, int | None]:
    if sys.platform == "win32":
        return _windows_memory_bytes(warnings)
    if sys.platform.startswith("linux"):
        return _linux_memory_bytes(warnings)
    warnings.append("MEMORY_DISCOVERY_UNSUPPORTED")
    return None, None


def _windows_memory_bytes(warnings: list[str]) -> tuple[int | None, int | None]:
    try:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("GlobalMemoryStatusEx failed")
        return int(status.total_physical), int(status.available_physical)
    except (AttributeError, OSError):
        warnings.append("MEMORY_DISCOVERY_FAILED")
        return None, None


def _linux_memory_bytes(warnings: list[str]) -> tuple[int | None, int | None]:
    try:
        values: dict[str, int] = {}
        with Path("/proc/meminfo").open(encoding="utf-8") as source:
            for line in source:
                key, raw = line.split(":", maxsplit=1)
                values[key] = int(raw.strip().split()[0]) * 1024
        return values["MemTotal"], values["MemAvailable"]
    except (OSError, KeyError, ValueError):
        warnings.append("MEMORY_DISCOVERY_FAILED")
        return None, None
