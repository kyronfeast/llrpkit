"""Dashboard API and WebSocket tests against the in-process emulator.

`create_app(demo_emulator=...)` runs the emulator inside the app's own event
loop (exactly what `llrpkit demo` does), which makes the whole stack — reader,
registry tasks, REST, WebSocket — exercisable from a synchronous TestClient.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from llrpkit.dashboard import create_app, create_demo_app
from llrpkit.emulator import LLRPEmulator, default_population


@pytest.fixture(name="client")
def fixture_client(tmp_path: Path) -> Iterator[TestClient]:
    emulator = LLRPEmulator(
        tags=default_population(8, 4), reads_per_sec=300.0, antenna_count=4, seed=3
    )
    app = create_app(
        demo_emulator=emulator, demo_autostart=False, profile_dir=tmp_path / "profiles"
    )
    with TestClient(app) as test_client:
        yield test_client


def reader_id(client: TestClient) -> str:
    readers = client.get("/api/state").json()["readers"]
    assert readers, "expected the emulator-backed reader"
    return str(readers[0]["id"])


def wait_for(predicate, timeout: float = 5.0, interval: float = 0.1):  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met in time")


def test_state_reports_the_emulated_reader(client: TestClient) -> None:
    state = client.get("/api/state").json()
    assert state["demo"] is True
    reader = state["readers"][0]
    assert reader["connected"] is True
    assert reader["model_number"] == 700
    assert reader["is_impinj"] is True
    assert reader["max_antennas"] == 4
    assert reader["power_min_dbm"] == 10.0
    assert reader["power_max_dbm"] == 30.0
    assert reader["inventory_running"] is False


def test_inventory_start_stats_and_stop(client: TestClient) -> None:
    rid = reader_id(client)
    info = client.post(
        f"/api/readers/{rid}/inventory/start", json={"search_mode": 2, "session": 1}
    ).json()
    assert info["inventory_running"] is True
    assert info["settings"]["search_mode"] == 2

    def total() -> int:
        return int(client.get(f"/api/readers/{rid}/health").json()["stats"]["total"])

    wait_for(lambda: total() > 10)
    health = client.get(f"/api/readers/{rid}/health").json()
    assert health["stats"]["unique"] >= 4
    assert any(a["reads"] > 0 for a in health["antennas"].values())
    info = client.post(f"/api/readers/{rid}/inventory/stop").json()
    assert info["inventory_running"] is False


def test_websocket_streams_tags_stats_and_health(client: TestClient) -> None:
    rid = reader_id(client)
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["type"] == "state"
        assert first["readers"][0]["id"] == rid
        client.post(f"/api/readers/{rid}/inventory/start", json={"include_phase": True})
        seen: set[str] = set()
        tag_batch = None
        for _ in range(300):
            msg = ws.receive_json()
            seen.add(msg["type"])
            if msg["type"] == "tags" and tag_batch is None:
                tag_batch = msg
            if {"tags", "stats", "health"} <= seen:
                break
        assert {"tags", "stats", "health"} <= seen
        assert tag_batch is not None
        row = tag_batch["items"][0]
        assert set(row) >= {"epc", "antenna", "rssi", "phase", "at"}
        assert row["epc"].startswith("e2")
    client.post(f"/api/readers/{rid}/inventory/stop")


def test_modes_endpoint_serves_curated_guidance(client: TestClient) -> None:
    rid = reader_id(client)
    data = client.get(f"/api/readers/{rid}/modes").json()
    by_id = {m["mode_id"]: m for m in data["modes"]}
    assert by_id[2]["name"] == "Dense Reader M4"
    assert "workhorse" in by_id[2]["summary"]
    assert by_id[1003]["autoset"] is True
    dense = client.get(f"/api/readers/{rid}/modes", params={"dense": True}).json()
    assert dense["suggestion"]["mode_id"] == 1002


def test_temperature_endpoint(client: TestClient) -> None:
    rid = reader_id(client)
    celsius = client.get(f"/api/readers/{rid}/temperature").json()["celsius"]
    assert celsius == pytest.approx(41.5, abs=1.0)


def test_profiles_roundtrip(client: TestClient) -> None:
    body = {"name": "dock door #2", "search_mode": 3, "session": 1, "antennas": [1, 2]}
    saved = client.post("/api/profiles", json=body).json()
    assert saved["saved"] == "dock-door--2.json"
    profiles = client.get("/api/profiles").json()
    assert [p["name"] for p in profiles] == ["dock door #2"]
    assert profiles[0]["antennas"] == [1, 2]
    assert profiles[0]["search_mode"] == 3


def test_unknown_reader_is_404(client: TestClient) -> None:
    assert client.get("/api/readers/nope/health").status_code == 404
    assert client.post("/api/readers/nope/inventory/stop").status_code == 404


def test_add_reader_connect_failure_is_502(client: TestClient) -> None:
    response = client.post("/api/readers", json={"host": "127.0.0.1", "port": 9})
    assert response.status_code == 502
    assert "cannot connect" in response.json()["detail"]


def test_remove_reader(client: TestClient) -> None:
    rid = reader_id(client)
    assert client.delete(f"/api/readers/{rid}").status_code == 204
    assert client.get("/api/state").json()["readers"] == []
    assert client.get(f"/api/readers/{rid}/health").status_code == 404


def test_index_and_static_are_served(client: TestClient) -> None:
    index = client.get("/")
    assert index.status_code == 200
    assert "llrpkit" in index.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_demo_app_autostarts_inventory(tmp_path: Path) -> None:
    app = create_demo_app(tags=6, rate=300.0)
    with TestClient(app) as client:
        state = client.get("/api/state").json()
        assert state["demo"] is True
        rid = state["readers"][0]["id"]
        assert state["readers"][0]["inventory_running"] is True

        def total() -> int:
            return int(client.get(f"/api/readers/{rid}/health").json()["stats"]["total"])

        wait_for(lambda: total() > 10)
