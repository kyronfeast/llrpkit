"""Bridge a reader's tag stream to an MQTT broker (Mosquitto, EMQX, ...).

The reader stays in LLRP mode — full ROSpec / RF-mode / TagFocus control —
while reads fan out over MQTT to everything downstream. Needs the extra:

    pip install "llrpkit[mqtt]"

Run with a broker and the emulator, no hardware:

    mosquitto &                      # or any broker on 1883
    llrpkit emulate --port 5084 &
    python examples/mqtt_bridge.py --host 127.0.0.1 --broker 127.0.0.1

Watch the stream (mosquitto-clients package):

    mosquitto_sub -t 'llrpkit/#' -v

If no broker is listening, this example explains and exits cleanly, so it
can run in environments without one.
"""

import argparse
import asyncio

from llrpkit import Reader
from llrpkit.mqtt import MQTTBridge


async def broker_listening(host: str, port: int) -> bool:
    try:
        async with asyncio.timeout(1.0):
            _, writer = await asyncio.open_connection(host, port)
    except (OSError, TimeoutError):
        return False
    writer.close()
    return True


async def main(host: str, port: int, broker: str, broker_port: int, seconds: float) -> None:
    if not await broker_listening(broker, broker_port):
        print(f"no MQTT broker at {broker}:{broker_port} — start one first, e.g.:  mosquitto")
        return
    async with Reader(host, port) as reader:
        bridge = MQTTBridge(broker, broker_port, base_topic=f"llrpkit/{host}")
        print(f"bridging {host} → mqtt://{broker}:{broker_port}  topic {bridge.tags_topic!r}")
        # TagFocus keeps the bus quiet in a dock-door scenario: each tag
        # publishes once instead of hundreds of times per second.
        published = await bridge.run(
            reader, search_mode=3, session=1, include_phase=True, duration=seconds
        )
        print(f"published {published} tag message(s); retained status now 'offline'")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1", help="reader/emulator host")
    ap.add_argument("--port", type=int, default=5084, help="LLRP port")
    ap.add_argument("--broker", default="127.0.0.1", help="MQTT broker host")
    ap.add_argument("--broker-port", type=int, default=1883, help="MQTT broker port")
    ap.add_argument("--seconds", type=float, default=5.0, help="how long to bridge")
    args = ap.parse_args()
    asyncio.run(main(args.host, args.port, args.broker, args.broker_port, args.seconds))
