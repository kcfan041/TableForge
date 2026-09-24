from __future__ import annotations

import re

from tableforge.domain.ids import uuid7

_UUID7 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def test_uuid7_has_expected_version_and_variant() -> None:
    assert _UUID7.fullmatch(uuid7())


def test_uuid7_is_unique() -> None:
    assert len({uuid7() for _ in range(1000)}) == 1000
