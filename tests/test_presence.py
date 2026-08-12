"""Presence tracking: arrive/depart edges from raw read streams."""

from __future__ import annotations

import asyncio
import json

import pytest

from llrpkit.inventory import TagReport
from llrpkit.presence import PresenceTracker

EPC_1 = b"\xe2\x11" + b"\x00" * 10
EPC_2 = b"\xe2\x22" + b"\x00" * 10


def read(epc: bytes, antenna: int = 1) -> TagReport:
    return TagReport(epc=epc, antenna=antenna)


def test_arrival_then_departure_with_dwell() -> None:
    now = 100.0
    tracker = PresenceTracker(depart_after=2.0, clock=lambda: now)
    events = tracker.observe(read(EPC_1, antenna=3))
    assert [e.kind for e in events] == ["arrived"]
    assert events[0].antenna == 3
    assert tracker.present == {EPC_1}
    now = 101.5
    tracker.observe(read(EPC_1))
    assert tracker.check() == []  # still chatting
    now = 104.0  # 2.5 s of silence
    departed = tracker.check()
    assert [e.kind for e in departed] == ["departed"]
    assert departed[0].dwell_s == pytest.approx(1.5)
    assert departed[0].reads == 2
    assert tracker.present == set()


def test_min_reads_debounce_suppresses_stray_reads() -> None:
    now = 0.0
    tracker = PresenceTracker(depart_after=1.0, min_reads=3, clock=lambda: now)
    assert tracker.observe(read(EPC_1)) == []
    assert tracker.observe(read(EPC_1)) == []
    arrived = tracker.observe(read(EPC_1))
    assert [e.kind for e in arrived] == ["arrived"]
    # a one-off stray never announces and never "departs"
    assert tracker.observe(read(EPC_2)) == []
    now = 5.0
    assert [e.kind for e in tracker.check()] == ["departed"]  # only EPC_1


def test_reappearance_is_a_fresh_arrival() -> None:
    now = 0.0
    tracker = PresenceTracker(depart_after=1.0, clock=lambda: now)
    tracker.observe(read(EPC_1))
    now = 3.0
    assert [e.kind for e in tracker.check()] == ["departed"]
    events = tracker.observe(read(EPC_1))
    assert [e.kind for e in events] == ["arrived"]


async def test_cli_events_mode_reports_arrivals() -> None:
    """End to end: emulator -> inventory --events prints arrive edges."""
    from typer.testing import CliRunner

    from llrpkit.cli import app
    from tests.test_cli_e2e import EmulatorThread

    runner = CliRunner()
    with EmulatorThread() as emu:
        result = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: runner.invoke(
                app,
                [
                    "inventory",
                    "127.0.0.1",
                    "--port",
                    str(emu.port),
                    "--events",
                    "--duration",
                    "2.5",
                    "--depart-after",
                    "0.8",
                ],
            ),
        )
    assert result.exit_code == 0, result.output
    assert "presence events" in result.output
    assert "+ arrived" in result.output
    assert "present)" in result.output


