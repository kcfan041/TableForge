from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tableforge.infrastructure.node_identity import NodeIdentityStore


def test_node_identity_is_persisted(tmp_path: Path) -> None:
    store = NodeIdentityStore(tmp_path / "node-id")

    first = store.load_or_create()
    second = NodeIdentityStore(tmp_path / "node-id").load_or_create()

    assert second == first
    assert (tmp_path / "node-id").read_text(encoding="ascii").strip() == first


def test_concurrent_creation_returns_one_identity(tmp_path: Path) -> None:
    path = tmp_path / "node-id"

    with ThreadPoolExecutor(max_workers=8) as executor:
        identities = list(
            executor.map(lambda _: NodeIdentityStore(path).load_or_create(), range(32))
        )

    assert len(set(identities)) == 1


def test_corrupt_identity_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "node-id"
    path.write_text("not-a-node-id\n", encoding="ascii")

    with pytest.raises(ValueError, match="invalid node identity"):
        NodeIdentityStore(path).load_or_create()


def test_concurrent_creation_across_processes(tmp_path: Path) -> None:
    import subprocess
    import sys

    path = tmp_path / "node-id"
    code = (
        "import sys; from pathlib import Path; "
        "from tableforge.infrastructure.node_identity import NodeIdentityStore; "
        "print(NodeIdentityStore(Path(sys.argv[1])).load_or_create())"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(path)], stdout=subprocess.PIPE, text=True
        )
        for _ in range(8)
    ]
    identities = {process.communicate(timeout=60)[0].strip() for process in processes}

    assert all(process.returncode == 0 for process in processes)
    assert len(identities) == 1
    assert not list(tmp_path.glob(".node-id.*.tmp"))
