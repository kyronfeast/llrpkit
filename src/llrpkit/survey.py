"""RF surveys: sweep power and RF mode, measure what each setting delivers.

The tuning methodology in the field guide — change one thing, watch one
number — as a function. :func:`sweep` runs a short inventory per setting
combination and records reads/second and unique tag count::

    from llrpkit.survey import sweep

    points = await sweep(reader, powers_dbm=[15, 20, 25, 30], seconds=3.0)
    best = max(points, key=lambda p: (p.unique, p.reads_per_sec))

Unique count is the number that usually matters: a slower mode or lower
power that still finds every tag beats a fast setting that misses the weak
ones. The CLI wrapper is ``llrpkit sweep``.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from llrpkit.reader import Reader

__all__ = ["SweepPoint", "sweep"]


@dataclass(frozen=True)
class SweepPoint:
    """One measured setting combination."""

    tx_power_dbm: float | None
    mode_index: int | None
    seconds: float
    reads: int
    unique: int

    @property
    def reads_per_sec(self) -> float:
        return self.reads / self.seconds if self.seconds > 0 else 0.0


async def sweep(
    reader: Reader,
    *,
    powers_dbm: list[float | None] | None = None,
    mode_indexes: list[int | None] | None = None,
    seconds: float = 3.0,
    **inventory_kwargs: Any,
) -> list[SweepPoint]:
    """Measure every power x mode combination with a short inventory each.

    ``None`` in either axis means "leave that setting at the reader default".
    Additional ``inventory_kwargs`` (session, search mode, filters...) apply
    to every run, so the sweep changes exactly one variable at a time.
    """
    powers = powers_dbm if powers_dbm else [None]
    modes = mode_indexes if mode_indexes else [None]
    points: list[SweepPoint] = []
    for mode_index in modes:
        for power in powers:
            reads = 0
            unique: set[bytes] = set()
            stream = reader.inventory(
                tx_power_dbm=power,
                mode_index=mode_index,
                duration=seconds,
                **inventory_kwargs,
            )
            async with contextlib.aclosing(stream):
                async for tag in stream:
                    reads += 1
                    unique.add(tag.epc)
            points.append(
                SweepPoint(
                    tx_power_dbm=power,
                    mode_index=mode_index,
                    seconds=seconds,
                    reads=reads,
                    unique=len(unique),
                )
            )
    return points
