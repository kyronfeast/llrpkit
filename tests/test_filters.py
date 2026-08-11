"""C1G2 select filters: build_rospec plumbing and end-to-end behavior."""

from __future__ import annotations

import contextlib

import pytest

from llrpkit.inventory import build_rospec
from llrpkit.protocol import enums, params
from llrpkit.reader import Reader
from tests.test_hardening import make_emulator


def _filters_of(rospec: params.ROSpec) -> list[params.C1G2Filter]:
    ai = rospec.spec_parameters[0]
    assert isinstance(ai, params.AISpec)
    cmd = ai.inventory_parameter_specs[0].antenna_configurations[0]
    return cmd.air_protocol_inventory_command_settings[0].c1_g2_filters


def test_build_rospec_epc_filter_include() -> None:
    rospec = build_rospec(epc_filter="e23301")
    (flt,) = _filters_of(rospec)
    mask = flt.c1_g2_tag_inventory_mask
    assert mask.mb == 1
    assert mask.pointer == 0x20  # EPC proper starts after CRC+PC
    assert mask.tag_mask.bit_len == 24
    assert mask.tag_mask.data == bytes.fromhex("e23301")
    action = flt.c1_g2_tag_inventory_state_unaware_filter_action
    assert action is not None
    assert int(action.action) == int(enums.C1G2StateUnawareAction.Select_Unselect)


def test_build_rospec_epc_filter_exclude_and_bytes() -> None:
    rospec = build_rospec(epc_filter=b"\xe2\x33", filter_action="exclude")
    (flt,) = _filters_of(rospec)
    action = flt.c1_g2_tag_inventory_state_unaware_filter_action
    assert action is not None
    assert int(action.action) == int(enums.C1G2StateUnawareAction.Unselect_Select)


def test_build_rospec_filter_validation() -> None:
    with pytest.raises(ValueError, match="filter_action"):
        build_rospec(epc_filter=b"\xe2", filter_action="sideways")
    with pytest.raises(ValueError, match="hex"):
        build_rospec(epc_filter="zz")
    assert _filters_of(build_rospec()) == []  # no filter by default


async def _collect(reader: Reader, n: int, **kwargs: object) -> list[bytes]:
    epcs: list[bytes] = []
    stream = reader.inventory(max_tags=n, duration=8.0, **kwargs)  # type: ignore[arg-type]
    async with contextlib.aclosing(stream):
        async for tag in stream:
            epcs.append(tag.epc)
    return epcs


async def test_emulator_honors_include_filter() -> None:
    """Only the filtered tag family may be reported."""
    async with make_emulator() as emu, Reader("127.0.0.1", emu.port) as reader:
        target = bytes([0xE2, 0x33, 0x02])
        epcs = await _collect(reader, 15, epc_filter=target)
        assert epcs, "filtered inventory produced nothing"
        assert all(e.startswith(target) for e in epcs)


async def test_emulator_honors_exclude_filter() -> None:
    async with make_emulator() as emu, Reader("127.0.0.1", emu.port) as reader:
        banned = bytes([0xE2, 0x33, 0x00])
        epcs = await _collect(reader, 25, epc_filter=banned, filter_action="exclude")
        assert epcs
        assert all(not e.startswith(banned) for e in epcs)
        assert len({e[:3] for e in epcs}) >= 2  # the others still answer


async def test_low_power_hides_weak_tags_and_high_power_finds_them() -> None:
    """The emulator's power model: weak tags need power to be energized."""
    from llrpkit.emulator import EmulatedTag

    strong = EmulatedTag(epc=b"\xaa" * 12, antennas=(1,), rssi_dbm=-40.0)
    weak = EmulatedTag(epc=b"\xbb" * 12, antennas=(1,), rssi_dbm=-68.0)
    async with make_emulator(tags=[strong, weak], reads_per_sec=400.0) as emu:
        reader = Reader("127.0.0.1", emu.port)
        await reader.connect()
        try:
            low = set(await _collect(reader, 12, tx_power_dbm=12.0))
            assert low == {strong.epc}, "weak tag must be invisible at 12 dBm"
            high = set(await _collect(reader, 40, tx_power_dbm=30.0))
            assert high == {strong.epc, weak.epc}, "30 dBm must reach both"
        finally:
            await reader.close()
