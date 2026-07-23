"""Named, shareable inventory settings profiles.

A profile captures the knobs that make one deployment's inventory different
from another's — antennas, session, search mode, RF mode, power, report
content — as a small JSON document you can commit to a repo, hand to a
colleague, or select from the dashboard. Apply one with::

    profile = InventoryProfile.load("dock-door.json")
    async for tag in reader.inventory(**profile.inventory_kwargs()):
        ...
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(kw_only=True)
class InventoryProfile:
    """Inventory settings with a name, serializable to JSON."""

    name: str = "default"
    description: str = ""
    antennas: tuple[int, ...] = ()
    session: int = 1
    search_mode: int | None = None
    mode_index: int | None = None
    tx_power_dbm: float | None = None
    tag_population: int = 32
    report_every_n: int = 1
    include_phase: bool = False
    include_doppler: bool = False
    include_tid: bool = False
    keepalive_ms: int | None = None

    def inventory_kwargs(self) -> dict[str, Any]:
        """Keyword arguments for :meth:`llrpkit.reader.Reader.inventory`."""
        return {
            "antennas": self.antennas,
            "session": self.session,
            "search_mode": self.search_mode,
            "mode_index": self.mode_index,
            "tx_power_dbm": self.tx_power_dbm,
            "tag_population": self.tag_population,
            "report_every_n": self.report_every_n,
            "include_phase": self.include_phase,
            "include_doppler": self.include_doppler,
            "include_tid": self.include_tid,
        }

    # -- serialization -----------------------------------------------------

    def to_json(self) -> str:
        payload = dataclasses.asdict(self)
        payload["antennas"] = list(self.antennas)
        return json.dumps(payload, indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> InventoryProfile:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"profile is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("profile JSON must be an object")
        known = {f.name for f in dataclasses.fields(cls)}
        unknown = set(payload) - known
        if unknown:
            raise ValueError(f"profile has unknown field(s): {', '.join(sorted(unknown))}")
        if "antennas" in payload:
            payload["antennas"] = tuple(int(a) for a in payload["antennas"])
        return cls(**payload)

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.write_text(self.to_json())
        return target

    @classmethod
    def load(cls, path: str | Path) -> InventoryProfile:
        return cls.from_json(Path(path).read_text())
