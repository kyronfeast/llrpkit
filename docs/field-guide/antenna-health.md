# Antenna placement and health

RFID systems rarely die loudly. The failure that costs real money is the port
that *was* reading and quietly stopped — a forklift-clipped cable, a
work-hardened connector, a pallet stacked in front of an antenna. Weeks later
someone asks why receiving counts are down at door 7.

## The failure taxonomy

**Hard disconnect.** Readers measure return loss and notice when an antenna
vanishes; LLRP delivers this as an `AntennaEvent` (disconnected/connected) in
the event stream. Trustworthy, immediate — and only catches full disconnects.

**Soft silence.** A damaged cable, detuned antenna, or blocked field keeps the
port electrically present but functionally dead. No event will ever fire. The
only detector is inference: *this port used to produce reads, its neighbors
still do, and it has produced nothing for N seconds.* That is exactly the rule
llrpkit's `HealthMonitor` implements, with once-per-incident latching so a
flaky port doesn't page you 40 times.

**Degradation.** RSSI tells you about trend: a port whose mean RSSI drifts
down 6 dB over a month has a corroding connector or a shifted antenna, and
will become a soft silence eventually. The monitor tracks last/mean/min/max
per port for exactly this comparison.

## Using the monitor

```python
from llrpkit import HealthMonitor, Reader

monitor = HealthMonitor(antennas=range(1, 5), quiet_after=10.0)

async with Reader(host) as reader:
    events = asyncio.create_task(watch_events(reader, monitor))  # handle_event()
    async for tag in reader.inventory(session=1):
        monitor.observe(tag)
        for alert in monitor.check():
            page_someone(alert)       # "quiet", once, until recovery
```

`observe()` feeds reads, `handle_event()` feeds reader notifications,
`check()` evaluates the quiet rule, and `snapshot()` returns the per-port
JSON the dashboard's antenna cards render. Tune `quiet_after` to your traffic:
a conveyor reading constantly can use seconds; a dock door that sees a truck
an hour cannot use this rule at all on arrival gaps — scope it to periods when
*some* antenna is active, which is precisely what the monitor's "while other
antennas are active" condition does.

## Placement notes that prevent the alerts

Torque connectors to spec and strain-relieve the last 30 cm of cable — the
failures live at the ends. Mount antennas where material flow *cannot* stack
in front of them, or accept that your health data doubles as an "is someone
blocking door 7 again" detector (this is occasionally a feature). Separate
ports' fields enough that a dead port isn't masked by its neighbor reading
the same zone — overlap is good for coverage and terrible for observability;
the per-port unique-EPC counts in the monitor make that overlap measurable.

Fault-inject before production: `llrpkit demo`, Tuning tab — or
`emulator.set_antenna_connected(2, False)` in tests — and confirm your
alerting path end to end. An alert nobody receives is decoration.
