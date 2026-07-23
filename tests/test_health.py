"""Unit tests for the antenna health monitor (deterministic fake clock)."""

from __future__ import annotations

from llrpkit.health import HealthMonitor
from llrpkit.inventory import TagReport
from llrpkit.protocol import enums, messages, params


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def tag(antenna: int, rssi: float = -50.0, epc: bytes = b"\x01" * 12) -> TagReport:
    return TagReport(epc=epc, antenna=antenna, rssi_dbm=rssi)


def antenna_event(antenna: int, connected: bool) -> messages.READER_EVENT_NOTIFICATION:
    event_type = (
        enums.AntennaEventType.Antenna_Connected
        if connected
        else enums.AntennaEventType.Antenna_Disconnected
    )
    return messages.READER_EVENT_NOTIFICATION(
        reader_event_notification_data=params.ReaderEventNotificationData(
            timestamp=params.UTCTimestamp(microseconds=1),
            antenna_event=params.AntennaEvent(event_type=event_type, antenna_id=antenna),
        )
    )


def test_quiet_port_alert_fires_once_and_recovers() -> None:
    clock = FakeClock()
    monitor = HealthMonitor(antennas=(1, 2), quiet_after=5.0, clock=clock)
    monitor.observe(tag(1))
    monitor.observe(tag(2))
    assert monitor.check() == []
    # antenna 2 goes silent while antenna 1 keeps reading
    clock.now += 6.0
    monitor.observe(tag(1))
    alerts = monitor.check()
    assert [a.kind for a in alerts] == ["quiet"]
    assert alerts[0].antenna == 2
    assert "silent" in alerts[0].message
    # fires exactly once while the condition persists
    clock.now += 1.0
    monitor.observe(tag(1))
    assert monitor.check() == []
    # recovery clears the latch and reports it
    recovered = monitor.observe(tag(2))
    assert [a.kind for a in recovered] == ["recovered"]
    clock.now += 6.0
    monitor.observe(tag(1))
    assert [a.kind for a in monitor.check()] == ["quiet"]  # can fire again later


def test_no_quiet_alert_when_everything_is_silent() -> None:
    clock = FakeClock()
    monitor = HealthMonitor(antennas=(1, 2), quiet_after=5.0, clock=clock)
    monitor.observe(tag(1))
    monitor.observe(tag(2))
    clock.now += 60.0  # the whole reader is idle (no inventory running)
    assert monitor.check() == []


def test_disconnected_port_is_not_reported_quiet() -> None:
    clock = FakeClock()
    monitor = HealthMonitor(quiet_after=5.0, clock=clock)
    monitor.observe(tag(1))
    monitor.observe(tag(2))
    alerts = monitor.handle_event(antenna_event(2, connected=False))
    assert [a.kind for a in alerts] == ["disconnected"]
    clock.now += 10.0
    monitor.observe(tag(1))
    assert monitor.check() == []  # port 2 is known-down, not "quiet"
    alerts = monitor.handle_event(antenna_event(2, connected=True))
    assert [a.kind for a in alerts] == ["connected"]
    assert monitor.antennas[2].connected


def test_exception_event_becomes_alert() -> None:
    monitor = HealthMonitor()
    msg = messages.READER_EVENT_NOTIFICATION(
        reader_event_notification_data=params.ReaderEventNotificationData(
            timestamp=params.UTCTimestamp(microseconds=1),
            reader_exception_event=params.ReaderExceptionEvent(message="antenna VSWR fault"),
        )
    )
    alerts = monitor.handle_event(msg)
    assert [a.kind for a in alerts] == ["exception"]
    assert "VSWR" in alerts[0].message


def test_non_notification_messages_are_ignored() -> None:
    monitor = HealthMonitor()
    assert monitor.handle_event(messages.KEEPALIVE()) == []
    assert monitor.observe(TagReport(epc=b"\x00" * 12)) == []  # no antenna id


def test_snapshot_shape_and_stats() -> None:
    clock = FakeClock()
    monitor = HealthMonitor(antennas=(1,), quiet_after=5.0, clock=clock)
    monitor.observe(tag(1, rssi=-40.0, epc=b"\x0a" * 12))
    clock.now += 0.5
    monitor.observe(tag(1, rssi=-60.0, epc=b"\x0b" * 12))
    snap = monitor.snapshot()
    port = snap[1]
    assert port["reads"] == 2
    assert port["unique_epcs"] == 2
    assert port["rssi_min_dbm"] == -60.0
    assert port["rssi_max_dbm"] == -40.0
    assert port["rssi_mean_dbm"] == -50.0
    assert port["connected"] is True
    assert port["quiet_alert_active"] is False
    assert port["reads_per_sec"] == 1.0  # 2 reads inside the 2 s window
