from __future__ import annotations

from pathlib import Path

import pytest

from tableforge.models import ModelLibrary


def test_add_list_read_and_detect_damage(tmp_path: Path) -> None:
    library = ModelLibrary(tmp_path / "models")

    info = library.add(
        "pko-10bb-s11", b"weights", "nfsp", "6max-pko-10bb", training={"tournaments": 60000}
    )

    assert [item.model_id for item in library.list()] == ["pko-10bb-s11"]
    assert library.info("pko-10bb-s11") == info
    assert library.checkpoint("pko-10bb-s11") == b"weights"
    with pytest.raises(FileExistsError):
        library.add("pko-10bb-s11", b"other", "nfsp", "6max-pko-10bb")
    (tmp_path / "models" / "pko-10bb-s11" / "checkpoint.zip").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="sha256"):
        library.checkpoint("pko-10bb-s11")


def test_ids_are_validated_and_models_can_be_replaced_and_removed(tmp_path: Path) -> None:
    library = ModelLibrary(tmp_path)
    for bad in ("", "Upper", "../escape", "a" * 65, "spaces here"):
        with pytest.raises(ValueError):
            library.add(bad, b"x", "nfsp", "g")

    library.add("m", b"one", "nfsp", "g")
    library.add("m", b"two", "nfsp", "g", replace=True)
    assert library.checkpoint("m") == b"two"
    library.remove("m")
    assert library.list() == []
    with pytest.raises(KeyError):
        library.info("m")
