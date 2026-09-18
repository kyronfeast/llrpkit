"""Gated inventory: turn a sensor line into one window of tags per object.

In plain words: on a production line you usually don't want a firehose of tag
reads — you want to know "a pail just went past station 3, and *these* are the
tags it carried". A photo eye (or any switch) wired to the reader's GPI line
tells the reader when something is in front of the antenna. This module turns
that into **windows**: each time the line trips, a window opens; every tag read
until it releases lands inside; when it releases, the window closes and is
handed to you — *even if it's empty*. An empty window is the most valuable one
of all: it's a pail that went by with no readable tag.

Two building blocks live here, both reader-agnostic so that any driver (LLRP
or serial) can present the same shape:

* :class:`GPIEdge` — one transition of an input line (which port, went high or
  low, when).
* :class:`InventoryWindow` — the tags seen between an opening edge and a
  closing edge.

:func:`assemble_windows` folds an interleaved feed of edges and tags into
windows. It reads from an :class:`asyncio.Queue` rather than an iterator on
purpose: the reader's edge notifications and its tag reports arrive on two
different channels, and a queue lets both be pumped in by tasks while this
function waits with a timeout that is safe to cancel.

The *settle* period matters for LLRP readers: the "line released" event and the
last tag reports of the ROSpec it stops are sent on different message types
and are not guaranteed to arrive in order, so after a closing edge the window
stays open for a short grace period to catch stragglers.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from typing import Any

from llrpkit.inventory import TagReport

#: Sentinel a producer puts on the queue when its source is exhausted.
END_OF_STREAM: Any = object()


@dataclass(frozen=True)
class GPIEdge:
    """One transition on a reader's general-purpose input line.

    ``high`` is the level the line moved *to*. ``at`` is a Unix timestamp in
    seconds (float) so it lines up with everything else in the value layer.
    """

    port: int
    high: bool
    at: float

    @property
    def low(self) -> bool:
        return not self.high


@dataclass(frozen=True)
class InventoryWindow:
    """Everything one trip of the gate produced.

    ``opened_at``/``closed_at`` are Unix seconds. ``tags`` is every observation
    in order, repeats included — use :attr:`epcs` for the distinct set. A
    window with no tags is still yielded: that's an object that passed with
    nothing readable on it, which is exactly the exception a line needs to
    catch.
    """

    port: int
    opened_at: float
    closed_at: float
    tags: tuple[TagReport, ...]

    @property
    def epcs(self) -> tuple[str, ...]:
        """Distinct EPCs (hex) in first-seen order."""
        seen: dict[str, None] = {}
        for t in self.tags:
            seen.setdefault(t.epc_hex, None)
        return tuple(seen)

    @property
    def empty(self) -> bool:
        return not self.tags

    @property
    def duration(self) -> float:
        return self.closed_at - self.opened_at


async def assemble_windows(
    queue: asyncio.Queue[Any],
    *,
    port: int,
    active_high: bool = False,
    settle: float = 0.1,
    max_open: float | None = 30.0,
) -> AsyncGenerator[InventoryWindow, None]:
    """Fold a queue of ``GPIEdge`` / ``TagReport`` items into windows.

    The gate is considered *open* while the line sits at the active level
    (``active_high=False`` means active-low: a sensor pulling the input to
    ground opens the gate, which is how photo eyes are usually wired).

    * An edge to the active level opens a window (if one isn't already open).
    * Tags while open are collected. Tags while closed are dropped — nothing
      was in front of the antenna, so they're noise from neighbouring lines.
    * An edge to the inactive level starts the ``settle`` grace period; tags
      that arrive inside it still count. Then the window is yielded.
    * ``max_open`` (seconds) force-closes a window whose closing edge never
      came — a stuck sensor or a lost notification must not swallow the line
      forever. ``None`` disables it.

    Stops when :data:`END_OF_STREAM` is dequeued; an open window is yielded
    on the way out so nothing already observed is lost.
    """
    loop = asyncio.get_running_loop()
    open_at: float | None = None
    close_at: float | None = None  # set once the closing edge is seen (settling)
    tags: list[TagReport] = []
    opened_mono = 0.0  # loop time the window opened, for max_open

    def _window() -> InventoryWindow:
        assert open_at is not None
        return InventoryWindow(
            port=port,
            opened_at=open_at,
            closed_at=close_at if close_at is not None else _now(),
            tags=tuple(tags),
        )

    def _now() -> float:
        return time.time()

    settle_deadline: float | None = None
    while True:
        # How long we may wait for the next item before a timer fires.
        timeout: float | None = None
        if settle_deadline is not None:
            timeout = max(0.0, settle_deadline - loop.time())
        elif open_at is not None and max_open is not None:
            timeout = max(0.0, opened_mono + max_open - loop.time())
        try:
            if timeout is None:
                item = await queue.get()
            else:
                async with asyncio.timeout(timeout):
                    item = await queue.get()
        except TimeoutError:
            if settle_deadline is not None:
                # grace period over: hand the window out
                yield _window()
                open_at = close_at = settle_deadline = None
                tags = []
            elif open_at is not None:
                # max_open hit: force-close
                close_at = _now()
                yield _window()
                open_at = close_at = None
                tags = []
            continue

        if item is END_OF_STREAM:
            if open_at is not None:
                yield _window()
            return
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, GPIEdge):
            if item.port != port:
                continue
            active = item.high == active_high
            if active:
                if open_at is None or settle_deadline is not None:
                    # A new object arrived. If we were still settling the
                    # previous one, close it out first.
                    if settle_deadline is not None:
                        yield _window()
                        tags = []
                    open_at = item.at
                    close_at = None
                    settle_deadline = None
                    opened_mono = loop.time()
            elif open_at is not None and settle_deadline is None:
                close_at = item.at
                settle_deadline = loop.time() + settle
            continue
        if isinstance(item, TagReport):
            if open_at is not None:
                tags.append(item)
            continue


async def pump_into(
    source: AsyncIterator[Any],
    queue: asyncio.Queue[Any],
) -> None:
    """Task body: forward every item from ``source`` into ``queue``.

    Exceptions are forwarded as items (so the consumer raises them) rather
    than lost in a task; ``END_OF_STREAM`` is *not* sent — the owner decides
    when the merged stream is over.
    """
    try:
        async for item in source:
            await queue.put(item)
    except asyncio.CancelledError:
        raise
    except BaseException as exc:
        await queue.put(exc)
