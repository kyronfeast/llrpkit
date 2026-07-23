"""Asyncio LLRP client: framing, correlation, keepalive, and event routing.

:class:`LLRPClient` owns one TCP connection to a reader and provides the
low-level machinery everything else builds on:

* stream framing (LLRP messages are length-prefixed; the read loop reassembles
  exact frames and decodes them),
* the connection handshake (readers announce themselves with a
  ``ConnectionAttemptEvent``; anything but ``Success`` is a refusal),
* request/response correlation by message ID via :meth:`transact`,
* automatic ``KEEPALIVE`` acknowledgement, and
* routing of unsolicited traffic: tag reports land in :attr:`reports`,
  everything else in :attr:`events`.

Most applications use :class:`llrpkit.reader.Reader` instead, which layers a
friendly API on top of this class.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Final

from llrpkit.constants import LLRP_PORT, MESSAGE_HEADER_LEN
from llrpkit.exceptions import (
    LLRPConnectionError,
    LLRPError,
    LLRPStatusError,
    LLRPTimeoutError,
    MessageDecodeError,
)
from llrpkit.protocol import LLRPMessage, decode_message, enums, messages, params

log = logging.getLogger(__name__)

#: Upper bound on a single LLRP frame; larger lengths are treated as protocol errors.
MAX_MESSAGE_BYTES: Final = 4 * 1024 * 1024


def check_status(response: LLRPMessage) -> LLRPMessage:
    """Raise :class:`LLRPStatusError` if ``response`` carries a non-success status.

    Returns the response unchanged otherwise, so calls compose:
    ``check_status(await client.transact(msg))``.
    """
    status = getattr(response, "llrp_status", None)
    if isinstance(status, params.LLRPStatus) and int(status.status_code) != int(
        enums.StatusCode.M_Success
    ):
        raise LLRPStatusError(int(status.status_code), status.error_description)
    return response


class LLRPClient:
    """One LLRP connection to a reader (or emulator).

    Usage::

        async with LLRPClient("192.168.1.10") as client:
            caps = check_status(await client.transact(messages.GET_READER_CAPABILITIES()))
    """

    def __init__(
        self,
        host: str,
        port: int = LLRP_PORT,
        *,
        response_timeout: float = 5.0,
        connect_timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self._response_timeout = response_timeout
        self._connect_timeout = connect_timeout
        #: Unsolicited ``RO_ACCESS_REPORT`` messages (tag data).
        self.reports: asyncio.Queue[messages.RO_ACCESS_REPORT] = asyncio.Queue()
        #: Unsolicited reader events (notifications, unmatched responses).
        self.events: asyncio.Queue[LLRPMessage] = asyncio.Queue()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[LLRPMessage]] = {}
        self._next_id = 0
        self._conn_event: asyncio.Future[params.ConnectionAttemptEvent] | None = None
        self._close_exc: LLRPConnectionError | None = None
        self._closing = False

    # -- lifecycle ---------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._writer is not None and self._close_exc is None

    async def connect(self) -> None:
        """Open the TCP connection and wait for a successful connection event."""
        if self._writer is not None:
            raise LLRPError("client is already connected")
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self._connect_timeout
            )
        except (OSError, TimeoutError) as exc:
            raise LLRPConnectionError(f"cannot connect to {self.host}:{self.port}: {exc}") from exc
        self._conn_event = asyncio.get_running_loop().create_future()
        self._read_task = asyncio.create_task(self._read_loop(), name=f"llrp-read-{self.host}")
        try:
            event = await asyncio.wait_for(self._conn_event, self._connect_timeout)
        except TimeoutError as exc:
            await self._abort()
            raise LLRPConnectionError(
                f"{self.host} sent no ConnectionAttemptEvent (is this an LLRP endpoint?)"
            ) from exc
        except LLRPConnectionError:
            await self._abort()
            raise
        if int(event.status) != int(enums.ConnectionAttemptStatusType.Success):
            await self._abort()
            raise LLRPConnectionError(f"reader refused the connection: {event.status!r}")

    async def close(self) -> None:
        """Politely close (``CLOSE_CONNECTION``), then tear down the transport."""
        if self.connected and not self._closing:
            self._closing = True
            with contextlib.suppress(LLRPError, OSError):
                await self.transact(messages.CLOSE_CONNECTION(), timeout=2.0)
        await self._abort()

    async def __aenter__(self) -> LLRPClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def _abort(self) -> None:
        self._shutdown(None)
        task = self._read_task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            self._read_task = None

    # -- sending -----------------------------------------------------------

    def _ensure_open(self) -> asyncio.StreamWriter:
        if self._writer is None:
            raise LLRPConnectionError("client is not connected")
        if self._close_exc is not None:
            raise self._close_exc
        return self._writer

    def _alloc_message_id(self) -> int:
        self._next_id = (self._next_id % 0xFFFFFFFF) + 1
        return self._next_id

    async def transact(self, msg: LLRPMessage, *, timeout: float | None = None) -> LLRPMessage:
        """Send ``msg`` and await the response with the same message ID."""
        writer = self._ensure_open()
        mid = self._alloc_message_id()
        fut: asyncio.Future[LLRPMessage] = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        wait = self._response_timeout if timeout is None else timeout
        try:
            writer.write(msg.to_bytes(message_id=mid))
            await writer.drain()
            return await asyncio.wait_for(fut, wait)
        except TimeoutError as exc:
            raise LLRPTimeoutError(
                f"no response to {type(msg).__name__} within {wait:.1f}s"
            ) from exc
        except (ConnectionError, OSError) as exc:
            raise LLRPConnectionError(f"send failed: {exc}") from exc
        finally:
            self._pending.pop(mid, None)

    def send(self, msg: LLRPMessage, *, message_id: int = 0) -> None:
        """Fire-and-forget send (used for acknowledgements)."""
        writer = self._ensure_open()
        writer.write(msg.to_bytes(message_id=message_id))

    # -- receiving ---------------------------------------------------------

    async def _read_loop(self) -> None:
        assert self._reader is not None
        reader = self._reader
        try:
            while True:
                header = await reader.readexactly(MESSAGE_HEADER_LEN)
                length = int.from_bytes(header[2:6], "big")
                if not MESSAGE_HEADER_LEN <= length <= MAX_MESSAGE_BYTES:
                    raise MessageDecodeError(f"unreasonable message length {length}")
                body = b""
                if length > MESSAGE_HEADER_LEN:
                    body = await reader.readexactly(length - MESSAGE_HEADER_LEN)
                self._dispatch(decode_message(header + body))
        except asyncio.CancelledError:
            raise
        except (asyncio.IncompleteReadError, ConnectionError, OSError) as exc:
            self._shutdown(
                None
                if self._closing
                else LLRPConnectionError(f"connection to {self.host} lost: {exc}")
            )
        except MessageDecodeError as exc:
            self._shutdown(LLRPConnectionError(f"protocol error from {self.host}: {exc}"))

    def _dispatch(self, msg: LLRPMessage) -> None:
        if isinstance(msg, messages.KEEPALIVE):
            with contextlib.suppress(LLRPError, OSError):
                self.send(messages.KEEPALIVE_ACK(), message_id=msg.message_id)
            return
        if isinstance(msg, messages.RO_ACCESS_REPORT):
            self.reports.put_nowait(msg)
            return
        if isinstance(msg, messages.READER_EVENT_NOTIFICATION):
            event = msg.reader_event_notification_data.connection_attempt_event
            if event is not None and self._conn_event is not None and not self._conn_event.done():
                self._conn_event.set_result(event)
                return
            self.events.put_nowait(msg)
            return
        fut = self._pending.pop(msg.message_id, None)
        if fut is not None and not fut.done():
            fut.set_result(msg)
        else:
            self.events.put_nowait(msg)

    def _shutdown(self, exc: LLRPConnectionError | None) -> None:
        if self._close_exc is None and exc is not None:
            self._close_exc = exc
            log.warning("LLRP connection error: %s", exc)
        final = self._close_exc or LLRPConnectionError("connection closed")
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(final)
        self._pending.clear()
        if self._conn_event is not None and not self._conn_event.done():
            self._conn_event.set_exception(final)
        writer = self._writer
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.close()
