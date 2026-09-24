from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

_PROBE = (
    "import json, torch\n"
    "ok = torch.cuda.is_available()\n"
    "print(json.dumps({'cuda': ok, 'vram': torch.cuda.get_device_properties(0).total_memory "
    "if ok else 0}))\n"
)


def _gpu_state() -> tuple[bool, int]:
    if os.environ.get("TABLEFORGE_DISABLE_GPU_TESTS"):
        return False, 0
    try:
        result = subprocess.run(
            [sys.executable, "-c", _PROBE], capture_output=True, text=True, timeout=120
        )
        state = json.loads(result.stdout.strip().splitlines()[-1])
        return bool(state["cuda"]), int(state["vram"])
    except Exception:
        return False, 0


_CUDA, _VRAM = _gpu_state()
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
_TIERS = {"vram_8gb": 7 * 1024**3, "vram_16gb": 15 * 1024**3, "vram_24gb": 23 * 1024**3}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "requires_cuda: needs a CUDA-capable GPU (runs in a subprocess)"
    )
    for name in _TIERS:
        config.addinivalue_line("markers", f"{name}: needs at least that much GPU memory")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.get_closest_marker("requires_cuda") and not _CUDA:
            item.add_marker(pytest.mark.skip(reason="CUDA GPU not available"))
            continue
        for name, minimum in _TIERS.items():
            if item.get_closest_marker(name) and minimum > _VRAM:
                item.add_marker(
                    pytest.mark.skip(
                        reason=f"requires {name}; detected {_VRAM / 1024**3:.1f} GiB"
                    )
                )


def gpu_subprocess_env() -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("CUDA_VISIBLE_DEVICES", None)
    return environment
