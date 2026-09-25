from __future__ import annotations

import os
import secrets
from collections.abc import Iterator

import pytest

S3_ENDPOINT_ENV = "TABLEFORGE_TEST_S3_ENDPOINT"
DEFAULT_S3_ENDPOINT = "http://127.0.0.1:59000"
S3_ACCESS_KEY = os.environ.get("TABLEFORGE_TEST_S3_ACCESS_KEY", "tableforge")
S3_SECRET_KEY = os.environ.get("TABLEFORGE_TEST_S3_SECRET_KEY", "tableforge-secret")


def _s3_endpoint() -> str | None:
    if os.environ.get("TABLEFORGE_DISABLE_S3_TESTS"):
        return None
    configured = os.environ.get(S3_ENDPOINT_ENV)
    endpoint = configured or DEFAULT_S3_ENDPOINT
    try:
        client = _client(endpoint)
        client.list_buckets()
    except ImportError:
        return None
    except Exception:
        if configured:
            raise
        return None
    return endpoint


def _client(endpoint: str) -> object:
    import boto3
    import botocore.config

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name="us-east-1",
        config=botocore.config.Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=2,
            read_timeout=10,
            retries={"max_attempts": 1},
        ),
    )


S3_ENDPOINT = _s3_endpoint()


@pytest.fixture
def s3_store() -> Iterator[object]:
    if S3_ENDPOINT is None:
        pytest.skip(f"S3 endpoint not reachable; start it or set {S3_ENDPOINT_ENV}")
    from tableforge.infrastructure.s3_artifacts import S3ArtifactStore
    from tableforge.settings import ArtifactSettings

    bucket = f"tf-test-{secrets.token_hex(6)}"
    store = S3ArtifactStore.from_settings(
        ArtifactSettings(
            backend="s3",
            bucket=bucket,
            endpoint_url=S3_ENDPOINT,
            access_key=S3_ACCESS_KEY,
            secret_key=S3_SECRET_KEY,
            endpoints=f"alias={alias_endpoint(S3_ENDPOINT)}",
        )
    )
    yield store
    client = _client(S3_ENDPOINT)
    paginator = client.get_paginator("list_objects_v2")  # type: ignore[attr-defined]
    for page in paginator.paginate(Bucket=bucket):
        for item in page.get("Contents", ()):
            client.delete_object(Bucket=bucket, Key=item["Key"])  # type: ignore[attr-defined]
    client.delete_bucket(Bucket=bucket)  # type: ignore[attr-defined]


def alias_endpoint(endpoint: str) -> str:
    """The same S3 server reached through a different host name."""
    if "127.0.0.1" in endpoint:
        return endpoint.replace("127.0.0.1", "localhost")
    return endpoint.replace("localhost", "127.0.0.1")
