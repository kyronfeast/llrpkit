"""POST tag activity straight to an HTTP endpoint — no broker in the loop.

Requires the ``webhook`` extra: ``pip install "llrpkit[webhook]"``.

For small deployments where MQTT is one moving part too many,
:class:`WebhookSink` delivers the same semantics as the MQTT bridge as
batched HTTP POSTs — presence (arrive/depart) events as the primary
stream, raw reads optional::

    from llrpkit import Reader
    from llrpkit.webhook import WebhookSink

    async with Reader("192.168.1.10") as reader:
        sink = WebhookSink("https://erp.local/gielow/rfid/event", token="s3cret")
        await sink.run(reader, search_mode=3, session=1)   # posts until cancelled

The request contract (pinned — built for receivers like the ``gielow_rfid``
Odoo controller, and stable for any other consumer):

* ``POST`` with JSON body ``{"reader": <label>, "token": <str|null>,
  "events": [...]}`` — the token travels in the body.
* Every event entry carries the same keys; ``epc`` is the only one a
  receiver must rely on, the rest are ``null`` where not applicable::

      {"epc": "<hex>", "kind": "arrived"|"departed"|"read",
       "antenna": <int>|null, "rssi": <float>|null,
       "dwell_s": <float>|null, "reads": <int>|null, "at": <epoch float>}

* Batches contain at most ``batch_max`` (default 500) entries; a flush
  happens when the batch fills or ``flush_interval`` elapses with entries
  pending.
* Expected responses: ``200`` (``{"ok": true, "created": N}``) on success,
  ``403`` for a bad token (raises :class:`WebhookAuthError`), ``400`` for a
  malformed body (raises :class:`WebhookError` — that is a bug, not a
  retry). Connection failures and 5xx responses are retried on the next
  flush; the buffer is bounded, drop-oldest, with a ``dropped`` counter.

MQTT remains the right answer for multi-consumer setups; this is the
one-consumer shortcut. Field-name note: the MQTT ``{base}/events`` schema
uses ``"event"`` for the edge type (pinned before this sink existed); the
webhook contract uses ``"kind"``. Both are pinned as-is.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any

from llrpkit._cancel import resurface_swallowed_cancel as _resurface
from llrpkit.inventory import TagReport
from llrpkit.presence import PresenceEvent, PresenceTracker, ticked_stream
from llrpkit.reader import Reader

try:
    import httpx
except ImportError as _exc:  # pragma: no cover - depends on install flavor
    raise ImportError(
        'llrpkit.webhook needs the "webhook" extra:\n\n    pip install "llrpkit[webhook]"\n'
    ) from _exc

__all__ = ["WebhookAuthError", "WebhookError", "WebhookSink", "presence_entry", "read_entry"]


class WebhookError(Exception):
    """The receiver rejected a request in a way that will not heal by retrying."""


class WebhookAuthError(WebhookError):
    """The receiver answered 403: the token is wrong."""


def presence_entry(edge: PresenceEvent) -> dict[str, Any]:
    """One webhook event entry for an arrive/depart edge (pinned keys)."""
    return {
        "epc": edge.epc_hex,
        "kind": edge.kind,
        "antenna": edge.antenna,
        "rssi": None,
        "dwell_s": round(edge.dwell_s, 2) if edge.dwell_s is not None else None,
        "reads": edge.reads,
        "at": round(time.time(), 3),
    }


def read_entry(tag: TagReport) -> dict[str, Any]:
    """One webhook event entry for a raw read (``kind: "read"``, pinned keys)."""
    return {
        "epc": tag.epc_hex,
        "kind": "read",
        "antenna": tag.antenna,
        "rssi": tag.rssi_dbm,
        "dwell_s": None,
        "reads": None,
        "at": round(time.time(), 3),
    }


@dataclass
class WebhookSink:
    """Deliver one reader's activity to an HTTP endpoint as batched POSTs."""

    url: str
    token: str | None = None
    batch_max: int = 500
    flush_interval: float = 1.0
    #: Also send every raw read as a ``kind: "read"`` entry (high volume).
    include_tags: bool = False
    #: Silence meaning "departed", in seconds.
    depart_after: float = 2.0
    #: Per-request time bound, seconds.
    request_timeout: float = 10.0
    #: Event entries acknowledged by the receiver so far.
    posted: int = field(default=0, init=False)
    #: Successful POST requests so far.
    batches: int = field(default=0, init=False)
    #: Entries discarded because the receiver stayed unreachable too long.
    dropped: int = field(default=0, init=False)

    async def run(
        self,
        reader: Reader,
        *,
        reader_label: str | None = None,
        **inventory_kwargs: Any,
    ) -> int:
        """Stream ``reader.inventory(**inventory_kwargs)`` into the endpoint.

        Runs until the inventory ends (``duration``/``max_tags``) or the task
        is cancelled; either way the ROSpec is torn down and a bounded final
        flush is attempted. Returns the number of entries acknowledged.
        """
        label = reader_label or f"{reader.client.host}:{reader.client.port}"
        tracker = PresenceTracker(depart_after=self.depart_after)
        pending: list[dict[str, Any]] = []
        last_flush = time.monotonic()
        # Bounded backlog while the receiver is down: ~10 full batches.
        max_pending = self.batch_max * 10

        async with httpx.AsyncClient(timeout=self.request_timeout) as client:

            async def flush() -> None:
                nonlocal last_flush
                last_flush = time.monotonic()
                while pending:
                    chunk = pending[: self.batch_max]
                    body = {"reader": label, "token": self.token, "events": chunk}
                    try:
                        response = await client.post(self.url, json=body)
                    except httpx.HTTPError:
                        return  # receiver unreachable; keep the batch, retry later
                    finally:
                        _resurface()
                    if response.status_code == 403:
                        raise WebhookAuthError(f"{self.url} answered 403: check the webhook token")
                    if response.status_code == 400:
                        raise WebhookError(
                            f"{self.url} answered 400 (malformed body): {response.text[:200]}"
                        )
                    if response.status_code >= 500 or response.status_code >= 300:
                        return  # server-side trouble; keep the batch, retry later
                    del pending[: len(chunk)]
                    self.batches += 1
                    self.posted += len(chunk)

            ticked = ticked_stream(reader.inventory(**inventory_kwargs))
            try:
                async with contextlib.aclosing(ticked):
                    async for tag in ticked:
                        if tag is not None and self.include_tags:
                            pending.append(read_entry(tag))
                        edges = list(tracker.observe(tag)) if tag is not None else []
                        edges.extend(tracker.check())
                        pending.extend(presence_entry(edge) for edge in edges)
                        if len(pending) > max_pending:  # receiver down too long
                            overflow = len(pending) - max_pending
                            del pending[:overflow]
                            self.dropped += overflow
                        if len(pending) >= self.batch_max or (
                            pending and time.monotonic() - last_flush >= self.flush_interval
                        ):
                            await flush()
            finally:
                if pending:  # a bounded goodbye flush, whatever ended the stream
                    with contextlib.suppress(WebhookError, Exception):
                        async with asyncio.timeout(5.0):
                            await flush()
        return self.posted
