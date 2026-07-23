"""Production-hardening regression tests.

Each test here reproduces a defect found during the pre-release QA pass; the
docstrings state the original faulty behavior so the fixes stay honest.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from llrpkit.client import LLRPClient, check_status
from llrpkit.emulator import EmulatedTag, LLRPEmulator
from llrpkit.exceptions import LLRPConnectionError
from llrpkit.health import HealthMonitor
from llrpkit.inventory import TagReport, build_rospec
from llrpkit.protocol import messages, params
from llrpkit.reader import Reader

TAGS = [EmulatedTag(epc=bytes([0xE2, 0x33, i] + [0] * 9), antennas=(1 + i % 2,)) for i in range(4)]


def make_emulator(**kwargs: object) -> LLRPEmulator:
    kwargs.setdefault("tags", TAGS)
    kwargs.setdefault("reads_per_sec", 300.0)
    return LLRPEmulator(**kwargs)  # type: ignore[arg-type]


# --- QA-1: client lifecycle truthfulness -----------------------------------


async def test_client_reports_disconnected_after_clean_close() -> None:
    """BUG: after close(), `connected` stayed True and transact() wrote to a
    dead socket, because _writer was never cleared on the clean-close path."""
    async with make_emulator() as emu:
        client = LLRPClient("127.0.0.1", emu.port)
        await client.connect()
        before = client.connected
        assert before
        await client.close()
        after = client.connected  # separate reads: keep mypy's narrowing honest
        assert not after
        with pytest.raises(LLRPConnectionError):
            await client.transact(messages.GET_ROSPECS(), timeout=1.0)


async def test_client_can_reconnect_after_close() -> None:
    """BUG: connect() after close() raised 'already connected' forever."""
    async with make_emulator() as emu:
        client = LLRPClient("127.0.0.1", emu.port)
        await client.connect()
        await client.close()
        await client.connect()  # must be reusable
        check_status(await client.transact(messages.GET_ROSPECS()))
        await client.close()
        assert not client.connected


async def test_client_usable_after_refused_connection() -> None:
    """BUG: a refused connection (second client) left _writer set, so the
    object claimed to be connected and could never connect again."""
    async with make_emulator() as emu:
        first = LLRPClient("127.0.0.1", emu.port)
        await first.connect()
        second = LLRPClient("127.0.0.1", emu.port)
        with pytest.raises(LLRPConnectionError, match="refused"):
            await second.connect()
        assert not second.connected
        await first.close()
        await asyncio.sleep(0.05)  # let the emulator free its client slot
        await second.connect()  # the same object must be able to retry
        check_status(await second.transact(messages.GET_ROSPECS()))
        await second.close()


# --- QA-2: unbounded queues on a long-lived connection ---------------------


async def test_event_queue_is_bounded_with_drop_accounting() -> None:
    """BUG: client.events grew without bound if nobody consumed it — a slow
    leak on any long-lived connection that receives notifications."""
    client = LLRPClient("127.0.0.1", 1, max_queued_events=10)
    event = messages.READER_EVENT_NOTIFICATION(
        reader_event_notification_data=params.ReaderEventNotificationData(
            timestamp=params.UTCTimestamp(microseconds=1)
        )
    )
    for _ in range(50):
        client._dispatch(event)
    assert client.events.qsize() == 10
    assert client.dropped_events == 40


async def test_report_queue_is_bounded_with_drop_accounting() -> None:
    client = LLRPClient("127.0.0.1", 1, max_queued_reports=5)
    report = messages.RO_ACCESS_REPORT()
    for _ in range(12):
        client._dispatch(report)
    assert client.reports.qsize() == 5
    assert client.dropped_reports == 7


# --- QA-3: health metrics must be accurate at production read rates --------


def test_reads_per_sec_is_accurate_at_high_rates() -> None:
    """BUG: AntennaHealth kept only 512 recent timestamps, silently capping
    the reported rate at 256 reads/s per port (window is 2 s)."""
    clock_now = 1000.0
    monitor = HealthMonitor(antennas=(1,), clock=lambda: clock_now)
    total = 900
    for i in range(total):
        clock_now = 1000.0 + (i / total) * 2.0  # spread evenly over the 2 s window
        monitor.observe(TagReport(epc=b"\x01" * 12, antenna=1))
    clock_now = 1002.0
    rate = monitor.antennas[1].reads_per_sec(clock_now)
    assert rate == pytest.approx(total / 2.0, rel=0.05), rate


# --- QA-4: dashboard-style cancellation must clean up reader state ---------


async def test_registry_stop_inventory_deletes_rospec_on_reader() -> None:
    """The registry stops streams by task cancellation; the ROSpec teardown
    in the generator's finally must survive that path, not just break/return."""
    from llrpkit.dashboard.registry import ReaderRegistry

    async with make_emulator() as emu:
        registry = ReaderRegistry()
        managed = await registry.add("127.0.0.1", emu.port)
        await managed.start_inventory({"search_mode": 2})
        queue = registry.hub.subscribe()
        try:
            for _ in range(200):  # wait until tags actually flow
                event = await asyncio.wait_for(queue.get(), 5.0)
                if event["type"] == "tags":
                    break
            else:
                pytest.fail("no tags arrived")
            await managed.stop_inventory()
            response = check_status(await managed.reader.client.transact(messages.GET_ROSPECS()))
            assert isinstance(response, messages.GET_ROSPECS_RESPONSE)
            assert response.ro_specs == [], "cancelled stream must delete its ROSpec"
        finally:
            registry.hub.unsubscribe(queue)
            await registry.shutdown()
        assert not managed.reader.client.connected


