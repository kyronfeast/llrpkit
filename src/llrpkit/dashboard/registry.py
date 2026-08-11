"""Managed reader sessions behind the dashboard.

The registry owns long-lived :class:`~llrpkit.reader.Reader` connections and
the background tasks around each one — the inventory stream, the event
watcher, and the periodic health check — and fans everything out to WebSocket
subscribers through a :class:`Broadcast` hub as small JSON events.

Event shapes published to the hub (every event carries ``reader``):

* ``{"type": "tags", "reader", "items": [...]}`` — batched tag rows
* ``{"type": "stats", "reader", "reads_per_sec", "total", "unique", "running"}``
* ``{"type": "health", "reader", "antennas": {...}}`` — monitor snapshot
* ``{"type": "alert", "reader", "kind", "antenna", "message"}``
* ``{"type": "readers", "items": [...]}`` — roster changed
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from typing import Any

from llrpkit.exceptions import LLRPError
from llrpkit.health import HealthMonitor
from llrpkit.inventory import TagReport
from llrpkit.reader import Reader

log = logging.getLogger(__name__)

_TAG_BATCH_MAX = 40
_TAG_FLUSH_S = 0.2
_HEALTH_TICK_S = 1.0
_RATE_WINDOW_S = 2.0
_MAX_UNIQUE_TRACKED = 50_000

DEFAULT_SETTINGS: dict[str, Any] = {
    "antennas": [],
    "session": 1,
    "search_mode": None,
    "mode_index": None,
    "tx_power_dbm": None,
    "tag_population": 32,
    "epc_filter": None,
    "filter_action": "include",
    "include_phase": True,
    "include_doppler": False,
    "include_tid": False,
}


class Broadcast:
    """Fan-out hub: each subscriber gets a bounded queue; slow ones drop."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            # A slow consumer drops events rather than ever blocking readers.
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


