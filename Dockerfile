# syntax=docker/dockerfile:1.7
#
# Targets:
#   coordinator  - gRPC coordinator and web UI, PostgreSQL/S3 clients, no PyTorch (~small)
#   worker-cpu   - worker with CPU-only PyTorch
#   worker-cuda  - worker with CUDA 13 PyTorch; run with --gpus all
#
# Base images are pinned by digest so rebuilds are reproducible.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.8.5@sha256:9ac8566d708f42bae522b050004f75ebc7c344bc726d6d4e70f1d308b18c4471

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build-base
COPY --from=uv /uv /usr/local/bin/uv
# Many parallel multi-hundred-MB downloads overwhelm Docker Desktop's userspace network
# (observed: stalled builds and truncated wheels), so downloads are limited and retried.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_CONCURRENT_DOWNLOADS=2 \
    UV_HTTP_TIMEOUT=300 \
    UV_HTTP_RETRIES=5
WORKDIR /src
COPY pyproject.toml uv.lock README.md ./

FROM build-base AS build-coordinator
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --extra postgres --extra s3
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --extra postgres --extra s3

FROM build-base AS build-worker-cuda
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --extra learning --extra gpu
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --extra learning --extra gpu

FROM build-base AS build-worker-cpu
# The lock file pins the CUDA build of torch on Linux. CPU workers reuse every other
# locked version but swap torch for the matching CPU wheel and drop the CUDA libraries.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv export --frozen --no-dev --no-emit-project --no-hashes --extra learning \
        --output-file /tmp/locked.txt \
 && TORCH_VERSION="$(sed -n "s/^torch==\([0-9.]*\)+cu[0-9]* ; .*sys_platform == 'linux'.*/\1/p" /tmp/locked.txt)" \
 && test -n "${TORCH_VERSION}" \
 && grep -Ev '^(torch|triton|nvidia-)' /tmp/locked.txt > /tmp/cpu.txt \
 && uv venv /opt/venv \
 && uv pip install --python /opt/venv/bin/python --no-deps -r /tmp/cpu.txt \
 && uv pip install --python /opt/venv/bin/python --no-deps \
        --index-url https://download.pytorch.org/whl/cpu "torch==${TORCH_VERSION}+cpu"
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/venv/bin/python --no-deps . \
 && /opt/venv/bin/python -c "import torch, tableforge.learning.registry; assert not torch.cuda.is_available(); print('torch', torch.__version__)"

FROM ${PYTHON_IMAGE} AS runtime-base
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TABLEFORGE_DATA_DIR=/data
RUN groupadd --system --gid 10001 tableforge \
 && useradd --system --uid 10001 --gid tableforge --home-dir /data --shell /usr/sbin/nologin tableforge \
 && mkdir -p /data /shared \
 && chown tableforge:tableforge /data /shared
COPY docker/entrypoint.sh /usr/local/bin/tableforge-entrypoint
RUN chmod 0755 /usr/local/bin/tableforge-entrypoint
WORKDIR /data
VOLUME ["/data"]
USER tableforge
ENTRYPOINT ["tableforge-entrypoint"]

FROM runtime-base AS coordinator
COPY --from=build-coordinator /opt/venv /opt/venv
ENV TABLEFORGE_ROLE=coordinator
EXPOSE 7443 8080 9464
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
    CMD ["python", "-c", "import socket,sys; socket.create_connection(('127.0.0.1', 7443), 3); sys.exit(0)"]
CMD ["serve"]

FROM runtime-base AS worker-cpu
COPY --from=build-worker-cpu /opt/venv /opt/venv
ENV TABLEFORGE_ROLE=worker \
    TABLEFORGE_WORKER__USE_GPU=false
CMD ["work"]

FROM runtime-base AS worker-cuda
COPY --from=build-worker-cuda /opt/venv /opt/venv
ENV TABLEFORGE_ROLE=worker \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility
CMD ["work"]
