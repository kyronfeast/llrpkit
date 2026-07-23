"""Dashboard API tests, run fully in-loop via the ASGI transport.

These deliberately avoid Starlette's thread-portal ``TestClient``: during QA a
rare deadlock was traced into the portal machinery (an HTTP request wedged
while the application loop sat idle, with no llrpkit frames on any stack).
Running the ASGI app directly in the test's event loop removes that layer
entirely; the two tests that genuinely need the portal (WebSocket, demo
lifespan) live in ``test_dashboard_portal.py`` under a tight timeout.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from llrpkit.dashboard.app import create_app
from llrpkit.dashboard.registry import ReaderRegistry
from llrpkit.emulator import LLRPEmulator, default_population


@dataclass
class DashboardContext:
    http: AsyncClient
    emulator: LLRPEmulator
    registry: ReaderRegistry
    rid: str


@pytest.fixture(name="ctx")
async def fixture_ctx(tmp_path: Path) -> AsyncIterator[DashboardContext]:
    emulator = LLRPEmulator(tags=default_population(8, 4), reads_per_sec=300.0, seed=3)
    await emulator.start()
    registry = ReaderRegistry()
    registry.demo = True
    app = create_app(registry, profile_dir=tmp_path / "profiles")
    managed = await registry.add("127.0.0.1", emulator.port)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://dash") as http:
        yield DashboardContext(http=http, emulator=emulator, registry=registry, rid=managed.id)
    await registry.shutdown()
    await emulator.stop()


async def eventually(
    probe: Callable[[], Awaitable[bool]], timeout: float = 5.0, interval: float = 0.1
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await probe():
            return
        await asyncio.sleep(interval)
    raise AssertionError("condition not met in time")


async def test_state_reports_the_emulated_reader(ctx: DashboardContext) -> None:
    state = (await ctx.http.get("/api/state")).json()
    assert state["demo"] is True
    reader = state["readers"][0]
    assert reader["connected"] is True
    assert reader["model_number"] == 700
    assert reader["is_impinj"] is True
    assert reader["max_antennas"] == 4
    assert reader["power_min_dbm"] == 10.0
    assert reader["power_max_dbm"] == 30.0
    assert reader["inventory_running"] is False


async def test_inventory_start_stats_and_stop(ctx: DashboardContext) -> None:
    response = await ctx.http.post(
        f"/api/readers/{ctx.rid}/inventory/start", json={"search_mode": 2, "session": 1}
    )
    info = response.json()
    assert info["inventory_running"] is True
    assert info["settings"]["search_mode"] == 2

    async def has_reads() -> bool:
        health = (await ctx.http.get(f"/api/readers/{ctx.rid}/health")).json()
        return int(health["stats"]["total"]) > 10

    await eventually(has_reads)
    health = (await ctx.http.get(f"/api/readers/{ctx.rid}/health")).json()
    assert health["stats"]["unique"] >= 4
    assert any(a["reads"] > 0 for a in health["antennas"].values())
    info = (await ctx.http.post(f"/api/readers/{ctx.rid}/inventory/stop")).json()
    assert info["inventory_running"] is False


async def test_hub_streams_tags_stats_and_health(ctx: DashboardContext) -> None:
    """The event stream the WebSocket relays, consumed at the hub level."""
    queue = ctx.registry.hub.subscribe()
    try:
        await ctx.http.post(f"/api/readers/{ctx.rid}/inventory/start", json={"include_phase": True})
        seen: set[str] = set()
        tag_batch = None
        for _ in range(400):
            event = await asyncio.wait_for(queue.get(), 5.0)
            seen.add(event["type"])
            if event["type"] == "tags" and tag_batch is None:
                tag_batch = event
            if {"tags", "stats", "health"} <= seen:
                break
        assert {"tags", "stats", "health"} <= seen
        assert tag_batch is not None
        row = tag_batch["items"][0]
        assert set(row) >= {"epc", "antenna", "rssi", "phase", "at"}
        assert row["epc"].startswith("e2")
    finally:
        ctx.registry.hub.unsubscribe(queue)
        await ctx.http.post(f"/api/readers/{ctx.rid}/inventory/stop")


async def test_modes_endpoint_serves_curated_guidance(ctx: DashboardContext) -> None:
    data = (await ctx.http.get(f"/api/readers/{ctx.rid}/modes")).json()
    by_id = {m["mode_id"]: m for m in data["modes"]}
    assert by_id[2]["name"] == "Dense Reader M4"
    assert "workhorse" in by_id[2]["summary"]
    assert by_id[1003]["autoset"] is True
    dense = (await ctx.http.get(f"/api/readers/{ctx.rid}/modes", params={"dense": True})).json()
    assert dense["suggestion"]["mode_id"] == 1002


async def test_temperature_endpoint(ctx: DashboardContext) -> None:
    ctx.emulator.set_temperature(47.2)
    celsius = (await ctx.http.get(f"/api/readers/{ctx.rid}/temperature")).json()["celsius"]
    assert celsius == 47.0  # reported in whole °C


async def test_profiles_roundtrip(ctx: DashboardContext) -> None:
    body = {"name": "dock door #2", "search_mode": 3, "session": 1, "antennas": [1, 2]}
    saved = (await ctx.http.post("/api/profiles", json=body)).json()
    assert saved["saved"] == "dock-door--2.json"
    profiles = (await ctx.http.get("/api/profiles")).json()
    assert [p["name"] for p in profiles] == ["dock door #2"]
    assert profiles[0]["antennas"] == [1, 2]
    assert profiles[0]["search_mode"] == 3


async def test_unknown_reader_is_404(ctx: DashboardContext) -> None:
    assert (await ctx.http.get("/api/readers/nope/health")).status_code == 404
    assert (await ctx.http.post("/api/readers/nope/inventory/stop")).status_code == 404


async def test_add_reader_connect_failure_is_502(ctx: DashboardContext) -> None:
    response = await ctx.http.post("/api/readers", json={"host": "127.0.0.1", "port": 9})
    assert response.status_code == 502
    assert "cannot connect" in response.json()["detail"]


async def test_remove_reader(ctx: DashboardContext) -> None:
    assert (await ctx.http.delete(f"/api/readers/{ctx.rid}")).status_code == 204
    assert (await ctx.http.get("/api/state")).json()["readers"] == []
    assert (await ctx.http.get(f"/api/readers/{ctx.rid}/health")).status_code == 404


async def test_index_and_static_are_served(ctx: DashboardContext) -> None:
    index = await ctx.http.get("/")
    assert index.status_code == 200
    assert "llrpkit" in index.text
    assert (await ctx.http.get("/static/app.js")).status_code == 200
    assert (await ctx.http.get("/static/style.css")).status_code == 200


async def test_settings_validation_is_422(ctx: DashboardContext) -> None:
    response = await ctx.http.post(f"/api/readers/{ctx.rid}/inventory/start", json={"session": 9})
    assert response.status_code == 422
