"""Phase 3 integration: tuning changes emulator behavior; health alerts fire."""

from __future__ import annotations

import asyncio
import contextlib
from collections import Counter

from llrpkit.emulator import EmulatedTag, LLRPEmulator
from llrpkit.health import HealthAlert, HealthMonitor
from llrpkit.inventory import TagReport
from llrpkit.reader import Reader

TAGS = [
    EmulatedTag(epc=bytes([0xE2, 0x22, i] + [0] * 9), antennas=(1 + i % 2,), rssi_dbm=-50.0)
    for i in range(8)
]


def make_emulator(**kwargs: object) -> LLRPEmulator:
    kwargs.setdefault("tags", TAGS)
    kwargs.setdefault("reads_per_sec", 400.0)
    return LLRPEmulator(**kwargs)  # type: ignore[arg-type]


async def collect(reader: Reader, **inventory_kwargs: object) -> list[TagReport]:
    out = []
    stream = reader.inventory(**inventory_kwargs)  # type: ignore[arg-type]
    async with contextlib.aclosing(stream):
        async for tag in stream:
            out.append(tag)
    return out


async def test_mode_switch_visibly_changes_read_rate() -> None:
    """The Phase 3 acceptance test: tuning the RF mode changes behavior."""
    async with make_emulator() as emu, Reader("127.0.0.1", emu.port) as reader:
        fast = await collect(reader, mode_index=0, duration=0.7)  # Max Throughput
        slow = await collect(reader, mode_index=3, duration=0.7)  # Dense Reader M8
        assert len(slow) > 0
        # factors are 1.6x vs 0.55x (~2.9x); assert with a generous margin
        assert len(fast) > len(slow) * 1.6, (len(fast), len(slow))


async def test_tagfocus_quiets_the_population() -> None:
    async with make_emulator() as emu, Reader("127.0.0.1", emu.port) as reader:
        focus = Counter(
            t.epc for t in await collect(reader, search_mode=3, session=1, duration=0.8)
        )
        dual = Counter(t.epc for t in await collect(reader, search_mode=2, session=1, duration=0.8))
        # TagFocus still finds every tag...
        assert len(focus) == len(TAGS)
        # ...but the field goes quiet once the population is suppressed,
        # while dual target keeps re-reading everything continuously.
        assert sum(focus.values()) < sum(dual.values()) * 0.5, (focus, dual)


async def test_temperature_via_octane_extension() -> None:
    async with make_emulator() as emu, Reader("127.0.0.1", emu.port) as reader:
        emu.set_temperature(47.2)
        assert await reader.get_temperature() == 47.0  # reported in whole °C


async def test_periodic_keepalive_round_trips() -> None:
    async with make_emulator() as emu, Reader("127.0.0.1", emu.port) as reader:
        await reader.set_keepalive(60)
        await asyncio.sleep(0.35)
        await reader.set_keepalive(None)
        assert emu.keepalive_acks >= 3
        acks = emu.keepalive_acks
        await asyncio.sleep(0.25)
        assert emu.keepalive_acks <= acks + 1  # disabled: no meaningful growth


async def test_disconnect_event_reaches_health_monitor() -> None:
    async with make_emulator() as emu, Reader("127.0.0.1", emu.port) as reader:
        monitor = HealthMonitor(antennas=range(1, 5))
        alerts = []

        async def watch() -> None:
            async for msg in reader.events():
                alerts.extend(monitor.handle_event(msg))

        watcher = asyncio.create_task(watch())
        try:
            await emu.set_antenna_connected(2, False)
            await asyncio.sleep(0.3)
            assert [(a.kind, a.antenna) for a in alerts] == [("disconnected", 2)]
            assert monitor.antennas[2].connected is False
            # and the port genuinely stops producing reads
            tags = await collect(reader, duration=0.5)
            assert tags
            assert all(t.antenna != 2 for t in tags)
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher


async def test_quiet_port_alert_fires_in_a_live_stream() -> None:
    """The other Phase 3 acceptance test: the quiet-port alert fires."""
    async with make_emulator() as emu, Reader("127.0.0.1", emu.port) as reader:
        monitor = HealthMonitor(quiet_after=0.4)
        quiet_alerts: list[HealthAlert] = []
        stream = reader.inventory(duration=1.6)
        cut = False
        async with contextlib.aclosing(stream):
            async for tag in stream:
                monitor.observe(tag)
                # once both ports have produced reads, pull antenna 2's cable
                if not cut and len(monitor.antennas) >= 2:
                    await emu.set_antenna_connected(2, False)
                    cut = True
                quiet_alerts.extend(a for a in monitor.check() if a.kind == "quiet")
        assert cut, "population never covered two antennas"
        assert any(a.antenna == 2 for a in quiet_alerts), monitor.snapshot()
        assert monitor.snapshot()[2]["quiet_alert_active"] is True


async def test_events_stream_ends_when_connection_closes() -> None:
    emu = make_emulator()
    await emu.start()
    reader = Reader("127.0.0.1", emu.port)
    await reader.connect()
    received = []

    async def watch() -> None:
        async for msg in reader.events():
            received.append(msg)

    watcher = asyncio.create_task(watch())
    await asyncio.sleep(0.1)
    await emu.stop()
    await asyncio.wait_for(watcher, timeout=2.0)  # generator returns by itself
    await reader.close()


async def test_profile_drives_an_inventory() -> None:
    from llrpkit.profiles import InventoryProfile

    profile = InventoryProfile(name="test", search_mode=2, session=1, tx_power_dbm=25.0)
    async with make_emulator() as emu, Reader("127.0.0.1", emu.port) as reader:
        tags = await collect(reader, **profile.inventory_kwargs(), duration=0.4)
        assert tags


async def test_suggestion_against_emulator_table() -> None:
    from llrpkit.modes import suggest_mode

    async with make_emulator() as emu, Reader("127.0.0.1", emu.port) as reader:
        pick, _reason = suggest_mode(reader.capabilities.modes, dense_environment=True)
        # emulator advertises no 1000/1004; deep-scan AutoSet is the right call
        assert pick.mode_id == 1002
        annotated = reader.annotated_modes()
        assert any(m.name == "Dense Reader M4" for m in annotated)
