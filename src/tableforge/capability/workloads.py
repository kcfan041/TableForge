from __future__ import annotations

from tableforge.domain.capabilities import WorkloadProfile
from tableforge.domain.jobs import ResourceRequest

KIB = 1024
MIB = 1024**2
GIB = 1024**3


def _cpu_profile(
    key: str,
    payload: str,
    role: str,
    minimum: tuple[int, int, int],
    recommended: tuple[int, int, int],
) -> WorkloadProfile:
    def request(values: tuple[int, int, int]) -> ResourceRequest:
        cores, memory_gib, storage_gib = values
        return ResourceRequest(
            cpu_cores=cores,
            memory_bytes=memory_gib * GIB,
            storage_bytes=storage_gib * GIB,
            required_role=role,
        )

    return WorkloadProfile(
        schema_version="1.0",
        key=key,
        version="1",
        payload_type=f"tableforge.v1.{payload}",
        minimum=request(minimum),
        recommended=request(recommended),
        supported_runtimes=("cpu",),
    )

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
        key="hu-nfsp-evaluation",
        version="1",
        payload_type="tableforge.v1.NfspEvaluationJobPayload",
        minimum=ResourceRequest(
            cpu_cores=1,
            memory_bytes=2 * 1024**3,
            storage_bytes=1024**3,
            required_role="evaluation",
        ),
        recommended=ResourceRequest(
            cpu_cores=2,
            memory_bytes=4 * 1024**3,
            storage_bytes=2 * 1024**3,
            required_role="evaluation",
        ),
        supported_runtimes=("cpu",),
    ),
    _cpu_profile("hu-nfsp-init", "NfspInitJobPayload", "nfsp_learner", (1, 1, 1), (1, 2, 1)),
    _cpu_profile("hu-nfsp-actor", "NfspActorJobPayload", "nfsp_actor", (1, 2, 1), (2, 4, 2)),
    _cpu_profile(
        "hu-nfsp-learner", "NfspLearnerJobPayload", "nfsp_learner", (2, 4, 2), (4, 8, 10)
    ),
    _cpu_profile("hu-nfsp-match", "NfspMatchJobPayload", "evaluation", (1, 2, 1), (2, 4, 2)),
    _cpu_profile(
        "hu-league-matchup", "LeagueMatchupJobPayload", "evaluation", (1, 2, 1), (2, 4, 2)
    ),
    _cpu_profile(
        "tournament-replay", "TournamentReplayJobPayload", "evaluation", (1, 1, 1), (1, 2, 1)
    ),
    _cpu_profile(
        "strategy-table", "StrategyTableJobPayload", "evaluation", (1, 1, 1), (1, 2, 1)
    ),
    WorkloadProfile(
        schema_version="1.0",
        key="tournament-rule-benchmark",
        version="1",
        payload_type="tableforge.v1.TournamentJobPayload",
        minimum=ResourceRequest(
            cpu_cores=1,
            memory_bytes=512 * MIB,
            storage_bytes=GIB,
            required_role="simulation",
        ),
        recommended=ResourceRequest(
            cpu_cores=2,
            memory_bytes=2 * GIB,
            storage_bytes=2 * GIB,
            required_role="simulation",
        ),
        supported_runtimes=("cpu",),
    ),
    WorkloadProfile(
        schema_version="1.0",
        key="hu-nfsp-learner-gpu",
        version="1",
        payload_type="tableforge.v1.NfspLearnerJobPayload",
        minimum=ResourceRequest(
            cpu_cores=2,
            memory_bytes=4 * GIB,
            gpu_count=1,
            storage_bytes=2 * GIB,
            required_role="nfsp_learner",
            required_runtime="cuda",
            exclusive_gpu=True,
        ),
        recommended=ResourceRequest(
            cpu_cores=4,
            memory_bytes=8 * GIB,
            gpu_count=1,
            gpu_memory_bytes=2 * GIB,
            storage_bytes=10 * GIB,
            required_role="nfsp_learner",
            required_runtime="cuda",
            exclusive_gpu=True,
        ),
        supported_runtimes=("cuda",),
        fallback_batch_sizes=(8_192, 4_096, 2_048, 1_024),
        base_gpu_memory_bytes=512 * MIB,
        gpu_memory_per_batch_item=24 * KIB,
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
        fallback_batch_sizes=(262_144, 131_072, 65_536, 16_384, 4_096),
        base_gpu_memory_bytes=512 * MIB,
        gpu_memory_per_batch_item=24 * KIB,
    ),
)
