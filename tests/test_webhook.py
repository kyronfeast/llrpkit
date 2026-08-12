"""Webhook sink tests against a real in-process receiver.

The receiver implements the exact contract of the downstream consumer this
sink was built for (an Odoo controller): token authenticated in the body,
``epc`` the only required event key, ``200 {"ok": true, "created": N}`` /
``403`` / ``400`` responses. Serving it with real uvicorn keeps the whole
HTTP path honest.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest

pytest.importorskip("httpx")

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from llrpkit.emulator import EmulatedTag
from llrpkit.inventory import TagReport
from llrpkit.presence import PresenceEvent
from llrpkit.reader import Reader
from llrpkit.webhook import WebhookAuthError, WebhookSink, presence_entry, read_entry
from tests.test_hardening import make_emulator

TOKEN = "s3cret-token"


@dataclass
class Receiver:
    port: int
    bodies: list[dict[str, Any]] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/gielow/rfid/event"

    @property
    def entries(self) -> list[dict[str, Any]]:
        return [entry for body in self.bodies for entry in body["events"]]


def make_app(receiver: Receiver) -> FastAPI:
    app = FastAPI()

    @app.post("/gielow/rfid/event")
    async def event(request: Request) -> Any:
        body = await request.json()
        if body.get("token") != TOKEN:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        events = body.get("events")
        if not isinstance(events, list) or any("epc" not in e for e in events):
            return JSONResponse({"error": "malformed"}, status_code=400)
        receiver.bodies.append(body)
        return {"ok": True, "created": len(events)}

    return app


@pytest.fixture(name="receiver")
async def fixture_receiver() -> AsyncIterator[Receiver]:
    receiver = Receiver(port=0)
    server = uvicorn.Server(
        uvicorn.Config(
            make_app(receiver), host="127.0.0.1", port=0, log_level="warning", lifespan="off"
        )
    )
    task = asyncio.create_task(server.serve())
    try:
        deadline = asyncio.get_running_loop().time() + 10.0
        while not server.started:
            if asyncio.get_running_loop().time() > deadline:
                pytest.fail("receiver did not start")
            await asyncio.sleep(0.05)
        receiver.port = server.servers[0].sockets[0].getsockname()[1]
        yield receiver
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10.0)


# --- entry shapes are pinned -------------------------------------------------


def test_webhook_entry_schemas_are_pinned() -> None:
    keys = ["epc", "kind", "antenna", "rssi", "dwell_s", "reads", "at"]
    edge = presence_entry(
        PresenceEvent(kind="departed", epc=b"\xe2" * 12, antenna=2, at=5.0, dwell_s=3.25, reads=9)
    )
    assert list(edge) == keys
    assert edge["kind"] == "departed"
    assert edge["dwell_s"] == 3.25
    assert edge["rssi"] is None
    read = read_entry(TagReport(epc=b"\xe2" * 12, antenna=1, rssi_dbm=-50.25))
    assert list(read) == keys
    assert read["kind"] == "read"
    assert read["rssi"] == -50.25
    assert read["dwell_s"] is None


# --- end to end --------------------------------------------------------------


async def test_sink_posts_presence_events_with_token(receiver: Receiver) -> None:
    async with make_emulator() as emu, Reader("127.0.0.1", emu.port) as reader:
        sink = WebhookSink(receiver.url, token=TOKEN, flush_interval=0.3)
        posted = await sink.run(reader, search_mode=2, session=1, duration=1.5)
    assert posted > 0
    assert sink.posted == posted
    assert receiver.bodies, "receiver saw no batches"
    body = receiver.bodies[0]
    assert body["token"] == TOKEN
    assert body["reader"].startswith("127.0.0.1:")
    kinds = {entry["kind"] for entry in receiver.entries}
    assert kinds == {"arrived"}  # nothing departed within the window
    assert all(entry["epc"].startswith("e2") for entry in receiver.entries)


async def test_sink_includes_raw_reads_and_batches(receiver: Receiver) -> None:
    async with make_emulator(reads_per_sec=400.0) as emu, Reader("127.0.0.1", emu.port) as reader:
        sink = WebhookSink(
            receiver.url, token=TOKEN, include_tags=True, batch_max=5, flush_interval=0.2
        )
        posted = await sink.run(reader, search_mode=2, session=1, duration=1.2)
    assert posted >= 10
    assert sink.batches >= 2, "small batch_max must produce multiple POSTs"
    assert all(len(body["events"]) <= 5 for body in receiver.bodies)
    kinds = {entry["kind"] for entry in receiver.entries}
    assert "read" in kinds
    assert "arrived" in kinds


async def test_wrong_token_raises_auth_error(receiver: Receiver) -> None:
    async with make_emulator() as emu, Reader("127.0.0.1", emu.port) as reader:
        sink = WebhookSink(receiver.url, token="wrong", flush_interval=0.2)
        with pytest.raises(WebhookAuthError, match="403"):
            await sink.run(reader, search_mode=2, session=1, duration=3.0)
    assert receiver.bodies == []  # nothing was accepted


async def test_unreachable_endpoint_buffers_then_delivers() -> None:
    """Entries survive an outage: the sink retries and the late receiver
    still gets everything (bounded, drop-oldest beyond the cap)."""
    # reserve a port with nothing listening yet
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    receiver = Receiver(port=port)
    tag = EmulatedTag(epc=b"\xe2\x55" + b"\x00" * 10, antennas=(1,))
    async with make_emulator(tags=[tag]) as emu, Reader("127.0.0.1", emu.port) as reader:
        sink = WebhookSink(receiver.url, token=TOKEN, flush_interval=0.2)
        run_task = asyncio.create_task(sink.run(reader, search_mode=2, session=1, duration=4.0))
        await asyncio.sleep(1.0)  # arrival happens while the endpoint is down
        server = uvicorn.Server(
            uvicorn.Config(
                make_app(receiver),
                host="127.0.0.1",
                port=port,
                log_level="warning",
                lifespan="off",
            )
        )
        server_task = asyncio.create_task(server.serve())
        try:
            posted = await run_task
            assert posted >= 1, "buffered arrival must be delivered once the endpoint is up"
            assert any(e["kind"] == "arrived" for e in receiver.entries)
        finally:
            server.should_exit = True
            await asyncio.wait_for(server_task, 10.0)


async def test_cancellation_is_prompt_and_cleans_up(receiver: Receiver) -> None:
    async with make_emulator(reads_per_sec=300.0) as emu:
        reader = Reader("127.0.0.1", emu.port)
        await reader.connect()
        try:
            sink = WebhookSink(receiver.url, token=TOKEN, include_tags=True, flush_interval=0.2)
            task = asyncio.create_task(sink.run(reader, search_mode=2, session=1))
            await asyncio.sleep(1.0)
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=5.0)
            assert done, "webhook sink did not stop promptly after cancel"
            from llrpkit.client import check_status
            from llrpkit.protocol import messages

            response = check_status(await reader.client.transact(messages.GET_ROSPECS()))
            assert isinstance(response, messages.GET_ROSPECS_RESPONSE)
            assert response.ro_specs == [], "cancelled sink must delete its ROSpec"
        finally:
            await reader.close()


def test_cli_inventory_posts_to_webhook() -> None:
    """End to end through the console entry point: reader -> CLI -> receiver."""
    import threading

    from typer.testing import CliRunner

    from llrpkit.cli import app
    from tests.test_cli_e2e import EmulatorThread

    receiver = Receiver(port=0)
    started = threading.Event()
    server_box: dict[str, uvicorn.Server] = {}

    def serve() -> None:
        async def main() -> None:
            server = uvicorn.Server(
                uvicorn.Config(
                    make_app(receiver),
                    host="127.0.0.1",
                    port=0,
                    log_level="warning",
                    lifespan="off",
                )
            )
            server_box["server"] = server
            task = asyncio.get_running_loop().create_task(server.serve())
            while not server.started:
                await asyncio.sleep(0.05)
            receiver.port = server.servers[0].sockets[0].getsockname()[1]
            started.set()
            await task

        asyncio.run(main())

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert started.wait(10.0), "receiver thread did not start"
    try:
        runner = CliRunner()
        with EmulatorThread() as emu:
            result = runner.invoke(
                app,
                [
                    "inventory",
                    "127.0.0.1",
                    "--port",
                    str(emu.port),
                    "--webhook",
                    receiver.url,
                    "--webhook-token",
                    TOKEN,
                    "--duration",
                    "1.5",
                ],
            )
        assert result.exit_code == 0, result.output
        assert "posting →" in result.output
        assert "event(s) posted" in result.output
        assert receiver.entries, "receiver saw nothing from the CLI"
        assert all("epc" in entry for entry in receiver.entries)
    finally:
        server_box["server"].should_exit = True
        thread.join(timeout=10.0)
