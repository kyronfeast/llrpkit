"""Capture tag reads to files: CSV for spreadsheets, JSONL for pipelines.

:class:`TagWriter` picks the format from the file suffix and writes one row
per :class:`~llrpkit.inventory.TagReport`::

    from llrpkit.capture import TagWriter

    with TagWriter("dock-door.csv") as writer:      # or .jsonl
        async for tag in reader.inventory(...):
            writer.write(tag)

Both formats share the same columns: wall-clock ``at``, ``epc``, ``antenna``,
``rssi_dbm``, ``phase_deg``, ``doppler_hz``, ``channel``, ``tid``, and —
when the EPC is a GS1 encoding — the decoded ``scheme`` and ``identity``
(pure-identity URI). The CLI flag is ``llrpkit inventory --output FILE``.
"""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from types import TracebackType
from typing import Any, TextIO

from llrpkit.epc import decode_epc
from llrpkit.inventory import TagReport

__all__ = ["TagWriter", "tag_row"]

COLUMNS = [
    "at",
    "epc",
    "antenna",
    "rssi_dbm",
    "phase_deg",
    "doppler_hz",
    "channel",
    "tid",
    "scheme",
    "identity",
]


def tag_row(tag: TagReport) -> dict[str, Any]:
    """One export row for a tag read (shared by CSV and JSONL)."""
    decoded = decode_epc(tag.epc)
    return {
        "at": round(time.time(), 3),
        "epc": tag.epc_hex,
        "antenna": tag.antenna,
        "rssi_dbm": tag.rssi_dbm,
        "phase_deg": round(tag.phase_deg, 1) if tag.phase_deg is not None else None,
        "doppler_hz": tag.doppler_hz,
        "channel": tag.channel_index,
        "tid": tag.tid.hex() if tag.tid is not None else None,
        "scheme": decoded.scheme if decoded else None,
        "identity": decoded.pure_identity_uri if decoded else None,
    }


class TagWriter:
    """Write tag reads to ``.csv`` or ``.jsonl`` (chosen by file suffix)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        suffix = self.path.suffix.lower()
        if suffix not in (".csv", ".jsonl"):
            raise ValueError(f"unsupported capture format {suffix!r}; use .csv or .jsonl")
        self.format = suffix[1:]
        self.rows = 0
        self._handle: TextIO | None = None
        self._csv: Any = None

    def __enter__(self) -> TagWriter:
        self._handle = self.path.open("w", newline="", encoding="utf-8")
        if self.format == "csv":
            self._csv = csv.DictWriter(self._handle, fieldnames=COLUMNS)
            self._csv.writeheader()
        return self

    def write(self, tag: TagReport) -> None:
        assert self._handle is not None, "TagWriter must be used as a context manager"
        row = tag_row(tag)
        if self._csv is not None:
            self._csv.writerow(row)
        else:
            self._handle.write(json.dumps(row) + "\n")
        self.rows += 1

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
