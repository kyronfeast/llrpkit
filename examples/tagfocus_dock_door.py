"""The dock-door pattern: TagFocus, one arrival event per tag.

TagFocus (single target with suppression, session 1) makes each tag announce
itself once and then hold quiet while it stays in the field — the debouncing
happens in the tags, not in your code. Compare the output volume with
read_tags.py against the same emulator.

    llrpkit emulate --port 5084 &
    python examples/tagfocus_dock_door.py --seconds 6
"""

import argparse
import asyncio
import contextlib

from llrpkit import Reader
from llrpkit.protocol.impinj import ImpinjInventorySearchType


async def main(host: str, port: int, seconds: float) -> None:
    arrivals: dict[str, float] = {}
    async with Reader(host, port) as reader:
        stream = reader.inventory(
            session=1,  # TagFocus requires S1 — see the field guide
            search_mode=ImpinjInventorySearchType.Single_Target_With_Suppression,
            include_tid=True,
            duration=seconds,
        )
        async with contextlib.aclosing(stream):
            async for tag in stream:
                if tag.epc_hex not in arrivals:
                    arrivals[tag.epc_hex] = tag.first_seen_us or 0
                    tid = tag.tid.hex() if tag.tid else "-"
                    print(f"ARRIVED  {tag.epc_hex}  ant {tag.antenna}  tid {tid}")
    print(
        f"\n{len(arrivals)} unique tags — with TagFocus the report count is "
        "close to the unique count; with dual target it would be hundreds."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5084)
    parser.add_argument("--seconds", type=float, default=6.0)
    args = parser.parse_args()
    asyncio.run(main(args.host, args.port, args.seconds))
