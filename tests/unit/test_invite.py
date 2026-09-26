from __future__ import annotations

import pytest

from tableforge.domain.auth import InviteScope, OperatorRole
from tableforge.transport.invite import INVITE_PREFIX, InviteCode, InviteFormatError


def code(**overrides: object) -> InviteCode:
    values: dict[str, object] = {
        "cluster_id": "01a0-cluster",
        "address": "100.64.0.1:7443",
        "fingerprint": "ab" * 32,
        "secret": "secret-value",
        "scope": InviteScope(node=True, role=OperatorRole.OPERATOR),
    }
    values.update(overrides)
    return InviteCode(**values)  # type: ignore[arg-type]


def test_round_trip_and_prefix() -> None:
    original = code()

    encoded = original.encode()

    assert encoded.startswith(INVITE_PREFIX)
    assert "=" not in encoded and "+" not in encoded and "/" not in encoded
    assert InviteCode.decode(f"  {encoded}\n") == original


def test_ipv6_address_with_port_is_accepted() -> None:
    assert InviteCode.decode(code(address="[fd7a::1]:7443").encode()).address == "[fd7a::1]:7443"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("hello", "tfinv1_"),
        (INVITE_PREFIX + "!!!", "corrupted"),
        (INVITE_PREFIX + "eyJ4Ijo", "corrupted"),
    ],
)
def test_bad_codes_explain_the_problem(value: str, message: str) -> None:
    with pytest.raises(InviteFormatError, match=message):
        InviteCode.decode(value)


def test_truncated_code_is_detected() -> None:
    encoded = code().encode()

    with pytest.raises(InviteFormatError):
        InviteCode.decode(encoded[: len(encoded) // 2])


@pytest.mark.parametrize(
    "overrides",
    [
        {"fingerprint": "AB" * 32},
        {"fingerprint": "ab" * 31},
        {"address": "100.64.0.1"},
        {"secret": ""},
    ],
)
def test_invalid_fields_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(InviteFormatError):
        code(**overrides)
