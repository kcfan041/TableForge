from __future__ import annotations

from pathlib import Path

import pytest

from tableforge.transport.tls import (
    CertificateHostsError,
    certificate_fingerprint,
    covers,
    generate_self_signed,
)


def test_certificate_covers_defaults_and_extra_hosts(tmp_path: Path) -> None:
    material = generate_self_signed(tmp_path, ("coordinator", "100.64.0.7"))

    hosts = material.hosts()
    assert {"localhost", "127.0.0.1", "coordinator", "100.64.0.7"} <= set(hosts)
    assert len(material.fingerprint()) == 64
    assert material.fingerprint() == certificate_fingerprint(
        material.certificate_path.read_bytes()
    )


def test_existing_certificate_is_reused_when_it_covers_hosts(tmp_path: Path) -> None:
    first = generate_self_signed(tmp_path, ("coordinator",))
    fingerprint = first.fingerprint()

    again = generate_self_signed(tmp_path, ("coordinator", "LOCALHOST"))

    assert again.fingerprint() == fingerprint


def test_missing_host_requires_explicit_regeneration(tmp_path: Path) -> None:
    first = generate_self_signed(tmp_path, ())
    old = first.fingerprint()

    with pytest.raises(CertificateHostsError) as error:
        generate_self_signed(tmp_path, ("100.64.0.7",))
    assert error.value.missing == ("100.64.0.7",)
    assert first.fingerprint() == old

    renewed = generate_self_signed(tmp_path, ("100.64.0.7",), regenerate=True)
    assert renewed.fingerprint() != old
    assert covers(renewed.hosts(), "100.64.0.7")


def test_ip_comparison_is_semantic() -> None:
    assert covers(("::1",), "0:0:0:0:0:0:0:1")
    assert covers(("Example.TS.net",), "example.ts.net")
    assert not covers(("100.64.0.7",), "100.64.0.8")
