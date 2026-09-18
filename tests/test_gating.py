"""Gated inventory: one window of tags per trip of a sensor line — no hardware.

The emulator plays the reader; ``emu.set_gpi`` plays the photo eye. The tests
prove the three things a production line needs: the reader (not the host)
starts and stops reading on the line, every tag seen while the line is tripped
lands in that trip's window, and a trip that reads *nothing* still produces a
window — the pail-with-no-tag exception.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator

import pytest

from llrpkit import GPIEdge, InventoryWindow, Reader, TagReport
from llrpkit.emulator import EmulatedTag, LLRPEmulator
from llrpkit.gating import END_OF_STREAM, assemble_windows

PAIL_A = bytes.fromhex("e200aa00000000000000000a")
PAIL_B = bytes.fromhex("e200bb00000000000000000b")


# --- pure window assembly ---------------------------------------------------


async def _drive(items: list[object], **kw: object) -> list[InventoryWindow]:
    q: asyncio.Queue[object] = asyncio.Queue()
    for it in items:
        await q.put(it)
    await q.put(END_OF_STREAM)
    out: list[InventoryWindow] = []
    async for w in assemble_windows(q, port=1, settle=0.01, **kw):  # type: ignore[arg-type]
        out.append(w)
    return out


def _tag(epc: bytes) -> TagReport:
    return TagReport(epc=epc, antenna=1)


async def test_assemble_one_window_with_tags_and_one_empty() -> None:
    t0 = time.time()
    items = [
        GPIEdge(port=1, high=False, at=t0),  # trip (active-low)
        _tag(PAIL_A),
        _tag(PAIL_A),
        GPIEdge(port=1, high=True, at=t0 + 0.4),  # release
        GPIEdge(port=1, high=False, at=t0 + 1.0),  # second pail...
        GPIEdge(port=1, high=True, at=t0 + 1.3),  # ...nothing read
    ]
    windows = await _drive(items)
    assert len(windows) == 2
    first, second = windows
    assert first.epcs == (PAIL_A.hex(),)
    assert len(first.tags) == 2  # repeats kept in .tags, deduped in .epcs
    assert first.duration == pytest.approx(0.4)
    assert second.empty  # the pail-with-no-tag case
    assert second.tags == ()


async def test_tags_outside_a_window_are_dropped_and_other_ports_ignored() -> None:
    t0 = time.time()
    items = [
        _tag(PAIL_B),  # gate closed: noise
        GPIEdge(port=2, high=False, at=t0),  # a different sensor
        _tag(PAIL_B),
        GPIEdge(port=1, high=False, at=t0),
        _tag(PAIL_A),
        GPIEdge(port=1, high=True, at=t0 + 0.2),
    ]
    windows = await _drive(items)
    assert [w.epcs for w in windows] == [(PAIL_A.hex(),)]


async def test_settle_catches_late_reports_and_active_high_works() -> None:
    # LLRP delivers "line released" and the last RO_ACCESS_REPORT on different
    # channels; a report that lands just after the release still counts.
    t0 = time.time()
    q: asyncio.Queue[object] = asyncio.Queue()
    await q.put(GPIEdge(port=1, high=True, at=t0))  # active-high sensor
    await q.put(_tag(PAIL_A))
    await q.put(GPIEdge(port=1, high=False, at=t0 + 0.3))
    await q.put(_tag(PAIL_B))  # straggler inside the settle window
    await q.put(END_OF_STREAM)
    windows = [w async for w in assemble_windows(q, port=1, active_high=True, settle=0.05)]
    assert len(windows) == 1
    assert windows[0].epcs == (PAIL_A.hex(), PAIL_B.hex())


async def test_max_open_force_closes_a_stuck_gate() -> None:
    q: asyncio.Queue[object] = asyncio.Queue()
    await q.put(GPIEdge(port=1, high=False, at=time.time()))
    await q.put(_tag(PAIL_A))
    gen = assemble_windows(q, port=1, settle=0.01, max_open=0.05)
    w = await asyncio.wait_for(anext(gen), timeout=2.0)  # no release edge ever comes
    assert w.epcs == (PAIL_A.hex(),)
    await gen.aclose()


# --- against the emulator ----------------------------------------------------


@pytest.fixture
async def gated_emu() -> AsyncIterator[LLRPEmulator]:
    emu = LLRPEmulator(reads_per_sec=300.0, seed=3)
    emu.tags = [EmulatedTag(epc=PAIL_A, antennas=(1,), rssi_dbm=-45.0)]
    await emu.start()
    try:
        yield emu
    finally:
        await emu.stop()


async def test_gpi_triggered_rospec_reads_only_while_line_is_tripped(
    gated_emu: LLRPEmulator,
) -> None:
    emu = gated_emu
    async with Reader("127.0.0.1", emu.port) as reader:
        seen: list[TagReport] = []

        async def collect() -> None:
            async for t in reader.inventory(gpi_trigger=1):
                seen.append(t)

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.3)
        assert seen == []  # armed, but the line hasn't tripped: silence

        await emu.set_gpi(1, True)  # photo eye blocked → voltage on GPI1 → reader starts
        await asyncio.sleep(0.3)
        assert len(seen) > 0
        n = len(seen)

        await emu.set_gpi(1, False)  # released → reader stops
        await asyncio.sleep(0.3)
        assert len(seen) == n  # nothing more

        await emu.set_gpi(1, True)  # re-armed automatically: second trip reads again
        await asyncio.sleep(0.3)
        assert len(seen) > n

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_gpi_events_feed_reports_edges(gated_emu: LLRPEmulator) -> None:
    emu = gated_emu
    async with Reader("127.0.0.1", emu.port) as reader:
        edges: list[GPIEdge] = []

        async def collect() -> None:
            async for e in reader.gpi_events():
                edges.append(e)
                if len(edges) == 2:
                    return

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.1)
        await emu.set_gpi(2, True)
        await emu.set_gpi(2, False)
        await asyncio.wait_for(task, timeout=2.0)
    assert [(e.port, e.high) for e in edges] == [(2, True), (2, False)]
    assert edges[0].at > 1_600_000_000  # a real Unix timestamp, in seconds


async def test_windows_yields_one_per_trip_including_an_empty_one(
    gated_emu: LLRPEmulator,
) -> None:
    emu = gated_emu
    async with Reader("127.0.0.1", emu.port) as reader:
        windows: list[InventoryWindow] = []

        async def collect() -> None:
            async for w in reader.windows(1, settle=0.05):
                windows.append(w)
                if len(windows) == 2:
                    return

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.2)

        await emu.set_gpi(1, True)  # pail 1 — tagged
        await asyncio.sleep(0.3)
        await emu.set_gpi(1, False)

        await asyncio.sleep(0.2)
        emu.tags = []  # pail 2 — no readable tag
        await emu.set_gpi(1, True)
        await asyncio.sleep(0.2)
        await emu.set_gpi(1, False)

        await asyncio.wait_for(task, timeout=5.0)

    tagged, empty = windows
    assert tagged.epcs == (PAIL_A.hex(),)
    assert tagged.port == 1
    assert tagged.duration >= 0.25
    assert empty.empty  # the exception the line exists to catch
    assert empty.opened_at > tagged.closed_at


async def test_gpi_stop_timeout_caps_a_stuck_line(gated_emu: LLRPEmulator) -> None:
    emu = gated_emu
    async with Reader("127.0.0.1", emu.port) as reader:
        seen: list[TagReport] = []

        async def collect() -> None:
            async for t in reader.inventory(gpi_trigger=1, gpi_stop_timeout=0.3):
                seen.append(t)

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.1)
        await emu.set_gpi(1, True)  # trips and never releases
        await asyncio.sleep(0.6)  # past the 300 ms cap
        n = len(seen)
        assert n > 0
        await asyncio.sleep(0.3)
        assert len(seen) == n  # reader stopped itself
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
