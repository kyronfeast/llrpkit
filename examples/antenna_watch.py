"""Antenna health monitoring: reads, events, and quiet-port alerts.

Feeds the inventory stream and the reader's event notifications into a
HealthMonitor and prints alerts plus a final per-port summary. Try pulling a
"cable" while it runs by using the dashboard's fault injection, or in code:
``await emulator.set_antenna_connected(2, False)``.

    llrpkit emulate --port 5084 &
    python examples/antenna_watch.py --seconds 6 --quiet-after 2
"""

import argparse
import asyncio
import contextlib
import json

from llrpkit import HealthMonitor, Reader


async def watch_events(reader: Reader, monitor: HealthMonitor) -> None:
    async for msg in reader.events():
        for alert in monitor.handle_event(msg):
            print(f"ALERT [{alert.kind}] {alert.message}")


async def main(host: str, port: int, seconds: float, quiet_after: float) -> None:
    async with Reader(host, port) as reader:
        monitor = HealthMonitor(antennas=range(1, reader.max_antennas + 1), quiet_after=quiet_after)
        events = asyncio.create_task(watch_events(reader, monitor))
        try:
            stream = reader.inventory(duration=seconds)
            async with contextlib.aclosing(stream):
                async for tag in stream:
                    for alert in monitor.observe(tag):
                        print(f"ALERT [{alert.kind}] {alert.message}")
                    for alert in monitor.check():
                        print(f"ALERT [{alert.kind}] {alert.message}")
        finally:
            events.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await events
        print("\nper-port summary:")
        print(json.dumps(monitor.snapshot(), indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5084)
    parser.add_argument("--seconds", type=float, default=6.0)
    parser.add_argument("--quiet-after", type=float, default=2.0)
    args = parser.parse_args()
    asyncio.run(main(args.host, args.port, args.seconds, args.quiet_after))
