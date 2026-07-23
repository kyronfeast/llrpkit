"""Demo lifespan + WebSocket, tested against a real in-process uvicorn server.

History: this file originally used Starlette's thread-portal ``TestClient``,
which exhibited a rare internal deadlock during QA stress runs (an HTTP
request wedged while the application loop sat idle; no llrpkit frames on any
stack). Serving the app with real uvicorn in the test's own event loop and
speaking real HTTP/WebSocket removes the portal entirely — and is higher
fidelity anyway: this arrangement exercises uvicorn's WebSocket protocol
backend, the exact layer whose missing dependency once broke the live demo
while the portal-based tests stayed green.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest
import uvicorn
from websockets.asyncio.client import connect as ws_connect

from llrpkit.dashboard import create_demo_app


@pytest.fixture(name="demo_port")
async def fixture_demo_port() -> AsyncIterator[int]:
    app = create_demo_app(tags=6, rate=300.0)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    )
    task = asyncio.create_task(server.serve())
    try:
        deadline = asyncio.get_running_loop().time() + 10.0
        while not server.started:
            if asyncio.get_running_loop().time() > deadline:
                pytest.fail("uvicorn did not start")
            await asyncio.sleep(0.05)
        yield server.servers[0].sockets[0].getsockname()[1]
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10.0)


@pytest.mark.timeout(60)
async def test_demo_lifespan_websocket_and_autostart(demo_port: int) -> None:
    base = f"http://127.0.0.1:{demo_port}"
    async with httpx.AsyncClient(base_url=base) as http:
        state = (await http.get("/api/state")).json()
        assert state["demo"] is True
        reader = state["readers"][0]
        assert reader["inventory_running"] is True, "demo must autostart its inventory"
        rid = reader["id"]

        # real WebSocket, through uvicorn's actual protocol backend
        async with ws_connect(f"ws://127.0.0.1:{demo_port}/ws") as ws:
            first = json.loads(await asyncio.wait_for(ws.recv(), 5.0))
            assert first["type"] == "state"
            assert first["readers"][0]["id"] == rid
            seen: set[str] = set()
            tag_row = None
            for _ in range(400):
                event = json.loads(await asyncio.wait_for(ws.recv(), 5.0))
                seen.add(event["type"])
                if event["type"] == "tags" and tag_row is None:
                    tag_row = event["items"][0]
                if {"tags", "stats", "health"} <= seen:
                    break
            assert {"tags", "stats", "health"} <= seen
            assert tag_row is not None
            assert tag_row["epc"].startswith("e2")

        async def total() -> int:
            health = (await http.get(f"/api/readers/{rid}/health")).json()
            return int(health["stats"]["total"])

        deadline = asyncio.get_running_loop().time() + 5.0
        while asyncio.get_running_loop().time() < deadline:
            if await total() > 10:
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("demo inventory produced no reads")