async def test_registry_remove_survives_close_errors() -> None:
    """remove() must fully detach the reader even if the LLRP close fails."""
    async with make_emulator() as emu:
        from llrpkit.dashboard.registry import ReaderRegistry

        registry = ReaderRegistry()
        managed = await registry.add("127.0.0.1", emu.port)
        await emu.stop()  # yank the transport out from under the client
        await asyncio.sleep(0.05)
        await registry.remove(managed.id)  # must not raise, must not leak
        assert registry.readers == {}
        assert managed._events_task is None
        assert managed._health_task is None


# --- QA-5: hostile input on the wire ---------------------------------------


async def test_emulator_survives_garbage_and_keeps_serving() -> None:
    """A port-scanner or confused client sending junk must not wedge the
    emulator or poison the next legitimate session."""
    async with make_emulator() as emu:
        reader, writer = await asyncio.open_connection("127.0.0.1", emu.port)
        await reader.readexactly(10)  # its ConnectionAttemptEvent header
        writer.write(b"\x00" * 64)  # nonsense framing
        await writer.drain()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(reader.read(), 2.0)  # emulator drops us
        writer.close()
        await asyncio.sleep(0.05)
        # ...and a real client connects fine afterwards
        client = LLRPClient("127.0.0.1", emu.port)
        await client.connect()
        check_status(await client.transact(messages.GET_ROSPECS()))
        await client.close()


# --- QA-6: correlation under concurrency -----------------------------------


async def test_fifty_concurrent_transactions_correlate_correctly() -> None:
    async with make_emulator() as emu:
        client = LLRPClient("127.0.0.1", emu.port)
        await client.connect()
        try:
            rospec = build_rospec(ro_spec_id=42)
            check_status(await client.transact(messages.ADD_ROSPEC(ro_spec=rospec)))
            responses = await asyncio.gather(
                *(client.transact(messages.GET_ROSPECS()) for _ in range(50))
            )
            for response in responses:
                checked = check_status(response)
                assert isinstance(checked, messages.GET_ROSPECS_RESPONSE)
                assert [s.ro_spec_id for s in checked.ro_specs] == [42]
        finally:
            await client.close()


# --- QA-7: emulator report loop must recover from a crashed task -----------


