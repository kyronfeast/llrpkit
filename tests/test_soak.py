"""Soak test: rapid dashboard churn must not leak tasks or reader state."""

from __future__ import annotations

import asyncio

from httpx import ASGITransport, AsyncClient

from llrpkit.dashboard.app import create_app
from llrpkit.dashboard.registry import ReaderRegistry
from llrpkit.emulator import LLRPEmulator, default_population


async def test_no_task_or_state_leaks_under_churn() -> None:
    emulator = LLRPEmulator(tags=default_population(6, 4), reads_per_sec=300.0)
    await emulator.start()
    registry = ReaderRegistry()
    app = create_app(registry)
    baseline = {t for t in asyncio.all_tasks() if not t.done()}
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://soak") as http:
            added = await http.post(
                "/api/readers", json={"host": "127.0.0.1", "port": emulator.port}
            )
            rid = added.json()["id"]

            # rapid tuning churn: every start replaces the previous stream
            for i in range(25):
                r = await http.post(
                    f"/api/readers/{rid}/inventory/start",
                    json={"search_mode": 2, "mode_index": [0, 2, 3][i % 3]},
                )
                assert r.status_code == 200, r.text
            r = await http.post(f"/api/readers/{rid}/inventory/stop")
            assert r.status_code == 200

            # subscriber churn on the broadcast hub
            for _ in range(20):
                queue = registry.hub.subscribe()
                registry.hub.unsubscribe(queue)
            assert registry.hub.subscriber_count == 0

            # reader add/remove churn (emulator allows one client at a time)
            for _ in range(6):
                r = await http.delete(f"/api/readers/{rid}")
                assert r.status_code == 204
                await asyncio.sleep(0.05)  # let the emulator free its slot
                added = await http.post(
                    "/api/readers", json={"host": "127.0.0.1", "port": emulator.port}
                )
                assert added.status_code == 201, added.text
                rid = added.json()["id"]

            # the surviving reader must be fully functional and clean
            r = await http.get(f"/api/readers/{rid}/health")
            assert r.status_code == 200
    finally:
        await registry.shutdown()
        await emulator.stop()
    await asyncio.sleep(0.2)  # give cancelled tasks a beat to finish

    remaining = {t for t in asyncio.all_tasks() if not t.done()} - baseline
    assert not remaining, f"leaked tasks: {[t.get_name() for t in remaining]}"
    assert emulator._rospecs == {} or all(
        state != "Active" for _, state in emulator._rospecs.values()
    )
