from __future__ import annotations

import hashlib
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tableforge.application.ports import (
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactStore,
    SignedTransfer,
    storage_key_for,
)
from tableforge.domain.artifacts import ArtifactDescriptor, ArtifactKind, ArtifactStatus
from tableforge.domain.ids import ArtifactId, new_artifact_id
from tableforge.infrastructure.artifact_gc import collect_garbage
from tableforge.infrastructure.local_artifacts import LocalArtifactStore
from tableforge.transport import direct

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def descriptor(artifact_id: str | None = None) -> ArtifactDescriptor:
    return ArtifactDescriptor(
        artifact_id=ArtifactId(artifact_id) if artifact_id else new_artifact_id(),
        kind=ArtifactKind.EXPERIENCE_SHARD,
        media_type="application/octet-stream",
        schema_version="1.0",
    )


@pytest.fixture(params=["local", pytest.param("s3", marks=pytest.mark.s3)])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> ArtifactStore:
    if request.param == "local":
        return LocalArtifactStore(tmp_path)
    result: ArtifactStore = request.getfixturevalue("s3_store")
    return result


def test_put_get_and_read(store: ArtifactStore) -> None:
    artifact = store.put(descriptor(), b"experience")

    assert artifact.status is ArtifactStatus.AVAILABLE
    assert artifact.storage_key == storage_key_for(artifact.sha256)
    assert store.exists(artifact.sha256)
    assert store.get(str(artifact.descriptor.artifact_id)).sha256 == artifact.sha256
    loaded, content = store.read(str(artifact.descriptor.artifact_id))
    assert content == b"experience"
    assert loaded.size_bytes == len(b"experience")


def test_empty_and_binary_content(store: ArtifactStore) -> None:
    for content in (b"", bytes(range(256)) * 4096):
        artifact = store.put(descriptor(), content)
        assert store.read(str(artifact.descriptor.artifact_id))[1] == content


def test_content_is_deduplicated_by_hash(store: ArtifactStore) -> None:
    first = store.put(descriptor(), b"same")
    second = store.put(descriptor(), b"same")

    assert first.storage_key == second.storage_key
    assert len([item for item in store.list_objects()]) == 1


def test_missing_artifacts_raise(store: ArtifactStore) -> None:
    with pytest.raises(ArtifactNotFoundError):
        store.get("missing")
    with pytest.raises(ArtifactNotFoundError):
        store.read("../escape")
    assert not store.exists("0" * 64)


def test_concurrent_puts_of_same_artifact_succeed(store: ArtifactStore) -> None:
    def put(_: int) -> str:
        return store.put(descriptor("shared"), b"same content").sha256

    with ThreadPoolExecutor(max_workers=8) as pool:
        digests = set(pool.map(put, range(24)))

    assert len(digests) == 1
    assert store.read("shared")[1] == b"same content"


def test_gc_uses_store_listing(store: ArtifactStore) -> None:
    kept = store.put(descriptor(), b"kept")
    orphan = store.put(descriptor(), b"orphan")
    later = datetime.now(UTC) + timedelta(days=2)

    dry = collect_garbage(store, {kept.sha256}, timedelta(days=1), dry_run=True, now=later)
    recent = collect_garbage(store, {kept.sha256}, timedelta(days=1), dry_run=False)
    real = collect_garbage(store, {kept.sha256}, timedelta(days=1), dry_run=False, now=later)

    assert (dry.candidates, dry.deleted_files) == (1, 1)
    assert (recent.kept_recent, recent.deleted_files) == (1, 0)
    assert real.deleted_files == 1
    assert store.exists(kept.sha256)
    assert not store.exists(orphan.sha256)
    with pytest.raises(ArtifactNotFoundError):
        store.read(str(orphan.descriptor.artifact_id))