async def test_emulator_reporting_restarts_if_task_died() -> None:
    """_sync_reporting treated a completed-but-set task as alive, so a
    crashed report loop silently disabled reporting for the session."""
    async with make_emulator() as emu:
        client = LLRPClient("127.0.0.1", emu.port)
        await client.connect()
        try:
            rospec = build_rospec(ro_spec_id=9)
            check_status(await client.transact(messages.ADD_ROSPEC(ro_spec=rospec)))
            check_status(await client.transact(messages.ENABLE_ROSPEC(ro_spec_id=9)))
            check_status(await client.transact(messages.START_ROSPEC(ro_spec_id=9)))
            assert emu._report_task is not None
            emu._report_task.cancel()  # simulate an unexpected task death
            with contextlib.suppress(asyncio.CancelledError):
                await emu._report_task
            emu._sync_reporting()  # a state change must notice and restart
            report = await asyncio.wait_for(client.reports.get(), 3.0)
            assert isinstance(report, messages.RO_ACCESS_REPORT)
        finally:
            await client.close()


# --- QA-8: clear errors for invalid tuning input ---------------------------


def test_build_rospec_rejects_invalid_session_clearly() -> None:
    """BUG: session=5 surfaced as a cryptic bit-level encode error instead of
    a clear ValueError at the API boundary."""
    with pytest.raises(ValueError, match="session"):
        build_rospec(session=5)


# --- QA-9: task cancellation must never be swallowed by a hot queue ---------
#
# Python 3.11's asyncio.wait_for(fut, t) has a known race
# (python/cpython#86296): if Task.cancel() lands while `fut` has already
# completed, the CancelledError is caught internally and the *value* is
# returned — the cancellation is consumed. llrpkit's streaming loops polled
# their queues through wait_for, so cancelling an inventory stream at the
# exact moment a report was queued left the stream running forever and
# deadlocked the canceller (`await task` after .cancel() never returned).
# This was the intermittent full-suite hang: the dashboard stop endpoint
# cancelling the inventory task while the emulator streamed at 300 reads/s.


async def test_inventory_cancel_wins_even_with_a_report_already_queued() -> None:
    """BUG: put_nowait + cancel in the same tick un-cancelled the stream."""
    async with make_emulator(tags=[]) as emu:  # no tags -> queue stays empty
        reader = Reader("127.0.0.1", emu.port)
        await reader.connect()
        try:

            async def consume() -> None:
                async for _ in reader.inventory(search_mode=2, session=1):
                    pass

            task = asyncio.create_task(consume())
            await asyncio.sleep(0.4)  # stream is up, parked on the empty queue
            assert not task.done(), task.exception() if task.done() else None
            # The race, made deterministic: a report lands and the cancel
            # arrives before the consumer runs again.
            reader.client.reports.put_nowait(messages.RO_ACCESS_REPORT())
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=3.0)
            assert done, "cancellation was swallowed; inventory stream still running"
            assert task.cancelled()
        finally:
            await reader.close()


async def test_events_generator_cancel_wins_even_with_an_event_queued() -> None:
    async with make_emulator() as emu:
        reader = Reader("127.0.0.1", emu.port)
        await reader.connect()
        try:

            async def consume() -> None:
                async for _ in reader.events():
                    pass

            task = asyncio.create_task(consume())
            await asyncio.sleep(0.3)  # parked on the empty events queue
            reader.client.events.put_nowait(
                messages.READER_EVENT_NOTIFICATION(
                    reader_event_notification_data=params.ReaderEventNotificationData(
                        timestamp=params.UTCTimestamp(microseconds=7)
                    )
                )
            )
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=3.0)
            assert done, "cancellation was swallowed; events stream still running"
            assert task.cancelled()
        finally:
            await reader.close()


# --- QA-10: cancelling connect() must not leak a half-open transport --------


async def test_cancelled_connect_releases_the_transport() -> None:
    """BUG: cancelling connect() while it waited for the ConnectionAttemptEvent
    skipped the abort path: the socket and read task leaked, and `connected`
    reported True on a client that never finished its handshake."""
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)  # silent peer
    port = server.sockets[0].getsockname()[1]
    try:
        client = LLRPClient("127.0.0.1", port)
        task = asyncio.create_task(client.connect())
        await asyncio.sleep(0.2)  # parked waiting for the (never-sent) event
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=3.0)
        assert done
        assert task.cancelled()
        assert not client.connected
        assert client._writer is None
        assert client._read_task is None
    finally:
        server.close()
        await server.wait_closed()
