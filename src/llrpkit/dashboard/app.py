"""The llrpkit web dashboard: FastAPI application factory.

``create_app()`` serves the single-page UI, a small REST API for reader and
inventory control, and a WebSocket that streams tag batches, health
snapshots, statistics, and alerts from the :class:`ReaderRegistry` hub.

``create_demo_app()`` is the same application wired to an in-process
:class:`~llrpkit.emulator.LLRPEmulator` with an inventory already running —
the zero-hardware experience behind ``llrpkit demo``.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from llrpkit import __version__
from llrpkit.dashboard.registry import ManagedReader, ReaderRegistry
from llrpkit.exceptions import LLRPError
from llrpkit.modes import suggest_mode
from llrpkit.profiles import InventoryProfile

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_PROFILE_DIR = Path.home() / ".llrpkit" / "profiles"


class AddReaderBody(BaseModel):
    host: str
    port: int = Field(default=5084, ge=1, le=65535)


class SettingsBody(BaseModel):
    antennas: list[int] = Field(default_factory=list)
    session: int = Field(default=1, ge=0, le=3)
    search_mode: int | None = Field(default=None, ge=0, le=6)
    mode_index: int | None = None
    tx_power_dbm: float | None = None
    tag_population: int = Field(default=32, ge=1)
    epc_filter: str | None = Field(default=None, pattern=r"^([0-9a-fA-F]{2})+$")
    filter_action: str = Field(default="include", pattern=r"^(include|exclude)$")
    include_phase: bool = True
    include_doppler: bool = False
    include_tid: bool = False


class ProfileBody(SettingsBody):
    name: str = "default"
    description: str = ""


def create_app(
    registry: ReaderRegistry | None = None,
    *,
    profile_dir: Path | None = None,
    demo_emulator: Any | None = None,
    demo_autostart: bool = True,
) -> FastAPI:
    """Build the dashboard application around a (possibly shared) registry."""
    reg = registry if registry is not None else ReaderRegistry()
    profiles_path = profile_dir if profile_dir is not None else DEFAULT_PROFILE_DIR

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if demo_emulator is not None:
            reg.demo = True
            await demo_emulator.start()
            managed = await reg.add("127.0.0.1", demo_emulator.port)
            if demo_autostart:
                await managed.start_inventory({"search_mode": 2, "include_phase": True})
        try:
            yield
        finally:
            await reg.shutdown()
            if demo_emulator is not None:
                await demo_emulator.stop()

    app = FastAPI(title="llrpkit dashboard", version=__version__, lifespan=lifespan)
    app.state.registry = reg

    # -- pages -------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # -- helpers -----------------------------------------------------------

    def managed(reader_id: str) -> ManagedReader:
        try:
            return reg.get(reader_id)
        except KeyError:
            raise HTTPException(404, f"no reader {reader_id!r}") from None

    # -- api ---------------------------------------------------------------

    @app.get("/api/state")
    async def state() -> dict[str, Any]:
        return {
            "version": __version__,
            "demo": reg.demo,
            "readers": [m.info() for m in reg.readers.values()],
        }

    @app.post("/api/readers", status_code=201)
    async def add_reader(body: AddReaderBody) -> dict[str, Any]:
        try:
            added = await reg.add(body.host, body.port)
        except LLRPError as exc:
            raise HTTPException(502, f"cannot connect: {exc}") from exc
        return added.info()

    @app.delete("/api/readers/{reader_id}", status_code=204)
    async def remove_reader(reader_id: str) -> None:
        managed(reader_id)
        await reg.remove(reader_id)

    @app.post("/api/readers/{reader_id}/inventory/start")
    async def start_inventory(reader_id: str, body: SettingsBody) -> dict[str, Any]:
        m = managed(reader_id)
        await m.start_inventory(body.model_dump())
        reg.publish_roster()
        return m.info()

    @app.post("/api/readers/{reader_id}/inventory/stop")
    async def stop_inventory(reader_id: str) -> dict[str, Any]:
        m = managed(reader_id)
        await m.stop_inventory()
        reg.publish_roster()
        return m.info()

    @app.get("/api/readers/{reader_id}/modes")
    async def reader_modes(
        reader_id: str, dense: bool = False, fast: bool = False
    ) -> dict[str, Any]:
        m = managed(reader_id)
        annotated = m.reader.annotated_modes()
        pick, reason = suggest_mode(
            m.reader.capabilities.modes, dense_environment=dense, prioritize_speed=fast
        )
        return {
            "modes": [
                {
                    "mode_id": a.mode_id,
                    "name": a.name,
                    "summary": a.summary,
                    "autoset": a.is_autoset,
                    "m_value": a.rf.m_value,
                    "bdr": a.rf.bdr_value,
                    "speed": a.guidance.speed if a.guidance else None,
                    "resilience": a.guidance.resilience if a.guidance else None,
                }
                for a in annotated
            ],
            "suggestion": {"mode_id": pick.mode_id, "name": pick.name, "reason": reason},
        }

    @app.get("/api/readers/{reader_id}/health")
    async def reader_health(reader_id: str) -> dict[str, Any]:
        m = managed(reader_id)
        return {"antennas": m.snapshot_health(), "stats": m.stats()}

    @app.get("/api/readers/{reader_id}/temperature")
    async def reader_temperature(reader_id: str) -> dict[str, Any]:
        m = managed(reader_id)
        try:
            celsius = await m.reader.get_temperature()
        except LLRPError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"celsius": celsius}

    # -- profiles ----------------------------------------------------------

    @app.get("/api/profiles")
    async def list_profiles() -> list[dict[str, Any]]:
        out = []
        if profiles_path.is_dir():
            for path in sorted(profiles_path.glob("*.json")):
                try:
                    profile = InventoryProfile.load(path)
                except (ValueError, TypeError, OSError):
                    continue
                entry = profile.inventory_kwargs()
                entry.update({"name": profile.name, "description": profile.description})
                entry["antennas"] = list(profile.antennas)
                out.append(entry)
        return out

    @app.post("/api/profiles", status_code=201)
    async def save_profile(body: ProfileBody) -> dict[str, str]:
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in body.name) or "profile"
        profile = InventoryProfile(
            name=body.name,
            description=body.description,
            antennas=tuple(body.antennas),
            session=body.session,
            search_mode=body.search_mode,
            mode_index=body.mode_index,
            tx_power_dbm=body.tx_power_dbm,
            tag_population=body.tag_population,
            include_phase=body.include_phase,
            include_doppler=body.include_doppler,
            include_tid=body.include_tid,
        )
        profiles_path.mkdir(parents=True, exist_ok=True)
        target = profile.save(profiles_path / f"{safe}.json")
        return {"saved": str(target.name)}

    # -- websocket ---------------------------------------------------------

    @app.websocket("/ws")
    async def websocket(ws: WebSocket) -> None:
        await ws.accept()
        queue = reg.hub.subscribe()
        try:
            await ws.send_json(
                {
                    "type": "state",
                    "demo": reg.demo,
                    "readers": [m.info() for m in reg.readers.values()],
                }
            )
            while True:
                await ws.send_json(await queue.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            reg.hub.unsubscribe(queue)

    return app


def create_demo_app(
    *, tags: int = 16, rate: float = 60.0, antennas: int = 4, seed: int = 1
) -> FastAPI:
    """The full zero-hardware experience: emulator + dashboard, pre-started."""
    from llrpkit.emulator import LLRPEmulator, default_population

    emulator = LLRPEmulator(
        tags=default_population(tags, antennas),
        reads_per_sec=rate,
        antenna_count=antennas,
        seed=seed,
    )
    return create_app(demo_emulator=emulator)