def test_local_store_detects_corruption(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    artifact = store.put(descriptor(), b"original")
    store.path_for(artifact).write_bytes(b"tampered")

    with pytest.raises(ArtifactIntegrityError):
        store.read(str(artifact.descriptor.artifact_id))


def _http(transfer: SignedTransfer, body: bytes | None = None, **headers: str) -> int:
    request = urllib.request.Request(
        transfer.url,
        data=body,
        method=transfer.method,
        headers={**dict(transfer.headers), **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)


@pytest.mark.s3
def test_direct_upload_register_and_download(s3_store: Any) -> None:
    content = b"direct-bytes" * 2048
    sha256 = hashlib.sha256(content).hexdigest()

    upload = s3_store.sign_upload(sha256, len(content), timedelta(minutes=5))
    direct.put(upload, content)
    artifact = s3_store.register(descriptor(), sha256, len(content))
    download = s3_store.sign_download(artifact, timedelta(minutes=5))

    assert upload.expires_at > datetime.now(UTC)
    assert direct.get(download, sha256, len(content)) == content
    assert s3_store.read(str(artifact.descriptor.artifact_id))[1] == content


@pytest.mark.s3
def test_signed_upload_rejects_other_content(s3_store: Any) -> None:
    content = b"expected content"
    sha256 = hashlib.sha256(content).hexdigest()
    upload = s3_store.sign_upload(sha256, len(content), timedelta(minutes=5))

    assert _http(upload, b"X" + content[1:]) >= 400
    assert _http(replace(upload, headers=()), content) >= 400
    assert _http(upload, b"Y" * len(content)) >= 400
    assert not s3_store.exists(sha256)
    with pytest.raises(ArtifactNotFoundError):
        s3_store.register(descriptor(), sha256, len(content))

    _http(upload, content + b"extra bytes past the signed length")
    if s3_store.exists(sha256):
        artifact = s3_store.register(descriptor(), sha256, len(content))
        assert s3_store.read(str(artifact.descriptor.artifact_id))[1] == content


@pytest.mark.s3
def test_signed_urls_cannot_be_forged_or_reused_elsewhere(s3_store: Any) -> None:
    artifact = s3_store.put(descriptor(), b"secret model")
    other = s3_store.put(descriptor(), b"other model")
    download = s3_store.sign_download(artifact, timedelta(minutes=5))
    signature = download.url.split("X-Amz-Signature=")[1].split("&")[0]
    flipped = ("0" if signature[0] != "0" else "1") + signature[1:]

    assert _http(replace(download, url=download.url.replace(signature, flipped))) == 403
    assert _http(replace(download, url=download.url.replace(artifact.sha256, other.sha256))) == 403
    assert _http(replace(download, url=download.url.split("?")[0])) == 403
    with pytest.raises(ValueError):
        s3_store.sign_upload("not-a-hash", 1, timedelta(minutes=5))


@pytest.mark.s3
def test_register_rejects_size_mismatch(s3_store: Any) -> None:
    artifact = s3_store.put(descriptor(), b"twelve bytes")

    with pytest.raises(ArtifactIntegrityError):
        s3_store.register(descriptor(), artifact.sha256, artifact.size_bytes + 1)


@pytest.mark.s3
def test_named_endpoint_signs_for_that_host(s3_store: Any) -> None:
    from urllib.parse import urlsplit

    from tableforge.infrastructure.s3_artifacts import UnknownEndpointError

    content = b"named endpoint bytes"
    sha256 = hashlib.sha256(content).hexdigest()
    default = s3_store.sign_upload(sha256, len(content), timedelta(minutes=5))
    aliased = s3_store.sign_upload(sha256, len(content), timedelta(minutes=5), "alias")

    assert urlsplit(default.url).hostname != urlsplit(aliased.url).hostname
    direct.put(aliased, content)
    artifact = s3_store.register(descriptor(), sha256, len(content))
    download = s3_store.sign_download(artifact, timedelta(minutes=5), "alias")
    assert direct.get(download, sha256, len(content)) == content
    rehosted = replace(
        download,
        url=download.url.replace(
            urlsplit(download.url).netloc, urlsplit(default.url).netloc, 1
        ),
    )
    assert _http(rehosted) == 403
    assert s3_store.endpoint_names == ("alias",)
    with pytest.raises(UnknownEndpointError):
        s3_store.sign_download(artifact, timedelta(minutes=5), "missing")


def test_endpoint_list_validation() -> None:
    from tableforge.infrastructure.s3_artifacts import parse_endpoints

    assert parse_endpoints(" docker=http://rustfs:9000 , host=http://127.0.0.1:59000 ") == {
        "docker": "http://rustfs:9000",
        "host": "http://127.0.0.1:59000",
    }
    assert parse_endpoints("") == {}
    for bad in ("docker", "=http://x", "a=ftp://x", "a=http://x,a=http://y", "a b=http://x"):
        with pytest.raises(ValueError):
            parse_endpoints(bad)
