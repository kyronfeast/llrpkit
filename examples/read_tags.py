"""Hello, tags: connect to a reader and stream an inventory.

Run against the emulator (no hardware):

    llrpkit emulate --port 5084 &
    python examples/read_tags.py --host 127.0.0.1 --seconds 5

or point --host at a real Impinj reader in LLRP mode.
"""

import argparse
import asyncio
import contextlib

from llrpkit import Reader


async def main(host: str, port: int, seconds: float) -> None:
    async with Reader(host, port) as reader:
        print(
            f"model {reader.model_number}, firmware {reader.firmware!r}, "
            f"{reader.max_antennas} antenna ports, Octane={reader.impinj_extensions_enabled}"
        )
        # session 1 is the sensible default; include_phase costs nothing on
        # Impinj readers and gives you a feel for RF phase behavior.
        stream = reader.inventory(session=1, include_phase=True, duration=seconds)
        async with contextlib.aclosing(stream):
            async for tag in stream:
                phase = f"{tag.phase_deg:6.1f}°" if tag.phase_deg is not None else "   -  "
                print(f"{tag.epc_hex}  ant {tag.antenna}  {tag.rssi_dbm:7.2f} dBm  {phase}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5084)
    parser.add_argument("--seconds", type=float, default=5.0)
    args = parser.parse_args()
    asyncio.run(main(args.host, args.port, args.seconds))
