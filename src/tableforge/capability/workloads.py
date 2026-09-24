from __future__ import annotations

from tableforge.domain.capabilities import WorkloadProfile
from tableforge.domain.jobs import ResourceRequest

BUILTIN_WORKLOADS = (
    WorkloadProfile(
        schema_version="1.0",
        key="hu-rule-simulation",
        version="1",
        payload_type="tableforge.v1.SimulationJobPayload",
        minimum=ResourceRequest(
            cpu_cores=1,
            memory_bytes=512 * 1024**2,
            storage_bytes=1024**3,
            required_role="simulation",
        ),
        recommended=ResourceRequest(
            cpu_cores=4,
            memory_bytes=4 * 1024**3,
            storage_bytes=10 * 1024**3,
            required_role="simulation",
        ),
        supported_runtimes=("cpu",),
    ),
    WorkloadProfile(
        schema_version="1.0",
        key="hu-nfsp-cpu-smoke",
        version="1",
        payload_type="tableforge.v1.NfspTrainingJobPayload",
        minimum=ResourceRequest(
            cpu_cores=2,
            memory_bytes=4 * 1024**3,
            storage_bytes=2 * 1024**3,
            required_role="nfsp_learner",
        ),
        recommended=ResourceRequest(
            cpu_cores=4,
            memory_bytes=8 * 1024**3,
            storage_bytes=10 * 1024**3,
            required_role="nfsp_learner",
        ),
        supported_runtimes=("cpu",),
    ),
    WorkloadProfile(
        schema_version="1.0",
        key="hu-nfsp-small-gpu",
        version="1",
        payload_type="tableforge.v1.NfspTrainingJobPayload",
        minimum=ResourceRequest(
            cpu_cores=4,
            memory_bytes=16 * 1024**3,
            gpu_count=1,
            gpu_memory_bytes=8 * 1024**3,
            storage_bytes=10 * 1024**3,
            required_role="nfsp_learner",
            required_runtime="cuda",
            exclusive_gpu=True,
        ),
        recommended=ResourceRequest(
            cpu_cores=8,
            memory_bytes=32 * 1024**3,
            gpu_count=1,
            gpu_memory_bytes=12 * 1024**3,
            storage_bytes=50 * 1024**3,
            required_role="nfsp_learner",
            required_runtime="cuda",
            exclusive_gpu=True,
        ),
        supported_runtimes=("cuda",),
        fallback_batch_sizes=(1024, 512, 256),
    ),
)
