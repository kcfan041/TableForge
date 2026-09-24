from __future__ import annotations

import os


def hide_accelerators() -> None:
    """Keep this process from creating a CUDA context.

    PyTorch's optimizers query the current accelerator on every step, which creates a
    CUDA context whenever a GPU is visible, even when all tensors live on the CPU. Each
    context costs hundreds of MB of committed memory, so CPU-only workers hide the GPU
    before torch initializes CUDA. An empty value is not reliable across shells, so
    ``-1`` is used. This must run before the first CUDA call in the process.
    """
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