class ManagedReader:
    """One reader connection plus its dashboard bookkeeping."""

    def __init__(self, reader_id: str, host: str, port: int, hub: Broadcast) -> None:
        self.id = reader_id
        self.host = host
        self.port = port
        self.hub = hub
        self.reader = Reader(host, port)
        self.monitor = HealthMonitor()
        self.settings: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.error: str | None = None
        self.total_reads = 0
        self.unique_epcs: set[bytes] = set()
        self.recent: deque[dict[str, Any]] = deque(maxlen=200)
        self._read_times: deque[float] = deque(maxlen=4096)
        self._inventory_task: asyncio.Task[None] | None = None
        self._events_task: asyncio.Task[None] | None = None
        self._health_task: asyncio.Task[None] | None = None

    # -- lifecycle ---------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self.reader.client.connected

    @property
    def inventory_running(self) -> bool:
        return self._inventory_task is not None and not self._inventory_task.done()

    async def connect(self) -> None:
        await self.reader.connect()
        self.error = None
        self._events_task = asyncio.create_task(self._run_events(), name=f"{self.id}-events")
        self._health_task = asyncio.create_task(self._run_health(), name=f"{self.id}-health")

    async def disconnect(self) -> None:
        await self.stop_inventory()
        for task in (self._events_task, self._health_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._events_task = self._health_task = None
        await self.reader.close()

    # -- inventory control -------------------------------------------------

    async def start_inventory(self, settings: dict[str, Any] | None = None) -> None:
        await self.stop_inventory()
        if settings is not None:
            merged = dict(DEFAULT_SETTINGS)
            merged.update({k: v for k, v in settings.items() if k in DEFAULT_SETTINGS})
            self.settings = merged
        self._inventory_task = asyncio.create_task(
            self._run_inventory(dict(self.settings)), name=f"{self.id}-inventory"
        )

    async def stop_inventory(self) -> None:
        task = self._inventory_task
        self._inventory_task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # -- background tasks --------------------------------------------------

    async def _run_inventory(self, settings: dict[str, Any]) -> None:
        batch: list[dict[str, Any]] = []
        last_flush = time.monotonic()

        def flush() -> None:
            nonlocal batch, last_flush
            if batch:
                self.hub.publish({"type": "tags", "reader": self.id, "items": batch})
                batch = []
            last_flush = time.monotonic()

        stream = self.reader.inventory(**settings)
        try:
            async with contextlib.aclosing(stream):
                async for tag in stream:
                    self._note_tag(tag)
                    for alert in self.monitor.observe(tag):
                        self._publish_alert(alert.kind, alert.antenna, alert.message)
                    batch.append(self._tag_row(tag))
                    now = time.monotonic()
                    if len(batch) >= _TAG_BATCH_MAX or now - last_flush >= _TAG_FLUSH_S:
                        flush()
        except asyncio.CancelledError:
            flush()
            raise
        except LLRPError as exc:
            self.error = str(exc)
            self._publish_alert("exception", None, f"inventory stopped: {exc}")
        finally:
            flush()

    async def _run_events(self) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            async for msg in self.reader.events():
                for alert in self.monitor.handle_event(msg):
                    self._publish_alert(alert.kind, alert.antenna, alert.message)

    async def _run_health(self) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            while True:
                await asyncio.sleep(_HEALTH_TICK_S)
                for alert in self.monitor.check():
                    self._publish_alert(alert.kind, alert.antenna, alert.message)
                self.hub.publish(
                    {"type": "health", "reader": self.id, "antennas": self.snapshot_health()}
                )
                self.hub.publish({"type": "stats", "reader": self.id, **self.stats()})

    # -- bookkeeping ---------------------------------------------------------

    def _note_tag(self, tag: TagReport) -> None:
        self.total_reads += 1
        self._read_times.append(time.monotonic())
        if len(self.unique_epcs) < _MAX_UNIQUE_TRACKED:
            self.unique_epcs.add(tag.epc)

    def _tag_row(self, tag: TagReport) -> dict[str, Any]:
        row = {
            "epc": tag.epc_hex,
            "antenna": tag.antenna,
            "rssi": tag.rssi_dbm,
            "phase": round(tag.phase_deg, 1) if tag.phase_deg is not None else None,
            "channel": tag.channel_index,
            "tid": tag.tid.hex() if tag.tid is not None else None,
            "at": time.time(),
        }
        self.recent.append(row)
        return row

    def _publish_alert(self, kind: str, antenna: int | None, message: str) -> None:
        self.hub.publish(
            {
                "type": "alert",
                "reader": self.id,
                "kind": kind,
                "antenna": antenna,
                "message": message,
                "at": time.time(),
            }
        )

    def stats(self) -> dict[str, Any]:
        now = time.monotonic()
        cutoff = now - _RATE_WINDOW_S
        rate = sum(1 for t in self._read_times if t >= cutoff) / _RATE_WINDOW_S
        return {
            "reads_per_sec": round(rate, 1),
            "total": self.total_reads,
            "unique": len(self.unique_epcs),
            "running": self.inventory_running,
        }

    def snapshot_health(self) -> dict[str, dict[str, object]]:
        return {str(k): v for k, v in self.monitor.snapshot().items()}

    def info(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "host": self.host,
            "port": self.port,
            "connected": self.connected,
            "inventory_running": self.inventory_running,
            "settings": dict(self.settings),
            "error": self.error,
        }
        if self.connected:
            caps = self.reader.capabilities
            out.update(
                {
                    "model_number": caps.model_number,
                    "firmware": caps.firmware,
                    "is_impinj": caps.is_impinj,
                    "max_antennas": caps.max_antennas,
                    "power_min_dbm": min(caps.transmit_powers.values(), default=None),
                    "power_max_dbm": max(caps.transmit_powers.values(), default=None),
                }
            )
        return out


class ReaderRegistry:
    """All managed readers plus the shared broadcast hub."""

    def __init__(self) -> None:
        self.hub = Broadcast()
        self.readers: dict[str, ManagedReader] = {}
        self._next_id = 0
        self.demo = False

    def _allocate_id(self) -> str:
        self._next_id += 1
        return f"r{self._next_id}"

    def get(self, reader_id: str) -> ManagedReader:
        if reader_id not in self.readers:
            raise KeyError(reader_id)
        return self.readers[reader_id]

    async def add(self, host: str, port: int) -> ManagedReader:
        managed = ManagedReader(self._allocate_id(), host, port, self.hub)
        await managed.connect()
        self.readers[managed.id] = managed
        self.publish_roster()
        return managed

    async def remove(self, reader_id: str) -> None:
        managed = self.get(reader_id)
        del self.readers[reader_id]
        try:
            await managed.disconnect()
        finally:
            # Roster consistency does not depend on the LLRP goodbye succeeding.
            self.publish_roster()

    async def shutdown(self) -> None:
        for reader_id in list(self.readers):
            with contextlib.suppress(Exception):
                await self.remove(reader_id)

    def publish_roster(self) -> None:
        self.hub.publish({"type": "readers", "items": [m.info() for m in self.readers.values()]})