@pytest.mark.timeout(60)
async def test_mqtt_bridge_publishes_presence_edges() -> None:
    """arrived + departed edges reach {base}/events on a real broker."""
    aiomqtt = pytest.importorskip("aiomqtt")
    from tests.test_mqtt import MOSQUITTO
    from tests.test_mqtt import fixture_broker_port as _  # noqa: F401  (skip logic lives there)

    if MOSQUITTO is None:
        pytest.skip("mosquitto broker not installed")
    import shutil
    import socket
    import subprocess
    import tempfile
    import time as _time

    from llrpkit.emulator import EmulatedTag
    from llrpkit.mqtt import MQTTBridge
    from llrpkit.reader import Reader
    from tests.test_hardening import make_emulator

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as conf:
        conf.write(f"listener {port} 127.0.0.1\nallow_anonymous true\n")
    proc = subprocess.Popen(
        [shutil.which("mosquitto") or "mosquitto", "-c", conf.name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = _time.monotonic() + 5
        while _time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                await asyncio.sleep(0.1)
        tag = EmulatedTag(epc=b"\xe2\x77" + b"\x00" * 10, antennas=(1,))
        async with make_emulator(tags=[tag], reads_per_sec=120.0) as emu:
            reader = Reader("127.0.0.1", emu.port)
            await reader.connect()
            bridge = MQTTBridge(
                "127.0.0.1", port, base_topic="edges", publish_events=True, depart_after=0.6
            )
            task: asyncio.Task[int] | None = None
            try:
                async with aiomqtt.Client("127.0.0.1", port, identifier="edge-sub") as sub:
                    await sub.subscribe("edges/events", qos=1)  # BEFORE the bridge starts
                    task = asyncio.create_task(bridge.run(reader, search_mode=2, session=1))
                    async with asyncio.timeout(10):
                        async for message in sub.messages:
                            body = json.loads(bytes(message.payload))
                            assert body["event"] == "arrived"
                            assert body["epc"] == tag.epc.hex()
                            break
                    # silence the field -> a departed edge must follow
                    await emu.set_antenna_connected(1, False)
                    async with asyncio.timeout(10):
                        async for message in sub.messages:
                            body = json.loads(bytes(message.payload))
                            if body["event"] == "departed":
                                assert body["epc"] == tag.epc.hex()
                                assert body["dwell_s"] is not None
                                break
            finally:
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await reader.close()
    finally:
        proc.terminate()
        proc.wait(timeout=5)


async def test_ticked_stream_survives_quiet_and_cancels_cleanly() -> None:
    """Quiet ticks must NOT tear down the stream (the anext-cancel trap),
    and cancellation during a quiet period must still delete the ROSpec."""
    import contextlib

    from llrpkit.client import check_status
    from llrpkit.presence import ticked_stream
    from llrpkit.protocol import messages
    from llrpkit.reader import Reader
    from tests.test_hardening import make_emulator

    async with make_emulator(tags=[]) as emu:  # a silent field: ticks only
        reader = Reader("127.0.0.1", emu.port)
        await reader.connect()
        try:
            ticks = 0

            async def consume() -> None:
                nonlocal ticks
                ticked = ticked_stream(reader.inventory(session=1), tick=0.1)
                async with contextlib.aclosing(ticked):
                    async for tag in ticked:
                        assert tag is None  # no tags exist; only heartbeats
                        ticks += 1

            task = asyncio.create_task(consume())
            await asyncio.sleep(1.0)
            assert ticks >= 4, f"stream died during quiet period after {ticks} ticks"
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=3.0)
            assert done, "cancellation must be prompt from a quiet tick"
            response = check_status(await reader.client.transact(messages.GET_ROSPECS()))
            assert isinstance(response, messages.GET_ROSPECS_RESPONSE)
            assert response.ro_specs == [], "quiet-cancel must still delete the ROSpec"
        finally:
            await reader.close()


def test_events_payload_schema_is_pinned() -> None:
    """Downstream consumers build against these exact keys — never rename."""
    pytest.importorskip("aiomqtt")
    from llrpkit.mqtt import event_payload
    from llrpkit.presence import PresenceEvent

    arrived = event_payload(
        PresenceEvent(kind="arrived", epc=b"\xe2\x01" + b"\x00" * 10, antenna=3, at=1.0, reads=2),
        "dock1",
    )
    assert list(arrived) == ["event", "reader", "epc", "antenna", "dwell_s", "reads", "at"]
    assert arrived["event"] == "arrived"
    assert arrived["reader"] == "dock1"
    assert arrived["epc"] == "e201" + "00" * 10
    assert arrived["antenna"] == 3
    assert arrived["dwell_s"] is None
    assert arrived["reads"] == 2
    departed = event_payload(
        PresenceEvent(
            kind="departed",
            epc=b"\xe2\x02" + b"\x00" * 10,
            antenna=None,
            at=9.0,
            dwell_s=4.567,
            reads=18,
        ),
        "dock1",
    )
    assert departed["dwell_s"] == 4.57
    assert departed["antenna"] is None


def test_tags_payload_schema_is_pinned() -> None:
    pytest.importorskip("aiomqtt")
    from llrpkit.mqtt import tag_payload

    row = tag_payload(TagReport(epc=b"\xe2" * 12), "r1")
    assert list(row) == [
        "reader",
        "epc",
        "antenna",
        "rssi_dbm",
        "phase_deg",
        "doppler_hz",
        "channel",
        "tid",
        "at",
    ]
