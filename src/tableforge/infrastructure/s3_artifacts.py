from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from tableforge.application.ports import (
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    SignedTransfer,
    StoredObject,
    storage_key_for,
)
from tableforge.domain.artifacts import Artifact, ArtifactDescriptor
from tableforge.infrastructure.artifact_metadata import artifact_from_record, artifact_record

if TYPE_CHECKING:
    from tableforge.settings import ArtifactSettings

MAX_PRESIGN = timedelta(hours=1)
_MISSING = {"404", "NoSuchKey", "NotFound"}


class S3ArtifactStore:
    """Content-addressed artifacts in an S3-compatible bucket.

    Objects live at ``sha256/aa/bb/<sha256>`` and carry an ``x-amz-checksum-sha256`` so the
    storage service itself rejects corrupted uploads. Per-artifact metadata is a small JSON
    object at ``metadata/<artifact_id>.json``.
    """

    def __init__(
        self,
        client: Any,
        bucket: str,
        presign_client: Any | None = None,
        named_presign_clients: dict[str, Any] | None = None,
    ) -> None:
        self._s3 = client
        self._presign = presign_client or client
        self._named = dict(named_presign_clients or {})
        self._bucket = bucket

    @classmethod
    def from_settings(cls, settings: ArtifactSettings) -> S3ArtifactStore:
        client = _client(settings, settings.endpoint_url)
        public = settings.public_endpoint_url or settings.endpoint_url
        presign = client if public == settings.endpoint_url else _client(settings, public)
        endpoints = parse_endpoints(settings.endpoints)
        named = {name: _client(settings, url) for name, url in endpoints.items()}
        store = cls(client, settings.bucket, presign, named)
        store.ensure_bucket()
        return store

    @property
    def endpoint_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._named))

    def _signer(self, endpoint: str) -> Any:
        if not endpoint:
            return self._presign
        try:
            return self._named[endpoint]
        except KeyError:
            raise UnknownEndpointError(endpoint) from None

    @property
    def bucket(self) -> str:
        return self._bucket

    def ensure_bucket(self) -> None:
        import botocore.exceptions

        try:
            self._s3.head_bucket(Bucket=self._bucket)
        except botocore.exceptions.ClientError as error:
            if _code(error) not in _MISSING | {"NoSuchBucket"}:
                raise
            try:
                self._s3.create_bucket(Bucket=self._bucket)
            except botocore.exceptions.ClientError as create_error:
                if _code(create_error) not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                    raise

    def put(self, descriptor: ArtifactDescriptor, content: bytes) -> Artifact:
        import botocore.exceptions

        sha256 = hashlib.sha256(content).hexdigest()
        key = storage_key_for(sha256)
        if not self._object_matches(key, sha256, len(content)):
            try:
                self._s3.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=content,
                    ChecksumSHA256=_b64(sha256),
                    IfNoneMatch="*",
                )
            except botocore.exceptions.ClientError as error:
                if _code(error) not in {"PreconditionFailed", "ConditionalRequestConflict"}:
                    raise
                if not self._object_matches(key, sha256, len(content)):
                    raise ArtifactIntegrityError(key) from error
        artifact = Artifact.available(descriptor, sha256, len(content), key)
        self._write_metadata(artifact)
        return artifact

    def register(self, descriptor: ArtifactDescriptor, sha256: str, size_bytes: int) -> Artifact:
        key = storage_key_for(sha256)
        head = self._head(key)
        if head is None:
            raise ArtifactNotFoundError(key)
        if int(head["ContentLength"]) != size_bytes or head.get("ChecksumSHA256") != _b64(sha256):
            raise ArtifactIntegrityError(key)
        artifact = Artifact.available(descriptor, sha256, size_bytes, key)
        self._write_metadata(artifact)
        return artifact

    def get(self, artifact_id: str) -> Artifact:
        import botocore.exceptions

        try:
            response = self._s3.get_object(Bucket=self._bucket, Key=_metadata_key(artifact_id))
        except botocore.exceptions.ClientError as error:
            if _code(error) in _MISSING:
                raise ArtifactNotFoundError(artifact_id) from error
            raise
        return artifact_from_record(json.loads(response["Body"].read()))

    def read(self, artifact_id: str) -> tuple[Artifact, bytes]:
        import botocore.exceptions

        artifact = self.get(artifact_id)
        try:
            response = self._s3.get_object(Bucket=self._bucket, Key=artifact.storage_key)
        except botocore.exceptions.ClientError as error:
            if _code(error) in _MISSING:
                raise ArtifactNotFoundError(artifact_id) from error
            raise
        content = response["Body"].read()
        if (
            hashlib.sha256(content).hexdigest() != artifact.sha256
            or len(content) != artifact.size_bytes
        ):
            raise ArtifactIntegrityError(artifact_id)
        return artifact, content

    def exists(self, sha256: str) -> bool:
        return self._head(storage_key_for(sha256)) is not None

    def sign_upload(
        self, sha256: str, size_bytes: int, ttl: timedelta, endpoint: str = ""
    ) -> SignedTransfer:
        key = storage_key_for(sha256)
        if size_bytes < 0:
            raise ValueError("size_bytes cannot be negative")
        seconds = _ttl_seconds(ttl)
        checksum = _b64(sha256)
        url = self._signer(endpoint).generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ChecksumSHA256": checksum,
                "ContentLength": size_bytes,
            },
            ExpiresIn=seconds,
        )
        return SignedTransfer(
            method="PUT",
            url=url,
            headers=(
                ("content-length", str(size_bytes)),
                ("x-amz-checksum-sha256", checksum),
            ),
            expires_at=datetime.now(UTC) + timedelta(seconds=seconds),
        )

    def sign_download(
        self, artifact: Artifact, ttl: timedelta, endpoint: str = ""
    ) -> SignedTransfer:
        seconds = _ttl_seconds(ttl)
        url = self._signer(endpoint).generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": artifact.storage_key},
            ExpiresIn=seconds,
        )
        return SignedTransfer(
            method="GET",
            url=url,
            headers=(),
            expires_at=datetime.now(UTC) + timedelta(seconds=seconds),
        )

    def list_objects(self) -> Iterator[StoredObject]:
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix="sha256/"):
            for item in page.get("Contents", ()):
                yield StoredObject(
                    storage_key=item["Key"],
                    size_bytes=int(item["Size"]),
                    modified_at=item["LastModified"],
                )

    def delete_object(self, storage_key: str) -> None:
        self._s3.delete_object(Bucket=self._bucket, Key=storage_key)
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix="metadata/"):
            for item in page.get("Contents", ()):
                body = self._s3.get_object(Bucket=self._bucket, Key=item["Key"])["Body"].read()
                try:
                    record = json.loads(body)
                except ValueError:
                    continue
                if record.get("storage_key") == storage_key:
                    self._s3.delete_object(Bucket=self._bucket, Key=item["Key"])

    def _write_metadata(self, artifact: Artifact) -> None:
        body = json.dumps(artifact_record(artifact), sort_keys=True, separators=(",", ":")).encode()
        self._s3.put_object(
            Bucket=self._bucket,
            Key=_metadata_key(str(artifact.descriptor.artifact_id)),
            Body=body,
            ContentType="application/json",
        )

    def _head(self, key: str) -> dict[str, Any] | None:
        import botocore.exceptions

        try:
            head: dict[str, Any] = self._s3.head_object(
                Bucket=self._bucket, Key=key, ChecksumMode="ENABLED"
            )
        except botocore.exceptions.ClientError as error:
            if _code(error) in _MISSING:
                return None
            raise
        return head

    def _object_matches(self, key: str, sha256: str, size_bytes: int) -> bool:
        head = self._head(key)
        return (
            head is not None
            and int(head["ContentLength"]) == size_bytes
            and head.get("ChecksumSHA256") in (None, _b64(sha256))
        )


