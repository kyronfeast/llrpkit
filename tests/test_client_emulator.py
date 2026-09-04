"""Integration tests: LLRPClient/Reader against the in-process emulator."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from llrpkit.client import LLRPClient, check_status
from llrpkit.emulator import EmulatedTag, LLRPEmulator
from llrpkit.exceptions import LLRPConnectionError, LLRPStatusError, LLRPTimeoutError
from llrpkit.inventory import build_rospec
from llrpkit.protocol import impinj, messages
from llrpkit.reader import Reader

TAGS = [
    EmulatedTag(epc=bytes([0xE2, 0x11, i] + [0] * 9), antennas=(1 + i % 2,), rssi_dbm=-48.0 - i)
    for i in range(6)
]


def make_emulator(**kwargs: object) -> LLRPEmulator:
    kwargs.setdefault("tags", TAGS)
    kwargs.setdefault("reads_per_sec", 400.0)
    return LLRPEmulator(**kwargs)  # type: ignore[arg-type]


async def test_connect_capabilities_and_extensions() -> None:
    async with make_emulator() as emu, Reader("127.0.0.1", emu.port) as reader:
        caps = reader.capabilities
        assert caps.is_impinj
        assert reader.is_impinj
        assert reader.impinj_extensions_enabled
        assert reader.max_antennas == 4
        assert reader.model_number == 700
        assert reader.firmware.startswith("llrpkit-emu")
        assert {m.mode_id for m in caps.modes} >= {0, 1, 2, 3, 1002}
        assert caps.hopping
        # power table: 10..30 dBm in 1 dB steps -> 20 dBm is index 11
        assert caps.power_index_for_dbm(20.0) == 11
        assert caps.power_index_for_dbm(20.7) == 11
        assert caps.mode(1002).mode_id == 1002


async def test_antenna_hub_expands_to_32_antennas() -> None:
    # An R700 with R702 antenna hubs reports up to 32 antennas; llrpkit reads
    # the count dynamically, so a hub-sized reader needs no special handling.
    hub_tag = EmulatedTag(epc=bytes([0xE2, 0x00, 0x09] + [0] * 9), antennas=(9,))
    async with (
        make_emulator(tags=[hub_tag], antenna_count=32) as emu,
        Reader("127.0.0.1", emu.port) as reader,
    ):
        assert reader.max_antennas == 32  # the whole point: not capped at 4
        seen = []
        stream = reader.inventory(antennas=(9,), duration=5.0, max_tags=3)
        async with contextlib.aclosing(stream):
            async for tag in stream:
                seen.append(tag)
        assert seen, "no tags read on hub-range antenna 9"
        assert all(t.antenna == 9 for t in seen)


async def test_power_and_mode_capability_errors() -> None:
    from llrpkit.exceptions import CapabilityError

    async with make_emulator() as emu, Reader("127.0.0.1", emu.port) as reader:
        with pytest.raises(CapabilityError):
            reader.capabilities.power_index_for_dbm(1.0)  # below table minimum
        with pytest.raises(CapabilityError):
            reader.capabilities.mode(9999)


async def test_second_connection_refused() -> None:
    async with make_emulator() as emu, Reader("127.0.0.1", emu.port):
        second = Reader("127.0.0.1", emu.port)
        with pytest.raises(LLRPConnectionError, match="refused"):
            await second.connect()


async def test_inventory_stream_end_to_end() -> None:
    async with make_emulator() as emu, Reader("127.0.0.1", emu.port) as reader:
        collected = []
        stream = reader.inventory(session=1, search_mode=3, include_phase=True, max_tags=15)
        async with contextlib.aclosing(stream):
            async for tag in stream:
                collected.append(tag)
        assert len(collected) == 15
        population = {t.epc for t in TAGS}
        assert {t.epc for t in collected} <= population
        assert all(t.antenna in (1, 2) for t in collected)
        assert all(t.rssi_dbm is not None and t.rssi_dbm < -30 for t in collected)
        # Octane content: sub-dBm RSSI resolution and phase present
        assert all(t.phase_deg is not None for t in collected)
        assert any(t.rssi_dbm is not None and t.rssi_dbm % 1 != 0 for t in collected)
        assert all(t.seen_count == 1 for t in collected)
        assert all(t.first_seen_us is not None for t in collected)


async def test_inventory_cleans_up_rospec() -> None:
    async with make_emulator() as emu, Reader("127.0.0.1", emu.port) as reader:
        stream = reader.inventory(max_tags=3)
        async with contextlib.aclosing(stream):
            async for _ in stream:
                pass
        response = check_status(await reader.client.transact(messages.GET_ROSPECS()))
        assert isinstance(response, messages.GET_ROSPECS_RESPONSE)
        assert response.ro_specs == []


async def test_inventory_duration_and_tid() -> None:
    async with make_emulator() as emu, Reader("127.0.0.1", emu.port) as reader:
        collected = []
        stream = reader.inventory(duration=0.6, include_tid=True)
        async with contextlib.aclosing(stream):
            async for tag in stream:
                collected.append(tag)
        assert collected, "expected at least one report within the duration"
        assert all(t.tid is not None and t.tid.startswith(b"\xe2\x80\x11\x05") for t in collected)


async def test_single_stream_guard() -> None:
    async with make_emulator() as emu, Reader("127.0.0.1", emu.port) as reader:
        from llrpkit.exceptions import LLRPError

        stream = reader.inventory(max_tags=50)
        async with contextlib.aclosing(stream):
            got_one = False
            async for _ in stream:
                got_one = True
                second = reader.inventory(max_tags=1)
                with pytest.raises(LLRPError, match="already active"):
                    await anext(second)
                await second.aclose()
                break
            assert got_one


async def test_duplicate_rospec_is_status_error() -> None:
    async with make_emulator() as emu, LLRPClient("127.0.0.1", emu.port) as client:
        rospec = build_rospec(ro_spec_id=7)
        check_status(await client.transact(messages.ADD_ROSPEC(ro_spec=rospec)))
        with pytest.raises(LLRPStatusError) as excinfo:
            check_status(await client.transact(messages.ADD_ROSPEC(ro_spec=rospec)))
        assert excinfo.value.status_code == 104  # M_DuplicateParameter
        assert "exists" in excinfo.value.error_description


async def test_keepalive_is_acknowledged() -> None:
    async with make_emulator() as emu, LLRPClient("127.0.0.1", emu.port) as client:
        assert client.connected
        await emu.send_keepalive()
        await emu.wait_keepalive_ack(timeout=2.0)


async def test_dropped_response_times_out() -> None:
    async with make_emulator() as emu, LLRPClient("127.0.0.1", emu.port) as client:
        emu.drop_next(messages.GET_READER_CONFIG)
        with pytest.raises(LLRPTimeoutError):
            await client.transact(
                messages.GET_READER_CONFIG(
                    antenna_id=0, requested_data=0, gpi_port_num=0, gpo_port_num=0
                ),
                timeout=0.3,
            )
        # the connection is still healthy afterwards
        check_status(await client.transact(messages.GET_ROSPECS()))


async def test_unsupported_message_yields_error_status() -> None:
    async with make_emulator() as emu, LLRPClient("127.0.0.1", emu.port) as client:
        response = await client.transact(messages.GET_REPORT())
        with pytest.raises(LLRPStatusError) as excinfo:
            check_status(response)
        assert excinfo.value.status_code == 109  # M_UnsupportedMessage


async def test_connection_lost_fails_transactions() -> None:
    emu = make_emulator()
    await emu.start()
    client = LLRPClient("127.0.0.1", emu.port)
    await client.connect()
    try:
        await emu.stop()
        await asyncio.sleep(0.05)
        with pytest.raises(LLRPConnectionError):
            await client.transact(messages.GET_ROSPECS(), timeout=1.0)
        assert not client.connected
    finally:
        await client.close()


async def test_impinj_save_settings_roundtrip_against_emulator() -> None:
    # Not a handled message in the emulator yet -> clean unsupported error,
    # proving unknown *custom* messages flow end-to-end too.
    async with make_emulator() as emu, LLRPClient("127.0.0.1", emu.port) as client:
        response = await client.transact(impinj.IMPINJ_SAVE_SETTINGS(save_configuration=True))
        with pytest.raises(LLRPStatusError):
            check_status(response)


async def test_client_double_connect_rejected() -> None:
    async with make_emulator() as emu:
        client = LLRPClient("127.0.0.1", emu.port)
        await client.connect()
        try:
            from llrpkit.exceptions import LLRPError

            with pytest.raises(LLRPError, match="already connected"):
                await client.connect()
        finally:
            await client.close()


async def test_connect_to_closed_port_raises() -> None:
    emu = make_emulator()
    await emu.start()
    port = emu.port
    await emu.stop()
    client = LLRPClient("127.0.0.1", port, connect_timeout=2.0)
    with pytest.raises(LLRPConnectionError):
        await client.connect()
