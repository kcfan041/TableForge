from __future__ import annotations

import importlib.util
import shutil
import subprocess
from collections.abc import Callable
from typing import Any

from tableforge.domain.capabilities import GpuCapability

CommandRunner = Callable[[list[str]], str]

_SMI_FIELDS = "index,name,memory.total,memory.free,driver_version,compute_cap"
_AUTO: Any = object()


def discover_gpus(
    warnings: list[str],
    nvml: Any = _AUTO,
    run_command: CommandRunner | None = _AUTO,
    cuda_runtime: bool | None = None,
) -> tuple[GpuCapability, ...]:
    has_cuda = _torch_cuda_available() if cuda_runtime is None else cuda_runtime
    runtimes = ("cuda",) if has_cuda else ()
    module = _import_nvml() if nvml is _AUTO else nvml
    if module is not None:
        try:
            return _from_nvml(module, runtimes)
        except Exception:
            warnings.append("GPU_NVML_FAILED")
    runner = _default_runner() if run_command is _AUTO else run_command
    if runner is not None:
        try:
            return _from_smi(runner, runtimes)
        except Exception:
            warnings.append("GPU_SMI_FAILED")
    if has_cuda:
        warnings.append("GPU_PRESENT_BUT_UNDESCRIBED")
    return ()


def _from_nvml(module: Any, runtimes: tuple[str, ...]) -> tuple[GpuCapability, ...]:
    module.nvmlInit()
    try:
        driver = _text(module.nvmlSystemGetDriverVersion())
        gpus = []
        for index in range(module.nvmlDeviceGetCount()):
            handle = module.nvmlDeviceGetHandleByIndex(index)
            memory = module.nvmlDeviceGetMemoryInfo(handle)
            major, minor = module.nvmlDeviceGetCudaComputeCapability(handle)
            gpus.append(
                GpuCapability(
                    device_id=f"cuda:{index}",
                    vendor="nvidia",
                    model=_text(module.nvmlDeviceGetName(handle)),
                    total_memory_bytes=int(memory.total),
                    available_memory_bytes=int(memory.free),
                    driver_version=driver,
                    compute_capability=f"{major}.{minor}",
                    runtimes=runtimes,
                )
            )
        return tuple(gpus)
    finally:
        module.nvmlShutdown()


def _from_smi(runner: CommandRunner, runtimes: tuple[str, ...]) -> tuple[GpuCapability, ...]:
    output = runner(
        ["nvidia-smi", f"--query-gpu={_SMI_FIELDS}", "--format=csv,noheader,nounits"]
    )
    gpus = []
    for line in output.strip().splitlines():
        index, name, total, free, driver, compute = (part.strip() for part in line.split(","))
        gpus.append(
            GpuCapability(
                device_id=f"cuda:{int(index)}",
                vendor="nvidia",
                model=name,
                total_memory_bytes=int(float(total)) * 1024 * 1024,
                available_memory_bytes=int(float(free)) * 1024 * 1024,
                driver_version=driver,
                compute_capability=compute,
                runtimes=runtimes,
            )
        )
    return tuple(gpus)


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _import_nvml() -> Any | None:
    if importlib.util.find_spec("pynvml") is None:
        return None
    return importlib.import_module("pynvml")


def _default_runner() -> CommandRunner | None:
    if shutil.which("nvidia-smi") is None:
        return None

    def run(arguments: list[str]) -> str:
        return subprocess.run(
            arguments, check=True, capture_output=True, text=True, timeout=15
        ).stdout

    return run


def _torch_cuda_available() -> bool:
    if importlib.util.find_spec("torch") is None:
        return False
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False
