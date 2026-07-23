"""MQTT bridge tests: payload unit tests plus integration against a real
Mosquitto broker (skipped cleanly when the ``mosquitto`` binary is absent).

The broker fixture runs an actual ``mosquitto`` subprocess on an ephemeral
port — the integration tests exercise real MQTT wire behavior: retained
status, the Last Will registration, QoS handshakes, and (in the cancellation
test) the QA-9 discipline that a bridge cancelled mid-flood must stop
promptly.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

from llrpkit.inventory import TagReport
from llrpkit.reader import Reader
from tests.test_hardening import make_emulator

pytest.importorskip("aiomqtt")

import aiomqtt

from llrpkit.mqtt import MQTTBridge, _resurface_swallowed_cancel, tag_payload

MOSQUITTO = shutil.which("mosquitto")
needs_broker = pytest.mark.skipif(MOSQUITTO is None, reason="mosquitto broker not installed")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(name="broker_port", scope="module")
def fixture_broker_port(tmp_path_factory: pytest.TempPathFactory) -> Iterator[int]:
    if MOSQUITTO is None:
        pytest.skip("mosquitto broker not installed")
    port = _free_port()
    conf = tmp_path_factory.mktemp("mosq") / "mosquitto.conf"
    conf.write_text(f"listener {port} 127.0.0.1\nallow_anonymous true\n")
    proc = subprocess.Popen(
        [MOSQUITTO, "-c", str(conf)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:  # wait until the listener accepts
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("mosquitto did not start")
        yield port
    finally:
        proc.terminate()
        proc.wait(timeout=5)


# --- unit: payload shape ----------------------------------------------------


def test_tag_payload_shape_and_units() -> None:
    tag = TagReport(
        epc=bytes.fromhex("e28011702000112233445566"),
        antenna=3,
        rssi_dbm=-52.25,
        phase_deg=123.456,
        doppler_hz=-12.5,
        channel_index=7,
        tid=bytes.fromhex("e2801170"),
    )
    row = tag_payload(tag, "10.0.0.9:5084")
    assert row["reader"] == "10.0.0.9:5084"
    assert row["epc"] == "e28011702000112233445566"
    assert row["antenna"] == 3
    assert row["rssi_dbm"] == -52.25
    assert row["phase_deg"] == 123.5  # rounded for the wire
    assert row["doppler_hz"] == -12.5
    assert row["channel"] == 7
    assert row["tid"] == "e2801170"
    assert isinstance(row["at"], float)


def test_tag_payload_optional_fields_are_null() -> None:
    row = tag_payload(TagReport(epc=b"\xe2" + b"\x00" * 11), "r")
    assert row["antenna"] is None
    assert row["phase_deg"] is None
    assert row["tid"] is None
    assert json.dumps(row)  # JSON-serializable as-is


def test_topics_and_status_payload_shape() -> None:
    bridge = MQTTBridge("broker.local", base_topic="rfid/door-1")
    assert bridge.tags_topic == "rfid/door-1/tags"
    assert bridge.status_topic == "rfid/door-1/status"
    body = json.loads(bridge._status_payload("online", "r1"))
    assert body["status"] == "online"
    assert body["reader"] == "r1"
    assert body["topic"] == "rfid/door-1/tags"


# --- unit: the swallowed-cancel resurface guard (QA-11) ---------------------


async def test_resurface_guard_brings_back_a_swallowed_cancel() -> None:
    """A dependency that eats CancelledError must not un-cancel the bridge."""

    async def victim() -> str:
        # a third-party wait_for just swallowed the cancellation...
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(30)
        _resurface_swallowed_cancel()  # ...and the guard brings it back
        return "survived"

    task = asyncio.create_task(victim())
    await asyncio.sleep(0.05)
    task.cancel()
    done, _ = await asyncio.wait({task}, timeout=2.0)
    assert done
    assert task.cancelled()


async def test_resurface_guard_is_a_noop_without_pending_cancel() -> None:
    _resurface_swallowed_cancel()  # runs inside the test task; must not raise


# --- integration: a real broker, a real reader (emulator) -------------------


@pytest.fixture(name="subscriber")
async def fixture_subscriber(broker_port: int) -> AsyncIterator[aiomqtt.Client]:
    async with aiomqtt.Client("127.0.0.1", broker_port, identifier="qa-sub") as sub:
        await sub.subscribe("bridge-test/#", qos=1)
        yield sub


@needs_broker
async def test_bridge_publishes_tags_status_and_stops_promptly(
    broker_port: int, subscriber: aiomqtt.Client
) -> None:
    async with make_emulator() as emu:
        reader = Reader("127.0.0.1", emu.port)
        await reader.connect()
        bridge = MQTTBridge(
            "127.0.0.1", broker_port, base_topic="bridge-test", qos=1, client_id="qa-bridge"
        )
        task = asyncio.create_task(
            bridge.run(reader, reader_label="dock1", search_mode=2, session=1)
        )
        try:
            statuses: list[dict[str, object]] = []
            tags: list[dict[str, object]] = []
            async with asyncio.timeout(10):
                async for message in subscriber.messages:
                    body = json.loads(bytes(message.payload))
                    if str(message.topic).endswith("/status"):
                        statuses.append(body)
                    else:
                        tags.append(body)
                    if len(tags) >= 10 and statuses:
                        break
            assert statuses[0]["status"] == "online"
            assert statuses[0]["reader"] == "dock1"
            first = tags[0]
            assert str(first["epc"]).startswith("e233")  # the emulated population
            assert first["reader"] == "dock1"
            assert first["antenna"] in (1, 2)
            assert isinstance(first["rssi_dbm"], float)

            # cancellation mid-flood must be prompt (QA-9 discipline holds
            # through the MQTT layer too) and must say a retained goodbye
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=5.0)
            assert done, "bridge did not stop promptly after cancel"
            assert bridge.published >= 10
            async with asyncio.timeout(5):
                async for message in subscriber.messages:
                    if str(message.topic).endswith("/status"):
                        body = json.loads(bytes(message.payload))
                        if body["status"] == "offline":
                            break
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await reader.close()


@needs_broker
async def test_bridge_offline_status_is_retained_after_clean_exit(broker_port: int) -> None:
    """A late subscriber must still learn the bridge is gone (retained status)."""
    async with make_emulator() as emu, Reader("127.0.0.1", emu.port) as reader:
        bridge = MQTTBridge("127.0.0.1", broker_port, base_topic="bridge-retained")
        published = await bridge.run(reader, search_mode=2, session=1, duration=0.8)
        assert published > 0
    # bridge is long gone; a brand-new subscriber reads the retained status
    async with aiomqtt.Client("127.0.0.1", broker_port, identifier="late-sub") as sub:
        await sub.subscribe("bridge-retained/status", qos=1)
        async with asyncio.timeout(5):
            async for message in sub.messages:
                assert message.retain, "status must be retained for late subscribers"
                assert json.loads(bytes(message.payload))["status"] == "offline"
                break


@needs_broker
def test_cli_inventory_publishes_to_mqtt(broker_port: int) -> None:
    """End to end through the console entry point: reader → CLI → broker."""
    from typer.testing import CliRunner

    from llrpkit.cli import app
    from tests.test_cli_e2e import EmulatorThread

    runner = CliRunner()
    with EmulatorThread() as emu:
        result = runner.invoke(
            app,
            [
                "inventory",
                "127.0.0.1",
                "--port",
                str(emu.port),
                "--count",
                "15",
                "--duration",
                "10",
                "--mqtt-broker",
                f"127.0.0.1:{broker_port}",
                "--mqtt-topic",
                "cli-test",
            ],
        )
    assert result.exit_code == 0, result.output
    assert "15 tag report(s) published" in result.output

    async def read_retained_status() -> dict[str, object]:
        async with aiomqtt.Client("127.0.0.1", broker_port, identifier="cli-sub") as sub:
            await sub.subscribe("cli-test/status", qos=1)
            async with asyncio.timeout(5):
                async for message in sub.messages:
                    return dict(json.loads(bytes(message.payload)))
        raise AssertionError("no retained status")

    status = asyncio.run(read_retained_status())
    assert status["status"] == "offline"  # the CLI said a clean goodbye


@needs_broker
def test_example_script_bridges_against_real_broker(broker_port: int) -> None:
    from tests.test_cli_e2e import EmulatorThread

    script = Path(__file__).parents[1] / "examples" / "mqtt_bridge.py"
    with EmulatorThread() as emu:
        proc = subprocess.run(
            [
                sys.executable,
                str(script),
                "--host",
                "127.0.0.1",
                "--port",
                str(emu.port),
                "--broker-port",
                str(broker_port),
                "--seconds",
                "1.0",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "published" in proc.stdout


def test_example_script_explains_when_no_broker() -> None:
    """The generic example runner has no broker; the script must exit 0."""
    script = Path(__file__).parents[1] / "examples" / "mqtt_bridge.py"
    port = _free_port()  # nothing listens here
    proc = subprocess.run(
        [sys.executable, str(script), "--broker-port", str(port), "--seconds", "1"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0
    assert "no MQTT broker" in proc.stdout