def _client(settings: ArtifactSettings, endpoint: str) -> Any:
    import boto3
    import botocore.config

    return boto3.client(
        "s3",
        endpoint_url=endpoint or None,
        aws_access_key_id=settings.access_key or None,
        aws_secret_access_key=settings.secret_key or None,
        region_name=settings.region,
        config=botocore.config.Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            retries={"max_attempts": 5, "mode": "standard"},
            connect_timeout=5,
            read_timeout=60,
        ),
    )


def _metadata_key(artifact_id: str) -> str:
    if not artifact_id or "/" in artifact_id or "\\" in artifact_id or artifact_id in (".", ".."):
        raise ArtifactNotFoundError(artifact_id)
    return f"metadata/{artifact_id}.json"


def _b64(sha256: str) -> str:
    return base64.b64encode(bytes.fromhex(sha256)).decode()


def _ttl_seconds(ttl: timedelta) -> int:
    if ttl <= timedelta(0):
        raise ValueError("presign ttl must be positive")
    return int(min(ttl, MAX_PRESIGN).total_seconds())


def _code(error: Exception) -> str:
    response = getattr(error, "response", {}) or {}
    return str(response.get("Error", {}).get("Code", ""))


class UnknownEndpointError(ValueError):
    pass


def parse_endpoints(value: str) -> dict[str, str]:
    """Parse ``name=url,name=url`` into a mapping of named public storage endpoints."""
    endpoints: dict[str, str] = {}
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        name, separator, url = item.partition("=")
        name, url = name.strip(), url.strip()
        if not separator or not name or not url:
            raise ValueError(f"invalid storage endpoint entry {item!r}; expected name=url")
        if not name.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"invalid storage endpoint name {name!r}")
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"storage endpoint {name!r} must be an http(s) URL")
        if name in endpoints:
            raise ValueError(f"duplicate storage endpoint name {name!r}")
        endpoints[name] = url
    return endpoints
