"""Sweep RF modes and compare read rates — the tuning methodology, automated.

Runs each mode from the reader's own RFModeTable for a few seconds and prints
a comparison. Against the emulator the rate differences are modeled (FM0-style
modes fast, Miller-8 slow); against a real reader they are physics.

    llrpkit emulate --port 5084 &
    python examples/mode_shootout.py --seconds 2
"""

import argparse
import asyncio
import contextlib

from llrpkit import Reader
from llrpkit.modes import annotate_modes


async def measure(reader: Reader, mode_index: int | None, seconds: float) -> tuple[int, int]:
    total, unique = 0, set()
    stream = reader.inventory(mode_index=mode_index, duration=seconds)
    async with contextlib.aclosing(stream):
        async for tag in stream:
            total += 1
            unique.add(tag.epc)
    return total, len(unique)


async def main(host: str, port: int, seconds: float) -> None:
    async with Reader(host, port) as reader:
        annotated = annotate_modes(reader.capabilities.modes)
        print(f"{'mode':>6}  {'name':<32} {'reads/s':>8}  {'unique':>6}")
        for mode in annotated:
            total, unique = await measure(reader, mode.mode_id, seconds)
            print(f"{mode.mode_id:>6}  {mode.name:<32} {total / seconds:>8.1f}  {unique:>6}")
        print(
            "\nPick for the environment, not the biggest number — "
            "see the field guide's reader-modes chapter."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5084)
    parser.add_argument("--seconds", type=float, default=2.0)
    args = parser.parse_args()
    asyncio.run(main(args.host, args.port, args.seconds))
