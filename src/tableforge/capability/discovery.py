from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from tableforge.capability.gpu import discover_gpus
from tableforge.domain.capabilities import (
    GpuCapability,
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
    detect_gpus: bool = True
    probe_cuda_runtime: bool = True

    def discover(self) -> NodeCapability:
        warnings: list[str] = []
        total_memory, available_memory = _memory_bytes(warnings)
        gpus = (
            discover_gpus(warnings, cuda_runtime=None if self.probe_cuda_runtime else False)
            if self.detect_gpus
            else ()
        )
        commit_free = windows_commit_available() if sys.platform == "win32" else None
        if gpus and commit_free is not None:
            gpus = cap_gpu_memory_by_commit(gpus, commit_free, warnings)
        runtimes = ("cpu", "cuda") if any("cuda" in gpu.runtimes for gpu in gpus) else ("cpu",)
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
            gpus=gpus,
            storage=(
                StorageCapability(
                    volume_id="local-data",
                    available_bytes=disk.free,
                    storage_class="unknown",
                ),
            ),
            runtime=RuntimeCapability(
                python_version=platform.python_version(),
                runtimes=runtimes,
            ),
            enabled_roles=self.roles,
            captured_at=datetime.now(UTC),
            warnings=tuple(warnings),
        )


COMMIT_RESERVE_BYTES = 1024**3


def cap_gpu_memory_by_commit(
    gpus: tuple[GpuCapability, ...], commit_available: int, warnings: list[str]
) -> tuple[GpuCapability, ...]:
    """On Windows (WDDM), device allocations are also charged to system commit.

    A GPU can therefore report free VRAM that the process cannot actually allocate
    when the page file is small. Usable GPU memory is capped at the commit headroom,
    keeping a reserve for the process itself.
    """
    headroom = max(0, commit_available - COMMIT_RESERVE_BYTES)
    capped = []
    for gpu in gpus:
        available = gpu.available_memory_bytes
        if available is not None and headroom < available:
            warnings.append("GPU_MEMORY_LIMITED_BY_SYSTEM_COMMIT")
            available = headroom
        capped.append(replace(gpu, available_memory_bytes=available))
    return tuple(capped)


def windows_commit_available() -> int | None:
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
            return None
        return int(status.available_page_file)
    except (AttributeError, OSError):
        return None


def _memory_bytes(warnings: list[str]) -> tuple[int | None, int | None]:
    if sys.platform == "win32":
        return _windows_memory_bytes(warnings)
    if sys.platform.startswith("linux"):
        return _linux_memory_bytes(warnings)
    if sys.platform == "darwin":
        return _macos_memory_bytes(warnings)
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


def _macos_memory_bytes(warnings: list[str]) -> tuple[int | None, int | None]:
    try:
        result = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        total = int(result.stdout.strip())
        if total <= 0:
            raise ValueError("hw.memsize must be positive")
        warnings.append("AVAILABLE_MEMORY_UNKNOWN")
        return total, None
    except (OSError, subprocess.SubprocessError, ValueError):
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
