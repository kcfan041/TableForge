from __future__ import annotations

import hashlib
import sys

import torch

from tableforge.capability.discovery import LocalCapabilityDiscovery
from tableforge.capability.evaluator import assess_compatibility
from tableforge.capability.workloads import BUILTIN_WORKLOADS
from tableforge.domain.capabilities import CompatibilityStatus
from tableforge.domain.ids import NodeId
from tableforge.domain.learning import NfspActorPayload, NfspInitPayload, NfspLearnerPayload
from tableforge.learning.encoding import FEATURE_SIZE
from tableforge.learning.experience import DatasetSnapshot, ShardReference
from tableforge.learning.iterative import NfspActorExecutor, NfspInitExecutor, NfspLearnerExecutor
from tableforge.learning.nfsp import NfspAgent, _network

SKIP = 3


def planned_batch() -> int:
    from pathlib import Path

    profile = next(w for w in BUILTIN_WORKLOADS if w.key == "hu-nfsp-small-gpu")
    capability = LocalCapabilityDiscovery(NodeId("probe"), ("nfsp_learner",), Path(".")).discover()
    assessment = assess_compatibility(capability, profile)
    if assessment.status not in (CompatibilityStatus.SUPPORTED, CompatibilityStatus.DEGRADED):
        print(f"skip: {assessment.status.value} {assessment.reason_codes}")
        return SKIP
    assert assessment.plan is not None and assessment.plan.batch_size is not None
    batch = assessment.plan.batch_size
    device = assessment.plan.device_id or "cuda:0"
    torch.cuda.reset_peak_memory_stats()
    network = _network(FEATURE_SIZE, 1024).to(device)
    optimizer = torch.optim.Adam(network.parameters())
    inputs = torch.randn(batch, FEATURE_SIZE, device=device)
    for _ in range(2):
        loss = network(inputs).sum()
        optimizer.zero_grad()
        loss.backward()  # type: ignore[no-untyped-call]
        optimizer.step()
    torch.cuda.synchronize()
    peak = torch.cuda.max_memory_allocated()
    budget = profile.gpu_memory_for(batch)
    print(f"status={assessment.status.value} batch={batch} peak={peak} budget={budget}")
    return 0 if peak <= budget else 1


def learner_parity() -> int:
    items: dict[str, bytes] = {}

    def put(content: bytes) -> tuple[str, str]:
        digest = hashlib.sha256(content).hexdigest()
        items[digest[:16]] = content
        return digest[:16], digest

    base = put(
        NfspInitExecutor().execute(
            NfspInitPayload("1.0", agent_seed=3, hidden_size=64, batch_size=64).to_bytes()
        )
    )
    shard = put(
        NfspActorExecutor(items.__getitem__).execute(
            NfspActorPayload("1.0", base[0], base[1], 150, 0, 1).to_bytes()
        )
    )
    snapshot = put(DatasetSnapshot(base[1], 1, (ShardReference(*shard),)).to_bytes())
    payload = NfspLearnerPayload(
        "1.0", base[0], base[1], snapshot[0], snapshot[1], 60, 1
    ).to_bytes()
    executor = NfspLearnerExecutor(items.__getitem__)
    torch.cuda.reset_peak_memory_stats()
    gpu_bundle = executor.execute_with_plan(
        payload, {"runtime": "cuda", "device_id": "cuda:0", "batch_size": 64}
    )
    peak = torch.cuda.max_memory_allocated()
    cpu_bundle = executor.execute_with_plan(payload, {"runtime": "cpu", "batch_size": 64})
    gpu_agent = NfspAgent.from_bundle(gpu_bundle, device="cpu")
    cpu_agent = NfspAgent.from_bundle(cpu_bundle, device="cpu")
    assert peak > 0
    assert gpu_agent.train_steps == cpu_agent.train_steps == 60
    for gpu_parameter, cpu_parameter in zip(
        gpu_agent.policy_network.parameters(), cpu_agent.policy_network.parameters(), strict=True
    ):
        assert gpu_parameter.device.type == "cpu"
        torch.testing.assert_close(gpu_parameter, cpu_parameter, rtol=1e-3, atol=1e-3)
    print(f"parity ok peak={peak}")
    return 0


if __name__ == "__main__":
    probes = {"planned-batch": planned_batch, "learner-parity": learner_parity}
    sys.exit(probes[sys.argv[1]]())
