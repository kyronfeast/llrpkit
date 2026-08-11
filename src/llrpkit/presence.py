"""Presence tracking: turn raw read streams into arrive/depart events.

Raw inventory is a firehose — the same tags, hundreds of times a second.
Most applications actually want the two edges: *this tag just showed up*
and *this tag is gone*. Impinj's IoT Device Interface offers exactly that
as "tag entry/exit" events; :class:`PresenceTracker` provides it on the
LLRP side, so you keep the full tuning control plane and still get clean
events::

    tracker = PresenceTracker(depart_after=2.0)
    async for tag in reader.inventory(session=1, search_mode=3):
        for event in (*tracker.observe(tag), *tracker.check()):
            print(event.kind, event.epc.hex())

``observe()`` feeds every read and returns any *arrived* events it caused;
``check()`` returns *departed* events for tags that have been silent longer
than ``depart_after`` — call it periodically (each read is enough while
traffic flows; tick it on a timer if the field can go completely quiet).

Debouncing knobs: ``min_reads`` requires N sightings before a tag counts as
arrived (suppresses one-off stray reads from a neighboring zone), and
``depart_after`` is how much silence means gone. Pair with TagFocus for the
quietest possible dock door: the tags debounce themselves in RF, and the
tracker turns what remains into edges.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field

from llrpkit.inventory import TagReport

__all__ = ["PresenceEvent", "PresenceTracker", "ticked_stream"]


async def ticked_stream(
    stream: AsyncGenerator[TagReport, None], tick: float = 0.25
) -> AsyncGenerator[TagReport | None, None]:
    """Yield tags as they arrive, and ``None`` heartbeats on quiet ticks.

    Presence departures need the clock to keep running when the field goes
    silent, but an async generator cannot be polled with a timeout — a
    cancelled ``anext()`` tears the generator down. This helper pumps the
    stream from a background task into a queue (queue gets ARE safely
    cancellable), so consumers get a steady rhythm::

        async with contextlib.aclosing(ticked_stream(reader.inventory(...))) as ticked:
            async for tag in ticked:            # TagReport, or None on a quiet tick
                events = tracker.observe(tag) if tag else []
                events += tracker.check()

    Ends when the underlying stream ends; propagates its exceptions;
    cancellation tears the inventory down exactly like direct consumption.
    """
    queue: asyncio.Queue[TagReport] = asyncio.Queue(maxsize=1024)

    async def pump() -> None:
        async with contextlib.aclosing(stream):
            async for item in stream:
                await queue.put(item)

    task = asyncio.create_task(pump(), name="llrpkit-ticked-pump")
    try:
        while True:
            try:
                async with asyncio.timeout(tick):
                    yield await queue.get()
            except TimeoutError:
                if task.done():
                    await task  # re-raise the stream's exception, if any
                    return  # stream ended cleanly and the queue is drained
                yield None
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@dataclass(frozen=True)
class PresenceEvent:
    """One presence edge: a tag arrived in, or departed from, the field."""

    kind: str  # "arrived" | "departed"
    epc: bytes
    antenna: int | None
    at: float
    #: Departures only: seconds between first and last sighting of the visit.
    dwell_s: float | None = None
    #: Reads accumulated during the visit so far (arrivals: the debounce count).
    reads: int = 0

    @property
    def epc_hex(self) -> str:
        return self.epc.hex()


@dataclass
class _Visit:
    first_seen: float
    last_seen: float
    antenna: int | None
    reads: int = 0
    announced: bool = False


@dataclass
class PresenceTracker:
    """Arrive/depart edge detection over a tag read stream."""

    #: Seconds of silence after which an announced tag is considered gone.
    depart_after: float = 2.0
    #: Sightings required before a tag counts as arrived (stray-read filter).
    min_reads: int = 1
    #: Injectable clock (monotonic seconds) for tests.
    clock: Callable[[], float] = time.monotonic
    _visits: dict[bytes, _Visit] = field(default_factory=dict, init=False, repr=False)

    def observe(self, tag: TagReport) -> list[PresenceEvent]:
        """Feed one read; returns the *arrived* event it triggered, if any."""
        now = self.clock()
        visit = self._visits.get(tag.epc)
        if visit is None:
            visit = self._visits[tag.epc] = _Visit(
                first_seen=now, last_seen=now, antenna=tag.antenna
            )
        visit.last_seen = now
        visit.reads += 1
        if tag.antenna is not None:
            visit.antenna = tag.antenna
        if not visit.announced and visit.reads >= self.min_reads:
            visit.announced = True
            return [
                PresenceEvent(
                    kind="arrived", epc=tag.epc, antenna=visit.antenna, at=now, reads=visit.reads
                )
            ]
        return []

    def check(self) -> list[PresenceEvent]:
        """Departures for every announced tag now silent > ``depart_after``."""
        now = self.clock()
        events: list[PresenceEvent] = []
        for epc, visit in list(self._visits.items()):
            if now - visit.last_seen < self.depart_after:
                continue
            del self._visits[epc]
            if visit.announced:  # never-announced strays vanish silently
                events.append(
                    PresenceEvent(
                        kind="departed",
                        epc=epc,
                        antenna=visit.antenna,
                        at=now,
                        dwell_s=visit.last_seen - visit.first_seen,
                        reads=visit.reads,
                    )
                )
        return events

    @property
    def present(self) -> set[bytes]:
        """EPCs currently considered present (announced and not departed)."""
        return {epc for epc, visit in self._visits.items() if visit.announced}
