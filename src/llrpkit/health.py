"""Antenna health monitoring: rolling per-port statistics and alerts.

The number-one silent failure in deployed RFID is a port that was reading
and stopped — a knocked cable, a failed antenna, a moved pallet blocking the
field. LLRP surfaces hard disconnects as ``AntennaEvent``; everything else
has to be inferred from the tag stream. :class:`HealthMonitor` does both:

* feed every :class:`~llrpkit.inventory.TagReport` to :meth:`observe`,
* feed reader event notifications to :meth:`handle_event`,
* call :meth:`check` periodically — a port that has gone quiet while other
  ports keep reading raises a ``quiet`` alert exactly once until it recovers.

The monitor is transport-agnostic and clock-injectable, so it works the same
against live readers, the emulator, and unit tests.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from llrpkit.inventory import TagReport
from llrpkit.protocol import LLRPMessage, enums, messages

#: How far back the reads/sec window looks.
RATE_WINDOW_S = 2.0
_MAX_UNIQUE_TRACKED = 10_000


@dataclass(frozen=True)
class HealthAlert:
    """One noteworthy health observation."""

    kind: str  # "quiet" | "recovered" | "disconnected" | "connected" | "exception"
    antenna: int | None
    message: str


@dataclass
class AntennaHealth:
    """Mutable rolling state for one antenna port."""

    antenna: int
    connected: bool = True
    reads: int = 0
    unique_epcs: set[bytes] = field(default_factory=set)
    last_seen: float | None = None
    rssi_last: float | None = None
    rssi_min: float | None = None
    rssi_max: float | None = None
    _rssi_sum: float = 0.0
    _rssi_count: int = 0
    _recent: deque[float] = field(default_factory=lambda: deque(maxlen=512))

    @property
    def rssi_mean(self) -> float | None:
        if self._rssi_count == 0:
            return None
        return self._rssi_sum / self._rssi_count

    @property
    def unique_count(self) -> int:
        return len(self.unique_epcs)

    def reads_per_sec(self, now: float) -> float:
        cutoff = now - RATE_WINDOW_S
        recent = sum(1 for t in self._recent if t >= cutoff)
        return recent / RATE_WINDOW_S

    def note_read(self, tag: TagReport, now: float) -> None:
        self.reads += 1
        self.last_seen = now
        self._recent.append(now)
        if len(self.unique_epcs) < _MAX_UNIQUE_TRACKED:
            self.unique_epcs.add(tag.epc)
        if tag.rssi_dbm is not None:
            self.rssi_last = tag.rssi_dbm
            self._rssi_sum += tag.rssi_dbm
            self._rssi_count += 1
            self.rssi_min = (
                tag.rssi_dbm if self.rssi_min is None else min(self.rssi_min, tag.rssi_dbm)
            )
            self.rssi_max = (
                tag.rssi_dbm if self.rssi_max is None else max(self.rssi_max, tag.rssi_dbm)
            )


class HealthMonitor:
    """Tracks per-antenna activity and turns anomalies into alerts."""

    def __init__(
        self,
        antennas: Iterable[int] | None = None,
        *,
        quiet_after: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.quiet_after = quiet_after
        self._clock = clock
        self.antennas: dict[int, AntennaHealth] = {}
        if antennas is not None:
            for a in antennas:
                self.antennas[a] = AntennaHealth(antenna=a)
        self._quiet_alerted: set[int] = set()

    def _port(self, antenna: int) -> AntennaHealth:
        if antenna not in self.antennas:
            self.antennas[antenna] = AntennaHealth(antenna=antenna)
        return self.antennas[antenna]

    # -- inputs ------------------------------------------------------------

    def observe(self, tag: TagReport) -> list[HealthAlert]:
        """Record one tag read; returns a recovery alert if a quiet port woke up."""
        if tag.antenna is None:
            return []
        now = self._clock()
        port = self._port(tag.antenna)
        port.note_read(tag, now)
        if tag.antenna in self._quiet_alerted:
            self._quiet_alerted.discard(tag.antenna)
            return [
                HealthAlert(
                    "recovered", tag.antenna, f"antenna {tag.antenna} is reading tags again"
                )
            ]
        return []

    def handle_event(self, msg: LLRPMessage) -> list[HealthAlert]:
        """Digest a ``READER_EVENT_NOTIFICATION`` into alerts (others ignored)."""
        if not isinstance(msg, messages.READER_EVENT_NOTIFICATION):
            return []
        data = msg.reader_event_notification_data
        alerts: list[HealthAlert] = []
        event = data.antenna_event
        if event is not None:
            port = self._port(event.antenna_id)
            connected = int(event.event_type) == int(enums.AntennaEventType.Antenna_Connected)
            port.connected = connected
            if connected:
                alerts.append(
                    HealthAlert(
                        "connected", event.antenna_id, f"antenna {event.antenna_id} connected"
                    )
                )
            else:
                alerts.append(
                    HealthAlert(
                        "disconnected",
                        event.antenna_id,
                        f"antenna {event.antenna_id} reported disconnected",
                    )
                )
        if data.reader_exception_event is not None:
            alerts.append(
                HealthAlert(
                    "exception", None, data.reader_exception_event.message or "reader exception"
                )
            )
        return alerts

    # -- evaluation --------------------------------------------------------

    def check(self, now: float | None = None) -> list[HealthAlert]:
        """Evaluate quiet-port conditions; each fires once until recovery."""
        current = self._clock() if now is None else now
        anything_recent = any(
            h.last_seen is not None and current - h.last_seen <= self.quiet_after
            for h in self.antennas.values()
        )
        alerts: list[HealthAlert] = []
        for antenna, port in sorted(self.antennas.items()):
            if not port.connected or port.last_seen is None:
                continue
            quiet_for = current - port.last_seen
            if (
                quiet_for > self.quiet_after
                and anything_recent
                and antenna not in self._quiet_alerted
            ):
                self._quiet_alerted.add(antenna)
                alerts.append(
                    HealthAlert(
                        "quiet",
                        antenna,
                        f"antenna {antenna} was reading but has been silent for "
                        f"{quiet_for:.1f}s while other antennas are active",
                    )
                )
        return alerts

    # -- output ------------------------------------------------------------

    def snapshot(self) -> dict[int, dict[str, object]]:
        """A JSON-friendly view per antenna, for dashboards and logs."""
        now = self._clock()
        out: dict[int, dict[str, object]] = {}
        for antenna, port in sorted(self.antennas.items()):
            out[antenna] = {
                "connected": port.connected,
                "reads": port.reads,
                "unique_epcs": port.unique_count,
                "reads_per_sec": round(port.reads_per_sec(now), 2),
                "rssi_last_dbm": port.rssi_last,
                "rssi_mean_dbm": (round(port.rssi_mean, 2) if port.rssi_mean is not None else None),
                "rssi_min_dbm": port.rssi_min,
                "rssi_max_dbm": port.rssi_max,
                "seconds_since_last_read": (
                    round(now - port.last_seen, 2) if port.last_seen is not None else None
                ),
                "quiet_alert_active": antenna in self._quiet_alerted,
            }
        return out
