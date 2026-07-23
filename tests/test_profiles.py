"""Unit tests for inventory settings profiles."""

from __future__ import annotations

from pathlib import Path

import pytest

from llrpkit.profiles import InventoryProfile


def test_roundtrip_json() -> None:
    profile = InventoryProfile(
        name="dock-door",
        description="Portal with TagFocus",
        antennas=(1, 2),
        session=1,
        search_mode=3,
        mode_index=1004,
        tx_power_dbm=27.5,
        include_phase=True,
        keepalive_ms=5000,
    )
    restored = InventoryProfile.from_json(profile.to_json())
    assert restored == profile
    assert restored.antennas == (1, 2)


def test_inventory_kwargs_match_reader_signature() -> None:
    import inspect

    from llrpkit.reader import Reader

    kwargs = InventoryProfile().inventory_kwargs()
    accepted = set(inspect.signature(Reader.inventory).parameters)
    assert set(kwargs) <= accepted


def test_save_and_load(tmp_path: Path) -> None:
    profile = InventoryProfile(name="shelf", antennas=(3,), tx_power_dbm=20.0)
    path = profile.save(tmp_path / "shelf.json")
    assert InventoryProfile.load(path) == profile
    assert path.read_text().endswith("\n")


def test_from_json_rejects_unknown_fields_and_bad_json() -> None:
    with pytest.raises(ValueError, match="unknown field"):
        InventoryProfile.from_json('{"name": "x", "warp_speed": 9}')
    with pytest.raises(ValueError, match="not valid JSON"):
        InventoryProfile.from_json("{nope")
    with pytest.raises(ValueError, match="must be an object"):
        InventoryProfile.from_json("[1, 2]")
