"""Dashboard control API: policy, GPIO, tag ops, and sweep endpoints."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from httpx import ASGITransport, AsyncClient

from llrpkit.dashboard.app import create_app
from llrpkit.dashboard.registry import ReaderRegistry
from llrpkit.emulator import EmulatedTag, LLRPEmulator

# Two families across two antennas so per-antenna policy is observable.
PAILS = [EmulatedTag(epc=bytes([0xE2, 0x00, 0xAA, i] + [0] * 8), antennas=(4,)) for i in range(3)]
PICKLES = [EmulatedTag(epc=bytes([0xE2, 0x00, 0xBB, i] + [0] * 8), antennas=(4,)) for i in range(3)]


@dataclass
class Ctx:
    http: AsyncClient
    emulator: LLRPEmulator
    registry: ReaderRegistry
    rid: str


@pytest.fixture(name="ctx")
async def fixture_ctx() -> AsyncIterator[Ctx]:
    emulator = LLRPEmulator(tags=PAILS + PICKLES, reads_per_sec=400.0, seed=3)
    await emulator.start()
    registry = ReaderRegistry()
    app = create_app(registry)
    managed = await registry.add("127.0.0.1", emulator.port)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://dash") as http:
        yield Ctx(http=http, emulator=emulator, registry=registry, rid=managed.id)
    await registry.shutdown()
    await emulator.stop()


async def eventually(probe, timeout: float = 6.0) -> None:  # type: ignore[no-untyped-def]
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await probe():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("condition not met in time")


LINE4_POLICY = {
    "catalog": [
        {"match": "epc_prefix", "value": "e200aa", "category": "pails"},
        {"match": "epc_prefix", "value": "e200bb", "category": "pickles-fresh"},
    ],
    "antennas": {"4": {"mode": "allow", "categories": ["pails"]}},
}


# --- policy ------------------------------------------------------------------


async def test_policy_get_put_delete(ctx: Ctx) -> None:
    # starts empty
    got = (await ctx.http.get(f"/api/readers/{ctx.rid}/policy")).json()
    assert got["policy"] is None

    put = await ctx.http.put(f"/api/readers/{ctx.rid}/policy", json=LINE4_POLICY)
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["policy"]["antennas"]["4"]["categories"] == ["pails"]
    assert body["counters"]["dropped"] == 0

    cleared = await ctx.http.delete(f"/api/readers/{ctx.rid}/policy")
    assert cleared.json()["policy"] is None


async def test_invalid_policy_is_422(ctx: Ctx) -> None:
    bad = {"antennas": {"4": {"mode": "sideways", "categories": []}}}
    r = await ctx.http.put(f"/api/readers/{ctx.rid}/policy", json=bad)
    assert r.status_code == 422
    assert "invalid policy" in r.json()["detail"]


async def test_policy_filters_live_stream_and_counts_drops(ctx: Ctx) -> None:
    await ctx.http.put(f"/api/readers/{ctx.rid}/policy", json=LINE4_POLICY)
    await ctx.http.post(f"/api/readers/{ctx.rid}/inventory/start", json={"search_mode": 2})

    queue = ctx.registry.hub.subscribe()
    try:
        # every tag row that reaches the hub must be a pail (pickles ignored)
        seen_pail = False
        for _ in range(300):
            event = await asyncio.wait_for(queue.get(), 6.0)
            if event["type"] == "tags":
                for row in event["items"]:
                    assert row["epc"].startswith("e200aa"), row["epc"]
                    assert row["category"] == "pails"
                    seen_pail = True
            if seen_pail:
                break
        assert seen_pail, "no pail rows observed"
    finally:
        ctx.registry.hub.unsubscribe(queue)

    async def has_drops() -> bool:
        counters = (await ctx.http.get(f"/api/readers/{ctx.rid}/policy")).json()["counters"]
        return bool(counters["by_category"].get("pickles-fresh", 0) > 0)

    await eventually(has_drops)
    await ctx.http.post(f"/api/readers/{ctx.rid}/inventory/stop")


# --- GPIO --------------------------------------------------------------------


async def test_gpio_read_and_drive(ctx: Ctx) -> None:
    state = (await ctx.http.get(f"/api/readers/{ctx.rid}/gpio")).json()
    assert state["gpos"] == {"1": False, "2": False, "3": False, "4": False}
    driven = await ctx.http.post(
        f"/api/readers/{ctx.rid}/gpio/output", json={"port": 2, "state": True}
    )
    assert driven.json()["gpos"]["2"] is True
    cfg = await ctx.http.post(
        f"/api/readers/{ctx.rid}/gpio/input", json={"port": 3, "enabled": False}
    )
    assert cfg.json()["gpis"]["3"] == "disabled"


# --- tag operations ----------------------------------------------------------


async def test_tag_read_write_roundtrip_when_idle(ctx: Ctx) -> None:
    target = PAILS[0].epc.hex()
    wrote = await ctx.http.post(
        f"/api/readers/{ctx.rid}/tag/write",
        json={"bank": "user", "word_pointer": 0, "data": "beef", "target_epc": target},
    )
    assert wrote.status_code == 200, wrote.text
    assert wrote.json()["ok"] is True
    read = await ctx.http.post(
        f"/api/readers/{ctx.rid}/tag/read",
        json={"bank": "user", "words": 1, "target_epc": target},
    )
    assert read.json()["data"] == "beef"


async def test_tag_ops_conflict_while_inventory_running(ctx: Ctx) -> None:
    await ctx.http.post(f"/api/readers/{ctx.rid}/inventory/start", json={"search_mode": 2})
    r = await ctx.http.post(f"/api/readers/{ctx.rid}/tag/read", json={"bank": "user", "words": 1})
    assert r.status_code == 409
    assert "stop the inventory" in r.json()["detail"]
    await ctx.http.post(f"/api/readers/{ctx.rid}/inventory/stop")


# --- sweep -------------------------------------------------------------------


async def test_sweep_endpoint_returns_points(ctx: Ctx) -> None:
    r = await ctx.http.post(
        f"/api/readers/{ctx.rid}/sweep",
        json={"powers_dbm": [12, 30], "seconds": 1.0},
    )
    assert r.status_code == 200, r.text
    points = r.json()["points"]
    assert len(points) == 2
    assert {p["tx_power_dbm"] for p in points} == {12.0, 30.0}
    assert all("unique" in p for p in points)


async def test_sweep_needs_an_axis(ctx: Ctx) -> None:
    r = await ctx.http.post(f"/api/readers/{ctx.rid}/sweep", json={"seconds": 1.0})
    assert r.status_code == 422


async def test_control_endpoints_404_on_unknown_reader(ctx: Ctx) -> None:
    assert (await ctx.http.get("/api/readers/nope/policy")).status_code == 404
    assert (await ctx.http.get("/api/readers/nope/gpio")).status_code == 404
    r = await ctx.http.post("/api/readers/nope/sweep", json={"powers_dbm": [20]})
    assert r.status_code == 404


async def test_write_epc_relabels_via_dashboard(ctx: Ctx) -> None:
    target = PAILS[1].epc.hex()
    new_epc = "e2009911223344556677aabb"
    r = await ctx.http.post(
        f"/api/readers/{ctx.rid}/tag/write-epc",
        json={"new_epc": new_epc, "target_epc": target},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert new_epc in {t.epc.hex() for t in ctx.emulator.tags}
